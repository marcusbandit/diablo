#!/usr/bin/env bash
# Screenshot the device framebuffer into captures/ (guest pixels, no compositor involved).
# Usage: scripts/shot.sh [name] [--size]
# A foldable has two physical displays and only the current posture's one is powered.
# A bare screencap prints a "Multiple displays" warning into the PNG and falls back to
# display 0, which is dark when folded, so ask for the active viewport's display explicitly.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p captures

name="shot"
size=0
for arg in "$@"; do
  case "$arg" in
    --size) size=1 ;;
    *) name="$arg" ;;
  esac
done
out="captures/${name}.png"

disp="$(adb shell dumpsys display \
  | grep -oE 'DisplayViewport\{[^}]*isActive=true[^}]*\}' \
  | sed -n "s/.*uniqueId='local:\([0-9]*\)'.*/\1/p" | head -1)"

if [ -n "$disp" ]; then
  adb exec-out screencap -d "$disp" -p > "$out"
else
  adb exec-out screencap -p > "$out"
fi
if ! file "$out" | grep -q "PNG image"; then
  echo "shot.sh: $out is not a PNG; the screen is probably not up yet." >&2
  exit 1
fi
echo "$out"
if [ "$size" = 1 ]; then
  adb shell wm size | tr -d '\r'
fi
