"""Revenue estimates and catalog value per creator. Every dollar figure here is an estimate, in three cases (low, mid, high).

Monthly revenue now
  ad        views over the last 30 days x blended RPM / 1000, where blended RPM = (1 - s) x long-form RPM + s x Shorts RPM
            and s is the share of the creator's recent views that went to Shorts (videos of 3 minutes or less)
  sponsor   sponsored videos a month (last 90 days / 3) x typical views of one long-form video x sponsor CPM / 1000
  RPM comes from TubeLab's niche estimates when the weekly pull has run (niche_rpm); until then from the published
  ranges in config/niche_economics.csv. Sponsor CPM is always the published rate card.

Catalog value (USCAP: the present value of a creator's existing uploads)
  Each video's daily views fall with age as a power law: views/day at age a+t = views/day at age a x ((a + t) / a)^-k.
  k is fitted per sector from every tracked video (log views/day on log age, within channel). Tracked videos are projected
  one by one from today's rate; the rest of the channel's daily views (its older back catalog, untracked) is projected as
  one block aged at least a year. Five years of monthly ad revenue at the blended RPM, discounted at DISCOUNT a year.
  No new uploads count: this is what the catalog is worth if the creator stopped today.

Financing capacity (house rules, stated so they can be argued with)
  advance  ADVANCE_RATE of the low-case catalog value over the first ADVANCE_MONTHS (what a catalog financier could pay up front)
  debt     the loan that DEBT_SHARE of today's low-case monthly revenue repays over DEBT_MONTHS at DEBT_RATE a year
"""
import csv
import datetime as dt
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ECONOMICS = ROOT / "config" / "niche_economics.csv"

SHORT_MAX_S = 180
HORIZON_MONTHS = 60
DISCOUNT = 0.12
BACK_AGE_MIN = 365
K_DEFAULT, K_MIN, K_MAX, K_MIN_VIDEOS = 1.0, 0.3, 2.0, 50
ADVANCE_RATE, ADVANCE_MONTHS = 0.6, 36
DEBT_SHARE, DEBT_MONTHS, DEBT_RATE = 0.3, 36, 0.15
MONTH_DAYS = 30.4
CASES = ("low", "mid", "high")


def economics(path: Path = ECONOMICS) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(line for line in f if not line.startswith("#")))
    out = {}
    for r in rows:
        num = {k: float(r[k]) for k in ("rpm_low", "rpm_high", "shorts_rpm_low", "shorts_rpm_high", "sponsor_cpm_low", "sponsor_cpm_high")}
        num["rpm_mid"] = math.sqrt(num["rpm_low"] * num["rpm_high"])
        num["shorts_rpm_mid"] = math.sqrt(num["shorts_rpm_low"] * num["shorts_rpm_high"])
        num["sponsor_cpm_mid"] = math.sqrt(num["sponsor_cpm_low"] * num["sponsor_cpm_high"])
        out[r["niche_code"]] = {**num, "rpm_source": "Published range", "rpm_basis": r["rpm_basis"], "sponsor_basis": r["sponsor_basis"]}
    return out


def rates_for(code: str | None, econ: dict[str, dict], tubelab: dict[str, dict] | None = None) -> dict | None:
    """The niche's own row, else its nearest parent, else the median of every row under it (a sector-only code)."""
    if not code:
        return None
    base = None
    parts = code.split(".")
    for n in range(len(parts), 0, -1):
        if ".".join(parts[:n]) in econ:
            base = dict(econ[".".join(parts[:n])])
            break
    if base is None:
        kids = [v for k, v in econ.items() if k.startswith(code + ".")]
        if not kids:
            return None
        base = {k: float(np.median([v[k] for v in kids])) for k in kids[0] if isinstance(kids[0][k], float)}
        base.update(rpm_source="Published range", rpm_basis="Median of the sector's niches", sponsor_basis="Median of the sector's niches")
    t = (tubelab or {}).get(code)
    if t and t.get("rpm_mid") is not None:
        base.update(rpm_low=t["rpm_low"], rpm_mid=t["rpm_mid"], rpm_high=t["rpm_high"], rpm_source="TubeLab estimate",
                    rpm_basis=f"TubeLab, {t['channels']} channels, as of {t['as_of']}")
    return base


def fit_decay(videos: pd.DataFrame) -> float | None:
    """k from log(views/day) on log(age), demeaned within channel. Videos need an age of 7+ days and positive gains."""
    v = videos[(videos["age"] >= 7) & (videos["daily"] > 0)]
    if len(v) < K_MIN_VIDEOS:
        return None
    x, y = np.log(v["age"].astype(float)), np.log(v["daily"].astype(float))
    x = x - x.groupby(v["channel_id"]).transform("mean")
    y = y - y.groupby(v["channel_id"]).transform("mean")
    if (x ** 2).sum() <= 0:
        return None
    return float(min(max(-(x * y).sum() / (x ** 2).sum(), K_MIN), K_MAX))


def project(tracked: list[tuple[float, float]], rest_daily: float, rest_age: float, k: float,
            months: int = HORIZON_MONTHS) -> np.ndarray:
    """Monthly views from existing videos. tracked = [(age_days, views_per_day_now)]; rest is the untracked back catalog."""
    t = (np.arange(months) + 0.5) * MONTH_DAYS
    out = np.zeros(months)
    for age, daily in tracked + [(rest_age, rest_daily)]:
        if daily > 0 and age > 0:
            out += daily * ((age + t) / age) ** -k * MONTH_DAYS
    return out


def pv(monthly: np.ndarray, rate: float = DISCOUNT) -> float:
    m = np.arange(1, len(monthly) + 1)
    return float((monthly / (1 + rate) ** (m / 12)).sum())


def annuity_pv(payment: float, months: int = DEBT_MONTHS, rate: float = DEBT_RATE) -> float:
    r = rate / 12
    return payment * (1 - (1 + r) ** -months) / r


def half_life(monthly: np.ndarray) -> int | None:
    if not len(monthly) or monthly[0] <= 0:
        return None
    hit = np.nonzero(monthly <= monthly[0] / 2)[0]
    return int(hit[0]) + 1 if len(hit) else None


def _has(con, *tables) -> bool:
    got = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    return set(tables) <= got


def _tubelab(con) -> dict[str, dict]:
    if not _has(con, "niche_rpm"):
        return {}
    cur = con.execute("SELECT * FROM niche_rpm WHERE as_of = (SELECT max(as_of) FROM niche_rpm)")
    cols = [d[0] for d in cur.description]
    return {r["niche_code"]: r for r in (dict(zip(cols, x)) for x in cur.fetchall())}


COLUMNS = """channel_id VARCHAR, niche_code VARCHAR, views_30d DOUBLE, shorts_share DOUBLE, rpm_source VARCHAR, rpm_basis VARCHAR,
    rpm_low DOUBLE, rpm_mid DOUBLE, rpm_high DOUBLE, ad_low DOUBLE, ad_mid DOUBLE, ad_high DOUBLE, sponsored_per_month DOUBLE,
    views_per_video DOUBLE, sponsor_cpm_mid DOUBLE, sponsor_low DOUBLE, sponsor_mid DOUBLE, sponsor_high DOUBLE,
    total_low DOUBLE, total_mid DOUBLE, total_high DOUBLE, decay_k DOUBLE, catalog_views_5y DOUBLE, catalog_half_life_months INTEGER,
    catalog_low DOUBLE, catalog_mid DOUBLE, catalog_high DOUBLE, advance_capacity DOUBLE, debt_capacity DOUBLE"""


def build(con, today: dt.date | None = None) -> str:
    con.execute(f"CREATE OR REPLACE TABLE creator_revenue ({COLUMNS})")
    con.execute("CREATE OR REPLACE TABLE niche_decay (sector VARCHAR, decay_k DOUBLE, videos INTEGER, fitted BOOLEAN)")
    if not _has(con, "channel_daily", "channel_niche", "videos", "video_snapshots"):
        return "no history yet"
    last = con.execute("SELECT max(snapshot_date) FROM channel_daily").fetchone()[0]
    if last is None:
        return "no history yet"
    today = today or last
    econ, tl = economics(), _tubelab(con)

    chan = con.execute("""
        SELECT n.channel_id, n.niche_code, n.sector,
               sum(greatest(d.views_gained, 0)) FILTER (WHERE d.snapshot_date > ? - 30) AS v30,
               count(*) FILTER (WHERE d.snapshot_date > ? - 30) AS n30,
               sum(greatest(d.views_gained, 0)) FILTER (WHERE d.snapshot_date > ? - 7)
                 / greatest(sum(coalesce(d.days_since_prev, 1)) FILTER (WHERE d.snapshot_date > ? - 7), 1) AS daily
        FROM channel_niche n JOIN channel_daily d USING (channel_id)
        WHERE d.views_gained IS NOT NULL GROUP BY ALL""", [last] * 4).df()
    vids = con.execute("""
        WITH w AS (SELECT video_id, max(views) AS v1, min(views) AS v0, max(snapshot_date) AS d1, min(snapshot_date) AS d0
                   FROM video_snapshots WHERE snapshot_date > ? - 8 GROUP BY 1)
        SELECT v.channel_id, v.duration_s <= ? AS short, w.v1 AS views,
               greatest(date_diff('day', v.published_at::DATE, ?), 1) AS age,
               CASE WHEN w.d1 > w.d0 THEN (w.v1 - w.v0) / date_diff('day', w.d0, w.d1) END AS daily
        FROM videos v JOIN w USING (video_id)""", [last, SHORT_MAX_S, last]).df()
    vids["daily"] = vids["daily"].fillna(0).clip(lower=0)
    vids["short"] = vids["short"].fillna(False).astype(bool)
    sector_of = chan.drop_duplicates("channel_id").set_index("channel_id")["sector"]
    vids["sector"] = vids["channel_id"].map(sector_of)

    k_of, decay_rows = {}, []
    for sec, g in vids.groupby("sector"):
        k = fit_decay(g)
        k_of[sec] = k if k is not None else K_DEFAULT
        decay_rows.append((sec, k_of[sec], int(len(g)), k is not None))
    if decay_rows:
        con.executemany("INSERT INTO niche_decay VALUES (?, ?, ?, ?)", decay_rows)

    spons = {}
    if _has(con, "creator_business"):
        spons = dict(con.execute("SELECT channel_id, sponsored_videos_90d FROM creator_business").fetchall())
    by_ch = dict(tuple(vids.groupby("channel_id")))

    out = []
    for c in chan.itertuples(index=False):
        r = rates_for(c.niche_code, econ, tl)
        if r is None or c.daily is None or pd.isna(c.daily):
            continue
        v = by_ch.get(c.channel_id, vids.iloc[0:0])
        gain = v["daily"].sum()
        s = float(v.loc[v["short"], "daily"].sum() / gain) if gain > 0 else (float(v["short"].mean()) if len(v) else 0.0)
        views_30d = float(c.v30 * 30 / max(c.n30, 1)) if c.n30 and not pd.isna(c.v30) else float(c.daily) * 30
        k = k_of.get(c.sector, K_DEFAULT)
        long_v, short_v = v[~v["short"]], v[v["short"]]
        rest = max(float(c.daily) - gain, 0.0)
        rest_age = float(max(v["age"].max() if len(v) else 0, BACK_AGE_MIN))
        s_up = float(v["short"].mean()) if len(v) else 0.0   # the back catalog's mix, taken from the upload mix
        m_long = project(list(zip(long_v["age"], long_v["daily"])), rest * (1 - s_up), rest_age, k)
        m_short = project(list(zip(short_v["age"], short_v["daily"])), rest * s_up, rest_age, k)

        lv = long_v[(long_v["age"] >= 30) & (long_v["age"] <= 120)]["views"]
        if not len(lv):
            lv = long_v["views"]
        per_video = float(lv.median()) if len(lv) else 0.0
        per_month = float(spons.get(c.channel_id) or 0) / 3

        row = {"channel_id": c.channel_id, "niche_code": c.niche_code, "views_30d": views_30d, "shorts_share": s,
               "rpm_source": r["rpm_source"], "rpm_basis": r["rpm_basis"], "sponsored_per_month": per_month,
               "views_per_video": per_video, "sponsor_cpm_mid": r["sponsor_cpm_mid"], "decay_k": k,
               "catalog_views_5y": float((m_long + m_short).sum()), "catalog_half_life_months": half_life(m_long + m_short)}
        for case in CASES:
            rpm, srpm, cpm = r[f"rpm_{case}"], r[f"shorts_rpm_{case}"], r[f"sponsor_cpm_{case}"]
            row[f"rpm_{case}"] = rpm
            row[f"ad_{case}"] = views_30d * ((1 - s) * rpm + s * srpm) / 1000
            row[f"sponsor_{case}"] = per_month * per_video * cpm / 1000
            row[f"total_{case}"] = row[f"ad_{case}"] + row[f"sponsor_{case}"]
            row[f"catalog_{case}"] = pv((m_long * rpm + m_short * srpm) / 1000)
        low_rev = (m_long * r["rpm_low"] + m_short * r["shorts_rpm_low"]) / 1000
        row["advance_capacity"] = ADVANCE_RATE * pv(low_rev[:ADVANCE_MONTHS])
        row["debt_capacity"] = annuity_pv(DEBT_SHARE * row["total_low"])
        out.append(row)
    if out:
        cols = [c.split()[0] for c in COLUMNS.replace("\n", " ").split(",")]
        con.executemany(f"INSERT INTO creator_revenue VALUES ({', '.join('?' * len(cols))})", [[o[c] for c in cols] for o in out])
    ks = ", ".join(f"{s} k={k:.2f}" for s, k, _, _ in decay_rows)
    return f"{len(out)} creators priced; decay {ks}"
