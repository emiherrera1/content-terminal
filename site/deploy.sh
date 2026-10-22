#!/usr/bin/env bash
# Upload site/public (page + today's data files) and site/functions (the password gate) to Cloudflare Pages.
# Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID (GitHub secrets). Without them it does nothing and says so.
set -euo pipefail
cd "$(dirname "$0")"
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] || [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  echo "::notice title=site not deployed::Add CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID as repository secrets to publish terminal.cvaccelerator.com"
  exit 0
fi
if [ ! -f public/data/manifest.json ]; then
  echo "::warning title=site not deployed::no data files were built (python -m terminal site)"
  exit 0
fi
PROJECT="${CF_PAGES_PROJECT:-content-terminal}"
# first run only: create the Pages project (an existing project makes this a no-op)
npx --yes wrangler@3.114.0 pages project create "$PROJECT" --production-branch main >/dev/null 2>&1 || true
npx --yes wrangler@3.114.0 pages deploy public --project-name "$PROJECT" --branch main --commit-dirty=true
