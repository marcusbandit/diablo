#!/usr/bin/env bash
# Ships hub/ and web/ from this checkout to the live hub on diablo-pi.
# Usage: deploy/ship-hub.sh [--dry-run]
# Keeps a dated copy of the live hub/ and web/ first (the newest 5 are kept),
# restarts diablo-hub and waits for /v1/health.
set -euo pipefail
LOCAL_ENV="${DIABLO_LOCAL_ENV:-$HOME/.config/diablo/local.env}"
[[ -f "$LOCAL_ENV" ]] && . "$LOCAL_ENV"
cd "$(dirname "$0")/.."

HOST=${DIABLO_PI:-diablo-pi}
DEST=diablo-hub
URL=${DIABLO_HUB_URL:-http://diablo-pi:8755}
DRY=()
[[ "${1:-}" == "--dry-run" ]] && DRY=(--dry-run)

if [[ -n "$(git status --porcelain -- hub web)" ]]; then
  echo "ship-hub: hub/ or web/ has uncommitted changes; commit first so the Pi runs a known commit." >&2
  exit 1
fi

rev=$(git rev-parse --short HEAD)
stamp=$(date +%Y%m%d-%H%M%S)
opts=(-a --delete --exclude __pycache__ --exclude '*.pyc')

if [[ ${#DRY[@]} -eq 0 ]]; then
  ssh "$HOST" "cp -a $DEST/hub $DEST/hub.bak-$stamp && cp -a $DEST/web $DEST/web.bak-$stamp \
    && ls -1d $DEST/hub.bak-2* | sort | head -n -5 | xargs -r rm -rf \
    && ls -1d $DEST/web.bak-2* | sort | head -n -5 | xargs -r rm -rf"
fi
rsync "${opts[@]}" "${DRY[@]}" -i hub/ "$HOST:$DEST/hub/"
rsync "${opts[@]}" "${DRY[@]}" -i --exclude fonts web/ "$HOST:$DEST/web/"
[[ ${#DRY[@]} -gt 0 ]] && exit 0

ssh "$HOST" "echo $rev > $DEST/hub/REVISION && sudo -n systemctl restart diablo-hub"
for _ in $(seq 1 30); do
  if curl -fsS -m 2 "$URL/v1/health" >/dev/null 2>&1; then
    echo "ship-hub: $rev live on $HOST (backups $DEST/hub.bak-$stamp and web.bak-$stamp)"
    exit 0
  fi
  sleep 1
done
echo "ship-hub: the hub did not answer after the restart; roll back with:" >&2
echo "  ssh $HOST 'rm -rf $DEST/hub $DEST/web && cp -a $DEST/hub.bak-$stamp $DEST/hub && cp -a $DEST/web.bak-$stamp $DEST/web && sudo -n systemctl restart diablo-hub'" >&2
exit 1
