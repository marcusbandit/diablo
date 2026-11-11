#!/usr/bin/env bash
# Ships hub/ and web/ from this checkout to the standby hub on nemurenai.
# Usage: deploy/ship-standby.sh [--dry-run]
# First run also makes the venv, copies the Pi's config.toml with a [standby]
# table added, and installs diablo-standby.service as a user unit.
set -euo pipefail
LOCAL_ENV="${DIABLO_LOCAL_ENV:-$HOME/.config/diablo/local.env}"
[[ -f "$LOCAL_ENV" ]] && . "$LOCAL_ENV"
cd "$(dirname "$0")/.."

HOST=${DIABLO_STANDBY:-nemurenai}
PI=${DIABLO_PI:-diablo-pi}
PRIMARY=${DIABLO_HUB_URL:-http://diablo-pi:8755}
URL=${DIABLO_STANDBY_URL:-http://nemurenai:8755}
DEST=diablo-hub
DRY=()
[[ "${1:-}" == "--dry-run" ]] && DRY=(--dry-run)

if [[ -n "$(git status --porcelain -- hub web deploy/diablo-standby.service)" ]]; then
  echo "ship-standby: hub/ or web/ has uncommitted changes; commit first so the standby runs a known commit." >&2
  exit 1
fi

rev=$(git rev-parse --short HEAD)
opts=(-a --delete --exclude __pycache__ --exclude '*.pyc')

ssh "$HOST" "mkdir -p $DEST/hub $DEST/web .config/systemd/user"
rsync "${opts[@]}" "${DRY[@]}" -i hub/ "$HOST:$DEST/hub/"
rsync "${opts[@]}" "${DRY[@]}" -i --exclude fonts web/ "$HOST:$DEST/web/"
[[ ${#DRY[@]} -gt 0 ]] && exit 0

if ! ssh "$HOST" "test -f $DEST/config.toml"; then
  ssh "$PI" "cat $DEST/config.toml" | ssh "$HOST" "umask 077 && cat > $DEST/config.toml.new \
    && printf '\n[standby]\nprimary = \"%s\"\n' '$PRIMARY' >> $DEST/config.toml.new \
    && mv $DEST/config.toml.new $DEST/config.toml"
  echo "ship-standby: copied the Pi's config to $HOST with [standby] primary = $PRIMARY"
fi

ssh "$HOST" "(test -x $DEST/venv/bin/python || uv venv -q --python 3.12 $DEST/venv) \
  && uv pip install -q --python $DEST/venv/bin/python -r $DEST/hub/requirements.txt"

scp -q deploy/diablo-standby.service "$HOST:.config/systemd/user/diablo-standby.service"
ssh "$HOST" "echo $rev > $DEST/hub/REVISION && systemctl --user daemon-reload \
  && systemctl --user enable -q diablo-standby && systemctl --user restart diablo-standby"
if ! ssh "$HOST" 'loginctl show-user "$USER" -p Linger' | grep -q Linger=yes; then
  echo "ship-standby: linger is off on $HOST, so the standby stops at logout; turn it on with: ssh $HOST loginctl enable-linger" >&2
fi
for _ in $(seq 1 30); do
  if state=$(curl -fsS -m 2 "$URL/v1/standby/status" 2>/dev/null); then
    echo "ship-standby: $rev live on $HOST: $state"
    exit 0
  fi
  sleep 1
done
echo "ship-standby: the standby did not answer; look at: ssh $HOST journalctl --user -u diablo-standby -n 50" >&2
exit 1
