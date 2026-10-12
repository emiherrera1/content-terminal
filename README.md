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
# 1. Pick sectors in config/settings.yaml and list channels in config/channels.csv
python -m terminal seed        # add the channels
python -m terminal daily       # pull, classify new channels, compute, export
python -m terminal audit       # 25 random classifications to check by hand
python -m terminal news        # pull news feeds and tag new articles
python -m pytest               # metric tests on synthetic data, no keys needed
```

Momentum needs 14 days of snapshots, alerts 8, and the screener's 28-day growth fills in over its first month.

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
- **Codebook version:** `config/ctncs_codebook.csv` is the public v1.0 (12 sectors). Replace it with the audited version when that is published.
- **Classification quality:** audit a sample from each sector before trusting niche figures.

## Next steps

- Seed list of channels per sector (TubeLab's niche and outlier searches work for this)
- A dashboard over the four exports for design partners
- Audit workflow: mark reviewed classifications and correct codes
- Second platform once the YouTube version has paying interest
