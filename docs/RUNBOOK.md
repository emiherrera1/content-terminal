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
| daily | 7:17 AM ET every day (`.github/workflows/daily.yml`) | `python -m terminal run daily` | **pull_youtube**, pull_tiktok, pull_instagram, classify_channels, classify_videos, notes, news, tag, comments, **compute**, later, ask, quality_gate, **export**, site; then publishes the site |
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
| `NOTES_SHEET_CSV_URL` | Creator Capital daily notes in News and Platform | a CSV link to a tab with only Date, Category, Label, Headline, Note, Sources, POSTED (never the Flags or Gmail columns) |
| `COMPANIES_SHEET_CSV_URL` | Companies view (stage ladder, Undercapitalized, Deals, Investors) and the Company block on tear sheets | the "Publish to web" CSV link of the Creator Companies sheet (CVA Work / Fund). The job reads only its three Feed tabs (gid 10, 11, 12), which hold Public rows; Private rows never leave the sheet |

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
| Creator Capital daily notes | News view, Platform news | Set `NOTES_SHEET_CSV_URL`, or drop an export at `data/inputs/daily_notes.csv`. Drafts show with a DRAFT tag; `notes.include_drafts: false` in `config/settings.yaml` shows posted notes only. A note deleted from the sheet leaves the terminal on the next run. |
| Creator Companies sheet | Companies view, tear sheet Company block, `ask_creator_companies`, `ask_creator_rounds` | Set `COMPANIES_SHEET_CSV_URL`, or drop exports at `data/inputs/companies_{creators,companies,rounds}.csv`. The sheet is the record: each daily run replaces the three tables. A blank stage means not researched yet; stage 1 means researched and nothing found. Every row carries a source and a confidence (Confirmed, Reported, Inferred). |
| Video and subscriber snapshots (already collected) | Surface view (3D surface, Table, Skew, Term, Rank), `SURF [niche]`, `SKEW`, `ask_niche_skew` | Nothing to set. Reach = median views per 100 subscribers by creator size (subscriber percentile inside the niche) and video age (7D to 1Y). Cells need 8+ videos from 2+ creators; videos over a year old are left out because we pull recent uploads. Skew = smallest third's reach / biggest third's, videos up to 3 months old. |
| Video descriptions and YouTube's paid-promotion flag (already pulled) | Companies view, Revenue streams and Sponsors tabs; tear sheet Business line; `STREAMS`, `SPONSORS`; `ask_creator_business`, `ask_sponsors` | Nothing to set. The daily `business` stage reads every stored description (now kept up to 5,000 characters, was 1,000). Link hosts map to streams in `terminal/business.py`; add a host there when a new affiliate network or course platform shows up. Shorteners are not opened. |
| Daily views history (already pulled) | Risk view, tear sheet Risk line, `RISK`, `ask_creator_risk` | Nothing to set. Needs 30+ days of returns per creator over a 90-day window, so live values start around mid-December 2026. |
| `config/funders.csv` plus the Rounds feed | Holders view (Holders, By company, Chart), `HDS [company or funder]`, `ask_holders`, `ask_holdings` | One row per funder with its type (Venture, Growth, Private equity, Credit, Creator finance, Revenue share, Catalog finance, Acquirer, Strategic, Family office, Accelerator, Angel, Founder) and a source. A name in the Rounds tab that is not in the registry shows as Unclassified until a row is added. Instrument comes from the round's wording and the holder's type. |
| `config/platform_rates.csv` and `config/platform_rate_changes.csv` | Platform view, Monetization rates board (Rates, Changes), `RATES`, `ask_platform_rates`, `ask_rate_changes` | Hand-kept public facts. Each program row has a creator share (or a dollar rate), Disclosed (Yes, Partly, No), a source and a verified date; a row turns stale after 90 days without re-checking. Log every change in the changes file with its date and source; a change marked scheduled applies on its date. |
| TubeLab API (`TUBELAB_API_KEY`, Pro or Team plan) | Platform view Market RPM and RPM by niche, `RPM`, `ask_niche_rpm` | Weekly job, at most one pull every 28 days: one channel search per niche (5 credits each, about 350 a month for 70 niches), long-form monetized English channels, median of TubeLab's estimated RPM range. Skipped until the secret is set. |
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

## Asking questions in chat

Claude's MotherDuck connector (Claude > Customize > Connectors > MotherDuck > Connect, then sign in to MotherDuck)
reads the database directly. The pipeline's `ask` stage rebuilds a set of plain-English views for it after every run
(`terminal/ask.py`): `ask_guide` lists them; `ask_creators`, `ask_niches`, `ask_niche_history`, `ask_creator_history`,
`ask_topic_attention`, `ask_news`, `ask_tier_changes`, `ask_creator_companies`, `ask_creator_rounds`, `ask_niche_skew`, `ask_creator_risk`, `ask_creator_business`, `ask_sponsors`, `ask_holders`, `ask_holdings`, `ask_platform_rates`, `ask_rate_changes`, `ask_niche_rpm`, `ask_glossary` and `ask_topics` use names instead of codes and
carry a definition on every view and key column. In the connector's settings, set the write tool (`query_rw`) to
**Blocked** so chat can read but never change the data. Each chat query uses a little of the MotherDuck Lite plan's
10 compute hours a month.
