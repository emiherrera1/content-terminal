"""Formula Library items that need months of history, outside data or more platforms.

They are built now and run every day. Until their inputs exist they return empty tables or blank values, and
they fill in on their own once the data arrives (history accumulates, an outcome series is loaded, a platform
adapter is switched on).

Tables (rebuilt by `compute`, after formulas.py):
  video_decay        2.6  decay rate and half-life of each video's daily views over days 1-30
  video_evergreen    2.7  share of a video's views that came after day 30 (videos older than 90 days)
  video_packaging_metrics 2.11 packaging format per video with its outlier and topic scores
  packaging_wins     2.11 which packaging formats beat their creators most often, per topic
  creator_flow       3.19 creators moving into or out of each topic's upload mix
  creator_drift      5.8  share of a creator's uploads outside its primary niche, now vs a year earlier
  creator_earnings   5.9  90-day earnings range from RPM bands (labeled with the bands' source)
  entity_daily       6.2  daily attention on videos naming each brand, company or product category
  purchase_intent    6.3  share of sampled comments asking where to buy, price, link or availability
  platform_daily     9.1  attention per topic per platform
  platform_z         9.2  each platform's weekly attention as a z-score against its own 26 weeks
  basket_coverage    10.4 tracked views as a share of an outside estimate of the topic's total views
"""
from . import formulas


def sql(cfg: dict) -> str:
    L = cfg["later"]
    rpm = cfg["rpm_bands"]
    d = rpm["default"]
    by = rpm.get("by_niche") or {}
    src = str(rpm.get("source", "placeholder")).replace("'", "")
    band_rows = ", ".join(f"('{k}', {float(v.get('long_low', d['long_low']))}, {float(v.get('long_high', d['long_high']))}, "
                          f"{float(v.get('shorts_low', d['shorts_low']))}, {float(v.get('shorts_high', d['shorts_high']))})"
                          for k, v in by.items()) or "(NULL, NULL, NULL, NULL, NULL)"
    return f"""
-- 2.6 video_decay: fit ln(daily gain) = c - lambda * age over days 1-{int(L['decay_max_age'])}; half-life = ln 2 / lambda
CREATE OR REPLACE TABLE video_decay AS
SELECT d.video_id, d.channel_id, count(*) AS points,
       -regr_slope(ln(d.views_gained), d.snapshot_date - CAST(d.published_at AS DATE)) AS decay_rate,
       CASE WHEN regr_slope(ln(d.views_gained), d.snapshot_date - CAST(d.published_at AS DATE)) < 0
            THEN ln(2) / -regr_slope(ln(d.views_gained), d.snapshot_date - CAST(d.published_at AS DATE)) END AS half_life_days,
       regr_r2(ln(d.views_gained), d.snapshot_date - CAST(d.published_at AS DATE)) AS fit_r2
FROM video_daily d
WHERE d.views_gained > 0 AND d.snapshot_date - CAST(d.published_at AS DATE) BETWEEN 1 AND {int(L['decay_max_age'])}
GROUP BY ALL
HAVING count(*) >= {int(L['decay_min_points'])};

-- 2.7 video_evergreen: views after day {int(L['evergreen_after_day'])} / total views, for videos older than {int(L['evergreen_min_age'])} days.
-- Needs a snapshot at day {int(L['evergreen_after_day'])}, so it only covers videos the terminal has followed since they were young.
CREATE OR REPLACE TABLE video_evergreen AS
WITH a AS (
    SELECT s.video_id, s.snapshot_date - CAST(v.published_at AS DATE) AS age, s.views
    FROM video_snapshots s JOIN videos v USING (video_id)
)
SELECT video_id, max(age) AS age, max(views) FILTER (WHERE age = {int(L['evergreen_after_day'])}) AS views_at_cut,
       arg_max(views, age) AS views_now,
       1 - max(views) FILTER (WHERE age = {int(L['evergreen_after_day'])}) / nullif(arg_max(views, age), 0) AS evergreen_share
FROM a GROUP BY video_id
HAVING max(age) > {int(L['evergreen_min_age'])} AND max(views) FILTER (WHERE age = {int(L['evergreen_after_day'])}) IS NOT NULL;

-- 2.11 packaging: each video's packaging with how it performed, then which packaging wins in each topic
CREATE OR REPLACE TABLE video_packaging_metrics AS
SELECT p.video_id, p.packaging_id, p.packaging_label, m.topic_code, m.outlier_score, m.topic_score, m.breakout
FROM video_packaging p JOIN video_metrics m USING (video_id);

CREATE OR REPLACE TABLE packaging_wins AS
SELECT t.level, t.code, p.packaging_label, count(*) AS videos,
       median(m.outlier_score) AS median_outlier, avg((m.outlier_score > 1)::INT) AS beat_creator_rate,
       avg(m.breakout::INT) AS breakout_rate
FROM video_packaging p JOIN video_metrics m USING (video_id) JOIN topic_map t USING (video_id)
WHERE m.outlier_score IS NOT NULL AND t.level BETWEEN 1 AND 3
GROUP BY ALL HAVING count(*) >= 5;

-- 3.19 creator_flow: a creator moves into a topic when its share of the creator's uploads rose by more than
-- {float(L['flow_points']) * 100:.0f} points over the last {int(L['flow_days'])} days against the {int(L['flow_days'])} before (out = the reverse)
CREATE OR REPLACE TABLE creator_flow AS
WITH last AS (SELECT max(pub_date) AS ld FROM topic_map),
mix AS (
    SELECT m.channel_id, m.level, m.code,
           count(*) FILTER (WHERE m.pub_date > last.ld - {int(L['flow_days'])}) AS n_now,
           count(*) FILTER (WHERE m.pub_date <= last.ld - {int(L['flow_days'])} AND m.pub_date > last.ld - {2 * int(L['flow_days'])}) AS n_before
    FROM topic_map m, last WHERE m.level BETWEEN 1 AND 3 AND m.pub_date > last.ld - {2 * int(L['flow_days'])}
    GROUP BY ALL
),
tot AS (
    SELECT channel_id,
           count(*) FILTER (WHERE m.pub_date > last.ld - {int(L['flow_days'])}) AS t_now,
           count(*) FILTER (WHERE m.pub_date <= last.ld - {int(L['flow_days'])} AND m.pub_date > last.ld - {2 * int(L['flow_days'])}) AS t_before
    FROM topic_map m, last WHERE m.level = 0 GROUP BY channel_id
),
shift AS (
    SELECT x.level, x.code, x.channel_id,
           x.n_now / nullif(t.t_now, 0) - x.n_before / nullif(t.t_before, 0) AS share_shift
    FROM mix x JOIN tot t USING (channel_id) WHERE t.t_now > 0 AND t.t_before > 0
)
SELECT level, code, count(*) FILTER (WHERE share_shift > {float(L['flow_points'])}) AS creators_in,
       count(*) FILTER (WHERE share_shift < -{float(L['flow_points'])}) AS creators_out,
       count(*) FILTER (WHERE share_shift > {float(L['flow_points'])}) - count(*) FILTER (WHERE share_shift < -{float(L['flow_points'])}) AS net_flow
FROM shift GROUP BY ALL;

-- 5.8 creator_drift: share of the last {int(L['drift_days'])} days' uploads outside the creator's primary niche,
-- minus the same share for the same window a year earlier
CREATE OR REPLACE TABLE creator_drift AS
WITH last AS (SELECT max(pub_date) AS ld FROM topic_map),
prim AS (SELECT channel_id, coalesce(niche, category) AS primary_niche FROM classifications WHERE ctncs_code <> 'unclassified'),
up AS (
    SELECT m.channel_id, m.code AS niche, m.pub_date FROM topic_map m WHERE m.level = 3
)
SELECT p.channel_id, p.primary_niche,
       avg((u.niche <> p.primary_niche)::INT) FILTER (WHERE u.pub_date > last.ld - {int(L['drift_days'])}) AS off_niche_now,
       avg((u.niche <> p.primary_niche)::INT) FILTER (WHERE u.pub_date <= last.ld - 365 AND u.pub_date > last.ld - 365 - {int(L['drift_days'])}) AS off_niche_year_ago,
       off_niche_now - off_niche_year_ago AS topic_drift
FROM prim p JOIN up u USING (channel_id), last
GROUP BY ALL;

-- 5.9 creator_earnings: a range, never a point estimate, and labeled with where the RPM bands came from
CREATE OR REPLACE TABLE creator_earnings AS
WITH bands AS (SELECT * FROM (VALUES {band_rows}) b(niche, long_low, long_high, shorts_low, shorts_high) WHERE niche IS NOT NULL),
last AS (SELECT max(snapshot_date) AS ld FROM video_daily),
v90 AS (
    SELECT d.channel_id,
           sum(d.views_gained) FILTER (WHERE coalesce(d.duration_s, 0) > 180) AS long_views_90d,
           sum(d.views_gained) FILTER (WHERE coalesce(d.duration_s, 0) <= 180) AS shorts_views_90d
    FROM video_daily d, last WHERE d.snapshot_date > last.ld - 90 AND d.views_gained IS NOT NULL GROUP BY d.channel_id
)
SELECT v.channel_id, n.niche_code, coalesce(v.long_views_90d, 0) AS long_views_90d, coalesce(v.shorts_views_90d, 0) AS shorts_views_90d,
       coalesce(v.long_views_90d, 0) / 1000 * coalesce(b.long_low, {float(d['long_low'])})
         + coalesce(v.shorts_views_90d, 0) / 1000 * coalesce(b.shorts_low, {float(d['shorts_low'])}) AS earnings_low_90d,
       coalesce(v.long_views_90d, 0) / 1000 * coalesce(b.long_high, {float(d['long_high'])})
         + coalesce(v.shorts_views_90d, 0) / 1000 * coalesce(b.shorts_high, {float(d['shorts_high'])}) AS earnings_high_90d,
       '{src}' AS rpm_source
FROM v90 v LEFT JOIN channel_niche n USING (channel_id) LEFT JOIN bands b ON b.niche = n.niche_code;

-- 6.2 entity_daily: views gained by videos that name each entity
CREATE OR REPLACE TABLE entity_daily AS
SELECT d.snapshot_date AS d, e.entity, e.entity_type, sum(d.views_gained) AS a, count(DISTINCT d.video_id) AS videos,
       count(DISTINCT d.channel_id) AS creators
FROM video_daily d JOIN video_entities e USING (video_id)
WHERE d.views_gained IS NOT NULL GROUP BY ALL;

-- 6.3 purchase_intent per video, and rolled up to topics
CREATE OR REPLACE TABLE purchase_intent AS
SELECT c.video_id, count(*) AS comments_sampled, avg(c.purchase_intent::INT) AS purchase_intent
FROM video_comments c GROUP BY c.video_id;

CREATE OR REPLACE TABLE topic_purchase_intent AS
SELECT t.level, t.code, sum(p.comments_sampled) AS comments_sampled,
       sum(p.purchase_intent * p.comments_sampled) / nullif(sum(p.comments_sampled), 0) AS purchase_intent
FROM purchase_intent p JOIN topic_map t USING (video_id) GROUP BY ALL;

-- 9.1 platform_daily: the same attention rules (section 3) run per platform
CREATE OR REPLACE TABLE platform_daily AS
SELECT t.d, coalesce(c.platform, 'youtube') AS platform, t.level, t.code, sum(t.a) AS a, count(*) AS creators,
       sum(s.subscribers) AS followers
FROM creator_topic_daily t
LEFT JOIN channels c USING (channel_id)
LEFT JOIN channel_snapshots s ON s.channel_id = t.channel_id AND s.snapshot_date = t.d
GROUP BY ALL;

-- 9.2 platform_z: platforms count views differently, so compare movement: weekly log attention as a z-score
-- against that platform's own last {int(L['platform_z_weeks'])} weeks
CREATE OR REPLACE TABLE platform_z AS
WITH w AS (
    SELECT CAST(date_trunc('week', d) AS DATE) AS week, platform, level, code, sum(a) AS a, count(DISTINCT d) AS days
    FROM platform_daily GROUP BY ALL HAVING sum(a) > 0
)
SELECT *, (ln(a) - avg(ln(a)) OVER h) / nullif(stddev_samp(ln(a)) OVER h, 0) AS z,
       count(*) OVER h AS weeks_history
FROM w
WINDOW h AS (PARTITION BY platform, code ORDER BY week ROWS BETWEEN {int(L['platform_z_weeks'])} PRECEDING AND 1 PRECEDING);

-- 10.4 basket_coverage: tracked views over an outside estimate of the topic's total, for the same period
CREATE OR REPLACE TABLE basket_coverage AS
SELECT o.code, o.period_start, o.period_end, o.source, sum(t.a) AS tracked_views, o.total_views AS estimated_total_views,
       sum(t.a) / nullif(o.total_views, 0) AS basket_coverage
FROM outside_estimates o LEFT JOIN topic_series t ON t.code = o.code AND t.d BETWEEN o.period_start AND o.period_end
GROUP BY o.code, o.period_start, o.period_end, o.source, o.total_views;
"""


TABLES = ["video_decay", "video_evergreen", "video_packaging_metrics", "packaging_wins", "creator_flow",
          "creator_drift", "creator_earnings", "entity_daily", "purchase_intent", "topic_purchase_intent",
          "platform_daily", "platform_z", "basket_coverage"]


def build(con, cfg: dict | None = None) -> None:
    con.execute(sql(cfg or formulas.config()))
