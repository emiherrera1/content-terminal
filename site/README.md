# terminal.cvaccelerator.com

## Two copies

| Address | Code | Data | Who |
| --- | --- | --- | --- |
| terminal.cvaccelerator.com | the `beta` branch, frozen | live, minus unposted Creator Capital drafts; Live / Demo switch | owner password, or the guest password |
| dev.content-terminal.pages.dev | `main`, the latest | everything; Live / Demo switch | owner password |

`site/publish.sh` publishes both after every daily and weekly run. Guests (`TERMINAL_GUEST_PASSWORD`) see every screen
but never the deal pipeline: the gate refuses them `/api/pipeline`, and the Watchlist hides stages and notes.

**Moving the beta forward** (when a new version is ready to show): point the `beta` branch at the commit, e.g.
`git push origin main:beta --force`, then run the **site** workflow. Tag each beta (`git tag v0.6-beta`) so any version
can be brought back. Version 0.5-beta is the one shown to the Dirigo team.

**Cloudflare settings for the two copies:** `TERMINAL_PASSWORD` must be set for both Production and Preview (the dev
copy is a Preview deployment; without it the dev copy refuses every request). `TERMINAL_GUEST_PASSWORD` only needs
Production. The `PIPELINE` KV binding goes on both if the pipeline should work on both.

The Content Terminal as a private website: one page, today's data files, and a password gate.

| Path | What it is |
| --- | --- |
| `public/index.html` | The terminal page. Edit it directly. |
| `public/vendor/d3.min.js` | Charting library, self-hosted (ISC licence in `d3.LICENSE.txt`). |
| `public/data/*.json` | Built each run by `python -m terminal site`. Never committed. |
| `functions/_middleware.js` | The password gate. Runs on Cloudflare before every request, data files included. |
| `functions/api/pipeline.js` | The Watchlist's deal pipeline: each creator's stage and note, saved in Cloudflare KV (binding `PIPELINE`), behind the same password. Keeps a log of every stage change. |
| `deploy.sh` | Uploads `public/` and `functions/` to Cloudflare Pages. Does nothing until the Cloudflare secrets exist. |
| `dash-shim.js`, `build_page.py` | How the page was ported from its first version; kept for the record. |

**Publishing.** The daily and weekly jobs build the data and publish on their own. To publish by hand (after a page
change, or to show the synthetic world), run the **site** workflow from the Actions tab and pick `live` or `synthetic`.
Synthetic data is labeled on the page in amber: "SYNTHETIC DATA: made-up channels, not results".

**The gate.** One shared password, stored only in Cloudflare as the secret `TERMINAL_PASSWORD`. A correct password sets
a signed, HttpOnly cookie for 30 days. Changing the password logs everyone out. If the secret is missing the site
refuses every request. `/logout` ends a session. For per-person logins (email codes, revoke one person), move
cvaccelerator.com's DNS to Cloudflare and put Cloudflare Access in front of the site; nothing here has to change.

**Run it locally.**

```
python -m terminal site --demo
cd site && npx wrangler@3.114.0 pages dev public --binding TERMINAL_PASSWORD=pick-one
```

## One-time setup

1. Create a free Cloudflare account. Workers & Pages > Create > Pages > **Upload assets**, name the project
   `content-terminal`, and upload any small file to create it.
2. In the project: Settings > Variables and secrets > Add > type **Secret**, name `TERMINAL_PASSWORD`, value your
   password (Production).
3. Custom domains > Set up a custom domain > `terminal.cvaccelerator.com`. Cloudflare shows a CNAME target
   (`content-terminal.pages.dev`). Do this step before the DNS record, or the domain returns a 522 error.
4. In Cargo: Account Settings > Domain Names > cvaccelerator.com > ··· > Edit DNS > add a **CNAME**, name `terminal`,
   value `content-terminal.pages.dev`. Cloudflare issues the certificate once the record resolves (minutes to an hour).
5. Cloudflare: My Profile > API Tokens > Create token > template **Edit Cloudflare Workers** (or a custom token with
   Account > Cloudflare Pages > Edit). Copy it once.
6. GitHub repo > Settings > Secrets and variables > Actions > add `CLOUDFLARE_API_TOKEN` (step 5) and
   `CLOUDFLARE_ACCOUNT_ID` (the Account ID on the right of the Workers & Pages overview).
7. Optional: `FORMATS_SHEET_CSV_URL` for the Formats view. In the Trending Formats sheet: File > Share > Publish to web >
   the `trending_titles_master_clean` tab > CSV > Publish, and paste the link as the secret.
8. Actions > **site** > Run workflow.
9. Pipeline storage for the Watchlist: Workers & Pages > KV (Storage & databases > Workers KV) > Create a namespace
   named `content-terminal-pipeline`. Then the Pages project > Settings > Bindings > Add > KV namespace: variable name
   `PIPELINE`, namespace `content-terminal-pipeline` (Production). Until this exists, stages save in each browser only
   and the Watchlist says so.
