#!/usr/bin/env bash
# Build the release wheels of the Rust engine (native/) and put them in hindsight/wheels/.
#
#   bash tools/build_native.sh                      # all six wheels into hindsight/wheels
#   bash tools/build_native.sh --out DIR            # somewhere else (e.g. to try it)
#   bash tools/build_native.sh --only aarch64-unknown-linux-gnu,aarch64-pc-windows-gnullvm
#   bash tools/build_native.sh --install-venvs /home/user/venvs   # then reinstall the Linux wheel into bpy* venvs
#
# Needs (tools/dev_env.sh installs them): Rust with the cross targets, maturin and zig (ziglang from PyPI,
# found in $HINDSIGHT_TOOLS, default /home/user/venvs/tools, or on PATH). Everything is built with
# `maturin build --release --zig`, so one Linux machine produces all platforms. One wheel per platform:
# the module uses the stable ABI (abi3, Python 3.11+) so Blender 4.2 (3.11) and 5.1+ (3.13) share it.
#
#   target                        wheel platform tag             Blender platform name
#   x86_64-unknown-linux-gnu      manylinux2014_x86_64           linux-x64        required
#   x86_64-pc-windows-gnu         win_amd64                      windows-x64      required
#   aarch64-apple-darwin          macosx_11_0_arm64              macos-arm64      required
#   x86_64-apple-darwin           macosx_10_12_x86_64            macos-x64        required
#   aarch64-unknown-linux-gnu     manylinux2014_aarch64          linux-arm64      optional
#   aarch64-pc-windows-gnullvm    win_arm64                      windows-arm64    optional
#
# (Blender maps the wheel tag to its platform name in bl_pkg/cli/blender_ext.py, blender_platforms_from_
# wheel_platform: win_arm64 -> windows-arm64, manylinux2014_aarch64 -> linux-arm64.)
# A failed optional wheel prints a warning and is skipped; a failed required one stops with exit 1.
# Wheels are only replaced in --out after every wanted wheel built, and only the platforms rebuilt.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/hindsight/wheels"
ONLY=""
INSTALL=""
TOOLS="${HINDSIGHT_TOOLS:-/home/user/venvs/tools}"
STAGE="${HINDSIGHT_BUILD_DIR:-$ROOT/.tmp/native-build}"

while [ $# -gt 0 ]; do
  case "$1" in
    --out) shift; mkdir -p "$1" && OUT="$(cd "$1" && pwd)" ;;
    --only) shift; ONLY="$1" ;;
    --install-venvs) shift; INSTALL="$1" ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "build_native: unknown option $1"; exit 2 ;;
  esac
  shift
done

# target|extra maturin flags|required
TARGETS=(
  "x86_64-unknown-linux-gnu|--compatibility manylinux2014|1"
  "x86_64-pc-windows-gnu||1"
  "aarch64-apple-darwin||1"
  "x86_64-apple-darwin||1"
  "aarch64-unknown-linux-gnu|--compatibility manylinux2014|0"
  "aarch64-pc-windows-gnullvm||0"
)

export PATH="$TOOLS/bin:$HOME/.cargo/bin:/root/.cargo/bin:$PATH"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$STAGE/target}"
command -v maturin >/dev/null || { echo "build_native: maturin not found (run tools/dev_env.sh)"; exit 1; }
command -v cargo >/dev/null || { echo "build_native: cargo not found (run tools/dev_env.sh)"; exit 1; }
INTERP=()
command -v python3.11 >/dev/null && INTERP=(-i python3.11)

mkdir -p "$OUT"
rm -rf "$STAGE/wheels"; mkdir -p "$STAGE/wheels"
FAILED_REQUIRED=0
BUILT=0
cd "$ROOT/native" || exit 1
for row in "${TARGETS[@]}"; do
  IFS='|' read -r target extra required <<< "$row"
  if [ -n "$ONLY" ] && ! [[ ",$ONLY," == *",$target,"* ]]; then continue; fi
  echo "build_native: $target"
  rustup target list --installed 2>/dev/null | grep -qx "$target" || rustup target add "$target" >/dev/null 2>&1
  # shellcheck disable=SC2086
  if maturin build --release --zig $extra --target "$target" "${INTERP[@]}" --out "$STAGE/wheels" \
       > "$STAGE/$target.log" 2>&1; then
    BUILT=$((BUILT + 1)); tail -1 "$STAGE/$target.log"
  else
    tail -15 "$STAGE/$target.log"
    if [ "$required" = 1 ]; then echo "build_native: FAILED required target $target"; FAILED_REQUIRED=1
    else echo "build_native: WARN optional target $target did not build, skipped"; fi
  fi
done
[ "$FAILED_REQUIRED" = 1 ] && { echo "build_native: nothing copied"; exit 1; }
[ "$BUILT" = 0 ] && { echo "build_native: no wheel built"; exit 1; }

for w in "$STAGE"/wheels/*.whl; do
  base="$(basename "$w" .whl)"
  tagplat="${base#hindsight_native-*-*-*-}"         # everything after name-version-python-abi-
  for old in "$OUT"/hindsight_native-*-"$tagplat".whl; do [ -e "$old" ] && rm -f "$old"; done
  cp "$w" "$OUT/"
done
ls -l "$OUT"

if [ -n "$INSTALL" ]; then
  for v in "$INSTALL"/bpy*/; do
    [ -x "$v/bin/python" ] || continue
    "$v/bin/python" -m pip install -q --no-index --find-links "$OUT" --force-reinstall --no-deps hindsight_native \
      && echo "build_native: installed into $v" || echo "build_native: WARN could not install into $v"
  done
fi
echo "build_native: done, $BUILT wheel(s) in $OUT"
