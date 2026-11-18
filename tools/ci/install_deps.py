#!/usr/bin/env python3
"""Install what the Hindsight tests need into a Python: Blender's bundled one, or a venv with bpy.

    python tools/ci/install_deps.py --blender-exe ~/blender/blender-5.3.0-linux-x64/blender
    python tools/ci/install_deps.py --python /home/user/venvs/bpy53/bin/python      # a bpy module venv
    python tools/ci/install_deps.py --python PY --dry-run                           # print only

Steps: make sure pip exists (ensurepip), install hypothesis and lz4 from PyPI (Blender bundles neither),
install numpy and zstandard only if the Python lacks them (Blender bundles both), then the committed
hindsight_native wheel from hindsight/wheels only (--no-index --find-links), and check that it imports.
"""
import argparse
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WHEELS = os.path.join(ROOT, "hindsight", "wheels")
PINS = ["hypothesis==6.170.15", "lz4==4.4.5"]       # the versions the suite was developed against
OPTIONAL = {"numpy": "numpy", "zstandard": "zstandard>=0.23"}   # import name -> requirement


def blender_python(exe):
    """The Python that ships inside an unpacked Blender (Linux, Windows or macOS layout)."""
    base = os.path.dirname(os.path.abspath(exe))
    pats = ["[0-9]*/python/bin/python3*", "[0-9]*/python/bin/python.exe", "[0-9]*/python/python.exe",
            "../Resources/[0-9]*/python/bin/python3*"]
    for pat in pats:
        for p in sorted(glob.glob(os.path.join(base, pat))):
            if os.path.isfile(p) and not p.endswith("-config"):
                return p
    raise SystemExit(f"no bundled Python found next to {exe}")


def run(cmd, dry):
    print("+", " ".join(cmd), flush=True)
    if dry:
        return 0
    return subprocess.run(cmd).returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--blender-exe")
    g.add_argument("--python")
    ap.add_argument("--wheels", default=WHEELS)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    py = blender_python(a.blender_exe) if a.blender_exe else (a.python or sys.executable)
    print("python:", py, flush=True)

    def works(args):
        return subprocess.run([py] + args, capture_output=True).returncode == 0

    if not a.dry_run and not works(["-m", "pip", "--version"]):
        if run([py, "-m", "ensurepip", "--upgrade"], False) != 0:
            raise SystemExit("pip is not available in " + py)
    need = list(PINS)
    for mod, req in OPTIONAL.items():
        if a.dry_run or not works(["-c", "import " + mod]):
            need.append(req)
    base = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input"]
    if run(base + need, a.dry_run):
        raise SystemExit("installing " + " ".join(need) + " failed")
    # the native wheel: only from the committed folder, never from an index
    if run(base + ["--no-index", "--find-links", a.wheels, "--force-reinstall", "--no-deps",
                   "hindsight_native"], a.dry_run):
        raise SystemExit("no committed hindsight_native wheel in %s fits this Python" % a.wheels)
    if not a.dry_run:
        code = ("import sys, hindsight_native as m, numpy, zstandard, hypothesis, lz4;"
                "print('python', sys.version.split()[0], '| hindsight_native', m.__file__,"
                " '| numpy', numpy.__version__, '| zstandard', zstandard.__version__)")
        if subprocess.run([py, "-c", code]).returncode != 0:
            raise SystemExit("the test dependencies do not import after installing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
