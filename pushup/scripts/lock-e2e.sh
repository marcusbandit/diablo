#!/usr/bin/env bash
# Lock the screen (a fake one, PUSHUP_FAKE_LOCK) while a card is up, then unlock. Cards render in a
# Broadway display. Passes when the card is up, closed by the lock with nothing logged, held while
# locked, and back after the unlock.
set -u
cd "$(dirname "$0")/.."
tmp=$(mktemp -d)
export XDG_CONFIG_HOME=$tmp/config XDG_STATE_HOME=$tmp/state GDK_BACKEND=broadway BROADWAY_DISPLAY=:5
export PUSHUP_OVERLAY_ARGS="--arm-delay 0" PUSHUP_FAKE_LOCK=$tmp/locked
mkdir -p "$XDG_CONFIG_HOME/pushup"
printf 'interval_min = 0.1\ninterval_max = 0.1\nsound = false\n' > "$XDG_CONFIG_HOME/pushup/config.toml"
touch "$XDG_CONFIG_HOME/pushup/onboarded"
pgrep -x gtk4-broadwayd >/dev/null || { gtk4-broadwayd :5 >/dev/null 2>&1 & }

./pushup daemon > "$tmp/out" 2>&1 &
daemon=$!
# Only this daemon's own card counts, never one the real pushup.service has up.
card() { pgrep -P "$daemon" -f "overlay --reps" >/dev/null && echo up || echo none; }
fail=0
expect() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: card $3"; fail=1; fi; }

sleep 13; expect "card shows when due" up "$(card)"
touch "$tmp/locked"; sleep 5; expect "lock closes the card" none "$(card)"
sleep 15; expect "no card while locked" none "$(card)"
rm "$tmp/locked"; sleep 12; expect "card back after unlock" up "$(card)"

pkill -P "$daemon"; kill "$daemon"; wait "$daemon" 2>/dev/null
grep -q "locked with the card up" "$tmp/out" && grep -q "unlocked, next set" "$tmp/out" || { echo "FAIL  daemon log"; fail=1; }
[ -s "$XDG_STATE_HOME/pushup/log.jsonl" ] && { echo "FAIL  something was logged:"; cat "$XDG_STATE_HOME/pushup/log.jsonl"; fail=1; }
echo "--- daemon"; cat "$tmp/out"
rm -rf "$tmp"
exit $fail
