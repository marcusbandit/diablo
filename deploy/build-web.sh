#!/usr/bin/env bash
# Builds the web app (web/, React) into web/dist for ship-hub.sh and ship-standby.sh.
# Usage: deploy/build-web.sh <caller name>
# Refuses a checkout with uncommitted changes in anything the hub or the build reads, so a box
# always runs a known commit.
set -euo pipefail
cd "$(dirname "$0")/.."
who="${1:-build-web}"

BUILT_FROM=(hub web packages design package.json bun.lock)
if [[ -n "$(git status --porcelain -- "${BUILT_FROM[@]}")" ]]; then
  echo "$who: ${BUILT_FROM[*]} has uncommitted changes; commit first so the box runs a known commit." >&2
  exit 1
fi
if ! command -v bun >/dev/null; then
  echo "$who: bun is needed to build the web app (https://bun.sh)." >&2
  exit 1
fi
# Only what the web build needs, not the phone app's dependencies.
bun install --frozen-lockfile --filter web --filter @diablo/hub
bun run --filter web build
[[ -f web/dist/index.html ]] || { echo "$who: the web build left no web/dist/index.html" >&2; exit 1; }
