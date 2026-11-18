#!/usr/bin/env bash
# Rebuild the Hindsight development environment from nothing (a fresh cloud container).
#
#   bash tools/dev_env.sh                 # venvs bpy42 and bpy53, tools venv, Rust + cross targets
#   bash tools/dev_env.sh --all           # also bpy50, bpy51, bpy52
#   bash tools/dev_env.sh --only 53,42    # just these venvs (42 50 51 52 53)
#   bash tools/dev_env.sh --venvs DIR     # target folder (default /home/user/venvs)
#   bash tools/dev_env.sh --no-rust       # skip rustup, maturin, zig (venvs only)
#
# Made to run as a cloud environment setup script: root, non-interactive, idempotent (a second run only
# checks), and it never fails the session. A part that fails prints one "dev_env: WARN ..." line and the
# rest goes on. Exit code is always 0 unless --strict is given.
#
# What you get in each venv (DIR/bpyNN/bin/python): the bpy module (the Blender of that number), numpy,
# zstandard (come with bpy), hypothesis and lz4 (pinned like the suite was developed), and the committed
# hindsight_native wheel for this machine from hindsight/wheels. DIR/tools holds maturin and ziglang, used
# by tools/build_native.sh. Rust comes from rustup (cargo in ~/.cargo/bin) with the targets of the wheels.
#
# Python versions: Blender 4.2 and 5.0 use Python 3.11, 5.1 and later 3.13 (the bpy wheels need exactly
# those). A missing interpreter is installed with apt, or with uv as a fallback.
# Test it without touching /home/user/venvs:  bash tools/dev_env.sh --only 53 --no-rust --venvs /tmp/x
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENVS=/home/user/venvs
SELECT="42,53"
RUST=1
STRICT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --all) SELECT="42,50,51,52,53" ;;
    --only) shift; SELECT="${1:-}" ;;
    --venvs) shift; VENVS="${1:-}" ;;
    --no-rust) RUST=0 ;;
    --strict) STRICT=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "dev_env: unknown option $1"; exit 2 ;;
  esac
  shift
done

# number -> bpy version and Python of the venv (read from the venvs in use on 2026-11-18)
bpy_version() { case "$1" in 42) echo 4.2.23;; 50) echo 5.0.1;; 51) echo 5.1.2;; 52) echo 5.2.2;; 53) echo 5.3.0;; esac; }
py_minor() { case "$1" in 42|50) echo 3.11;; *) echo 3.13;; esac; }
TEST_PINS=(hypothesis==6.170.15 lz4==4.4.5)
MATURIN_PINS=(maturin==1.16.0 ziglang==0.17.1 cargo-zigbuild==0.24.1)
RUST_TARGETS=(x86_64-unknown-linux-gnu x86_64-pc-windows-gnu aarch64-apple-darwin x86_64-apple-darwin
              aarch64-unknown-linux-gnu aarch64-pc-windows-gnullvm)

export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 PYTHONDONTWRITEBYTECODE=1
export DEBIAN_FRONTEND=noninteractive
WARNINGS=0
warn() { WARNINGS=$((WARNINGS + 1)); echo "dev_env: WARN $*"; }
info() { echo "dev_env: $*"; }

# find_python 3.13 -> path of an interpreter of that minor version, installing one if needed
find_python() {
  local v="$1" p
  for p in "python$v" "/usr/local/bin/python$v" "/usr/bin/python$v"; do
    if command -v "$p" >/dev/null 2>&1; then command -v "$p"; return 0; fi
  done
  if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y -q "python$v" "python$v-venv" >/dev/null 2>&1 || true
    if command -v "python$v" >/dev/null 2>&1; then command -v "python$v"; return 0; fi
  fi
  # fallback: uv downloads a standalone build of that Python
  if ! command -v uv >/dev/null 2>&1; then python3 -m pip install -q uv >/dev/null 2>&1 || true; fi
  if command -v uv >/dev/null 2>&1; then
    uv python install "$v" >/dev/null 2>&1 || true
    p="$(uv python find "$v" 2>/dev/null)" && [ -n "$p" ] && { echo "$p"; return 0; }
  fi
  return 1
}

make_venv() {   # make_venv DIR PYTHON
  local dir="$1" py="$2"
  [ -x "$dir/bin/python" ] && return 0
  "$py" -m venv "$dir" >/dev/null 2>&1 && return 0
  rm -rf "$dir"
  command -v uv >/dev/null 2>&1 || python3 -m pip install -q uv >/dev/null 2>&1
  uv venv --seed -p "$py" "$dir" >/dev/null 2>&1
}

pip_has() { "$1/bin/python" -m pip show "$2" 2>/dev/null | grep -q "^Version: $3\$"; }

setup_bpy_venv() {
  local n="$1" dir="$VENVS/bpy$1" ver py
  ver="$(bpy_version "$n")"
  [ -n "$ver" ] || { warn "unknown venv number $n"; return; }
  py="$(find_python "$(py_minor "$n")")" || { warn "bpy$n: no Python $(py_minor "$n") available"; return; }
  make_venv "$dir" "$py" || { warn "bpy$n: cannot create the venv in $dir"; return; }
  if pip_has "$dir" bpy "$ver"; then
    info "bpy$n: bpy $ver already installed"
  else
    "$dir/bin/python" -m pip install -q "bpy==$ver" || { warn "bpy$n: installing bpy==$ver failed"; return; }
    info "bpy$n: installed bpy $ver"
  fi
  "$dir/bin/python" -m pip install -q "${TEST_PINS[@]}" || warn "bpy$n: installing ${TEST_PINS[*]} failed"
  if "$dir/bin/python" -m pip install -q --no-index --find-links "$ROOT/hindsight/wheels" \
       --force-reinstall --no-deps hindsight_native; then
    info "bpy$n: hindsight_native wheel installed"
  else
    warn "bpy$n: no committed hindsight_native wheel fits this machine (the add-on falls back to Python)"
  fi
  "$dir/bin/python" -c "import bpy, numpy, zstandard, hypothesis, lz4; print('dev_env: bpy$n ok: bpy', bpy.app.version_string, '| numpy', numpy.__version__)" \
    2>&1 | tail -1 | grep -q "ok:" || warn "bpy$n: the venv does not import bpy and the test dependencies"
}

setup_tools_venv() {
  local dir="$VENVS/tools" py
  py="$(find_python 3.13)" || py="$(find_python 3.11)" || { warn "tools: no Python"; return; }
  make_venv "$dir" "$py" || { warn "tools: cannot create $dir"; return; }
  "$dir/bin/python" -m pip install -q "${MATURIN_PINS[@]}" && info "tools: ${MATURIN_PINS[*]} ready in $dir" \
    || warn "tools: installing ${MATURIN_PINS[*]} failed"
}

setup_rust() {
  export PATH="$HOME/.cargo/bin:$PATH"
  if ! command -v rustup >/dev/null 2>&1; then
    if command -v curl >/dev/null 2>&1; then
      curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable >/dev/null 2>&1 \
        || { warn "rust: installing rustup failed"; return; }
    else
      warn "rust: curl missing, cannot install rustup"; return
    fi
  fi
  export PATH="$HOME/.cargo/bin:$PATH"
  rustup toolchain list 2>/dev/null | grep -q . || rustup toolchain install stable --profile minimal >/dev/null 2>&1 \
    || { warn "rust: no toolchain"; return; }
  local missing=() t
  for t in "${RUST_TARGETS[@]}"; do rustup target list --installed 2>/dev/null | grep -qx "$t" || missing+=("$t"); done
  if [ ${#missing[@]} -gt 0 ]; then
    rustup target add "${missing[@]}" >/dev/null 2>&1 || warn "rust: adding targets ${missing[*]} failed"
  fi
  info "rust: $(rustc --version 2>/dev/null) with $(rustup target list --installed 2>/dev/null | wc -l) targets"
}

info "repository $ROOT, venvs $VENVS, venvs wanted: $SELECT"
mkdir -p "$VENVS" || warn "cannot create $VENVS"
IFS=',' read -r -a NUMS <<< "$SELECT"
for n in "${NUMS[@]}"; do [ -n "$n" ] && setup_bpy_venv "$n"; done
if [ "$RUST" = 1 ]; then setup_tools_venv; setup_rust; fi

if [ "$WARNINGS" -eq 0 ]; then info "done, no warnings"; else info "done with $WARNINGS warning(s) above"; fi
[ "$STRICT" = 1 ] && [ "$WARNINGS" -gt 0 ] && exit 1
exit 0
