#!/usr/bin/env bash
# Screenshot one walkthrough page without touching the real desktop. The app runs on a headless
# Broadway display and, with ROOM_SHOT_PNG set, renders its own widgets to the PNG once the
# bridge has answered, then quits. Fresh state: a temporary config and state dir.
# usage: scripts/shot.sh NAME PAGE [env assignments...]   e.g. scripts/shot.sh welcome 1 ROOM_SHOT_SCENE=cinema
set -u
cd "$(dirname "$0")/.."
mkdir -p captures
name=$1 page=$2; shift 2
fresh=$(mktemp -d)
out="$PWD/captures/$name.png"
rm -f "$out"
# Port 8086 answers when Broadway display :6 is up (a pgrep -f would match its own caller).
(exec 3<>/dev/tcp/127.0.0.1/8086) 2>/dev/null || { setsid nohup gtk4-broadwayd :6 >/tmp/room-broadway.log 2>&1 < /dev/null & sleep 0.8; }
# Broadway only runs GTK's frame clock while a browser is connected; without one, nothing after
# the first frame gets laid out. Keep a headless Chrome on the display while the app runs.
google-chrome-stable --headless=new --disable-gpu --user-data-dir="$fresh/chrome" --window-size=1200,900 \
    --remote-debugging-port=0 http://127.0.0.1:8086/ >/dev/null 2>&1 &
viewer=$!
env -u WAYLAND_DISPLAY GDK_BACKEND=broadway BROADWAY_DISPLAY=:6 ROOM_STILL=1 ROOM_SHOT_PNG="$out" \
    ROOM_CONFIG_DIR="$fresh/config" ROOM_STATE_DIR="$fresh/state" "$@" \
    timeout 120 python3 bin/room setup --page "$page" >"captures/$name.log" 2>&1
kill "$viewer" 2>/dev/null
wait "$viewer" 2>/dev/null
rm -rf "$fresh" 2>/dev/null  # chrome can still be closing its profile; /tmp is cleaned anyway
file "$out"
