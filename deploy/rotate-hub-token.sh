#!/usr/bin/env bash
# Replaces hub_token with a new long random value on the hub, on the standby hub and on
# every box that holds a copy in ~/.config/diablo/config.toml, then restarts what reads it.
# The token is never printed and never passed on a command line.
# Usage: deploy/rotate-hub-token.sh [extra-box ...]
#   extra boxes get their ~/.config/diablo/config.toml updated over ssh too.
# Paired phones and browsers keep working: they hold their own device tokens.
set -euo pipefail
LOCAL_ENV="${DIABLO_LOCAL_ENV:-$HOME/.config/diablo/local.env}"
[[ -f "$LOCAL_ENV" ]] && . "$LOCAL_ENV"
cd "$(dirname "$0")/.."

HOST=${DIABLO_PI:-diablo-pi}
STANDBY=${DIABLO_STANDBY:-nemurenai}
URL=${DIABLO_HUB_URL:-http://diablo-pi:8755}
stamp=$(date +%Y%m%d-%H%M%S)

read -r -d '' SET_TOKEN <<'PY' || true
import os, re, sys, tomllib
path = os.path.expanduser(sys.argv[1])
token = sys.stdin.readline().strip()
text = open(path).read() if os.path.exists(path) else ""
line = f'hub_token = "{token}"'
text, n = re.subn(r'(?m)^hub_token\s*=.*$', lambda _: line, text)
if not n:
    text = line + "\n" + text
tmp = path + ".tmp"
with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
    handle.write(text)
assert tomllib.loads(text)["hub_token"] == token
os.replace(tmp, path)
PY

token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')

backup() {
  local target=$1 path=$2
  if [[ $target == local ]]; then
    [[ -f $path ]] && install -m 600 "$path" "$path.bak-$stamp"
  else
    ssh "$target" "test -f $path && install -m 600 $path $path.bak-$stamp || true"
  fi
}

put() {
  local target=$1 path=$2
  backup "$target" "$path"
  if [[ $target == local ]]; then
    python3 -c "$SET_TOKEN" "$path" <<<"$token"
  else
    ssh "$target" "python3 -c $(printf %q "$SET_TOKEN") $path" <<<"$token"
  fi
  echo "rotate: $target $path updated (backup .bak-$stamp)"
}

put "$HOST" diablo-hub/config.toml
ssh "$HOST" "sudo -n systemctl restart diablo-hub"
for _ in $(seq 1 30); do
  curl -fsS -m 2 "$URL/v1/health" >/dev/null 2>&1 && break
  sleep 1
done

check() {
  curl -fsS -m 5 -o /dev/null -H @- "$URL/v1/whoami" <<<"X-Diablo-Token: $token"
}
if ! check; then
  echo "rotate: the hub does not accept the new token; restore with" >&2
  echo "  ssh $HOST 'cp diablo-hub/config.toml.bak-$stamp diablo-hub/config.toml && sudo -n systemctl restart diablo-hub'" >&2
  exit 1
fi
echo "rotate: the hub accepts the new token"

if ssh -o ConnectTimeout=5 "$STANDBY" "test -f diablo-hub/config.toml"; then
  put "$STANDBY" diablo-hub/config.toml
  ssh "$STANDBY" "systemctl --user restart diablo-standby"
  echo "rotate: restarted diablo-standby on $STANDBY"
else
  echo "rotate: no standby hub reached on $STANDBY; set its hub_token by rerunning this script when it is up" >&2
fi

put local "$HOME/.config/diablo/config.toml"
for box in "$@"; do
  put "$box" .config/diablo/config.toml
done

for unit in diablo-voice diablo-fast diablo-pool diablo-remind; do
  if systemctl --user is-active -q "$unit"; then
    systemctl --user restart "$unit"
    echo "rotate: restarted $unit"
  fi
done
echo "rotate: done. Browsers that used the old token show a pairing form; send Diablo /pair for a code."
