# terminal.cvaccelerator.com

The Content Terminal as a private website: one page, today's data files, and a password gate.

| Path | What it is |
| --- | --- |
| `public/index.html` | The terminal page. Edit it directly. |
| `public/vendor/d3.min.js` | Charting library, self-hosted (ISC licence in `d3.LICENSE.txt`). |
| `public/data/*.json` | Built each run by `python -m terminal site`. Never committed. |
| `functions/_middleware.js` | The password gate. Runs on Cloudflare before every request, data files included. |
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
