"""Creator metrics and the Creator Viability Score (Formula Library 5.3-5.7, 5.10-5.11, alert 7.7).

Tables:
  creator_metrics   latest day: growth, cadence, regularity, consistency, hit dependence, audience depth, topic focus
  creator_cvs       the five pillars, the score and the automatic gates
  cvs_history       one row per creator per day the score was computed (kept across rebuilds)
  creator_watchlist the tier, which requires the score to hold for the configured number of days
  creator_review    manual gates a person confirms: independent, clean record (kept across rebuilds)
  creator_alerts    tier changes since the previous computed day
"""
from . import formulas


def _pct(x: str, part: str) -> str:
    """Mid-rank percentile of x within a partition (0 to 1); nulls get 0.5 so missing data is neutral."""
    return (f"CASE WHEN {x} IS NULL THEN 0.5 ELSE "
            f"(rank() OVER (PARTITION BY {part}, {x} IS NULL ORDER BY {x}) - 0.5) "
            f"/ count(*) OVER (PARTITION BY {part}, {x} IS NULL) END")


def sql(cfg: dict) -> str:
    c = cfg["cvs"]
    w, g, win = c["weights"], c["gates"], c["windows"]
    n, days = int(win["last_videos"]), int(win["upload_days"])
    return f"""
CREATE TABLE IF NOT EXISTS creator_review (
    channel_id VARCHAR PRIMARY KEY, independent BOOLEAN, clean_record BOOLEAN,
    reviewed_by VARCHAR, reviewed_on DATE, notes VARCHAR);
CREATE TABLE IF NOT EXISTS cvs_history (
    d DATE, channel_id VARCHAR, cvs DOUBLE, tier VARCHAR, PRIMARY KEY (d, channel_id));

CREATE OR REPLACE TABLE creator_metrics AS
WITH last AS (SELECT max(snapshot_date) AS ld FROM channel_snapshots),
snap AS (
    SELECT s.channel_id,
           arg_max(s.subscribers, s.snapshot_date) AS subscribers,
           arg_max(s.views, s.snapshot_date) AS views,
           arg_min(s.views, s.snapshot_date) FILTER (WHERE s.snapshot_date >= last.ld - 28) AS views_28d_ago
    FROM channel_snapshots s JOIN last ON true GROUP BY s.channel_id
),
up AS (
    SELECT v.channel_id, CAST(v.published_at AS DATE) AS pd FROM videos v JOIN last ON CAST(v.published_at AS DATE) > last.ld - {days}
),
gaps AS (SELECT channel_id, pd - lag(pd) OVER (PARTITION BY channel_id ORDER BY pd) AS gap FROM up),
cadence AS (                                   -- 5.3
    SELECT u.channel_id, count(*) / ({days} / 7.0) AS uploads_per_week,
           (SELECT max(CAST(published_at AS DATE)) FROM videos v WHERE v.channel_id = u.channel_id) AS last_upload
    FROM up u GROUP BY u.channel_id
),
regular AS (
    SELECT channel_id, CASE WHEN count(gap) >= 2 THEN greatest(0, 1 - stddev_samp(gap) / nullif(avg(gap), 0)) END AS regularity
    FROM gaps GROUP BY channel_id
),
last_n AS (
    SELECT m.*, row_number() OVER (PARTITION BY m.channel_id ORDER BY v.published_at DESC) AS rn
    FROM video_metrics m JOIN videos v USING (video_id)
),
shape AS (                                     -- 5.4, 5.6
    SELECT channel_id,
           CASE WHEN count(outlier_score) >= 5
                THEN greatest(0, 1 - stddev_samp(outlier_score) / nullif(avg(outlier_score), 0))
                ELSE greatest(0, 1 - stddev_samp(views) FILTER (WHERE age >= 7) / nullif(avg(views) FILTER (WHERE age >= 7), 0))
           END AS consistency,
           median(views) AS median_views, avg(relative_er) AS relative_er, count(*) AS videos_used
    FROM last_n WHERE rn <= {n} GROUP BY channel_id
),
gini AS (                                      -- 5.5 Gini of the last N videos' views
    SELECT channel_id, CASE WHEN count(*) >= 2 AND sum(views) > 0 THEN
             2 * sum(r * views) / (count(*) * sum(views)) - (count(*) + 1.0) / count(*) END AS gini
    FROM (SELECT channel_id, views, row_number() OVER (PARTITION BY channel_id ORDER BY views) AS r
          FROM last_n WHERE rn <= {n}) GROUP BY channel_id
),
gains90 AS (
    SELECT d.channel_id, d.video_id, sum(d.views_gained) AS g
    FROM video_daily d JOIN last ON d.snapshot_date > last.ld - {days}
    WHERE d.views_gained IS NOT NULL GROUP BY ALL
),
hits AS (SELECT channel_id, max(g) / nullif(sum(g), 0) AS top_video_share FROM gains90 GROUP BY channel_id),
topics AS (                                    -- 5.7 focus across sub-niches (or the deepest code given)
    SELECT g.channel_id, c.topic_code, sum(g.g) AS g
    FROM gains90 g JOIN video_classifications c USING (video_id)
    WHERE c.topic_code <> 'unclassified' GROUP BY ALL
),
focus AS (
    SELECT channel_id, count(*) AS topics_used,
           CASE WHEN count(*) <= 1 THEN 1.0
                ELSE 1 - (-sum((g / tot) * ln(g / tot))) / ln(count(*)) END AS focus
    FROM (SELECT *, sum(g) OVER (PARTITION BY channel_id) AS tot FROM topics WHERE g > 0) GROUP BY channel_id
),
primary_niche AS (   -- the niche with the most of the creator's views; the channel's own classification otherwise
    SELECT channel_id, arg_max(niche, g) AS niche
    FROM (SELECT g.channel_id, c.niche, sum(g.g) AS g FROM gains90 g JOIN video_classifications c USING (video_id)
          WHERE c.niche IS NOT NULL GROUP BY ALL) GROUP BY channel_id
)
SELECT ch.channel_id, ch.title,
       coalesce(p.niche, n.niche_code) AS niche,
       s.subscribers, s.views - s.views_28d_ago AS views_28d,
       s.views / nullif(s.views_28d_ago, 0) - 1 AS growth_28d,
       ca.uploads_per_week, ca.last_upload, r.regularity, sh.consistency,
       h.top_video_share, gi.gini,
       sh.median_views / nullif(s.subscribers, 0) AS activation, sh.relative_er, sh.videos_used,
       f.focus, f.topics_used
FROM channels ch
LEFT JOIN snap s USING (channel_id)
LEFT JOIN cadence ca USING (channel_id)
LEFT JOIN regular r USING (channel_id)
LEFT JOIN shape sh USING (channel_id)
LEFT JOIN gini gi USING (channel_id)
LEFT JOIN hits h USING (channel_id)
LEFT JOIN focus f USING (channel_id)
LEFT JOIN primary_niche p USING (channel_id)
LEFT JOIN channel_niche n USING (channel_id);

CREATE OR REPLACE TABLE creator_cvs AS
WITH m AS (
    SELECT cm.*, cm.growth_28d - median(cm.growth_28d) OVER (PARTITION BY cm.niche) AS vs_niche,
           CASE WHEN cm.regularity IS NULL THEN cm.consistency WHEN cm.consistency IS NULL THEN cm.regularity
                ELSE (cm.regularity + cm.consistency) / 2 END AS steadiness,
           o.ccos
    FROM creator_metrics cm LEFT JOIN niche_opportunity o ON o.niche_code = cm.niche
),
p AS (
    SELECT *,
        {float(w['momentum'])} * {_pct('vs_niche', 'niche')} AS momentum,
        {float(w['consistency'])} * {_pct('steadiness', 'niche')} AS consistency_pillar,
        {float(w['audience_depth'])} * ({_pct('activation', 'niche')} + {_pct('relative_er', 'niche')}) / 2 AS audience_depth,
        {float(w['market_opportunity'])} * {_pct('ccos', '1')} AS market_opportunity,
        {float(w['hit_dependence'])} * (1 - {_pct('top_video_share', 'niche')}) AS hit_dependence
    FROM m
)
SELECT p.*, momentum + consistency_pillar + audience_depth + market_opportunity + hit_dependence AS cvs,
       coalesce(subscribers >= {int(g['min_subscribers'])}, false)
         AND coalesce(views_28d >= {int(g['min_views_28d'])}, false)
         AND coalesce(last_upload >= (SELECT max(snapshot_date) FROM channel_snapshots) - {int(g['max_days_since_upload'])}, false)
         AS auto_gates,
       coalesce(rv.independent AND rv.clean_record, false) AS manual_gates
FROM p LEFT JOIN creator_review rv USING (channel_id);
"""


def watchlist_sql(cfg: dict) -> str:
    t = cfg["cvs"]["tiers"]
    return f"""
CREATE OR REPLACE TABLE creator_watchlist AS
WITH last AS (SELECT max(d) AS ld FROM cvs_history),
recent AS (   -- the last N computed days for each creator, up to the longest tier window
    SELECT h.*, row_number() OVER (PARTITION BY h.channel_id ORDER BY h.d DESC) AS k
    FROM cvs_history h JOIN last ON h.d <= last.ld
),
held AS (
    SELECT channel_id,
        count(*) FILTER (WHERE k <= {int(t['watch']['days'])} AND cvs >= {float(t['watch']['score'])}) = {int(t['watch']['days'])} AS watch_held,
        count(*) FILTER (WHERE k <= {int(t['priority']['days'])} AND cvs >= {float(t['priority']['score'])}) = {int(t['priority']['days'])} AS priority_held,
        count(*) FILTER (WHERE k <= {int(t['diligence_ready']['days'])} AND cvs >= {float(t['diligence_ready']['score'])}) = {int(t['diligence_ready']['days'])} AS diligence_held
    FROM recent GROUP BY channel_id
)
SELECT c.channel_id, c.title, c.niche, c.cvs, c.momentum, c.consistency_pillar, c.audience_depth,
       c.market_opportunity, c.hit_dependence, c.auto_gates, c.manual_gates,
       CASE WHEN NOT c.auto_gates THEN NULL
            WHEN h.diligence_held AND c.manual_gates THEN 'Diligence-ready'
            WHEN h.priority_held THEN 'Priority'
            WHEN h.watch_held THEN 'Watch' END AS tier,
       h.diligence_held AND NOT c.manual_gates AS needs_review
FROM creator_cvs c LEFT JOIN held h USING (channel_id);
"""


ALERTS = """
CREATE OR REPLACE TABLE creator_alerts AS
WITH last AS (SELECT max(d) AS ld FROM cvs_history),
prev AS (
    SELECT h.channel_id, arg_max(h.tier, h.d) AS prev_tier
    FROM cvs_history h JOIN last ON h.d < last.ld GROUP BY h.channel_id
)
SELECT last.ld AS d, 'tier_change' AS kind, w.channel_id, w.title, p.prev_tier, w.tier, w.cvs
FROM creator_watchlist w JOIN last ON true LEFT JOIN prev p USING (channel_id)
WHERE coalesce(w.tier, '') <> coalesce(p.prev_tier, '')
  AND (w.tier IS NOT NULL OR p.prev_tier IS NOT NULL);
"""


def build(con, cfg: dict | None = None) -> None:
    cfg = cfg or formulas.config()
    con.execute(sql(cfg))
    day = con.execute("SELECT max(snapshot_date) FROM channel_snapshots").fetchone()[0]
    if day is None:
        return
    con.execute("INSERT OR REPLACE INTO cvs_history SELECT ?, channel_id, cvs, NULL FROM creator_cvs WHERE cvs IS NOT NULL",
                [day])
    con.execute(watchlist_sql(cfg))
    con.execute("UPDATE cvs_history SET tier = w.tier FROM creator_watchlist w "
                "WHERE cvs_history.channel_id = w.channel_id AND cvs_history.d = ?", [day])
    con.execute(ALERTS)


def review(con, channel_id: str, independent: bool, clean_record: bool, by: str, notes: str = "") -> None:
    """Record the manual gates for a creator (needed for Diligence-ready)."""
    import datetime as dt
    con.execute("CREATE TABLE IF NOT EXISTS creator_review (channel_id VARCHAR PRIMARY KEY, independent BOOLEAN, "
                "clean_record BOOLEAN, reviewed_by VARCHAR, reviewed_on DATE, notes VARCHAR)")
    con.execute("INSERT OR REPLACE INTO creator_review VALUES (?, ?, ?, ?, ?, ?)",
                [channel_id, independent, clean_record, by, dt.date.today(), notes])
