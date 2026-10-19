# Data dictionary

Generated from the schema by `python -m terminal dictionary`. Every table the terminal reads or writes, its columns and types. Section numbers point to the Formula Library (Methodology Handbook, Appendix C).

## Raw data (written by the pulls)

### `channels`

| Column | Type |
| --- | --- |
| `channel_id` | VARCHAR |
| `title` | VARCHAR |
| `description` | VARCHAR |
| `country` | VARCHAR |
| `published_at` | TIMESTAMP |
| `uploads_playlist` | VARCHAR |
| `added_at` | TIMESTAMP |
| `platform` | VARCHAR |

### `channel_snapshots`

| Column | Type |
| --- | --- |
| `snapshot_date` | DATE |
| `channel_id` | VARCHAR |
| `subscribers` | BIGINT |
| `views` | BIGINT |
| `video_count` | BIGINT |

### `videos`

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `channel_id` | VARCHAR |
| `title` | VARCHAR |
| `published_at` | TIMESTAMP |
| `duration_s` | INTEGER |
| `description` | VARCHAR |
| `tags` | VARCHAR |

### `video_snapshots`

| Column | Type |
| --- | --- |
| `snapshot_date` | DATE |
| `video_id` | VARCHAR |
| `views` | BIGINT |
| `likes` | BIGINT |
| `comments` | BIGINT |

### `classifications`

| Column | Type |
| --- | --- |
| `channel_id` | VARCHAR |
| `ctncs_code` | VARCHAR |
| `sector` | VARCHAR |
| `category` | VARCHAR |
| `niche` | VARCHAR |
| `confidence` | DOUBLE |
| `rationale` | VARCHAR |
| `model` | VARCHAR |
| `reviewed` | BOOLEAN |
| `classified_at` | TIMESTAMP |

### `video_classifications`

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `topic_code` | VARCHAR |
| `sector` | VARCHAR |
| `category` | VARCHAR |
| `niche` | VARCHAR |
| `subniche` | VARCHAR |
| `format_id` | VARCHAR |
| `format_label` | VARCHAR |
| `confidence` | DOUBLE |
| `model` | VARCHAR |
| `reviewed` | BOOLEAN |
| `classified_at` | TIMESTAMP |

## Inputs and hand-kept records

### `outcome_series`

| Column | Type |
| --- | --- |
| `series_id` | VARCHAR |
| `source` | VARCHAR |
| `code` | VARCHAR |
| `label` | VARCHAR |
| `d` | DATE |
| `value` | DOUBLE |

### `outside_estimates`

| Column | Type |
| --- | --- |
| `code` | VARCHAR |
| `period_start` | DATE |
| `period_end` | DATE |
| `total_views` | DOUBLE |
| `source` | VARCHAR |

### `classification_audits`

| Column | Type |
| --- | --- |
| `audited_on` | DATE |
| `kind` | VARCHAR |
| `item_id` | VARCHAR |
| `sector` | VARCHAR |
| `assigned_code` | VARCHAR |
| `correct_code` | VARCHAR |
| `correct` | BOOLEAN |
| `auditor` | VARCHAR |

### `video_comments`

| Column | Type |
| --- | --- |
| `comment_id` | VARCHAR |
| `video_id` | VARCHAR |
| `published_at` | TIMESTAMP |
| `text` | VARCHAR |
| `purchase_intent` | BOOLEAN |
| `tagged_by` | VARCHAR |
| `pulled_on` | DATE |

### `video_entities`

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `entity` | VARCHAR |
| `entity_type` | VARCHAR |
| `matched_in` | VARCHAR |

### `video_packaging`

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `packaging_id` | VARCHAR |
| `packaging_label` | VARCHAR |
| `tagged_by` | VARCHAR |

### `creator_review`

| Column | Type |
| --- | --- |
| `channel_id` | VARCHAR |
| `independent` | BOOLEAN |
| `clean_record` | BOOLEAN |
| `reviewed_by` | VARCHAR |
| `reviewed_on` | DATE |
| `notes` | VARCHAR |

### `topic_crosswalk`

6.5: CTNCS niche -> consumer categories, NAICS, public companies, private brands (config/crosswalk.csv)

| Column | Type |
| --- | --- |
| `code` | VARCHAR |
| `niche` | VARCHAR |
| `consumer_categories` | VARCHAR |
| `naics` | VARCHAR |
| `public_companies` | VARCHAR |
| `private_brands` | VARCHAR |
| `status` | VARCHAR |

## Core formula tables (critical stage: compute)

### `topic_map`

one row per classified video per CTNCS level, plus level 0 = ALL

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `channel_id` | VARCHAR |
| `level` | INTEGER |
| `code` | VARCHAR |
| `topic_code` | VARCHAR |
| `format_id` | VARCHAR |
| `confidence` | DOUBLE |
| `pub_date` | DATE |
| `duration_s` | INTEGER |
| `is_short` | BOOLEAN |

### `creator_topic_daily`

attention and intent-weighted attention per creator per topic per day

| Column | Type |
| --- | --- |
| `d` | DATE |
| `level` | INTEGER |
| `code` | VARCHAR |
| `channel_id` | VARCHAR |
| `a` | HUGEINT |
| `ia` | DECIMAL(38,1) |
| `short_a` | HUGEINT |
| `conf_a` | HUGEINT |
| `videos` | BIGINT |

### `creator_topic_first`

| Column | Type |
| --- | --- |
| `code` | VARCHAR |
| `channel_id` | VARCHAR |
| `first_seen` | DATE |
| `first_any` | DATE |
| `channel_created` | DATE |
| `entry_date` | DATE |

### `video_metrics`

2.1-2.5, 2.8-2.9: velocity, age-matched outlier score, topic-relative score, breakout, engagement

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `channel_id` | VARCHAR |
| `topic_code` | VARCHAR |
| `format_id` | VARCHAR |
| `is_short` | BOOLEAN |
| `snapshot_date` | DATE |
| `age` | BIGINT |
| `views` | BIGINT |
| `v24` | BIGINT |
| `v7` | BIGINT |
| `creator_baseline` | DOUBLE |
| `outlier_score` | DOUBLE |
| `topic_score` | DOUBLE |
| `breakout` | BOOLEAN |
| `er` | DOUBLE |
| `relative_er` | DOUBLE |
| `comment_rate` | DOUBLE |

### `creator_coverage`

1.6-1.7: share of a creator's daily views the terminal can attribute to topics

| Column | Type |
| --- | --- |
| `d` | DATE |
| `channel_id` | VARCHAR |
| `channel_gain` | BIGINT |
| `tracked_gain` | HUGEINT |
| `coverage` | DOUBLE |
| `back_catalog_gain` | HUGEINT |

### `topic_series`

3.1-3.10, 3.13, 6.1, 6.4: daily attention, share, growth, acceleration, seasonal adjustment,

| Column | Type |
| --- | --- |
| `d` | DATE |
| `level` | INTEGER |
| `code` | VARCHAR |
| `parent` | VARCHAR |
| `a` | HUGEINT |
| `a_sa` | DOUBLE |
| `ia` | DECIMAL(38,1) |
| `ia_sa` | DOUBLE |
| `short_a` | HUGEINT |
| `creators` | BIGINT |
| `uploads` | BIGINT |
| `minutes` | DOUBLE |
| `a7` | HUGEINT |
| `a28` | HUGEINT |
| `uploads28` | HUGEINT |
| `minutes28` | DOUBLE |
| `share` | DOUBLE |
| `share_change_28d` | DOUBLE |
| `dod` | DOUBLE |
| `wow` | DOUBLE |
| `g28` | DOUBLE |
| `ma7` | DOUBLE |
| `ewma` | DOUBLE |
| `acceleration` | DOUBLE |
| `weekday_factor` | DOUBLE |
| `intent_wow` | DOUBLE |
| `views_per_minute` | DOUBLE |
| `new_creator_share` | DOUBLE |
| `shorts_share` | DOUBLE |
| `confident_share` | DOUBLE |
| `surge_z` | DOUBLE |
| `share_change_sd` | DOUBLE |
| `days_history` | BIGINT |
| `dss` | DOUBLE |

### `topic_current`

3.3-3.4, 3.11-3.12, 3.20: active creators, demand per minute, breadth, HHI, CR3, THIN flag

| Column | Type |
| --- | --- |
| `d` | DATE |
| `level` | INTEGER |
| `code` | VARCHAR |
| `a` | HUGEINT |
| `a7` | HUGEINT |
| `a28` | HUGEINT |
| `share` | DOUBLE |
| `share_change_28d` | DOUBLE |
| `wow` | DOUBLE |
| `acceleration` | DOUBLE |
| `views_per_minute` | DOUBLE |
| `new_creator_share` | DOUBLE |
| `shorts_share` | DOUBLE |
| `dss` | DOUBLE |
| `active_creators` | BIGINT |
| `videos28` | BIGINT |
| `creators28` | BIGINT |
| `hhi` | DOUBLE |
| `cr3` | DOUBLE |
| `breadth` | DOUBLE |
| `breadth_n` | BIGINT |
| `advancers` | BIGINT |
| `decliners` | BIGINT |
| `advance_decline` | BIGINT |
| `confident_share28` | DOUBLE |
| `thin` | BOOLEAN |

### `topic_index`

4.1-4.7: chained index level, trend index, relative strength

| Column | Type |
| --- | --- |
| `d` | DATE |
| `level` | INTEGER |
| `code` | VARCHAR |
| `matched_creators` | BIGINT |
| `ratio` | DOUBLE |
| `base_date` | DATE |
| `index_level` | DOUBLE |
| `index_equal_weight` | DOUBLE |
| `trend_level` | DOUBLE |
| `parent` | VARCHAR |
| `relative_strength` | DOUBLE |

### `topic_alerts`

7.2-7.6 on the latest day

| Column | Type |
| --- | --- |
| `d` | DATE |
| `kind` | VARCHAR |
| `code` | VARCHAR |
| `subject` | VARCHAR |
| `value` | DOUBLE |

### `creator_subs`

1.8: subscriber changes above YouTube's rounding step, spread across the days between steps

| Column | Type |
| --- | --- |
| `d` | DATE |
| `channel_id` | VARCHAR |
| `subscribers` | BIGINT |
| `rounding_step` | BIGINT |
| `subs_change_reported` | BIGINT |
| `subs_gained_est` | DOUBLE |

### `topic_breaks`

1.11: splice factor per topic around each structural break in config

| Column | Type |
| --- | --- |
| `break_date` | DATE |
| `level` | INTEGER |
| `code` | VARCHAR |
| `before_median` | DOUBLE |
| `after_median` | DOUBLE |
| `before_days` | BIGINT |
| `after_days` | BIGINT |
| `splice_factor` | DOUBLE |

### `data_quality_daily`

10.1-10.2: pull completeness and the share of attention from confident classifications

| Column | Type |
| --- | --- |
| `d` | DATE |
| `channels_expected` | BIGINT |
| `channels_received` | BIGINT |
| `pull_completeness` | DOUBLE |
| `videos_snapshotted` | BIGINT |
| `views_gained` | HUGEINT |
| `confident_share` | DOUBLE |
| `unclassified_share` | DOUBLE |

### `audit_error_rate`

10.3: wrong codes per sector in the latest hand audit, flagged above the threshold

| Column | Type |
| --- | --- |
| `kind` | VARCHAR |
| `audited_on` | DATE |
| `sector` | VARCHAR |
| `checked` | BIGINT |
| `wrong` | BIGINT |
| `error_rate` | DOUBLE |
| `needs_fix` | BOOLEAN |

### `creator_metrics`

| Column | Type |
| --- | --- |
| `channel_id` | VARCHAR |
| `title` | VARCHAR |
| `niche` | VARCHAR |
| `subscribers` | BIGINT |
| `views_28d` | BIGINT |
| `growth_28d` | DOUBLE |
| `uploads_per_week` | DOUBLE |
| `last_upload` | DATE |
| `regularity` | DOUBLE |
| `consistency` | DOUBLE |
| `top_video_share` | DOUBLE |
| `gini` | DOUBLE |
| `activation` | DOUBLE |
| `relative_er` | DOUBLE |
| `videos_used` | BIGINT |
| `focus` | DOUBLE |
| `topics_used` | BIGINT |

### `creator_cvs`

| Column | Type |
| --- | --- |
| `channel_id` | VARCHAR |
| `title` | VARCHAR |
| `niche` | VARCHAR |
| `subscribers` | BIGINT |
| `views_28d` | BIGINT |
| `growth_28d` | DOUBLE |
| `uploads_per_week` | DOUBLE |
| `last_upload` | DATE |
| `regularity` | DOUBLE |
| `consistency` | DOUBLE |
| `top_video_share` | DOUBLE |
| `gini` | DOUBLE |
| `activation` | DOUBLE |
| `relative_er` | DOUBLE |
| `videos_used` | BIGINT |
| `focus` | DOUBLE |
| `topics_used` | BIGINT |
| `vs_niche` | DOUBLE |
| `steadiness` | DOUBLE |
| `ccos` | DOUBLE |
| `momentum` | DOUBLE |
| `consistency_pillar` | DOUBLE |
| `audience_depth` | DOUBLE |
| `market_opportunity` | DOUBLE |
| `hit_dependence` | DOUBLE |
| `cvs` | DOUBLE |
| `auto_gates` | BOOLEAN |
| `manual_gates` | BOOLEAN |

### `cvs_history`

| Column | Type |
| --- | --- |
| `d` | DATE |
| `channel_id` | VARCHAR |
| `cvs` | DOUBLE |
| `tier` | VARCHAR |

### `creator_watchlist`

| Column | Type |
| --- | --- |
| `channel_id` | VARCHAR |
| `title` | VARCHAR |
| `niche` | VARCHAR |
| `cvs` | DOUBLE |
| `momentum` | DOUBLE |
| `consistency_pillar` | DOUBLE |
| `audience_depth` | DOUBLE |
| `market_opportunity` | DOUBLE |
| `hit_dependence` | DOUBLE |
| `auto_gates` | BOOLEAN |
| `manual_gates` | BOOLEAN |
| `tier` | VARCHAR |
| `needs_review` | BOOLEAN |

### `creator_alerts`

| Column | Type |
| --- | --- |
| `d` | DATE |
| `kind` | VARCHAR |
| `channel_id` | VARCHAR |
| `title` | VARCHAR |
| `prev_tier` | VARCHAR |
| `tier` | VARCHAR |
| `cvs` | DOUBLE |

## Later set (stage: later)

### `video_decay`

2.6  decay rate and half-life of each video's daily views over days 1-30

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `channel_id` | VARCHAR |
| `points` | BIGINT |
| `decay_rate` | DOUBLE |
| `half_life_days` | DOUBLE |
| `fit_r2` | DOUBLE |

### `video_evergreen`

2.7  share of a video's views that came after day 30 (videos older than 90 days)

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `age` | BIGINT |
| `views_at_cut` | BIGINT |
| `views_now` | BIGINT |
| `evergreen_share` | DOUBLE |

### `video_packaging_metrics`

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `packaging_id` | VARCHAR |
| `packaging_label` | VARCHAR |
| `topic_code` | VARCHAR |
| `outlier_score` | DOUBLE |
| `topic_score` | DOUBLE |
| `breakout` | BOOLEAN |

### `packaging_wins`

2.11 which packaging formats beat their creators most often, per topic

| Column | Type |
| --- | --- |
| `level` | INTEGER |
| `code` | VARCHAR |
| `packaging_label` | VARCHAR |
| `videos` | BIGINT |
| `median_outlier` | DOUBLE |
| `beat_creator_rate` | DOUBLE |
| `breakout_rate` | DOUBLE |

### `creator_flow`

3.19 creators moving into or out of each topic's upload mix

| Column | Type |
| --- | --- |
| `level` | INTEGER |
| `code` | VARCHAR |
| `creators_in` | BIGINT |
| `creators_out` | BIGINT |
| `net_flow` | BIGINT |

### `creator_drift`

5.8  share of a creator's uploads outside its primary niche, now vs a year earlier

| Column | Type |
| --- | --- |
| `channel_id` | VARCHAR |
| `primary_niche` | VARCHAR |
| `off_niche_now` | DOUBLE |
| `off_niche_year_ago` | DOUBLE |
| `topic_drift` | DOUBLE |

### `creator_earnings`

5.9  90-day earnings range from RPM bands (labeled with the bands' source)

| Column | Type |
| --- | --- |
| `channel_id` | VARCHAR |
| `niche_code` | VARCHAR |
| `long_views_90d` | HUGEINT |
| `shorts_views_90d` | HUGEINT |
| `earnings_low_90d` | DOUBLE |
| `earnings_high_90d` | DOUBLE |
| `rpm_source` | VARCHAR |

### `entity_daily`

6.2  daily attention on videos naming each brand, company or product category

| Column | Type |
| --- | --- |
| `d` | DATE |
| `entity` | VARCHAR |
| `entity_type` | VARCHAR |
| `a` | HUGEINT |
| `videos` | BIGINT |
| `creators` | BIGINT |

### `purchase_intent`

6.3  share of sampled comments asking where to buy, price, link or availability

| Column | Type |
| --- | --- |
| `video_id` | VARCHAR |
| `comments_sampled` | BIGINT |
| `purchase_intent` | DOUBLE |

### `topic_purchase_intent`

| Column | Type |
| --- | --- |
| `level` | INTEGER |
| `code` | VARCHAR |
| `comments_sampled` | HUGEINT |
| `purchase_intent` | DOUBLE |

### `platform_daily`

9.1  attention per topic per platform

| Column | Type |
| --- | --- |
| `d` | DATE |
| `platform` | VARCHAR |
| `level` | INTEGER |
| `code` | VARCHAR |
| `a` | HUGEINT |
| `creators` | BIGINT |
| `followers` | HUGEINT |

### `platform_z`

9.2  each platform's weekly attention as a z-score against its own 26 weeks

| Column | Type |
| --- | --- |
| `week` | DATE |
| `platform` | VARCHAR |
| `level` | INTEGER |
| `code` | VARCHAR |
| `a` | HUGEINT |
| `days` | BIGINT |
| `z` | DOUBLE |
| `weeks_history` | BIGINT |

### `basket_coverage`

10.4 tracked views as a share of an outside estimate of the topic's total views

| Column | Type |
| --- | --- |
| `code` | VARCHAR |
| `period_start` | DATE |
| `period_end` | DATE |
| `source` | VARCHAR |
| `tracked_views` | HUGEINT |
| `estimated_total_views` | DOUBLE |
| `basket_coverage` | DOUBLE |

## Statistics (stage: later)

### `topic_ci`

3.21 bootstrap 5th-95th percentile range for each topic's week-over-week growth

| Column | Type |
| --- | --- |
| `d` | DATE |
| `level` | INTEGER |
| `code` | VARCHAR |
| `wow` | DOUBLE |
| `wow_low` | DOUBLE |
| `wow_high` | DOUBLE |
| `creators` | INTEGER |
| `samples` | INTEGER |

### `lead_lag`

6.6  correlation of weekly intent-weighted attention growth with an outcome series' growth,

| Column | Type |
| --- | --- |
| `series_id` | VARCHAR |
| `code` | VARCHAR |
| `source` | VARCHAR |
| `lag_weeks` | INTEGER |
| `rho` | DOUBLE |
| `n` | INTEGER |
| `is_best` | BOOLEAN |

### `predictive_tests`

6.7  Granger test: does lagged attention improve a forecast of the outcome beyond the

| Column | Type |
| --- | --- |
| `series_id` | VARCHAR |
| `code` | VARCHAR |
| `source` | VARCHAR |
| `lead_weeks` | INTEGER |
| `weeks` | INTEGER |
| `lags` | INTEGER |
| `f_stat` | DOUBLE |
| `p_value` | DOUBLE |
| `rmse_own_history` | DOUBLE |
| `rmse_with_attention` | DOUBLE |
| `out_of_sample_better` | BOOLEAN |
| `passes` | BOOLEAN |

### `call_scores`

8.2-8.5 each call checked against its outcome series: hit or miss, lead time, Brier score,

| Column | Type |
| --- | --- |
| `call_id` | INTEGER |
| `made_on` | DATE |
| `subject` | VARCHAR |
| `direction` | VARCHAR |
| `horizon_weeks` | INTEGER |
| `series_id` | VARCHAR |
| `probability` | DOUBLE |
| `manual_outcome` | VARCHAR |
| `status` | VARCHAR |
| `typical_move` | DOUBLE |
| `lead_days` | DOUBLE |
| `brier` | DOUBLE |
| `base_hit_rate` | DOUBLE |

### `call_summary`

8.2-8.5 the track record in one row

| Column | Type |
| --- | --- |
| `calls` | INTEGER |
| `resolved` | INTEGER |
| `hits` | INTEGER |
| `hit_rate` | DOUBLE |
| `median_lead_days` | DOUBLE |
| `brier` | DOUBLE |
| `base_hit_rate` | DOUBLE |
| `beat_base_rate` | DOUBLE |

### `composite_index`

9.3  per-platform chained attention index and a follower-weighted composite (weights reset quarterly)

| Column | Type |
| --- | --- |
| `d` | DATE |
| `level` | INTEGER |
| `code` | VARCHAR |
| `platform` | VARCHAR |
| `index_level` | DOUBLE |
| `weight` | DOUBLE |

### `platform_lead`

9.4  the 6.6 lead-lag run between platforms: which platform moves first in each topic

| Column | Type |
| --- | --- |
| `code` | VARCHAR |
| `leader` | VARCHAR |
| `follower` | VARCHAR |
| `best_lag_weeks` | INTEGER |
| `rho` | DOUBLE |
| `n` | INTEGER |

## Orchestration

### `pipeline_runs`

| Column | Type |
| --- | --- |
| `run_id` | VARCHAR |
| `job` | VARCHAR |
| `stage` | VARCHAR |
| `started_at` | TIMESTAMP |
| `finished_at` | TIMESTAMP |
| `status` | VARCHAR |
| `attempts` | INTEGER |
| `detail` | VARCHAR |

## Views (rebuilt on every compute)

- `alerts`
- `channel_daily`
- `channel_niche`
- `channel_video_stats`
- `content_daily`
- `content_format_daily`
- `niche_daily`
- `niche_momentum`
- `niche_opportunity`
- `niche_opportunity_daily`
- `screener`
- `video_daily`
- `video_latest`
- `video_topic_daily`
