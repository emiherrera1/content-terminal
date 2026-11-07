#!/usr/bin/env bash
# Publish both copies of the terminal after a run:
#   dev   this checkout's code + all data          -> dev.content-terminal.pages.dev   (owner password)
#   beta  the frozen `beta` branch's code + the     -> terminal.cvaccelerator.com       (owner or guest password)
#         shareable data (no unposted drafts)
# Both get the synthetic world next to the live data (the Live / Demo switch). To move the beta forward, point the
# `beta` branch at a newer commit (see site/README.md); nothing else changes.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f site/public/data/manifest.json ] || python -m terminal site
python -m terminal site --demo --out site/public/data-demo >/dev/null
site/deploy.sh dev site/public
if git fetch -q --depth 1 origin beta 2>/dev/null; then
  B="$(mktemp -d)"
  git archive FETCH_HEAD site | tar -x -C "$B"
  python -m terminal site --public --out "$B/site/public/data"
  cp -r site/public/data-demo "$B/site/public/"
  "$B/site/deploy.sh" main "$B/site/public"
else
  echo "::warning title=beta not published::there is no beta branch, so terminal.cvaccelerator.com was not updated"
fi
