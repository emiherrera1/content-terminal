# Runbook

How the Content Terminal's data moves, what runs when, and what to do when something breaks. Written so a data
engineer can take it over without a handoff call.

## The system in one picture

```
 Sources                         Storage (MotherDuck: md:content_terminal)        Outputs
 ───────                         ─────────────────────────────────────────        ───────
 YouTube Data API ──daily──▶ channels, channel_snapshots, videos, video_snapshots ─┐
 Claude (classify) ─daily──▶ classifications, video_classifications                │
 News feeds ────────daily──▶ news                                                  ├─▶ compute ─▶ core tables ─┐
 Rules (no API) ────daily──▶ video_packaging, video_entities                       │   (critical)              │
 YouTube comments ──daily──▶ video_comments            (off until switched on)     │                           ├─▶ export ─▶ data/exports/*.csv
 TikTok, Instagram ─daily──▶ same raw tables, platform column (off until keys)     │                           │    (artifact on every run)
 Google Trends ────weekly──▶ outcome_series                                        │   later ─▶ Later set +    │
 data/inputs/*.csv ─weekly─▶ outcome_series, outside_estimates                     ┘   (not     statistics ────┘
 Hand audits ──────as done─▶ classification_audits                                     critical)
 calls/*.csv (git, hash-chained) ─────────────────────────────────────────────────────────▶ call_scores, call_summary
```

## Jobs

| Job | When | Command | Stages (critical in bold) |
| --- | --- | --- | --- |
| daily | 7:17 AM ET every day (`.github/workflows/daily.yml`) | `python -m terminal run daily` | **pull_youtube**, pull_tiktok, pull_instagram, classify_channels, classify_videos, news, tag, comments, **compute**, later, quality_gate, **export**, site; then publishes the site |
| weekly | Mondays 8:03 AM ET (`weekly.yml`) | `python -m terminal run weekly` | google_trends, outcome_csvs, outside_estimates, **compute**, later, audit_sheet, **export**, site; then publishes the site |
| rebuild | by hand, after a formula or config change | `python -m terminal run rebuild` | tag, **compute**, later, quality_gate, **export** |
| tests | every push to main (`tests.yml`) | `python -m pytest -q` | 60+ tests, including a synthetic world |
| site | by hand (`site.yml`) | `python -m terminal site [--demo]` + `site/deploy.sh` | publishes terminal.cvaccelerator.com with live or synthetic data |
| setup | by hand (`setup.yml`) | discover / screen / seed | adds channels; never run seed without a reviewed screening file |

Every stage writes a row to `pipeline_runs` (ok, warn, failed, skipped, with attempts and a detail line). A critical
failure turns the run red, and GitHub emails the repo owner. Any other failure is a yellow warning annotation, and
the job carries on. A source with no keys or files yet shows as **skipped**, with what it needs.

`python -m terminal health` checks freshness (latest snapshot no older than a day), the latest pull completeness, and
whether the last daily and weekly runs had failures. It exits 1 when anything is wrong, so any monitor can call it.

## Secrets (GitHub > Settings > Secrets and variables > Actions)

| Secret | Used by | Status |
| --- | --- | --- |
| `YOUTUBE_API_KEY` | daily pull, discovery, screening, comments | set |
| `ANTHROPIC_API_KEY` | channel and video classification, screening, news tagging | set |
| `MOTHERDUCK_TOKEN` | every job | set |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` | 9.x | not yet: TikTok Research API access is by application (C-CPAR may qualify as a non-profit researcher) |
| `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_BUSINESS_ID` | 9.x | not yet: needs a Facebook app and an Instagram professional account |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | publishing terminal.cvaccelerator.com | not yet: see `site/README.md` |
| `FORMATS_SHEET_CSV_URL` | the site's Formats view | optional: the sheet's publish-to-web CSV link |

The site password is not a GitHub secret. It lives only in Cloudflare (`TERMINAL_PASSWORD` on the Pages project).

Keys never go in chat, code or config files.

## Switching a source on

| Source | Formulas | To switch on |
| --- | --- | --- |
| Purchase-intent comments | 6.3 | In `config/formulas.yaml`, set `comments.enabled: true` and list topic codes under `comments.topics`. Costs 1 YouTube unit per video (50 a day by default). |
| Google Trends | 6.6, 6.7, 8.x | Edit the terms in `config/outcomes.yaml` (the current terms are suggestions). The weekly job pulls them. |
| Any other outcome series (sales, revenue, a stock) | 6.6, 6.7, 8.x | Drop a CSV in `data/inputs/outcomes/` with columns `series_id, code, label, d, value`. |
| Outside view estimates | 10.4 | `data/inputs/outside_estimates.csv` with `code, period_start, period_end, total_views, source`. |
| RPM bands | 5.9 | Replace the placeholder bands under `rpm_bands` in `config/formulas.yaml` and set `source:` to where they came from. Until then every earnings figure is labeled `placeholder`. |
| TikTok, Instagram | 9.1-9.4 | Add the secrets, list handles in `config/channels_tiktok.csv` / `config/channels_instagram.csv` (column `handle`), and implement `fetch()` in `terminal/sources.py` (the class docstrings say what each API returns). Everything downstream already runs on any platform. |
| Packaging and entity tags | 2.11, 6.2 | Already on, rule-based. Edit `config/ctncs/packaging.csv` (draft CTNCS v3 codes) and `config/entities.csv`. |

## When something breaks

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| daily red at **pull_youtube**, `HTTPError 403` | YouTube quota used up (10,000 units a day) | Wait for the reset at midnight Pacific. If it repeats, lower `videos_per_channel` in `config/settings.yaml` or request more quota in Google Cloud. |
| daily red at **pull_youtube**, `400 API key not valid` | key rotated or restricted | Replace `YOUTUBE_API_KEY`. |
| any job red at the first database step | `MOTHERDUCK_TOKEN` expired | New token at motherduck.com > Settings > Access tokens; replace the secret. |
| classify stages yellow, `401` or `overloaded` | Anthropic key or capacity | Unclassified videos are picked up on the next run; nothing is lost. |
| quality_gate yellow, completeness below 95% | channels deleted, made private, or a partial pull | `SELECT * FROM data_quality_daily ORDER BY d DESC` to see the day; figures that day are partial and flagged. |
| **compute** red | a formula change broke on real data | Revert the last commit to `terminal/` and rerun `run rebuild`; the raw data is untouched. |
| later yellow | a statistic failed on real data | Core tables and exports are fine. Run `TERMINAL_DEBUG=1 python -m terminal run rebuild` locally to see the traceback. |
| site not updating | Cloudflare secrets missing (the run shows a notice), or the token expired | `site/README.md` steps 5 and 6. The site keeps serving the last good publish. |
| terminal.cvaccelerator.com shows 522 | domain added in Cargo before Cloudflare, or removed in Cloudflare | Pages project > Custom domains > add it again. |
| health says STALE | the daily job didn't run, or failed at the pull | Check the Actions tab; rerun with "Run workflow". YouTube has no history endpoint, so a missed day can't be recovered later; the gap-fill rule (1.4) spreads the next day's gain across it. |

## Changing a formula

1. Change the SQL or Python (`terminal/formulas.py`, `creators.py`, `later.py`, `analytics.py`) and any weight or
   threshold in `config/formulas.yaml`. Never hard-code a number that belongs in config.
2. Add or update its entry in `config/methodology.csv` (it feeds the terminal's DES page and the handbook's quick reference).
3. Add a test that checks it against an independent calculation (see `tests/test_formulas.py` for the pattern).
4. `python -m pytest -q`, then `python -m terminal demo` to see it run on the synthetic world.
5. Log it in the handbook's change log. Parameter change = minor version (1.2); a change to how a metric is built = major (2.0).

## The synthetic world

`python -m terminal demo` builds `data/demo.duckdb` with 100 creators across YouTube, TikTok and Instagram, 28 weeks
of daily snapshots, outcome series, comments, an audit and 24 back-dated calls, then runs every job stage on it and
writes `data/exports_demo/` (with a SYNTHETIC notice). It never opens the real database or call log. The world has
known answers (each niche's outcome lags its attention by a set number of weeks; TikTok leads YouTube by two), and
the tests check that the formulas recover them. Use it to see a change end to end before it touches real data.

## What a first data hire would pick up

- Implement the TikTok and Instagram `fetch()` clients once access is granted.
- Move from GitHub Actions to a scheduler with backfills and alerting (Dagster or Prefect) when the job count grows;
  each stage here maps one-to-one to an asset or task.
- Add a dbt-style layer if analysts start writing SQL against the tables directly.
- Partition `video_snapshots` by month once it passes ~100M rows.
- Replace the rule-based packaging, entity and purchase-intent taggers with model-based ones, keeping the same tables.
