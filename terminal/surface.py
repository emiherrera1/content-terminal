"""The niche surface: how reach changes with creator size and video age, drawn like an options volatility surface.

    size  (like delta)   a creator's subscriber percentile inside their own niche, smallest to biggest; the middle is "ATM"
    age   (like expiry)  days from upload to the video's latest snapshot
    reach (like vol)     median views per 100 subscribers for the videos in that cell
    disp  (realized vol) spread of outcomes: interquartile range of ln(views), how hit-or-miss the cell is

Skew is small creators' reach over big creators' reach at the same age. A steep skew means the audience is still
rewarding new supply: the undersaturation signal. Videos older than a year are left out, because we pull recent
uploads and old videos only come from creators who post rarely.
"""

# cell grids: the pooled and sector surfaces have enough videos for 5 x 5; one niche gets a coarser 3 x 4
AGES = [(7, "7D"), (30, "1M"), (90, "3M"), (180, "6M"), (365, "1Y")]
AGES_NICHE = [(7, "7D"), (30, "1M"), (90, "3M"), (365, "1Y")]
SIZES = ["Smallest 20%", "20-40%", "Median", "60-80%", "Biggest 20%"]
SIZES_NICHE = ["Smallest third", "Middle third", "Biggest third"]
MIN_N = 8          # fewer videos than this and the cell is left blank
MIN_CREATORS = 2   # and fewer creators than this: one channel is a story, not a market
SKEW_AGE = 90      # skew and dispersion are read on videos up to 3 months old, where most of the data is

REQUIRED = ("videos", "video_snapshots", "channel_snapshots", "channel_niche")

BASE = f"""
WITH v AS (SELECT video_id, arg_max(views, snapshot_date) AS views, max(snapshot_date) AS d FROM video_snapshots GROUP BY 1),
     c AS (SELECT channel_id, arg_max(subscribers, snapshot_date) AS subs FROM channel_snapshots GROUP BY 1),
     x AS (SELECT vi.video_id, vi.channel_id, n.niche_code, substr(n.niche_code, 1, 2) AS sector_code, c.subs, v.views,
                  date_diff('day', vi.published_at::DATE, v.d) AS age,
                  percent_rank() OVER (PARTITION BY n.niche_code ORDER BY c.subs) AS pr
           FROM videos vi JOIN v USING (video_id) JOIN c USING (channel_id) JOIN channel_niche n USING (channel_id)
           WHERE c.subs > 0 AND v.views IS NOT NULL AND vi.published_at IS NOT NULL)
SELECT * FROM x WHERE age BETWEEN 0 AND 365
"""


def _have(con) -> bool:
    got = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    return all(t in got for t in REQUIRED)


def _age_case(ages) -> str:
    return "CASE " + " ".join(f"WHEN age <= {d} THEN {i}" for i, (d, _) in enumerate(ages)) + " END"


def _cells(con, scope_col: str, ages, n_sizes: int) -> list[tuple]:
    rows = con.execute(f"""
        WITH b AS ({BASE})
        SELECT {scope_col} AS scope, least(floor(pr * {n_sizes}), {n_sizes - 1})::INT AS size_i, {_age_case(ages)} AS age_i,
               count(*) AS n, median(views / subs) * 100 AS reach,
               quantile_cont(ln(views + 1), 0.75) - quantile_cont(ln(views + 1), 0.25) AS disp,
               median(subs) AS med_subs, count(DISTINCT channel_id) AS creators
        FROM b GROUP BY ALL ORDER BY ALL""").fetchall()
    return rows


def cells(con, label=lambda c: c, sector=lambda c: c) -> list[dict]:
    """One row per cell: scope (ALL, a sector code, or a niche code), its size and age position, and the reach and spread."""
    if not _have(con):
        return []
    out = []
    plans = [("all", "'ALL'", AGES, SIZES), ("sector", "sector_code", AGES, SIZES), ("niche", "niche_code", AGES_NICHE, SIZES_NICHE)]
    for level, col, ages, sizes in plans:
        for scope, si, ai, n, reach, disp, med_subs, creators in _cells(con, col, ages, len(sizes)):
            ok = n >= MIN_N and creators >= MIN_CREATORS
            name = "All niches" if level == "all" else sector(scope) if level == "sector" else label(scope)
            out.append({"level": level, "scope": scope, "scope_label": name, "size_i": si, "size": sizes[si],
                        "age_i": ai, "age": ages[ai][1], "n": n, "creators": creators, "reach": round(reach, 2) if ok else None,
                        "disp": round(disp, 3) if ok else None, "med_subs": med_subs})
    return out


SKEW_SQL = f"""
WITH b AS ({BASE}),
     t AS (SELECT *, least(floor(pr * 3), 2)::INT AS third FROM b WHERE age <= {SKEW_AGE}),
     s AS (SELECT niche_code, any_value(sector_code) AS sector_code, count(*) AS videos, count(DISTINCT channel_id) AS creators,
                  median(views / subs) FILTER (WHERE third = 0) * 100 AS small_reach,
                  median(views / subs) FILTER (WHERE third = 1) * 100 AS mid_reach,
                  median(views / subs) FILTER (WHERE third = 2) * 100 AS big_reach,
                  count(*) FILTER (WHERE third = 0) AS small_n, count(*) FILTER (WHERE third = 2) AS big_n,
                  count(DISTINCT channel_id) FILTER (WHERE third = 0) AS small_c, count(DISTINCT channel_id) FILTER (WHERE third = 2) AS big_c,
                  quantile_cont(ln(views + 1), 0.75) - quantile_cont(ln(views + 1), 0.25) AS dispersion,
                  median(views / subs) FILTER (WHERE third = 1 AND age <= 7) * 100 AS mid_reach_7d
           FROM t GROUP BY 1)
SELECT niche_code, sector_code, videos, creators, small_reach, mid_reach, big_reach,
       CASE WHEN small_n >= {MIN_N} AND big_n >= {MIN_N} AND small_c >= {MIN_CREATORS} AND big_c >= {MIN_CREATORS} AND big_reach > 0 THEN small_reach / big_reach END AS skew,
       dispersion,
       CASE WHEN mid_reach_7d > 0 THEN mid_reach / mid_reach_7d END AS term
FROM s
"""


def skew(con, label=lambda c: c, sector=lambda c: c) -> list[dict]:
    """One row per niche, steepest skew first. skew = small creators' reach / big creators' reach (videos up to 3 months);
    term = the median creator's reach at up to 3 months over their first week, how much a video keeps earning after launch."""
    if not _have(con):
        return []
    cur = con.execute(SKEW_SQL + " ORDER BY skew DESC NULLS LAST")
    cols = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d["niche"], d["sector"] = label(d["niche_code"]), sector(d["sector_code"])
        for k in ("small_reach", "mid_reach", "big_reach", "skew", "dispersion", "term"):
            d[k] = None if d[k] is None else round(d[k], 3)
        out.append(d)
    ranked = [d for d in out if d["skew"] is not None]
    for i, d in enumerate(ranked):
        d["skew_pctl"] = 100 if len(ranked) == 1 else round(100 * (len(ranked) - 1 - i) / (len(ranked) - 1))
    return out
