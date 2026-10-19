"""The Formula Library's "Next" set, built as tables after the base views in metrics.py.

Numbers in comments (2.3, 4.3, 6.4...) refer to sections of "Content Terminal: Formula Library".
Every threshold and weight comes from config/formulas.yaml.

Tables (rebuilt by `compute`):
  topic_map            one row per classified video per CTNCS level, plus level 0 = ALL
  creator_topic_daily  attention and intent-weighted attention per creator per topic per day
  video_metrics        2.1-2.5, 2.8-2.9: velocity, age-matched outlier score, topic-relative score, breakout, engagement
  creator_coverage     1.6-1.7: share of a creator's daily views the terminal can attribute to topics
  topic_series         3.1-3.10, 3.13, 6.1, 6.4: daily attention, share, growth, acceleration, seasonal adjustment,
                       new-creator share, intent-weighted attention and the Demand Signal Score
  topic_current        3.3-3.4, 3.11-3.12, 3.20: active creators, demand per minute, breadth, HHI, CR3, THIN flag
  topic_index          4.1-4.7: chained index level, trend index, relative strength
  topic_alerts         7.2-7.6 on the latest day
  creator_subs         1.8: subscriber changes above YouTube's rounding step, spread across the days between steps
  topic_breaks         1.11: splice factor per topic around each structural break in config
  data_quality_daily   10.1-10.2: pull completeness and the share of attention from confident classifications
  audit_error_rate     10.3: wrong codes per sector in the latest hand audit, flagged above the threshold
  topic_crosswalk      6.5: CTNCS niche -> consumer categories, NAICS, public companies, private brands (config/crosswalk.csv)
"""
from pathlib import Path

import yaml

from . import ctncs

ROOT = Path(__file__).resolve().parent.parent


def config(path=ROOT / "config" / "formulas.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text())


def _intent_case(cfg: dict) -> str:
    """6.1: SQL CASE mapping format code -> intent weight."""
    by_label = {label: code for code, label in ctncs.formats().items()}
    w = cfg["intent_weights"]
    whens = " ".join(f"WHEN '{by_label[label]}' THEN {float(x)}" for label, x in w.items()
                     if label != "default" and label in by_label)
    return f"CASE format_id {whens} ELSE {float(w.get('default', 0.2))} END"


def _parent(col: str) -> str:
    """Parent topic code: level 1 -> ALL, deeper -> drop the last segment."""
    return (f"CASE WHEN level = 1 THEN 'ALL' WHEN level > 1 THEN "
            f"array_to_string(list_slice(string_split({col}, '.'), 1, level - 1), '.') END")


def sql(cfg: dict) -> str:
    W, T = cfg["windows"], cfg["thresholds"]
    alpha = float(W.get("ewma_alpha", 0.25))
    conf = float(T.get("min_confidence", 0.7))
    bw = int(W.get("break_window_days", 14))
    breaks = ", ".join(f"DATE '{b}'" for b in (cfg.get("breaks") or [])) or "NULL"
    dw = cfg["dss_weights"]
    base_date = cfg["index"].get("base_date")
    base = f"DATE '{base_date}'" if base_date else "NULL"
    return f"""
-- topic_map: each classified video at every level of its topic, plus the ALL root (level 0)
CREATE OR REPLACE TABLE topic_map AS
SELECT c.video_id, v.channel_id, x.level AS level, x.code AS code, c.topic_code, c.format_id, c.confidence,
       CAST(v.published_at AS DATE) AS pub_date, coalesce(v.duration_s, 0) AS duration_s,
       coalesce(v.duration_s, 0) <= 180 AS is_short
FROM video_classifications c JOIN videos v USING (video_id),
     unnest([struct_pack(level := 0, code := 'ALL'), struct_pack(level := 1, code := c.sector),
             struct_pack(level := 2, code := c.category), struct_pack(level := 3, code := c.niche),
             struct_pack(level := 4, code := c.subniche)]) AS t(x)
WHERE c.topic_code <> 'unclassified' AND x.code IS NOT NULL;

-- creator_topic_daily: 3.1 attention and 6.1 intent-weighted attention, per creator, topic and day
CREATE OR REPLACE TABLE creator_topic_daily AS
SELECT d.snapshot_date AS d, m.level, m.code, m.channel_id,
       sum(d.views_gained) AS a,
       sum(d.views_gained * {_intent_case(cfg)}) AS ia,
       sum(d.views_gained) FILTER (WHERE m.is_short) AS short_a,
       sum(d.views_gained) FILTER (WHERE m.confidence >= {conf}) AS conf_a,   -- 10.2
       count(*) AS videos
FROM video_daily d JOIN topic_map m USING (video_id)
WHERE d.views_gained IS NOT NULL
GROUP BY ALL;

-- 3.13 when each creator entered each topic. Only tracked uploads are visible, so a creator counts as a
-- newcomer only if the channel itself is young or it was already posting other topics before this one;
-- otherwise "first seen" would just be the edge of the tracking window.
CREATE OR REPLACE TABLE creator_topic_first AS
WITH f AS (SELECT code, channel_id, min(pub_date) AS first_seen FROM topic_map GROUP BY ALL),
anyt AS (SELECT channel_id, min(pub_date) AS first_any FROM topic_map GROUP BY ALL)
SELECT f.code, f.channel_id, f.first_seen, anyt.first_any, CAST(c.published_at AS DATE) AS channel_created,
       CASE WHEN CAST(c.published_at AS DATE) >= f.first_seen - {int(W['new_creator_channel_age_days'])}
              OR f.first_seen > anyt.first_any + {int(W['new_creator_entry_gap_days'])}
            THEN f.first_seen END AS entry_date
FROM f JOIN anyt USING (channel_id) LEFT JOIN channels c USING (channel_id);

-- 2.1-2.5, 2.8-2.9 video_metrics
CREATE OR REPLACE TABLE video_metrics AS
WITH ages AS (
    SELECT s.video_id, v.channel_id, s.snapshot_date, s.views, s.likes, s.comments,
           s.snapshot_date - CAST(v.published_at AS DATE) AS age
    FROM video_snapshots s JOIN videos v USING (video_id)
),
recent AS (   -- each creator's last N videos, newest first
    SELECT video_id, channel_id,
           row_number() OVER (PARTITION BY channel_id ORDER BY published_at DESC) AS rn
    FROM videos
),
latest AS (
    SELECT a.*, c.topic_code, c.format_id, coalesce(v.duration_s, 0) <= 180 AS is_short
    FROM ages a
    JOIN (SELECT video_id, max(snapshot_date) AS snapshot_date FROM video_snapshots GROUP BY ALL) m USING (video_id, snapshot_date)
    JOIN videos v USING (video_id)
    LEFT JOIN video_classifications c USING (video_id)
),
base AS (     -- 2.2 the creator's other recent videos at the same age
    SELECT l.video_id, median(o.views) AS base_views, count(*) AS base_n
    FROM latest l
    JOIN ages o ON o.channel_id = l.channel_id AND o.age = l.age AND o.video_id <> l.video_id
    JOIN recent r ON r.video_id = o.video_id AND r.rn <= {int(W['baseline_videos'])}
    WHERE l.age BETWEEN 1 AND {int(W['max_age_days'])}
    GROUP BY l.video_id
),
topic_base AS (  -- 2.4 every video in the same topic at the same age
    SELECT l.video_id, median(o.views) AS topic_median, quantile_cont(o.views, {float(T['breakout_topic_pct'])}) AS topic_p90,
           count(*) AS topic_n
    FROM latest l
    JOIN ages o ON o.age = l.age
    JOIN video_classifications oc ON oc.video_id = o.video_id AND oc.topic_code = l.topic_code
    WHERE l.age BETWEEN 1 AND {int(W['max_age_days'])} AND l.topic_code <> 'unclassified'
    GROUP BY l.video_id
),
vel AS (       -- 2.1 views at age 1 and 7
    SELECT video_id, max(views) FILTER (WHERE age = 1) AS v24, max(views) FILTER (WHERE age = 7) AS v7
    FROM ages GROUP BY video_id
),
er AS (
    SELECT *, (likes + comments) / nullif(views, 0) AS er FROM latest
)
SELECT e.video_id, e.channel_id, e.topic_code, e.format_id, e.is_short, e.snapshot_date, e.age, e.views,
       vel.v24, vel.v7,
       CASE WHEN b.base_n >= {int(W['baseline_min'])} THEN b.base_views END AS creator_baseline,
       CASE WHEN b.base_n >= {int(W['baseline_min'])} THEN e.views / nullif(b.base_views, 0) END AS outlier_score,
       e.views / nullif(tb.topic_median, 0) AS topic_score,
       coalesce(b.base_n >= {int(W['baseline_min'])}
                AND e.views / nullif(b.base_views, 0) >= {float(T['breakout_outlier'])}
                AND e.views >= tb.topic_p90, false) AS breakout,
       e.er,
       e.er / nullif(median(e.er) OVER (PARTITION BY e.topic_code, e.format_id, e.is_short), 0) AS relative_er,
       e.comments / nullif(e.views, 0) * 1000 AS comment_rate
FROM er e
LEFT JOIN base b USING (video_id)
LEFT JOIN topic_base tb USING (video_id)
LEFT JOIN vel USING (video_id);

-- 1.6-1.7 creator_coverage
CREATE OR REPLACE TABLE creator_coverage AS
SELECT c.snapshot_date AS d, c.channel_id, c.views_gained AS channel_gain,
       coalesce(t.tracked, 0) AS tracked_gain,
       coalesce(t.tracked, 0) / nullif(c.views_gained, 0) AS coverage,
       c.views_gained - coalesce(t.tracked, 0) AS back_catalog_gain
FROM channel_daily c
LEFT JOIN (SELECT snapshot_date, channel_id, sum(views_gained) AS tracked FROM video_daily
           WHERE views_gained IS NOT NULL GROUP BY ALL) t USING (snapshot_date, channel_id)
WHERE c.views_gained IS NOT NULL AND c.days_since_prev = 1;

-- 3.x and 6.x topic_series
CREATE OR REPLACE TABLE topic_series AS
WITH daily AS (
    SELECT t.d, t.level, t.code, sum(t.a) AS a, sum(t.ia) AS ia, sum(t.short_a) AS short_a, sum(t.conf_a) AS conf_a,
           count(*) AS creators,
           sum(t.a) FILTER (WHERE f.entry_date >= t.d - {int(W['new_creator_days'])}) AS new_a
    FROM creator_topic_daily t JOIN creator_topic_first f USING (code, channel_id)
    GROUP BY ALL
),
supply AS (
    SELECT pub_date AS d, code, count(*) AS uploads, sum(duration_s) / 60.0 AS minutes FROM topic_map GROUP BY ALL
),
j AS (
    SELECT d.*, coalesce(s.uploads, 0) AS uploads, coalesce(s.minutes, 0) AS minutes, {_parent('d.code')} AS parent,
           dayofweek(d.d) AS dow
    FROM daily d LEFT JOIN supply s USING (d, code)
),
w AS (
    SELECT *,
        sum(a) OVER r7 AS a7, sum(a) OVER p7 AS a7_prev, sum(a) OVER r28 AS a28, sum(a) OVER p28 AS a28_prev,
        sum(uploads) OVER r28 AS uploads28, sum(minutes) OVER r28 AS minutes28,
        sum(coalesce(new_a, 0)) OVER r28 AS new_a28,
        -- 3.10 weekday factor: this weekday's average over the last 8 weeks against all days' average
        avg(a) OVER (PARTITION BY code, dow ORDER BY d RANGE BETWEEN INTERVAL {int(W['seasonal_days']) - 1} DAYS PRECEDING AND CURRENT ROW)
          / nullif(avg(a) OVER (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL {int(W['seasonal_days']) - 1} DAYS PRECEDING AND CURRENT ROW), 0)
          AS weekday_factor,
        count(*) OVER (PARTITION BY code ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS days_history,
        row_number() OVER (PARTITION BY code ORDER BY d) - 1 AS k
    FROM j
    WINDOW r7 AS (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL 6 DAYS PRECEDING AND CURRENT ROW),
           p7 AS (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL 13 DAYS PRECEDING AND INTERVAL 7 DAYS PRECEDING),
           r28 AS (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL 27 DAYS PRECEDING AND CURRENT ROW),
           p28 AS (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL 55 DAYS PRECEDING AND INTERVAL 28 DAYS PRECEDING)
),
sa AS (
    SELECT *, a / nullif(weekday_factor, 0) AS a_sa, ia / nullif(weekday_factor, 0) AS ia_sa FROM w
),
sa7 AS (
    SELECT *, sum(ia_sa) OVER r7 AS ia_sa7, sum(ia_sa) OVER p7 AS ia_sa7_prev
    FROM sa
    WINDOW r7 AS (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL 6 DAYS PRECEDING AND CURRENT ROW),
           p7 AS (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL 13 DAYS PRECEDING AND INTERVAL 7 DAYS PRECEDING)
),
g AS (
    SELECT x.*,
        x.a / nullif(p.a, 0) AS share,                                   -- 3.6
        x.a / nullif(y.a, 0) - 1 AS dod,                                 -- 3.7
        x.a7 / nullif(x.a7_prev, 0) - 1 AS wow,
        x.a28 / nullif(x.a28_prev, 0) - 1 AS g28,
        x.a7 / 7.0 AS ma7,                                               -- 3.8
        x.ia_sa7 / nullif(x.ia_sa7_prev, 0) - 1 AS intent_wow,           -- 6.1 growth of seasonally adjusted intent
        x.a28 / nullif(x.minutes28, 0) AS views_per_minute,              -- 3.4
        x.new_a28 / nullif(x.a28, 0) AS new_creator_share,               -- 3.13
        x.short_a / nullif(x.a, 0) AS shorts_share,                      -- 3.15
        coalesce(x.conf_a, 0) / nullif(x.a, 0) AS confident_share        -- 10.2
    FROM sa7 x
    LEFT JOIN sa7 p ON p.code = x.parent AND p.d = x.d
    LEFT JOIN sa7 y ON y.code = x.code AND y.d = x.d - 1
),
g2 AS (
    SELECT g.*,
        g.wow - g7.wow AS acceleration,                                  -- 3.9
        g.share - g28.share AS share_change_28d                          -- 3.6
    FROM g
    LEFT JOIN g g7 ON g7.code = g.code AND g7.d = g.d - 7
    LEFT JOIN g g28 ON g28.code = g.code AND g28.d = g.d - 28
),
z AS (       -- 6.4 each component against the topic's own history
    SELECT *,
        count(intent_wow) OVER h AS z_n,
        (intent_wow - avg(intent_wow) OVER h) / nullif(stddev_samp(intent_wow) OVER h, 0) AS z_intent,
        (acceleration - avg(acceleration) OVER h) / nullif(stddev_samp(acceleration) OVER h, 0) AS z_accel,
        (share_change_28d - avg(share_change_28d) OVER h) / nullif(stddev_samp(share_change_28d) OVER h, 0) AS z_share,
        (new_creator_share - avg(new_creator_share) OVER h) / nullif(stddev_samp(new_creator_share) OVER h, 0) AS z_new,
        (a_sa - avg(a_sa) OVER s28) / nullif(stddev_samp(a_sa) OVER s28, 0) AS surge_z,
        stddev_samp(share_change_28d) OVER h AS share_change_sd
    FROM g2
    WINDOW h AS (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL {int(W['z_history_days'])} DAYS PRECEDING AND INTERVAL 1 DAYS PRECEDING),
           s28 AS (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL 28 DAYS PRECEDING AND INTERVAL 1 DAYS PRECEDING)
)
SELECT d, level, code, parent, a, a_sa, ia, ia_sa, short_a, creators, uploads, minutes,
       a7, a28, uploads28, minutes28, share, share_change_28d, dod, wow, g28, ma7,
       -- 3.8 EWMA(t) = a A(t) + (1 - a) EWMA(t - 1), started at the first day's attention. Written in closed form
       -- over the topic's k-th observed day so it runs as one window sum.
       pow({1 - alpha}, k) * sum(CASE WHEN k = 0 THEN a ELSE {alpha} * a * pow({1 - alpha}, -k) END)
           OVER (PARTITION BY code ORDER BY d ROWS UNBOUNDED PRECEDING) AS ewma,
       acceleration, weekday_factor, intent_wow, views_per_minute, new_creator_share, shorts_share,
       confident_share, surge_z, share_change_sd, days_history,
       CASE WHEN z_n >= {int(W['z_min_history'])} THEN
            {float(dw['intent_growth'])} * coalesce(z_intent, 0) + {float(dw['acceleration'])} * coalesce(z_accel, 0)
          + {float(dw['share_change'])} * coalesce(z_share, 0) + {float(dw['new_creator_share'])} * coalesce(z_new, 0)
       END AS dss
FROM z;

-- 3.3, 3.11, 3.12, 3.20 topic_current: the latest day's cross-section
CREATE OR REPLACE TABLE topic_current AS
WITH last AS (SELECT max(d) AS d FROM creator_topic_daily),
c28 AS (
    SELECT t.code, t.channel_id, sum(t.a) AS a, sum(coalesce(t.conf_a, 0)) AS conf_a, sum(t.videos) AS video_days
    FROM creator_topic_daily t JOIN last ON t.d > last.d - 28 GROUP BY ALL
),
adv AS (      -- 3.11 creators whose last 7 days beat the 7 before, minus those that fell short
    SELECT code, count(*) FILTER (WHERE a7 > a7p) AS advancers, count(*) FILTER (WHERE a7 < a7p) AS decliners
    FROM (SELECT t.code, t.channel_id,
                 sum(t.a) FILTER (WHERE t.d > last.d - 7) AS a7_raw, sum(t.a) FILTER (WHERE t.d <= last.d - 7) AS a7p_raw,
                 coalesce(a7_raw, 0) AS a7, coalesce(a7p_raw, 0) AS a7p
          FROM creator_topic_daily t JOIN last ON t.d > last.d - 14 GROUP BY ALL)
    GROUP BY code
),
conc AS (
    SELECT code, count(*) AS creators28,
           sum(pow(100.0 * a / nullif(tot, 0), 2)) AS hhi,
           sum(a) FILTER (WHERE rk <= 3) / nullif(max(tot), 0) AS cr3,
           sum(conf_a) / nullif(max(tot), 0) AS confident_share28
    FROM (SELECT *, sum(a) OVER (PARTITION BY code) AS tot,
                 row_number() OVER (PARTITION BY code ORDER BY a DESC) AS rk FROM c28)
    GROUP BY code
),
vids AS (
    SELECT m.code, count(DISTINCT d.video_id) AS videos28
    FROM video_daily d JOIN topic_map m USING (video_id) JOIN last ON d.snapshot_date > last.d - 28
    WHERE d.views_gained > 0 GROUP BY m.code
),
active AS (
    SELECT m.code, count(DISTINCT m.channel_id) AS active_creators
    FROM topic_map m JOIN last ON m.pub_date > last.d - 28 GROUP BY m.code
),
breadth AS (
    SELECT m.code, avg((vm.outlier_score > 1)::INT) AS breadth, count(vm.outlier_score) AS breadth_n
    FROM topic_map m JOIN video_metrics vm USING (video_id) JOIN last ON m.pub_date > last.d - 28
    WHERE vm.outlier_score IS NOT NULL GROUP BY m.code
)
SELECT s.d, s.level, s.code, s.a, s.a7, s.a28, s.share, s.share_change_28d, s.wow, s.acceleration, s.views_per_minute,
       s.new_creator_share, s.shorts_share, s.dss,
       coalesce(a.active_creators, 0) AS active_creators, coalesce(v.videos28, 0) AS videos28,
       coalesce(c.creators28, 0) AS creators28, c.hhi, c.cr3, b.breadth, b.breadth_n,
       coalesce(ad.advancers, 0) AS advancers, coalesce(ad.decliners, 0) AS decliners,
       coalesce(ad.advancers, 0) - coalesce(ad.decliners, 0) AS advance_decline, c.confident_share28,
       coalesce(v.videos28, 0) < {int(T['min_videos'])} OR coalesce(c.creators28, 0) < {int(T['min_creators'])} AS thin
FROM topic_series s
JOIN last ON s.d = last.d
LEFT JOIN conc c ON c.code = s.code
LEFT JOIN vids v ON v.code = s.code
LEFT JOIN active a ON a.code = s.code
LEFT JOIN breadth b ON b.code = s.code
LEFT JOIN adv ad ON ad.code = s.code;

-- 4.1-4.7 topic_index
CREATE OR REPLACE TABLE topic_index AS
WITH first_day AS (SELECT min(snapshot_date) AS d0 FROM video_snapshots),
presence AS (SELECT DISTINCT v.channel_id, s.snapshot_date AS d FROM video_snapshots s JOIN videos v USING (video_id)),
basket AS (   -- 4.1 creators added on or before the quarter's rebalance date (or the first day of data)
    SELECT p.channel_id, p.d FROM presence p JOIN channels c USING (channel_id), first_day
    WHERE CAST(c.added_at AS DATE) <= greatest(CAST(date_trunc('quarter', p.d) AS DATE), first_day.d0)
),
matched AS (  -- 4.2 in the basket and observed on both days
    SELECT b.channel_id, b.d FROM basket b JOIN basket y ON y.channel_id = b.channel_id AND y.d = b.d - 1
),
pairs AS (
    SELECT m.d, coalesce(t.level, y.level) AS level, coalesce(t.code, y.code) AS code, m.channel_id,
           coalesce(t.a, 0) AS a_t, coalesce(y.a, 0) AS a_y
    FROM matched m
    LEFT JOIN creator_topic_daily t ON t.channel_id = m.channel_id AND t.d = m.d
    FULL JOIN creator_topic_daily y ON y.channel_id = m.channel_id AND y.d = m.d - 1
         AND (t.code IS NULL OR y.code = t.code)
    WHERE m.d IS NOT NULL AND coalesce(t.code, y.code) IS NOT NULL
),
capped AS (   -- 4.5 cap each creator's share when the topic has enough creators
    SELECT *, count(*) OVER k AS n,
           CASE WHEN count(*) OVER k >= {int(T['cap_min_creators'])}
                THEN least(a_t, {float(T['creator_cap'])} * sum(a_t) OVER k) ELSE a_t END AS a_t_c,
           CASE WHEN count(*) OVER k >= {int(T['cap_min_creators'])}
                THEN least(a_y, {float(T['creator_cap'])} * sum(a_y) OVER k) ELSE a_y END AS a_y_c
    FROM pairs WINDOW k AS (PARTITION BY code, d)
),
ratio AS (
    SELECT d, level, code, count(*) AS matched_creators, sum(a_t_c) / nullif(sum(a_y_c), 0) AS ratio,
           avg(a_t / a_y) FILTER (WHERE a_y > 0) AS eq_ratio                      -- 4.6 the typical creator's change
    FROM capped GROUP BY ALL
),
based AS (
    SELECT r.*, coalesce({base}, min(d) OVER (PARTITION BY code)) AS base_date FROM ratio r
),
lvl AS (      -- 4.3 chain-link from the base date; a day with no valid ratio carries the level forward
    SELECT d, level, code, matched_creators, ratio, base_date,
           100 * exp(sum(CASE WHEN d > base_date AND ratio > 0 THEN ln(ratio) ELSE 0 END)
                     OVER (PARTITION BY code ORDER BY d ROWS UNBOUNDED PRECEDING)) AS index_level,
           100 * exp(sum(CASE WHEN d > base_date AND eq_ratio > 0 THEN ln(eq_ratio) ELSE 0 END)
                     OVER (PARTITION BY code ORDER BY d ROWS UNBOUNDED PRECEDING)) AS index_equal_weight
    FROM based WHERE d >= base_date
),
trend AS (    -- 4.4 geometric 7-day average of the daily level
    SELECT *, exp(avg(ln(index_level)) OVER (PARTITION BY code ORDER BY d RANGE BETWEEN INTERVAL 6 DAYS PRECEDING AND CURRENT ROW))
              AS trend_level,
           {_parent('code')} AS parent
    FROM lvl
)
SELECT t.*, t.index_level / nullif(p.index_level, 0) * 100 AS relative_strength      -- 4.7
FROM trend t LEFT JOIN trend p ON p.code = t.parent AND p.d = t.d;

-- 7.2-7.6 topic_alerts on the latest day
CREATE OR REPLACE TABLE topic_alerts AS
WITH last AS (SELECT max(d) AS ld FROM topic_series),
cur AS (SELECT s.*, c.thin FROM topic_series s JOIN topic_current c USING (code, d)),
dss_run AS (
    SELECT code, count(*) AS days, min(dss) AS min_dss
    FROM topic_series, last WHERE d > last.ld - {int(T['dss_alert_days'])} GROUP BY code
),
top7 AS (
    SELECT t.code, t.channel_id, sum(t.a) AS a7, f.entry_date,
           row_number() OVER (PARTITION BY t.code ORDER BY sum(t.a) DESC) AS rk
    FROM creator_topic_daily t JOIN creator_topic_first f USING (code, channel_id), last
    WHERE t.d > last.ld - 7 GROUP BY t.code, t.channel_id, f.entry_date
)
SELECT last.ld AS d, 'topic_surge' AS kind, code, NULL AS subject, surge_z AS value
FROM cur, last WHERE cur.d = last.ld AND NOT thin AND surge_z >= {float(T['surge_z'])}
UNION ALL
SELECT last.ld, 'share_shift', code, NULL, share_change_28d / nullif(share_change_sd, 0)
FROM cur, last WHERE cur.d = last.ld AND NOT thin AND level > 0
  AND share_change_28d >= {float(T['share_shift_sd'])} * share_change_sd
UNION ALL
SELECT last.ld, 'demand_signal', r.code, NULL, r.min_dss
FROM dss_run r, last WHERE r.days >= {int(T['dss_alert_days'])} AND r.min_dss >= {float(T['dss_alert'])}
UNION ALL
SELECT last.ld, 'newcomer_win', code, channel_id, a7
FROM top7, last WHERE rk <= {int(T['newcomer_top'])} AND entry_date >= last.ld - {int(W['new_creator_days'])}
  AND code <> 'ALL'
UNION ALL
SELECT last.ld, 'video_breakout', topic_code, video_id, outlier_score
FROM video_metrics, last WHERE breakout AND age <= 7;

-- 1.8 creator_subs: YouTube rounds public subscriber counts to 3 significant figures, so a change is only real
-- once it clears the rounding step r = 10^(digits - 3). The estimate spreads each observed step evenly over the
-- days since the previous step; days after the latest step stay blank until the next one arrives.
CREATE OR REPLACE TABLE creator_subs AS
WITH s AS (
    SELECT snapshot_date AS d, channel_id, subscribers,
           CAST(pow(10, greatest(length(CAST(subscribers AS VARCHAR)) - 3, 0)) AS BIGINT) AS rounding_step,
           subscribers - lag(subscribers) OVER w AS subs_change
    FROM channel_snapshots WHERE subscribers IS NOT NULL
    WINDOW w AS (PARTITION BY channel_id ORDER BY snapshot_date)
),
steps AS (    -- the first observation and every day the rounded count moved
    SELECT channel_id, d, subscribers, lag(d) OVER w AS prev_d, lag(subscribers) OVER w AS prev_subs
    FROM s WHERE subs_change IS NULL OR subs_change <> 0
    WINDOW w AS (PARTITION BY channel_id ORDER BY d)
)
SELECT s.d, s.channel_id, s.subscribers, s.rounding_step,
       CASE WHEN abs(s.subs_change) >= s.rounding_step THEN s.subs_change END AS subs_change_reported,
       (st.subscribers - st.prev_subs) / nullif(datediff('day', st.prev_d, st.d), 0) AS subs_gained_est
FROM s
LEFT JOIN steps st ON st.channel_id = s.channel_id AND s.d > st.prev_d AND s.d <= st.d;

-- 1.11 topic_breaks: splice factor s = median daily attention in the window after a break / in the window before,
-- per topic. Divide pre-break attention by s to compare across the break. Blank until both windows have data.
CREATE OR REPLACE TABLE topic_breaks AS
WITH b AS (SELECT unnest([{breaks}]) AS break_date)
SELECT b.break_date, t.level, t.code,
       median(t.a) FILTER (WHERE t.d BETWEEN b.break_date - {bw} AND b.break_date - 1) AS before_median,
       median(t.a) FILTER (WHERE t.d BETWEEN b.break_date AND b.break_date + {bw - 1}) AS after_median,
       count(*) FILTER (WHERE t.d BETWEEN b.break_date - {bw} AND b.break_date - 1) AS before_days,
       count(*) FILTER (WHERE t.d BETWEEN b.break_date AND b.break_date + {bw - 1}) AS after_days,
       after_median / nullif(before_median, 0) AS splice_factor
FROM b, topic_series t
WHERE b.break_date IS NOT NULL AND t.d BETWEEN b.break_date - {bw} AND b.break_date + {bw - 1}
GROUP BY ALL;

-- 10.1-10.2 data_quality_daily: did the pull reach every tracked channel, and how much of the day's views come
-- from videos the classifier was confident about
CREATE OR REPLACE TABLE data_quality_daily AS
WITH days AS (SELECT DISTINCT snapshot_date AS d FROM channel_snapshots
              UNION SELECT DISTINCT snapshot_date FROM video_snapshots),
expected AS (SELECT d, count(c.channel_id) AS channels_expected
             FROM days LEFT JOIN channels c ON CAST(c.added_at AS DATE) <= days.d GROUP BY d),
received AS (SELECT snapshot_date AS d, count(DISTINCT channel_id) AS channels_received FROM channel_snapshots GROUP BY ALL),
vids AS (SELECT snapshot_date AS d, count(*) AS videos_snapshotted FROM video_snapshots GROUP BY ALL),
conf AS (
    SELECT d.snapshot_date AS d, sum(d.views_gained) AS views_gained,
           sum(d.views_gained) FILTER (WHERE c.topic_code <> 'unclassified' AND c.confidence >= {conf}) AS confident_views,
           sum(d.views_gained) FILTER (WHERE c.video_id IS NULL OR c.topic_code = 'unclassified') AS unclassified_views
    FROM video_daily d LEFT JOIN video_classifications c USING (video_id)
    WHERE d.views_gained IS NOT NULL GROUP BY ALL
)
SELECT e.d, e.channels_expected, coalesce(r.channels_received, 0) AS channels_received,
       coalesce(r.channels_received, 0) / nullif(e.channels_expected, 0) AS pull_completeness,
       coalesce(v.videos_snapshotted, 0) AS videos_snapshotted,
       c.views_gained, c.confident_views / nullif(c.views_gained, 0) AS confident_share,
       c.unclassified_views / nullif(c.views_gained, 0) AS unclassified_share
FROM expected e LEFT JOIN received r USING (d) LEFT JOIN vids v USING (d) LEFT JOIN conf c USING (d);

-- 10.3 audit_error_rate: the latest hand audit per kind (channel or video), by sector
CREATE OR REPLACE TABLE audit_error_rate AS
WITH latest AS (SELECT kind, max(audited_on) AS audited_on FROM classification_audits GROUP BY kind)
SELECT a.kind, a.audited_on, a.sector, count(*) AS checked, count(*) FILTER (WHERE NOT a.correct) AS wrong,
       count(*) FILTER (WHERE NOT a.correct) / count(*) AS error_rate,
       count(*) FILTER (WHERE NOT a.correct) / count(*) > {float(T.get('audit_error_max', 0.15))} AS needs_fix
FROM classification_audits a JOIN latest USING (kind, audited_on)
GROUP BY ALL;
"""


TABLES = ["topic_map", "creator_topic_daily", "creator_topic_first", "video_metrics", "creator_coverage",
          "topic_series", "topic_current", "topic_index", "topic_alerts", "creator_subs", "topic_breaks",
          "data_quality_daily", "audit_error_rate", "topic_crosswalk"]
CROSSWALK = ROOT / "config" / "crosswalk.csv"


def load_crosswalk(con, path=CROSSWALK) -> None:
    """6.5: the hand-maintained topic-to-market table, loaded as-is so the terminal can join on it."""
    con.execute("CREATE OR REPLACE TABLE topic_crosswalk (code VARCHAR, niche VARCHAR, consumer_categories VARCHAR, "
                "naics VARCHAR, public_companies VARCHAR, private_brands VARCHAR, status VARCHAR)")
    if Path(path).exists():
        import csv
        with open(path, newline="", encoding="utf-8") as f:
            rows = [[r.get(k, "") for k in ("code", "niche", "consumer_categories", "naics", "public_companies",
                                             "private_brands", "status")] for r in csv.DictReader(f)]
        if rows:
            con.executemany("INSERT INTO topic_crosswalk VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def build(con, cfg: dict | None = None) -> None:
    """Rebuild every formula table. Needs the base views from metrics.build first."""
    con.execute(sql(cfg or config()))
    load_crosswalk(con)
