#!/usr/bin/env bash
# Build, install and launch the app on a running emulator.
# Usage: scripts/run.sh [--no-build]
set -euo pipefail
cd "$(dirname "$0")/.."
PKG=com.marcusrosado.diablo
ACT=.MainActivity

if ! adb devices | sed -n '2,$p' | grep -q '[[:space:]]device$'; then
  echo "run.sh: no device attached. Start one with: scripts/emulator.sh" >&2
  exit 1
fi
until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
  sleep 2
done

# Pixels lock the screen on fold by default, which hides the app mid-test.
adb shell settings put secure fold_lock_behavior_setting stay_awake_on_fold_key >/dev/null 2>&1 || true

if [[ "${1:-}" != "--no-build" ]]; then
  scripts/build.sh :app:assembleDebug
fi
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -W -n "$PKG/$ACT"
