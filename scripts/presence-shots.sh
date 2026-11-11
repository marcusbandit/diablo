#!/usr/bin/env bash
# Shoots the orb, its cards and the central card into captures/presence/ (gitignored).
#
# By default offscreen: presence/preview/render.py draws the real components with Qt, no
# compositor and no window, so it works with the screen locked and never lands on a workspace.
#
# --live shoots on the real compositor instead: a throwaway Quickshell instance of this
# checkout's presence/, its own state dir, a fixed cursor and no hub feed, so it never moves the
# real pointer or reads anything real. Needs an unlocked Wayland session with grim.
# Usage: scripts/presence-shots.sh [--live] [scene...]
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" != "--live" ]]; then
  exec python3 presence/preview/render.py captures/presence "$@"
fi
shift

out=captures/presence
mkdir -p "$out"
state=$(mktemp -d)
trap 'kill "${qs_pid:-}" ${ears_pid:-} 2>/dev/null || true; rm -rf "$state"' EXIT
mkdir -p "$state/diablo"

# The focused monitor, in Hyprland's global layout coordinates.
read -r mx my mw mh < <(hyprctl monitors -j | python3 -c 'import json,sys; m=[m for m in json.load(sys.stdin) if m["focused"]][0]; print(m["x"], m["y"], int(m["width"]/m["scale"]), int(m["height"]/m["scale"]))')

card() { # id title big line [video]
  printf '{"id":"%s","kind":"quick","title":"%s","big":"%s","lines":["%s"],"at":%s,"ttl":120}' "$1" "$2" "$3" "$4" "$(date +%s)"
}

# name | ears state | cursor x,y within the monitor | cards | panel.json | fake ears scene
SCENES=(
  "orb-listening|listening|900,500|"
  "orb-thinking|thinking|900,500|"
  "orb-speaking|speaking|900,500|"
  "card-below|asleep|900,400|$(card a Weather 7° 'cloudy, feels like 4°')"
  "card-above|asleep|900,$((mh - 90))|$(card a Weather 7° 'cloudy, feels like 4°')"
  "card-right-edge|asleep|$((mw - 40)),500|$(card a 'Next bus' '5 min' 'then 20 min')"
  "two-cards|speaking|900,400|$(card a Weather 7° 'cloudy, feels like 4°'),$(card b Diablo '' 'Here is the weather for today, sir.')"
  "panel-settings|asleep|900,400||{\"open\":true,\"view\":\"settings\"}|trained"
  "panel-training|listening|900,400||{\"open\":true,\"view\":\"settings\"}|speech"
  "panel-now|asleep|900,400||{\"open\":true,\"view\":\"now\",\"card\":{\"title\":\"Weather\",\"big\":\"7°\",\"lines\":[\"Cloudy, feels like 4°. Rain from 16:00.\",\"Tomorrow 4 to 8 degrees, light rain.\"]}}|trained"
)

want=("$@")
for scene in "${SCENES[@]}"; do
  IFS='|' read -r name mood at cards panel ears <<<"$scene"
  if (( ${#want[@]} )) && [[ ! " ${want[*]} " == *" $name "* ]]; then continue; fi
  printf '{"state":"%s"}' "$mood" > "$state/diablo/ears.json"
  printf '{"cards":[%s]}' "$cards" > "$state/diablo/presence.json"
  printf '%s' "${panel:-{\"open\":false\}}" > "$state/diablo/panel.json"
  if [[ -n "$ears" ]]; then
    python3 scripts/fakeears.py 18758 "$ears" >/dev/null 2>&1 &
    ears_pid=$!
  fi
  cx=$((mx + ${at%,*})); cy=$((my + ${at#*,}))
  XDG_STATE_HOME="$state" DIABLO_PRESENCE_TEST=1 DIABLO_EARS_URL=http://127.0.0.1:18758 DIABLO_PRESENCE_CURSOR="$cx,$cy" \
    qs -p "$PWD/presence" >"$state/qs.log" 2>&1 &
  qs_pid=$!
  sleep 2.2
  # A box around the cursor, kept on the monitor; the whole monitor for the central card.
  bx=$(( cx - 380 < mx ? mx : cx - 380 )); by=$(( cy - 300 < my ? my : cy - 300 ))
  bw=760; bh=600
  (( bx + bw > mx + mw )) && bx=$((mx + mw - bw))
  (( by + bh > my + mh )) && by=$((my + mh - bh))
  if [[ -n "$panel" ]]; then grim -g "$mx,$my ${mw}x$mh" -s 0.5 "$out/$name.png"; else grim -g "$bx,$by ${bw}x$bh" "$out/$name.png"; fi
  kill "$qs_pid" ${ears_pid:-} 2>/dev/null || true
  wait "$qs_pid" ${ears_pid:-} 2>/dev/null || true
  ears_pid=""
  if grep -qiE 'error|cannot|unavailable' "$state/qs.log"; then
    echo "$name: QML problems:" >&2
    grep -iE 'error|cannot|unavailable' "$state/qs.log" | head -5 >&2
  fi
  echo "$out/$name.png"
done
