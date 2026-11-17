#!/usr/bin/env bash
# Build the APK. Runs expo prebuild when android/ is missing or the native config changed,
# then Gradle inside a hard memory cap with swap forbidden, so a build never takes the desktop down.
# Usage: scripts/build.sh [debug|release] [gradle args...]
#   debug    loads JavaScript from Metro (bunx expo start), for fast refresh
#   release  carries its own bundle, signed with the debug key; this is the one to hand over
set -euo pipefail
cd "$(dirname "$0")/.."
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk}"
export ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}"
export NODE_ENV="${NODE_ENV:-development}"

variant=debug
case "${1:-}" in debug|release) variant="$1"; shift ;; esac
[ "$variant" = release ] && export NODE_ENV=production

stamp=android/.prebuild-stamp
stale=0
if [ ! -f "$stamp" ]; then
  stale=1
elif [ -n "$(find app.config.ts package.json plugins assets/images assets/fonts -newer "$stamp" -print -quit 2>/dev/null)" ]; then
  stale=1
fi
if [ "$stale" = 1 ]; then
  echo "build.sh: expo prebuild"
  CI=1 bunx expo prebuild --platform android --no-install --clean
  touch "$stamp"
fi

MIN_FREE_MB=4000
avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if (( avail < MIN_FREE_MB )); then
  echo "build.sh: only ${avail} MB available (< ${MIN_FREE_MB} MB). Close something first." >&2
  exit 1
fi

task=assembleDebug
[ "$variant" = release ] && task=assembleRelease
# Debug builds only for the emulator's x86_64; release adds arm64-v8a for a real phone.
# A release build is the one handed to a phone, so it always carries arm64-v8a: an APK with only
# the emulator's code installs on a phone and dies at launch.
archs="${DIABLO_ARCHS:-x86_64}"
if [ "$variant" = release ]; then
  archs="${DIABLO_ARCHS:-arm64-v8a,x86_64}"
  case ",$archs," in *,arm64-v8a,*) ;; *) archs="arm64-v8a,$archs" ;; esac
fi
# Gradle gets 3 GB, the Kotlin daemon, CMake and aapt2 the rest; a release build also runs Metro.
cd android
exec systemd-run --user --scope -q \
  -p MemoryMax="${DIABLO_BUILD_MEM:-7000}M" -p MemorySwapMax=0 -p CPUQuota=400% \
  ./gradlew --no-daemon --console=plain --max-workers=3 \
    -Dorg.gradle.jvmargs="-Xmx3g -XX:MaxMetaspaceSize=768m" \
    -PreactNativeArchitectures="$archs" \
    "$task" "$@"
