#!/usr/bin/env bash
# Run the daemon against throwaway config/state with a 6 s interval and a fake Vesktop mic stream.
# Cards render in a Broadway display, so nothing appears on screen. Expect: held for the call, then a set.
set -u
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
export XDG_CONFIG_HOME=$tmp/config XDG_STATE_HOME=$tmp/state GDK_BACKEND=broadway BROADWAY_DISPLAY=:5
export PUSHUP_OVERLAY_ARGS="--close-after 2 --arm-delay 0"
mkdir -p "$XDG_CONFIG_HOME/pushup"
printf 'interval_min = 0.1\ninterval_max = 0.1\nsound = false\n' > "$XDG_CONFIG_HOME/pushup/config.toml"
touch "$XDG_CONFIG_HOME/pushup/onboarded"
pgrep -x gtk4-broadwayd >/dev/null || { gtk4-broadwayd :5 >/dev/null 2>&1 & }
timeout 16 pw-record -P '{ application.name = "vesktop" }' "$tmp/mic.wav" >/dev/null 2>&1 &
timeout 30 ./pushup daemon
echo "--- log"; cat "$XDG_STATE_HOME/pushup/log.jsonl"
rm -rf "$tmp"
