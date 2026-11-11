#!/usr/bin/env bash
# The challenge against throwaway config/state, sets seconds apart. Cards render in a Broadway display,
# so nothing appears on screen. Expect: the day begins, the challenge page, then a push-up card.
set -u
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
export XDG_CONFIG_HOME=$tmp/config XDG_STATE_HOME=$tmp/state GDK_BACKEND=broadway BROADWAY_DISPLAY=:5
export PUSHUP_OVERLAY_ARGS="--close-after 2 --arm-delay 0" PUSHUP_TEST_GAP=6
mkdir -p "$XDG_CONFIG_HOME/pushup"
printf 'sound = false\nmode = "challenge"\nchallenge_start = "%s"\n' "$(date -d '-3 hours' +%F)" > "$XDG_CONFIG_HOME/pushup/config.toml"
touch "$XDG_CONFIG_HOME/pushup/onboarded" "$XDG_CONFIG_HOME/pushup/seen-moves-2"
pgrep -x gtk4-broadwayd >/dev/null || { gtk4-broadwayd :5 >/dev/null 2>&1 & }
timeout 25 ./pushup daemon
echo "--- log"; cat "$XDG_STATE_HOME/pushup/log.jsonl"
echo "--- seen"; ls "$XDG_CONFIG_HOME/pushup"
rm -rf "$tmp"
