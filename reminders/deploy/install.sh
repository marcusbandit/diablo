#!/usr/bin/env bash
# Install or update diablo-reminders on a host as a systemd user unit.
#   deploy/install.sh [host]        default host: diablo-pi
# Touches nothing of diablo-hub: code goes to ~/.local/lib/diablo-reminders,
# the CLI to ~/.local/bin/remind, the unit to ~/.config/systemd/user.
# Reminders already stored in ~/.local/state/diablo/reminders.db are kept.
set -euo pipefail

HOST="${1:-diablo-pi}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

tar -C "$ROOT" --exclude=__pycache__ -cf - diablo_reminders deploy completions README.md \
  | ssh "$HOST" 'set -e
      dest="$HOME/.local/lib/diablo-reminders"
      mkdir -p "$HOME/.local/lib"
      stage="$(mktemp -d "$HOME/.local/lib/.diablo-reminders.XXXXXX")"
      tar -C "$stage" -xf -
      rm -rf "$dest.old"
      if [ -d "$dest" ]; then mv "$dest" "$dest.old"; fi
      mv "$stage" "$dest"
      rm -rf "$dest.old"'

ssh "$HOST" 'set -e
  lib="$HOME/.local/lib/diablo-reminders"
  python3 -c "import sys; assert sys.version_info >= (3, 11), sys.version"
  mkdir -p "$HOME/.local/bin" "$HOME/.local/share/zsh/site-functions" \
           "$HOME/.local/share/bash-completion/completions" "$HOME/.config/systemd/user"
  printf "%s\n" "#!/bin/sh" "PYTHONPATH=\"\$HOME/.local/lib/diablo-reminders\" exec /usr/bin/python3 -m diablo_reminders \"\$@\"" > "$HOME/.local/bin/remind"
  chmod 755 "$HOME/.local/bin/remind"
  install -m 644 "$lib/completions/_remind" "$HOME/.local/share/zsh/site-functions/_remind"
  install -m 644 "$lib/completions/remind.bash" "$HOME/.local/share/bash-completion/completions/remind"
  install -m 644 "$lib/deploy/diablo-reminders.service" "$HOME/.config/systemd/user/diablo-reminders.service"
  systemctl --user daemon-reload
  systemctl --user enable diablo-reminders.service >/dev/null 2>&1
  systemctl --user restart diablo-reminders.service
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    systemctl --user is-active --quiet diablo-reminders.service && break
    sleep 0.5
  done
  echo "service: $(systemctl --user is-active diablo-reminders.service)"
  "$HOME/.local/bin/remind" list'
