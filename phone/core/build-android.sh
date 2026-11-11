#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <jniLibs-dir> <kotlin-out-dir>" >&2
    exit 2
fi

jni_dir=$1
kotlin_dir=$2
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)

export PATH="$HOME/.cargo/bin:$PATH"

# The hub addresses the app starts with come from this box's local.env, never the repo.
local_env=${DIABLO_LOCAL_ENV:-$HOME/.config/diablo/local.env}
if [ -f "$local_env" ]; then
    set -a
    . "$local_env"
    set +a
fi

if ! command -v cargo-ndk >/dev/null 2>&1; then
    echo "build-android: cargo-ndk is not installed (cargo install cargo-ndk)" >&2
    exit 1
fi

if [ -z "${ANDROID_NDK_HOME:-}" ]; then
    sdk=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}
    if [ -d "$sdk/ndk" ]; then
        ANDROID_NDK_HOME=$(ls -d "$sdk"/ndk/*/ 2>/dev/null | sort -V | tail -n 1)
        ANDROID_NDK_HOME=${ANDROID_NDK_HOME%/}
    fi
fi
if [ -z "${ANDROID_NDK_HOME:-}" ] || [ ! -d "$ANDROID_NDK_HOME" ]; then
    echo "build-android: Android NDK not found; set ANDROID_NDK_HOME" >&2
    exit 1
fi
export ANDROID_NDK_HOME

mkdir -p "$jni_dir" "$kotlin_dir"
cd "$root"

cargo ndk -t arm64-v8a -t x86_64 -o "$jni_dir" build --release -p diablo-phone --lib
cargo build --release -p diablo-phone --lib

target_dir=${CARGO_TARGET_DIR:-$root/target}
lib="$target_dir/release/libdiablo_phone.so"
if [ ! -f "$lib" ]; then
    echo "build-android: host library missing at $lib" >&2
    exit 1
fi

cargo run --release -p diablo-phone --bin uniffi-bindgen -- generate \
    --library "$lib" --language kotlin --out-dir "$kotlin_dir" \
    --config "$here/uniffi.toml"

for abi in arm64-v8a x86_64; do
    if [ ! -f "$jni_dir/$abi/libdiablo_phone.so" ]; then
        echo "build-android: missing $jni_dir/$abi/libdiablo_phone.so" >&2
        exit 1
    fi
done
echo "build-android: ok"
