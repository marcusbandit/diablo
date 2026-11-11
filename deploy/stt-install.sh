#!/bin/sh
# Installs diablo-stt on banditbox: faster-whisper in its own .venv-stt, a bearer token in
# ~/.config/diablo-stt/token, the cuBLAS and cuDNN path gvoice already uses, and
# diablo-stt.service started with the desktop session. Run it again after a pull.
# Afterwards the hub needs the token as its transcription.local.api_key setting.
set -eu
repo="$(cd "$(dirname "$0")/.." && pwd)"
conf="$HOME/.config/diablo-stt"

uv venv -q --allow-existing --python 3.12 "$repo/.venv-stt"
uv pip install --python "$repo/.venv-stt/bin/python" -q "faster-whisper==1.2.1" "av>=14,<16" fastapi uvicorn
model="$(grep '^DIABLO_STT_MODEL=' "$HOME/.config/diablo-stt/env" 2>/dev/null | cut -d= -f2-)"
"$repo/.venv-stt/bin/python" -c 'import sys; from faster_whisper import download_model; download_model(sys.argv[1])' "${model:-large-v3-turbo}" >/dev/null

mkdir -p "$conf" "$HOME/.config/systemd/user"
chmod 700 "$conf"
if [ ! -s "$conf/token" ]; then
  (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$conf/token")
fi
if [ -f "$HOME/.config/gvoice/env" ]; then
  libs="$(grep '^LD_LIBRARY_PATH=' "$HOME/.config/gvoice/env" || true)"
  if [ -n "$libs" ]; then
    touch "$conf/env"
    grep -v '^LD_LIBRARY_PATH=' "$conf/env" > "$conf/env.new" || true
    printf '%s\n' "$libs" >> "$conf/env.new"
    mv "$conf/env.new" "$conf/env"
  else
    echo "warning: no LD_LIBRARY_PATH in ~/.config/gvoice/env; diablo-stt may not find cuBLAS" >&2
  fi
fi

sed "s#%h/Projects/diablo#$repo#g" "$repo/deploy/diablo-stt.service" > "$HOME/.config/systemd/user/diablo-stt.service"
systemctl --user daemon-reload
systemctl --user enable diablo-stt.service
systemctl --user restart diablo-stt.service
echo "diablo-stt installed on port 8757. Token: $conf/token"
