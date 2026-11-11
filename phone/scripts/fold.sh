#!/usr/bin/env bash
# Drive the hinge on the foldable AVD.
#
#   scripts/fold.sh fold      close it: the narrow outer screen
#   scripts/fold.sh unfold    open it: the wide inner screen
#   scripts/fold.sh state     report the current posture and geometry
#
# The device is diablo_phone, a Pixel 10 Pro Fold profile. Folded is the narrow outer screen,
# unfolded is the near-square inner one, and the app has to survive the switch at runtime
# because a real user does it mid-session. Layout checked in one posture is half checked.
set -euo pipefail
SDK="${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}"
ADB="$SDK/platform-tools/adb"
[ -x "$ADB" ] || ADB=adb
AVD_HOME="${ANDROID_AVD_HOME:-$HOME/.config/.android/avd}"

cmd="${1:-state}"
case "$cmd" in fold|unfold|state) ;; *)
  echo "fold.sh: expected fold, unfold or state; got '$cmd'." >&2; exit 1 ;;
esac

if ! "$ADB" devices | sed -n '2,$p' | grep -q '[[:space:]]device$'; then
  echo "fold.sh: no device attached. Start one with:  scripts/emulator.sh" >&2
  exit 1
fi

geometry() { "$ADB" shell wm size 2>/dev/null | tr -d '\r' || true; }

posture() {
  "$ADB" shell cmd device_state state 2>/dev/null \
    | tr -d '\r' | sed -n "s/.*name='\([A-Z_]*\)'.*/\1/p" | head -1
}

if [ "$cmd" = state ]; then
  echo "posture:  $(posture)"
  echo "$(geometry)"
  "$ADB" shell wm density | tr -d '\r'
  exit 0
fi

avd="$("$ADB" emu avd name 2>/dev/null | head -1 | tr -d '\r' || true)"
if [ -z "$avd" ]; then
  echo "fold.sh: the attached device is not an emulator, so it has no hinge to drive." >&2
  exit 1
fi
cfg="$AVD_HOME/$avd.avd/config.ini"
if [ ! -f "$cfg" ]; then
  echo "fold.sh: no config for AVD '$avd' at $cfg." >&2
  exit 1
fi
if ! grep -qE '^hw\.sensor\.hinge[[:space:]]*=[[:space:]]*yes' "$cfg"; then
  echo "fold.sh: AVD '$avd' has no hinge (hw.sensor.hinge is not yes in $cfg)." >&2
  echo "fold.sh: use a foldable profile, for example diablo_phone (scripts/create-avd.sh)." >&2
  exit 1
fi

before="$(geometry)"
"$ADB" emu "$cmd"

for _ in $(seq 1 20); do
  now="$(geometry)"
  if [ -n "$now" ] && [ "$now" != "$before" ]; then break; fi
  sleep 0.5
done

echo "posture:  $(posture)"
echo "$(geometry)"
if [ "$(geometry)" = "$before" ]; then
  echo "fold.sh: geometry did not change; the device may already have been ${cmd}ed." >&2
fi
