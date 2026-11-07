#!/usr/bin/env bash
# Publish both copies of the terminal after a run:
#   dev   this checkout's code + all data          -> dev.content-terminal.pages.dev   (owner password)
#   beta  the newest v*-beta release's code + the     -> terminal.cvaccelerator.com       (owner or guest password)
#         shareable data (no unposted drafts)
# Both get the synthetic world next to the live data (the Live / Demo switch). To move the beta forward, publish a
# newer release tagged v0.6-beta, v0.7-beta... on GitHub (see site/README.md); nothing else changes.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f site/public/data/manifest.json ] || python -m terminal site
python -m terminal site --demo --out site/public/data-demo >/dev/null
site/deploy.sh dev site/public
# the beta is the newest release tagged v*-beta (GitHub > Releases); a branch named beta also works
REF=""
TAG="$(git ls-remote --tags --refs origin 'v*-beta' | awk '{print $2}' | sed 's#refs/tags/##' | sort -V | tail -1)"
if [ -n "$TAG" ] && git fetch -q --depth 1 origin "refs/tags/$TAG:refs/tags/$TAG"; then REF="$TAG"
elif git fetch -q --depth 1 origin beta 2>/dev/null; then REF="FETCH_HEAD"; fi
if [ -n "$REF" ]; then
  echo "beta: publishing $REF to terminal.cvaccelerator.com"
  B="$(mktemp -d)"
  git archive "$REF" site | tar -x -C "$B"
  python -m terminal site --public --out "$B/site/public/data"
  cp -r site/public/data-demo "$B/site/public/"
  "$B/site/deploy.sh" main "$B/site/public"
else
  echo "::warning title=beta not published::no v*-beta release (or beta branch) yet, so terminal.cvaccelerator.com was not updated"
fi
