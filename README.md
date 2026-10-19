# Content Terminal (MVP)

A first version of a Bloomberg-style terminal for content markets. It takes a daily snapshot of a few thousand YouTube channels, classifies each one with the [CTNCS taxonomy](https://github.com/emiherrera1/ctncs), and computes these views:

| View | What it answers |
| --- | --- |
| `niche_daily` | Views gained per CTNCS niche per day (the niche index) |
| `niche_momentum` | Which niches are accelerating: last 7 days of gains vs the 7 before |
| `screener` | Which creators are growing faster than their niche: 28-day growth, growth vs niche median, upload pace, outlier rate |
| `alerts` | Channels whose daily gain is 3+ standard deviations above their trailing 28 days and at least double their typical day |
| `niche_opportunity` | The opportunity metrics per niche: TVE (content minutes, 28 days), SI (saturation, 0 to 1), NGP (growth ratio), CCOS (opportunity score), CHG% of CCOS over 7 days, TREND and RISK |
| `niche_opportunity_daily` | The same per niche per day, for trend charts |
| `news_ranked` | Articles from the feeds in `config/feeds.yaml`, tagged by Claude (category, platform, impact, sentiment, creators named) and ranked by impact and recency; creators are matched to tracked channels |

Scope is deliberately narrow: YouTube only, two or three CTNCS sectors (set in `config/settings.yaml`), and daily refreshes. The open layer (codebook, niche index) is meant to sit with C-CPAR; the screener and alerts are the TerminalCo prototype.

## How it works

1. **Pull** (`terminal/youtube.py`): the official YouTube Data API v3. Channel stats in batches of 50, each channel's recent uploads, then video stats.
2. **Store** (`terminal/store.py`): DuckDB, one row per channel or video per day. A local file by default; set `TERMINAL_DB=md:content_terminal` to keep it in MotherDuck so the scheduled job can reach it.
3. **Classify** (`terminal/classify.py`): Claude reads each channel's title, description and recent video titles and assigns the most specific CTNCS code within the chosen sectors, or `unclassified`. Every classification is stored with its confidence and a one-line rationale, and `reviewed` stays false until a person checks it.
4. **Compute** (`terminal/metrics.py`): the four views above, in SQL.
5. **Export**: CSVs in `data/exports/` for a dashboard or a spreadsheet.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # add YOUTUBE_API_KEY and ANTHROPIC_API_KEY, then: set -a; source .env; set +a
```

- **YouTube key:** Google Cloud console, enable "YouTube Data API v3", create an API key.
- **Anthropic key:** console.anthropic.com.

## Run

```bash
# 1. Sectors are in config/settings.yaml; anchor channels in config/channels.csv; niche queries in config/discovery.yaml
python -m terminal discover    # find active mid-size channels per niche -> config/channels_discovered.csv
python -m terminal seed        # add anchors + discovered channels
python -m terminal daily       # pull, classify new channels, compute, export
python -m terminal audit       # 25 random classifications to check by hand
python -m terminal news        # pull news feeds and tag new articles
python -m pytest               # metric tests on synthetic data, no keys needed
```

Momentum needs 14 days of snapshots, alerts 8, and the screener's 28-day growth fills in over its first month.

## Content-level index ("how did woodworking do today")

Every recent video is classified on its own, not by its channel: topic down to CTNCS level 4 (sector > category > niche > sub-niche) and format (the CTNCS v2.0 format tag: Tutorials, Reviews, DIY...). A woodworking video from a home-renovation channel counts toward Woodworking.

```bash
python -m terminal classify-videos          # each new video once, in batches of 25 with the codebook cached
python -m terminal index woodworking        # or a code: python -m terminal index 02.05.010.019 --date 2026-10-18
```

| View | What it answers |
| --- | --- |
| `video_daily` | Views gained per video per day (a video's first snapshot counts in full if it was uploaded since the last pull) |
| `content_daily` | Views gained per topic per day at every level, with videos, channels, new uploads, Shorts views and the median video's gain |
| `content_format_daily` | The same split by format |

`index` prints the day's views gained, change vs yesterday and vs the 7-day average, the split by sub-topic and by format, and the top videos. It covers the tracked channels' recent uploads, so it is a sample (like a stock index's basket), not all of YouTube. Topic coverage follows the codebook: CTNCS v2.0 has 17 sectors and about 350 sub-niches. Classification uses `VIDEO_CLASSIFIER_MODEL` (default `claude-haiku-5-5`); `video_classify_daily_limit` in `config/settings.yaml` caps the daily run.

## Formula Library tables

`compute` (and the daily job) also rebuilds the tables in `terminal/formulas.py`, which implement the "Next" set of the Formula Library (Google Drive: "Content Terminal: Formula Library"). Every threshold and weight is in `config/formulas.yaml`.

| Table | Formulas | What it holds |
| --- | --- | --- |
| `video_metrics` | 2.1-2.5, 2.8-2.9 | Views at 1 and 7 days, outlier score against the creator's own videos at the same age, score against the topic, breakout flag, engagement and comment rates |
| `creator_coverage` | 1.6-1.7 | Share of a creator's daily views the terminal attributes to topics, and back-catalog views |
| `topic_series` | 3.1-3.10, 3.13, 3.15, 6.1, 6.4 | Per topic per day: attention, share of parent, growth, acceleration, weekday-adjusted attention, views per minute published, new-creator share, intent-weighted attention and the Demand Signal Score |
| `topic_current` | 3.3, 3.11-3.12, 3.20 | Latest day: active creators, breadth, HHI, CR3 and the THIN flag |
| `topic_index` | 4.1-4.7 | Chain-linked index (100 on the base date), trend index, relative strength against the parent topic |
| `topic_alerts` | 7.2-7.6 | Latest day's topic surges, share shifts, demand signals, newcomer wins and video breakouts |

`video_daily` now clips negative corrections to 0 and spreads gains over gaps of up to 7 days (flagged `corrected` and `imputed`). A creator counts as new to a topic only if the channel is young or it was already posting other topics before entering, so the edge of the tracking window isn't mistaken for a newcomer.

## Creator watchlist and the call log

`compute` also scores every tracked creator (`terminal/creators.py`, Formula Library 5.3-5.7 and 5.10-5.11): growth against their niche, upload cadence and regularity, consistency, hit dependence (top video's share and the Gini of recent videos), audience depth (median views per subscriber and relative engagement) and topic focus. These feed the Creator Viability Score: five pillars, each a percentile within the creator's niche, weighted 25/20/20/20/15 to total 100.

- **Gates:** 25K+ subscribers, 250K+ views in 28 days and an upload in the last 30 days are checked automatically. "Independent" and "clean record" are confirmed by a person: `python -m terminal review --creator UC... --independent yes --clean yes --by Emily`.
- **Tiers:** Watch at 60+, Priority at 75+ held for 14 computed days, Diligence-ready at 85+ held for 28 days with the manual gates confirmed. Scores are kept daily in `cvs_history`; tier changes land in `creator_alerts`.
- `python -m terminal watchlist` lists creators by score.

The call log lives in `calls/` (see `calls/README.md`). `python -m terminal call --topic woodworking --direction up --outcome "Google Trends: woodworking, US" --p 0.6` records the signal's value at that moment; `resolve` records the outcome; `calls` lists them and checks the log hasn't been edited. Each row carries the hash of the row before it, and committing the files gives every call a git timestamp.

## Building the channel list

The list has two parts:

- **Anchors** (`config/channels.csv`): about 100 established creators across eight consumer niches (fitness, food, fashion and beauty, consumer tech, personal finance, entrepreneurship, physical health, mental health). They benchmark each niche. Handles marked `verify` were written from memory; `seed` prints any that don't resolve.
- **Discovered** (`config/channels_discovered.csv`): `discover` searches recent videos (last 60 days, most viewed first) for consumer-demand phrases in each niche, collects the channels behind them, and keeps those with 10K to 2M subscribers and at least 20 videos, up to 375 per niche. This is where early movement shows up.

One discovery run uses about 5,000 units and usually adds 1,000 to 1,500 channels. Run it on two or three days before the daily schedule starts (quota resets daily; runs merge into the same file), then `seed`. Review the discovered file before seeding: drop brands, news outlets and anything off-niche.

## Opportunity metrics

Measurable versions of the prototype terminal's columns, per CTNCS niche (`terminal/metrics.py`):

- **TVE:** minutes of content published by tracked channels in the last 28 days (content supply)
- **SI:** uploads per 1,000 views gained over 28 days, as x / (x + that day's median across niches). 0 to 1; the median niche is 0.5
- **NGP:** views gained in the last 7 days ÷ the 7 days before (1.0 = flat)
- **CCOS:** 20 × NGP × (1 − SI). Growth and saturation each count once; the median flat niche scores 10
- **CHG% / TREND:** CCOS today vs 7 days ago; UP above +3%, DOWN below −3%, else STABLE
- **RISK:** average percentile of volatility (coefficient of variation of daily views, 28 days) and CR3 (top 3 channels' share of niche views), split LOW / MEDIUM / HIGH by thirds

SI and RISK are relative: they rank niches against the others being tracked.

## Daily schedule

`.github/workflows/daily.yml` runs `python -m terminal daily` every morning. It needs three repository secrets: `YOUTUBE_API_KEY`, `ANTHROPIC_API_KEY` and `MOTHERDUCK_TOKEN` (GitHub runners don't keep files between runs, so the database lives in MotherDuck). It can also be run by hand from the Actions tab.

## Limits to check before scaling

- **Quota:** the default YouTube quota is 10,000 units a day. Each channel costs one unit for its uploads list (up to 50 uploads) and about one more for those videos' stats; channel stats cost one unit per 50 channels. About 3,000 channels uses ~6,000 units. More needs a quota increase from Google.
- **Terms:** YouTube's API terms limit how long some data can be stored and how it is shown to others. Read them before showing data to design partners or keeping long history.
- **Codebook version:** `config/ctncs/` holds CTNCS v2.0 (`topics.csv`, `formats.csv`), exported from the CTNCS codebook sheet. Re-export when the codebook changes; codes are never reused. `crosswalk_v1_v2.csv` maps v1.0 codes, and `python -m terminal migrate-ctncs` rewrites any stored v1.0 codes. v1.0 is kept in `config/ctncs/archive/`.
- **Classification quality:** audit a sample from each sector before trusting niche figures.

## Next steps

- Seed list of channels per sector (TubeLab's niche and outlier searches work for this)
- A dashboard over the four exports for design partners
- Audit workflow: mark reviewed classifications and correct codes
- Second platform once the YouTube version has paying interest

## Methodology

`config/methodology.csv` is the registry of every metric code the terminal shows (what it measures, formula, inputs,
how to read it, handbook section). It feeds both the Methodology Handbook's quick reference and the terminal's DES
page. Change a definition here, in the same commit as the code change, and log it in the handbook's change log.
