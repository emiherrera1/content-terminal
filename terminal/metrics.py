"""The terminal's numbers, as SQL views over the daily snapshots.

niche_daily     views gained per CTNCS niche per day (the niche index)
niche_momentum  last 7 days of gains vs the 7 before, per niche
screener        each channel's 28-day growth against its niche's median
alerts          channels whose daily gain is 3+ standard deviations above their trailing 28 days
                and at least double their typical day

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
"""

EXPORTS = ["niche_daily", "niche_momentum", "screener", "alerts"]


def build(con) -> None:
    con.execute(VIEWS)


def export(con, out_dir: str = "data/exports") -> list[str]:
    """Write each view to CSV for the dashboard or a spreadsheet."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for name in EXPORTS:
        path = f"{out_dir}/{name}.csv"
        con.execute(f"COPY (SELECT * FROM {name}) TO '{path}' (HEADER)")
        paths.append(path)
    return paths
