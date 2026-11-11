#!/bin/bash
# The stagehand: keeps the whole diablo stage standing.
# Every ten seconds: the services running, ydotoold alive, the tmux stage
# built. Anything down comes back up; the systemd units restart the loops
# themselves, this catches everything else.

STATE="$HOME/.local/state/diablo"
mkdir -p "$STATE"
exec > >(tee -a "$STATE/stagehand-color.log") 2>&1

UNITS=(diablo-fast.service diablo-pool.service diablo-voice.service)

ensure_unit() {
  # A disabled unit was switched off on purpose; leave it down.
  systemctl --user is-enabled --quiet "$1" || return 0
  if ! systemctl --user is-active --quiet "$1"; then
    echo "$(date +%H:%M:%S) $1 is down, raising it"
    systemctl --user start "$1" 2>&1 | sed 's/^/  /'
  fi
}

while true; do
  for unit in "${UNITS[@]}"; do
    ensure_unit "$unit"
  done
  ensure_unit ydotoold.service
  "$HOME/Projects/diablo/deploy/tmux-start.sh" 2>/dev/null || true
  sleep 10
done
