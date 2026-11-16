#!/usr/bin/env bash
# Install and launch the app on the attached device or emulator.
# Usage: scripts/run.sh [debug|release] [--build]
#   debug (default) reads JavaScript from Metro on this box through adb reverse;
#   start it first with: bunx expo start (in phone/)
set -euo pipefail
cd "$(dirname "$0")/.."
PKG=com.marcusrosado.diablo
variant=debug
build=0
for arg in "$@"; do
  case "$arg" in
    debug|release) variant="$arg" ;;
    --build) build=1 ;;
    *) echo "run.sh: unknown argument $arg" >&2; exit 1 ;;
  esac
done

if ! adb devices | sed -n '2,$p' | grep -q '[[:space:]]device$'; then
  echo "run.sh: no device attached. Start one with: scripts/emulator.sh" >&2
  exit 1
fi
if [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ]; then
  echo "run.sh: the device is still booting; try again in a moment." >&2
  exit 1
fi

# Pixels lock the screen on fold by default, which hides the app mid-test.
adb shell settings put secure fold_lock_behavior_setting stay_awake_on_fold_key >/dev/null 2>&1 || true

[ "$build" = 1 ] && scripts/build.sh "$variant"
apk="android/app/build/outputs/apk/$variant/app-$variant.apk"
[ -f "$apk" ] || { echo "run.sh: no $apk yet. Build it with: scripts/build.sh $variant" >&2; exit 1; }

if [ "$variant" = debug ]; then
  adb reverse tcp:8081 tcp:8081 >/dev/null
  curl -sf -m 2 http://127.0.0.1:8081/status >/dev/null || echo "run.sh: Metro is not answering on 8081; start it with: bunx expo start" >&2
fi
adb install -r "$apk"
adb shell am start -W -n "$PKG/.MainActivity"
