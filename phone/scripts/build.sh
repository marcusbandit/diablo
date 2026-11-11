#!/usr/bin/env bash
# Run Gradle inside a hard memory cap so a build can never take the laptop down.
# Usage: scripts/build.sh :app:assembleDebug   (any gradle args)
set -euo pipefail
cd "$(dirname "$0")/.."
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk}"
export ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}"

MIN_FREE_MB=3000
avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if (( avail < MIN_FREE_MB )); then
  echo "build.sh: only ${avail} MB available (< ${MIN_FREE_MB} MB). Close something first." >&2
  exit 1
fi

exec systemd-run --user --scope -q \
  -p MemoryMax=3500M -p MemorySwapMax=0 -p CPUQuota=800% \
  ./gradlew --no-daemon --console=plain "$@"
