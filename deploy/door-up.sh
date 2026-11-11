#!/usr/bin/env bash
# Sets up Diablo's tailnet door on the hub machine: installs the user unit, joins the
# `diablo` node to the tailnet (prints a login link the first time) and points its
# Tailscale Serve at the hub on ports 80 and 443. Safe to run again.
set -euo pipefail
LOCAL_ENV="${DIABLO_LOCAL_ENV:-$HOME/.config/diablo/local.env}"
[[ -f "$LOCAL_ENV" ]] && . "$LOCAL_ENV"
HUB_URL="${1:-${DIABLO_HUB_URL:-http://diablo-pi:8755}}"
STATE="$HOME/.local/state/diablo-door"
SOCK="$STATE/tailscaled.sock"
ts() { tailscale --socket="$SOCK" "$@"; }

# a user unit only outlives logouts and reboots with linger on
if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
  loginctl enable-linger "$USER" || { echo "door-up: enable linger for $USER first: sudo loginctl enable-linger $USER" >&2; exit 1; }
fi
mkdir -p "$STATE" "$HOME/.config/systemd/user"
install -m644 "$(dirname "$0")/diablo-door.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now diablo-door.service
for _ in $(seq 50); do [[ -S "$SOCK" ]] && break; sleep 0.2; done

if ! ts status >/dev/null 2>&1; then
  echo "door-up: approve the new 'diablo' machine at the link below" >&2
  ts up --hostname=diablo --timeout=10m
fi
ts serve --bg --http=80 "$HUB_URL"
ts serve --bg --https=443 "$HUB_URL"
# Serve reaches a hub on this same machine over the host network, so the hub sees the
# machine's own tailnet address, not the door node's. Only local programs can use it.
echo "door-up: put $(tailscale ip -4) in [door] proxies in config.toml, then restart diablo-hub (the hub reads its config once)"
ts serve status
