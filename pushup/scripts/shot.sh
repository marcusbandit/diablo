#!/usr/bin/env bash
# Render a card in a Broadway display and screenshot it headless, so nothing lands on the real desktop.
# usage: scripts/shot.sh NAME [overlay args...]
set -u
cd "$(dirname "$0")/.."
mkdir -p captures
name=$1; shift
export SHOT_SIZE=${SHOT_SIZE:-1600,1000} PUSHUP_STILL=${PUSHUP_STILL:-0.45}
pgrep -x gtk4-broadwayd >/dev/null || { gtk4-broadwayd :5 >/tmp/pushup-broadway.log 2>&1 & sleep 0.5; }
env -u WAYLAND_DISPLAY GDK_BACKEND=broadway BROADWAY_DISPLAY=:5 ./pushup overlay --close-after 15 --arm-delay 0 "$@" >/dev/null 2>"captures/$name.log" &
ui=$!
sleep ${SHOT_WAIT:-1.5}
timeout 25 google-chrome-stable --headless=new --disable-gpu --hide-scrollbars --window-size=${SHOT_SIZE:-1600,1000} \
    --virtual-time-budget=4000 --screenshot="captures/$name.png" http://127.0.0.1:8085/ >/dev/null 2>&1
kill "$ui" 2>/dev/null
file "captures/$name.png"
