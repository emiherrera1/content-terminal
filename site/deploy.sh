#!/usr/bin/env bash
# Upload a site folder (page + data files) and the functions beside it (the password gate, the pipeline API) to
# Cloudflare Pages.  deploy.sh [branch] [folder]
#   branch main = production (terminal.cvaccelerator.com, the beta);  dev = dev.content-terminal.pages.dev
# Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID (GitHub secrets). Without them it does nothing and says so.
set -euo pipefail
BRANCH="${1:-main}"
SRC="$(cd "$(dirname "${2:-$(dirname "$0")/public}")" && pwd)/$(basename "${2:-public}")"
cd "$(dirname "$0")"
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] || [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  echo "::notice title=site not deployed::Add CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID as repository secrets to publish terminal.cvaccelerator.com"
  exit 0
fi
if [ ! -f "$SRC/data/manifest.json" ]; then
  echo "::warning title=site not deployed::no data files in $SRC/data (python -m terminal site)"
  exit 0
fi
PROJECT="${CF_PAGES_PROJECT:-content-terminal}"
# first run only: create the Pages project (an existing project makes this a no-op)
npx --yes wrangler@3.114.0 pages project create "$PROJECT" --production-branch main >/dev/null 2>&1 || true
npx --yes wrangler@3.114.0 pages deploy "$SRC" --project-name "$PROJECT" --branch "$BRANCH" --commit-dirty=true
