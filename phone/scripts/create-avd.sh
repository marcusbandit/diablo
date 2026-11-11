#!/usr/bin/env bash
# Create the template's foldable emulator, identically on any machine.
# Usage: scripts/create-avd.sh [name] [device]   (default diablo_phone, pixel_10_pro_fold)
set -euo pipefail
SDK="${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}"
NAME="${1:-diablo_phone}"
DEVICE="${2:-pixel_10_pro_fold}"
IMAGE="system-images;android-36;google_apis_playstore;x86_64"

export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk}"
export ANDROID_AVD_HOME="${ANDROID_AVD_HOME:-$HOME/.config/.android/avd}"
export ANDROID_SDK_HOME="${ANDROID_SDK_HOME:-$HOME/.config/.android}"
mkdir -p "$ANDROID_AVD_HOME"

SDKMANAGER="$SDK/cmdline-tools/latest/bin/sdkmanager"
AVDMANAGER="$SDK/cmdline-tools/latest/bin/avdmanager"
for tool in "$SDKMANAGER" "$AVDMANAGER"; do
  [ -x "$tool" ] || { echo "Missing $tool. Install the Android command line tools first." >&2; exit 1; }
done

if [ ! -d "$SDK/${IMAGE//;//}" ]; then
  echo "Downloading $IMAGE ..."
  yes | "$SDKMANAGER" "$IMAGE" >/dev/null
fi

if "$AVDMANAGER" list avd 2>/dev/null | grep -q "Name: ${NAME}$"; then
  echo "AVD $NAME already exists."
else
  echo no | "$AVDMANAGER" create avd -n "$NAME" -k "$IMAGE" -d "$DEVICE" >/dev/null
  echo "Created AVD $NAME."
fi

CONFIG="$ANDROID_AVD_HOME/$NAME.avd/config.ini"
set_prop() {
  if grep -q "^$1=" "$CONFIG"; then sed -i "s|^$1=.*|$1=$2|" "$CONFIG"; else echo "$1=$2" >> "$CONFIG"; fi
}
set_prop hw.keyboard yes
set_prop hw.ramSize 1536
set_prop hw.cpu.ncore 4
set_prop hw.sensor.hinge yes
echo "Configured $CONFIG (hinge on, keyboard on, 1.5 GB guest RAM)."
echo "Start it with: scripts/emulator.sh"
