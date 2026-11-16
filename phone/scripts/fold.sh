#!/usr/bin/env bash
# Drive the hinge on the foldable AVD.
#
#   scripts/fold.sh fold        close it: the narrow outer screen
#   scripts/fold.sh unfold      open it flat: the wide inner screen
#   scripts/fold.sh half [deg]  open it and bend it, 120 degrees unless given (180 is flat)
#   scripts/fold.sh state       report the posture, the hinge angle and the geometry
#
# The device is diablo_phone, a Pixel 10 Pro Fold profile. A layout checked in one posture is
# half checked: the app has to survive every switch at runtime.
set -euo pipefail
SDK="${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}"
ADB="$SDK/platform-tools/adb"
[ -x "$ADB" ] || ADB=adb
AVD_HOME="${ANDROID_AVD_HOME:-$HOME/.config/.android/avd}"

cmd="${1:-state}"
case "$cmd" in fold|unfold|half|state) ;; *)
  echo "fold.sh: expected fold, unfold, half or state; got '$cmd'." >&2; exit 1 ;;
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
angle() {
  "$ADB" emu sensor get hinge-angle0 2>/dev/null | tr -d '\r' | sed -n 's/.*= *//p' | head -1
}

if [ "$cmd" = state ]; then
  echo "posture:  $(posture)"
  echo "hinge:    $(angle)"
  geometry
  "$ADB" shell wm density | tr -d '\r'
  exit 0
fi

avd="$("$ADB" emu avd name 2>/dev/null | head -1 | tr -d '\r' || true)"
if [ -z "$avd" ]; then
  echo "fold.sh: the attached device is not an emulator, so it has no hinge to drive." >&2
  exit 1
fi
cfg="$AVD_HOME/$avd.avd/config.ini"
if [ ! -f "$cfg" ] || ! grep -qE '^hw\.sensor\.hinge[[:space:]]*=[[:space:]]*yes' "$cfg"; then
  echo "fold.sh: AVD '$avd' has no hinge (hw.sensor.hinge is not yes in $cfg)." >&2
  echo "fold.sh: use a foldable profile, for example diablo_phone (scripts/create-avd.sh)." >&2
  exit 1
fi

# The pixel_10_pro_fold image carries no display-feature config of its own, so apps see no
# FoldingFeature. The Pixel 9 Pro Fold overlay in the same image describes the same inner
# screen's hinge; switch it on once so the app can lay out around the seam.
overlay=com.android.internal.emulation.pixel_9_pro_fold
if "$ADB" shell cmd overlay list 2>/dev/null | tr -d '\r' | grep -q "^\[ \] $overlay$"; then
  "$ADB" shell cmd overlay enable --user 0 "$overlay" >/dev/null 2>&1 || true
fi

before="$(geometry)"
case "$cmd" in
  fold) "$ADB" emu fold >/dev/null ;;
  unfold) "$ADB" emu unfold >/dev/null; "$ADB" emu sensor set hinge-angle0 180 >/dev/null ;;
  half)
    deg="${2:-120}"
    "$ADB" emu unfold >/dev/null
    "$ADB" emu sensor set hinge-angle0 "$deg" >/dev/null
    ;;
esac

for _ in $(seq 1 20); do
  now="$(geometry)"
  if [ "$cmd" = half ] || { [ -n "$now" ] && [ "$now" != "$before" ]; }; then break; fi
  sleep 0.5
done

echo "posture:  $(posture)"
echo "hinge:    $(angle)"
geometry
