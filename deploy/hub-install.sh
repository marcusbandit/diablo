#!/usr/bin/env bash
set -euo pipefail
LOCAL_ENV="${DIABLO_LOCAL_ENV:-$HOME/.config/diablo/local.env}"
[[ -f "$LOCAL_ENV" ]] && . "$LOCAL_ENV"

REMOTE=diablo-pi
ROOT_VIA=homehub
HOST=${DIABLO_HUB_HOST:-diablo-pi}
PORT=8755
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ssh "$REMOTE" "mkdir -p diablo-hub"
rsync -a --delete "$ROOT/hub/" "$REMOTE:diablo-hub/hub/"
rsync -a "$ROOT/hub/requirements.txt" "$REMOTE:diablo-hub/requirements.txt"
rsync -a "$ROOT/deploy/" "$REMOTE:diablo-hub/deploy/"

ssh "$REMOTE" 'test -x diablo-hub/venv/bin/python || python3 -m venv diablo-hub/venv'
ssh "$REMOTE" 'diablo-hub/venv/bin/pip install -q -r diablo-hub/requirements.txt'

ssh "$REMOTE" 'test -f ~/.config/diablo/local.env || { echo "run scripts/setup-local.sh on this box first" >&2; exit 1; }'
ssh "$REMOTE" 'sed -e "s#@USER@#$(id -un)#g" -e "s#@HOME@#$HOME#g" diablo-hub/deploy/diablo-hub.service > diablo-hub/deploy/diablo-hub.service.rendered'
ssh "$ROOT_VIA" "ssh root@$REMOTE 'install -m 644 ~$(ssh "$REMOTE" id -un)/diablo-hub/deploy/diablo-hub.service.rendered /etc/systemd/system/diablo-hub.service'"
ssh "$REMOTE" 'sudo /usr/bin/systemctl daemon-reload && sudo /usr/bin/systemctl enable diablo-hub && sudo /usr/bin/systemctl restart diablo-hub'

if [[ -n "${HUB_TOKEN:-}" ]] && ! ssh "$REMOTE" "test -f diablo-hub/config.toml"; then
  tmpconf="$(mktemp)"
  cat > "$tmpconf" <<EOF
hub_token = "$HUB_TOKEN"
telegram_bot_token = "CHANGE_ME"
fireworks_api_key = "CHANGE_ME"
model = "accounts/fireworks/models/glm-5p3-flash"

[transcription]
base_url = "https://api.groq.com/openai/v1"
api_key = "CHANGE_ME"
model = "whisper-large-v3-turbo"

[tts]
provider = "none"
base_url = "https://api.openai.com/v1"
api_key = "CHANGE_ME"
voice = "onyx"
EOF
  scp -q "$tmpconf" "$REMOTE:/tmp/diablo-hub-config.toml"
  rm -f "$tmpconf"
  ssh "$REMOTE" "mv /tmp/diablo-hub-config.toml diablo-hub/config.toml && chmod 600 diablo-hub/config.toml"
fi

for _ in 1 2 3 4 5; do
  health="$(curl -s --max-time 3 -H "X-Diablo-Token: ${HUB_TOKEN:-}" "http://$HOST:$PORT/v1/health" || true)"
  [[ "$health" == *ok* ]] && break
  sleep 2
done
echo "health: ${health:-no response}"
echo "Diablo hub deployed: code synced, venv ready, unit enabled for boot, process running"
