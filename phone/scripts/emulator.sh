#!/usr/bin/env bash
# Launch the template's foldable emulator, headless, inside a hard memory/CPU cap.
# Usage: scripts/emulator.sh [--window]   (AVD from $DIABLO_AVD, default diablo_phone)
set -euo pipefail
SDK="${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}"
export ANDROID_AVD_HOME="${ANDROID_AVD_HOME:-$HOME/.config/.android/avd}"
export ANDROID_SDK_HOME="${ANDROID_SDK_HOME:-$HOME/.config/.android}"
AVD="${DIABLO_AVD:-diablo_phone}"

if [ ! -d "$ANDROID_AVD_HOME/$AVD.avd" ]; then
  echo "emulator.sh: no AVD named $AVD. Create it with: scripts/create-avd.sh" >&2
  exit 1
fi

MIN_FREE_MB=8500
avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if (( avail < MIN_FREE_MB )); then
  echo "emulator.sh: only ${avail} MB available (< ${MIN_FREE_MB} MB). Not starting; close something first." >&2
  exit 1
fi
if pgrep -f "qemu-system-x86_64 -avd ${AVD}( |$)" >/dev/null; then
  echo "emulator.sh: AVD ${AVD} is already running." >&2
  exit 1
fi

# Headless by default so no window lands on a workspace someone is using.
extra=(-no-window)
gpu="swiftshader_indirect"
if [[ "${1:-}" == "--window" ]]; then
  extra=()
  gpu="host"
  shift
fi
gpu="${FOLDABLE_EMU_GPU:-$gpu}"

# Guest RAM is 1.5 GB. Software GPU on the 2076x2152 inner display needs 4.3 GB at boot and
# climbs past 5.8 GB over a session of folds and installs, so the cap is 8 GB. Swap is
# forbidden, so the guest dies before the desktop does.
mem="${FOLDABLE_EMU_MEM:-8000}"
exec systemd-run --user --scope -q \
  -p "MemoryMax=${mem}M" -p MemorySwapMax=0 -p CPUQuota=600% \
  "$SDK/emulator/emulator" -avd "$AVD" -memory 1536 -no-audio -no-boot-anim -no-snapshot \
    -gpu "$gpu" "${extra[@]}" "$@"
