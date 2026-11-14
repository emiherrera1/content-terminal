"""Long-form against Shorts: the niche and creator numbers split by format.

A video is a Short when it runs 3 minutes or less (SHORT_MAX_S), the same rule YouTube uses. Every figure here is built
from tracked videos (video_daily: each channel's recent uploads), so it covers most of a channel's daily views but not its
oldest back catalog; channel-level figures elsewhere (CCOS, risk, revenue) stay on all views.

format_niche   one row per niche and format: views in the last 7 days and the 7 before, growth, share of the niche's
               tracked views, uploads and creators posting in 28 days, typical views of a recent video
format_creator one row per creator and format, with the same fields plus the share of views from the creator's top video
"""
SHORT_MAX_S = 180
RECENT_MIN, RECENT_MAX = 7, 30     # "typical views of a recent video": the median at 7 to 30 days old

_FMT = f"CASE WHEN coalesce(duration_s, 0) <= {SHORT_MAX_S} THEN 'SHORTS' ELSE 'LONG' END"


def _sql(key: str) -> str:
    """key is niche_code (joined through channel_niche) or channel_id."""
    return f"""
WITH last AS (SELECT max(snapshot_date) AS d, min(snapshot_date) AS d0 FROM video_daily WHERE views_gained IS NOT NULL),
d AS (SELECT vd.*, n.niche_code, {_FMT} AS fmt FROM video_daily vd JOIN channel_niche n USING (channel_id)
      WHERE vd.views_gained IS NOT NULL),
g AS (SELECT {key}, fmt,
             sum(views_gained) FILTER (WHERE snapshot_date > last.d - 7) AS views_7d,
             sum(views_gained) FILTER (WHERE snapshot_date <= last.d - 7 AND snapshot_date > last.d - 14) AS views_prev_7d,
             sum(views_gained) FILTER (WHERE snapshot_date > last.d - 28) AS views_28d,
             any_value(last.d0 <= last.d - 13) AS has_prev
      FROM d, last GROUP BY ALL),
lv AS (SELECT video_id, arg_max(views, snapshot_date) AS views, max(snapshot_date) AS sd FROM video_snapshots GROUP BY 1),
v AS (SELECT vi.video_id, vi.channel_id, n.niche_code, {_FMT} AS fmt, lv.views,
             date_diff('day', vi.published_at::DATE, lv.sd) AS age
      FROM videos vi JOIN lv USING (video_id) JOIN channel_niche n USING (channel_id) WHERE vi.published_at IS NOT NULL),
u AS (SELECT {key}, fmt, count(*) FILTER (WHERE age < 28) AS uploads_28d,
             count(DISTINCT channel_id) FILTER (WHERE age < 28) AS creators_posting,
             median(views) FILTER (WHERE age BETWEEN {RECENT_MIN} AND {RECENT_MAX}) AS views_per_video
      FROM v GROUP BY ALL),
top AS (SELECT {key}, fmt, max(vg) / nullif(sum(vg), 0) AS top_video_share
        FROM (SELECT {key}, fmt, video_id, sum(views_gained) AS vg FROM d, last WHERE snapshot_date > last.d - 28 GROUP BY ALL)
        GROUP BY ALL)
SELECT coalesce(g.{key}, u.{key}) AS {key}, coalesce(g.fmt, u.fmt) AS fmt,
       g.views_7d, g.views_prev_7d,
       CASE WHEN g.has_prev AND g.views_prev_7d > 0 THEN g.views_7d / g.views_prev_7d - 1 END AS growth_7d,
       g.views_28d,
       g.views_28d / nullif(sum(g.views_28d) OVER (PARTITION BY coalesce(g.{key}, u.{key})), 0) AS share,
       u.uploads_28d, u.creators_posting, u.views_per_video, top.top_video_share
FROM g FULL JOIN u ON g.{key} = u.{key} AND g.fmt = u.fmt
LEFT JOIN top ON top.{key} = coalesce(g.{key}, u.{key}) AND top.fmt = coalesce(g.fmt, u.fmt)
"""


def build(con) -> str:
    got = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if not {"video_daily", "channel_niche", "videos", "video_snapshots"} <= got:
        return "no history yet"
    con.execute(f"CREATE OR REPLACE TABLE format_niche AS {_sql('niche_code')}")
    con.execute(f"CREATE OR REPLACE TABLE format_creator AS {_sql('channel_id')}")
    n = con.execute("SELECT count(DISTINCT niche_code) FROM format_niche").fetchone()[0]
    c = con.execute("SELECT count(DISTINCT channel_id) FROM format_creator").fetchone()[0]
    sh = con.execute("SELECT sum(views_28d) FILTER (WHERE fmt = 'SHORTS') / nullif(sum(views_28d), 0) FROM format_niche").fetchone()[0]
    return f"{n} niches and {c} creators split; Shorts {0 if sh is None else round(100 * sh)}% of tracked views"
