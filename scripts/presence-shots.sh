#!/usr/bin/env bash
# Shoots the orb and its cards against fake state on the real compositor: a throwaway
# Quickshell instance of this checkout's presence/, its own state dir, a fixed cursor and no
# hub feed, so it never moves the real pointer or reads anything real. PNGs land in
# captures/presence/ (gitignored). Needs a Wayland session with grim.
# Usage: scripts/presence-shots.sh [scene...]     scenes: see SCENES below; default all
set -euo pipefail
cd "$(dirname "$0")/.."

out=captures/presence
mkdir -p "$out"
state=$(mktemp -d)
trap 'kill "${qs_pid:-}" 2>/dev/null || true; rm -rf "$state"' EXIT
mkdir -p "$state/diablo"

# The focused monitor, in Hyprland's global layout coordinates.
read -r mx my mw mh < <(hyprctl monitors -j | python3 -c 'import json,sys; m=[m for m in json.load(sys.stdin) if m["focused"]][0]; print(m["x"], m["y"], int(m["width"]/m["scale"]), int(m["height"]/m["scale"]))')

card() { # id title big line [video]
  printf '{"id":"%s","kind":"quick","title":"%s","big":"%s","lines":["%s"],"at":%s,"ttl":120}' "$1" "$2" "$3" "$4" "$(date +%s)"
}

# name | ears state | cursor x,y within the monitor | cards
SCENES=(
  "orb-listening|listening|900,500|"
  "orb-thinking|thinking|900,500|"
  "orb-speaking|speaking|900,500|"
  "card-below|asleep|900,400|$(card a Weather 7° 'cloudy, feels like 4°')"
  "card-above|asleep|900,$((mh - 90))|$(card a Weather 7° 'cloudy, feels like 4°')"
  "card-right-edge|asleep|$((mw - 40)),500|$(card a 'Next bus' '5 min' 'then 20 min')"
  "two-cards|speaking|900,400|$(card a Weather 7° 'cloudy, feels like 4°'),$(card b Diablo '' 'Here is the weather for today, sir.')"
)

want=("$@")
for scene in "${SCENES[@]}"; do
  IFS='|' read -r name mood at cards <<<"$scene"
  if (( ${#want[@]} )) && [[ ! " ${want[*]} " == *" $name "* ]]; then continue; fi
  printf '{"state":"%s"}' "$mood" > "$state/diablo/ears.json"
  printf '{"cards":[%s]}' "$cards" > "$state/diablo/presence.json"
  cx=$((mx + ${at%,*})); cy=$((my + ${at#*,}))
  XDG_STATE_HOME="$state" DIABLO_PRESENCE_TEST=1 DIABLO_PRESENCE_CURSOR="$cx,$cy" \
    qs -p "$PWD/presence" >"$state/qs.log" 2>&1 &
  qs_pid=$!
  sleep 2.2
  # A box around the cursor, kept on the monitor.
  bx=$(( cx - 380 < mx ? mx : cx - 380 )); by=$(( cy - 300 < my ? my : cy - 300 ))
  bw=760; bh=600
  (( bx + bw > mx + mw )) && bx=$((mx + mw - bw))
  (( by + bh > my + mh )) && by=$((my + mh - bh))
  grim -g "$bx,$by ${bw}x$bh" "$out/$name.png"
  kill "$qs_pid" 2>/dev/null || true
  wait "$qs_pid" 2>/dev/null || true
  if grep -qiE 'error|cannot|unavailable' "$state/qs.log"; then
    echo "$name: QML problems:" >&2
    grep -iE 'error|cannot|unavailable' "$state/qs.log" | head -5 >&2
  fi
  echo "$out/$name.png"
done
