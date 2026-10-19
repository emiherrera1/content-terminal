"""The terminal's numbers, as SQL views over the daily snapshots.

niche_daily     views gained per CTNCS niche per day (the niche index)
niche_momentum  last 7 days of gains vs the 7 before, per niche
screener        each channel's 28-day growth against its niche's median
alerts          channels whose daily gain is 3+ standard deviations above their trailing 28 days
                and at least double their typical day
niche_opportunity  per niche: TVE (content minutes, 28d), SI (saturation, 0-1), NGP (growth ratio),
                   CCOS (opportunity score), CHG% of CCOS over 7 days, TREND, RISK

Content-level views (each video counted by its own topic, not its channel's):
video_daily          views gained per video per day
content_daily        views gained per CTNCS topic per day, at every level (sector, category, niche, sub-niche)
content_format_daily the same split by format (Tutorials, Reviews, DIY...)

Channels classified only to category (level 2) roll up under that code, and
unclassified channels are left out of niche figures but stay in the screener.
"""

VIEWS = """
CREATE OR REPLACE VIEW channel_daily AS
SELECT
    s.snapshot_date,
    s.channel_id,
    s.subscribers,
    s.views,
    s.views - lag(s.views) OVER w AS views_gained,
    s.subscribers - lag(s.subscribers) OVER w AS subs_gained,
    s.snapshot_date - lag(s.snapshot_date) OVER w AS days_since_prev
FROM channel_snapshots s
WINDOW w AS (PARTITION BY s.channel_id ORDER BY s.snapshot_date);

CREATE OR REPLACE VIEW channel_niche AS
SELECT channel_id,
       coalesce(niche, category, sector) AS niche_code,
       sector
FROM classifications
WHERE ctncs_code <> 'unclassified';

CREATE OR REPLACE VIEW niche_daily AS
SELECT d.snapshot_date,
       n.niche_code,
       count(*)              AS channels,
       sum(d.views_gained)   AS views_gained,
       sum(d.subs_gained)    AS subs_gained,
       sum(d.subscribers)    AS subscribers
FROM channel_daily d
JOIN channel_niche n USING (channel_id)
WHERE d.views_gained IS NOT NULL AND d.days_since_prev = 1
GROUP BY ALL;

CREATE OR REPLACE VIEW niche_momentum AS
WITH bounds AS (SELECT max(snapshot_date) AS last_day FROM niche_daily)
SELECT niche_code,
       sum(views_gained) FILTER (WHERE snapshot_date >  last_day - 7)                                     AS views_last_7d,
       sum(views_gained) FILTER (WHERE snapshot_date <= last_day - 7 AND snapshot_date > last_day - 14) AS views_prev_7d,
       views_last_7d / nullif(views_prev_7d, 0) - 1                                                      AS momentum
FROM niche_daily, bounds
GROUP BY niche_code;

CREATE OR REPLACE VIEW video_latest AS
SELECT v.channel_id, v.video_id, v.published_at, s.views
FROM videos v
JOIN video_snapshots s USING (video_id)
WHERE s.snapshot_date = (SELECT max(snapshot_date) FROM video_snapshots);

CREATE OR REPLACE VIEW channel_video_stats AS
SELECT channel_id,
       count(*) FILTER (WHERE published_at > now() - INTERVAL 28 DAY) AS uploads_28d,
       median(views)                                                    AS median_video_views,
       avg((views > 3 * med)::INT)                                      AS outlier_rate
FROM (SELECT *, median(views) OVER (PARTITION BY channel_id) AS med FROM video_latest)
GROUP BY channel_id;

CREATE OR REPLACE VIEW screener AS
WITH last AS (SELECT max(snapshot_date) AS d FROM channel_snapshots),
base AS (  -- each channel's earliest snapshot in the 28-day window
    SELECT channel_id,
           arg_min(views, snapshot_date)       AS views0,
           arg_min(subscribers, snapshot_date) AS subs0
    FROM channel_snapshots, last
    WHERE snapshot_date >= last.d - 28
    GROUP BY channel_id
),
growth AS (
    SELECT s.channel_id,
           s.subscribers,
           s.views,
           s.views / nullif(b.views0, 0) - 1      AS view_growth_28d,
           s.subscribers / nullif(b.subs0, 0) - 1 AS sub_growth_28d
    FROM channel_snapshots s
    JOIN last ON s.snapshot_date = last.d
    LEFT JOIN base b USING (channel_id)
)
SELECT c.title,
       g.channel_id,
       k.ctncs_code,
       n.niche_code,
       g.subscribers,
       g.views,
       g.view_growth_28d,
       g.sub_growth_28d,
       g.view_growth_28d - median(g.view_growth_28d) OVER (PARTITION BY n.niche_code) AS growth_vs_niche,
       v.uploads_28d,
       v.median_video_views,
       v.outlier_rate
FROM growth g
JOIN channels c USING (channel_id)
LEFT JOIN classifications k USING (channel_id)
LEFT JOIN channel_niche n USING (channel_id)
LEFT JOIN channel_video_stats v USING (channel_id);

CREATE OR REPLACE VIEW alerts AS
WITH scored AS (
    SELECT d.*,
           avg(views_gained)        OVER t AS mean_28d,
           stddev_samp(views_gained) OVER t AS sd_28d,
           count(views_gained)      OVER t AS days_history
    FROM channel_daily d
    WHERE views_gained IS NOT NULL AND days_since_prev = 1
    WINDOW t AS (PARTITION BY channel_id ORDER BY snapshot_date ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING)
)
SELECT s.snapshot_date, c.title, s.channel_id, n.niche_code, s.views_gained,
       round(s.mean_28d) AS typical_daily_views,
       (s.views_gained - s.mean_28d) / nullif(s.sd_28d, 0) AS z_score
FROM scored s
JOIN channels c USING (channel_id)
LEFT JOIN channel_niche n USING (channel_id)
WHERE s.days_history >= 7
  AND (s.views_gained - s.mean_28d) / nullif(s.sd_28d, 0) >= 3
  AND s.views_gained >= 2 * s.mean_28d   -- a real jump, not steady growth in a hot niche
  AND s.snapshot_date = (SELECT max(snapshot_date) FROM channel_snapshots);

-- Opportunity metrics (measurable versions of the prototype's TVE, SI, NGP, CCOS, RISK).
-- One row per niche per day once 14 days of history exist.
CREATE OR REPLACE VIEW niche_opportunity_daily AS
WITH up AS (
    SELECT n.niche_code, CAST(v.published_at AS DATE) AS d,
           count(*) AS uploads, sum(v.duration_s) / 60.0 AS minutes
    FROM videos v JOIN channel_niche n USING (channel_id)
    GROUP BY ALL
),
base AS (
    SELECT nd.snapshot_date AS d, nd.niche_code, nd.views_gained,
           coalesce(up.uploads, 0) AS uploads, coalesce(up.minutes, 0) AS minutes
    FROM niche_daily nd LEFT JOIN up ON up.niche_code = nd.niche_code AND up.d = nd.snapshot_date
),
roll AS (
    SELECT *,
           sum(views_gained) OVER w7  AS views_7d,
           sum(views_gained) OVER wp7 AS views_prev_7d,
           sum(views_gained) OVER w28 AS views_28d,
           sum(uploads)      OVER w28 AS uploads_28d,
           sum(minutes)      OVER w28 AS minutes_28d,
           count(*)          OVER w28 AS days_in_window
    FROM base
    WINDOW w7  AS (PARTITION BY niche_code ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW),
           wp7 AS (PARTITION BY niche_code ORDER BY d ROWS BETWEEN 13 PRECEDING AND 7 PRECEDING),
           w28 AS (PARTITION BY niche_code ORDER BY d ROWS BETWEEN 27 PRECEDING AND CURRENT ROW)
),
supply AS (  -- uploads per 1,000 views gained: more videos chasing the same views = more crowded
    SELECT *, uploads_28d / nullif(views_28d / 1000.0, 0) AS supply_ratio
    FROM roll WHERE days_in_window >= 14
),
scored AS (
    SELECT *,
           -- SI: 0 to 1, where the median niche that day is 0.5
           supply_ratio / nullif(supply_ratio + median(supply_ratio) OVER (PARTITION BY d), 0) AS si,
           -- NGP: last 7 days of views over the 7 before (1.0 = flat)
           views_7d / nullif(views_prev_7d, 0) AS ngp
    FROM supply
)
SELECT d AS snapshot_date, niche_code, minutes_28d AS tve, uploads_28d, views_28d,
       si, ngp,
       20 * ngp * (1 - si) AS ccos,   -- trend and saturation each counted once; 20x puts the median niche near 10
       20 * ngp * (1 - si) / nullif(lag(20 * ngp * (1 - si), 7) OVER (PARTITION BY niche_code ORDER BY d), 0) - 1 AS ccos_chg_7d
FROM scored;

CREATE OR REPLACE VIEW niche_opportunity AS
WITH last AS (SELECT max(snapshot_date) AS d FROM niche_opportunity_daily),
vol AS (   -- volatility of daily views over 28 days (coefficient of variation)
    SELECT niche_code, stddev_samp(views_gained) / nullif(avg(views_gained), 0) AS volatility
    FROM niche_daily, last WHERE snapshot_date > last.d - 28 GROUP BY niche_code
),
ch AS (
    SELECT n.niche_code, d.channel_id, sum(d.views_gained) AS v
    FROM channel_daily d JOIN channel_niche n USING (channel_id), last
    WHERE d.snapshot_date > last.d - 28 AND d.views_gained IS NOT NULL
    GROUP BY ALL
),
conc AS (  -- CR3: share of the niche's views from its top 3 channels
    SELECT niche_code, sum(v) FILTER (WHERE rk <= 3) / nullif(sum(v), 0) AS cr3
    FROM (SELECT *, row_number() OVER (PARTITION BY niche_code ORDER BY v DESC) AS rk FROM ch)
    GROUP BY niche_code
),
risk AS (
    SELECT v.niche_code, v.volatility, c.cr3,
           (percent_rank() OVER (ORDER BY v.volatility) + percent_rank() OVER (ORDER BY c.cr3)) / 2 AS risk_score
    FROM vol v JOIN conc c USING (niche_code)
)
SELECT o.niche_code, o.tve, o.uploads_28d, o.views_28d, o.si, o.ngp, o.ccos, o.ccos_chg_7d,
       CASE WHEN o.ccos_chg_7d > 0.03 THEN 'UP' WHEN o.ccos_chg_7d < -0.03 THEN 'DOWN' ELSE 'STABLE' END AS trend,
       r.volatility, r.cr3,
       CASE ntile(3) OVER (ORDER BY r.risk_score) WHEN 1 THEN 'LOW' WHEN 2 THEN 'MEDIUM' ELSE 'HIGH' END AS risk
FROM niche_opportunity_daily o
JOIN last ON o.snapshot_date = last.d
LEFT JOIN risk r ON r.niche_code = o.niche_code;
-- Content level: each video counts toward its own topic, whatever its channel's niche.
CREATE OR REPLACE VIEW video_daily AS
WITH s AS (
    SELECT *, lag(views) OVER w AS prev_views, lag(likes) OVER w AS prev_likes, lag(comments) OVER w AS prev_comments,
           lag(snapshot_date) OVER w AS prev_date
    FROM video_snapshots
    WINDOW w AS (PARTITION BY video_id ORDER BY snapshot_date)
),
g AS (
    SELECT s.*, v.channel_id, v.published_at, v.duration_s,
           s.snapshot_date - s.prev_date AS gap,
           CASE WHEN s.prev_date IS NULL AND CAST(v.published_at AS DATE) >= s.snapshot_date - 1 THEN 1 END AS first_day
    FROM s JOIN videos v USING (video_id)
)
SELECT snapshot_date, video_id, channel_id, published_at, duration_s, views,
       -- 1.1 one day apart; 1.2 first snapshot of a new upload counts in full; 1.4 short gaps spread evenly;
       -- 1.3 negative corrections (YouTube removing invalid views) clipped to 0
       CASE WHEN gap = 1 THEN greatest(views - prev_views, 0)
            WHEN gap BETWEEN 2 AND 7 THEN CAST(round(greatest(views - prev_views, 0) / gap) AS BIGINT)
            WHEN first_day = 1 THEN views
       END AS views_gained,
       CASE WHEN gap = 1 THEN greatest(likes - prev_likes, 0)
            WHEN gap BETWEEN 2 AND 7 THEN CAST(round(greatest(likes - prev_likes, 0) / gap) AS BIGINT)
            WHEN first_day = 1 THEN likes END AS likes_gained,
       CASE WHEN gap = 1 THEN greatest(comments - prev_comments, 0)
            WHEN gap BETWEEN 2 AND 7 THEN CAST(round(greatest(comments - prev_comments, 0) / gap) AS BIGINT)
            WHEN first_day = 1 THEN comments END AS comments_gained,
       coalesce(gap BETWEEN 2 AND 7, false) AS imputed,
       coalesce(views < prev_views, false) AS corrected
FROM g;

CREATE OR REPLACE VIEW video_topic_daily AS
SELECT d.*, c.topic_code, c.sector, c.category, c.niche, c.subniche, c.format_id, c.format_label,
       coalesce(d.duration_s, 0) <= 180 AS is_short,
       CAST(d.published_at AS DATE) = d.snapshot_date OR CAST(d.published_at AS DATE) = d.snapshot_date - 1 AS is_new
FROM video_daily d JOIN video_classifications c USING (video_id)
WHERE c.topic_code <> 'unclassified' AND d.views_gained IS NOT NULL;

CREATE OR REPLACE VIEW content_daily AS
SELECT snapshot_date, x.level AS level, x.code AS code,
       sum(views_gained)                          AS views_gained,
       count(*)                                   AS videos,
       count(DISTINCT channel_id)                 AS channels,
       count(*) FILTER (WHERE is_new)             AS new_videos,
       sum(views_gained) FILTER (WHERE is_short)  AS short_views,
       median(views_gained)                       AS median_video_gain
FROM video_topic_daily,
     unnest([struct_pack(level := 1, code := sector), struct_pack(level := 2, code := category),
             struct_pack(level := 3, code := niche), struct_pack(level := 4, code := subniche)]) AS t(x)
WHERE x.code IS NOT NULL
GROUP BY snapshot_date, x.level, x.code;

CREATE OR REPLACE VIEW content_format_daily AS
SELECT snapshot_date, x.level AS level, x.code AS code, coalesce(format_label, 'Unspecified') AS format,
       sum(views_gained) AS views_gained, count(*) AS videos
FROM video_topic_daily,
     unnest([struct_pack(level := 1, code := sector), struct_pack(level := 2, code := category),
             struct_pack(level := 3, code := niche), struct_pack(level := 4, code := subniche)]) AS t(x)
WHERE x.code IS NOT NULL
GROUP BY ALL;
"""

EXPORTS = ["niche_daily", "niche_momentum", "screener", "alerts", "niche_opportunity", "niche_opportunity_daily", "content_daily", "content_format_daily",
           "topic_series", "topic_current", "topic_index", "topic_alerts", "video_metrics", "creator_coverage",
           "creator_metrics", "creator_watchlist", "creator_alerts", "news_ranked",
           "creator_subs", "topic_breaks", "data_quality_daily", "audit_error_rate", "topic_crosswalk"]


def build(con) -> None:
    """Base views, then the Formula Library tables (terminal/formulas.py)."""
    con.execute(VIEWS)
    from . import creators, formulas
    formulas.build(con)
    creators.build(con)


def export(con, out_dir: str = "data/exports") -> list[str]:
    """Write each view to CSV for the dashboard or a spreadsheet."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    have = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    for name in EXPORTS:
        if name not in have:
            continue
        path = f"{out_dir}/{name}.csv"
        con.execute(f"COPY (SELECT * FROM {name}) TO '{path}' (HEADER)")
        paths.append(path)
    from . import coverage
    paths.append(coverage.write(con, f"{out_dir}/coverage.csv"))
    return paths
