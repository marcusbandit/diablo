#!/usr/bin/env bash
# Show a real card on the focused monitor for a few seconds, no keyboard grab, and grab the screen once it has painted.
set -u
cd "$(dirname "$0")/.."
mkdir -p captures
./pushup overlay --reps "${1:-12}" --no-grab --close-after 5 --arm-delay 0 --paused "${2:-}" >/dev/null 2>captures/live.log &
for _ in $(seq 30); do hyprctl layers | grep -q "namespace: pushup" && break; sleep 0.1; done
sleep 1.2
mon=$(hyprctl monitors -j | python3 -c "import json,sys;print(next(m['name'] for m in json.load(sys.stdin) if m['focused']))")
grim -o "$mon" -s 0.3 captures/live.png
wait
file captures/live.png
