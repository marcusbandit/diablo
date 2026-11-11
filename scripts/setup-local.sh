#!/usr/bin/env bash
# Writes this box's ~/.config/diablo/local.env: the addresses, accounts and places that never
# go in the repo. It finds what the box can tell it and asks for the rest; Enter skips a
# question. Keys already in the file are kept. Run it again any time to fill in what is missing.
# Usage: scripts/setup-local.sh
set -euo pipefail

dir="${XDG_CONFIG_HOME:-$HOME/.config}/diablo"
file="${DIABLO_LOCAL_ENV:-$dir/local.env}"
mkdir -p "$(dirname "$file")"
touch "$file"
chmod 600 "$file"

have() { grep -q "^$1=" "$file"; }
put() {
  local key=$1 value=$2
  [[ -z "$value" ]] && return 0
  if have "$key"; then
    echo "  kept $key (already set)"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
    echo "  set  $key=$value"
  fi
}
ask() {
  local key=$1 prompt=$2 guess=${3:-} answer
  have "$key" && { echo "  kept $key (already set)"; return 0; }
  if [[ -n "$guess" ]]; then
    read -r -p "$prompt [$guess]: " answer || true
    answer=${answer:-$guess}
  else
    read -r -p "$prompt (Enter skips): " answer || true
  fi
  put "$key" "$answer"
}

echo "Discovering what this box knows..."
ip4=""; tailnet=""; subnet=""
if command -v tailscale >/dev/null 2>&1; then
  ip4=$(tailscale ip -4 2>/dev/null | head -n1 || true)
  tailnet=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("MagicDNSSuffix") or (d.get("CurrentTailnet") or {}).get("MagicDNSSuffix") or "").strip("."))' 2>/dev/null || true)
fi
dev=$(ip -4 route show default 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}')
[[ -n "$dev" ]] && subnet=$(ip -4 route show scope link dev "$dev" 2>/dev/null | awk '$1 ~ /^(10|172|192)\./ {print $1; exit}')
echo "  tailscale ip: ${ip4:-none}  tailnet: ${tailnet:-none}  lan: ${subnet:-none}"
echo

echo "Hubs"
ask DIABLO_HUB_URL "Primary hub URL" "http://diablo-pi:8755"
ask DIABLO_STANDBY_URL "Standby hub URL" "http://nemurenai:8755"
ask DIABLO_HUB_BIND "Address a hub on this box binds to" "$ip4"
put DIABLO_TAILNET "$tailnet"
put DIABLO_HOME_SUBNET "$subnet"
echo
echo "Optional"
ask DIABLO_WOL_MAC "MAC address of the desktop box, for wake-on-LAN"
ask DIABLO_MAIL_ACCOUNTS "Mail accounts as name=address pairs, comma separated"
ask DIABLO_MAIL_AGENT "The one agent address allowed to get mail replies"
cleartext=""
for v in "$(grep -E '^DIABLO_(HUB|STANDBY)_URL=' "$file" | sed -E 's#^[^=]*=https?://([^:/]+).*#\1#' | paste -sd, -)" "$tailnet"; do
  [[ -n "$v" ]] && cleartext=${cleartext:+$cleartext,}$v
done
ask DIABLO_CLEARTEXT_HOSTS "Hosts the phone may use plain http with" "$cleartext"

echo
echo "Wrote $file"
voice="$dir/config.toml"
if ! grep -qs '^home_lat' "$voice"; then
  cat <<EOF

The voice side keeps its places in $voice (TOML). Add any of these to get quick
weather and bus answers and the pizza shortcut:
  home_name = "Town"     home_lat = 55.0     home_lon = 12.0
  bus_stop_id = "..."    bus_stop_name = "..."    bus_line = "..."    bus_toward = "..."
  pizza_url = "https://..."
EOF
fi
