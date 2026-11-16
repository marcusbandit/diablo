#!/bin/sh
# Installs the ears on banditbox: the wake word library into .venv-voice, the diablo-ears
# command with its completion, and diablo-ears.service started with the desktop session. Run it
# again after a pull. It never touches the old diablo-voice.service.
set -eu
repo="$(cd "$(dirname "$0")/.." && pwd)"
py="$repo/.venv-voice/bin/python"

# openwakeword pulls tflite-runtime by default, which has no wheel for 3.12; the onnx path
# needs only what the venv already has plus tqdm and requests.
uv pip install --python "$py" -q --no-deps "openwakeword==0.6.0"
uv pip install --python "$py" -q tqdm requests onnx pyrnnoise

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/zsh/site-functions" \
  "$HOME/.local/share/bash-completion/completions" "$HOME/.config/systemd/user"
ln -sf "$repo/deploy/diablo-ears" "$HOME/.local/bin/diablo-ears"
ln -sf "$repo/voice/completions/_diablo-ears" "$HOME/.local/share/zsh/site-functions/_diablo-ears"
ln -sf "$repo/voice/completions/diablo-ears.bash" "$HOME/.local/share/bash-completion/completions/diablo-ears"
ln -sf "$repo/deploy/diablo-act" "$HOME/.local/bin/diablo-act"
ln -sf "$repo/voice/completions/_diablo-act" "$HOME/.local/share/zsh/site-functions/_diablo-act"
ln -sf "$repo/voice/completions/diablo-act.bash" "$HOME/.local/share/bash-completion/completions/diablo-act"
cp "$repo/deploy/diablo-ears.service" "$HOME/.config/systemd/user/diablo-ears.service"
systemctl --user daemon-reload
# reenable, not enable: an older install hangs off default.target and would still start at boot,
# before the compositor, with no Wayland to paste into.
systemctl --user reenable diablo-ears.service
systemctl --user start diablo-ears.service
echo "ears installed. First time: diablo-ears train"
