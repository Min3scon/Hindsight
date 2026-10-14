#!/usr/bin/env python3
"""Drive real Blender (with a window) against the built Hindsight zip, in an isolated profile.

Nothing here touches the user's real Blender preferences, add-ons or extensions: every run sets
BLENDER_USER_RESOURCES / CONFIG / SCRIPTS / EXTENSIONS / DATAFILES to folders under .tmp/ in the
repo (git-ignored).

    python tests_ui/hs_ui.py list
    python tests_ui/hs_ui.py build                         # real `blender --command extension build`
    python tests_ui/hs_ui.py install [--blender 5.1.2]     # CLI install into the sandbox profile
    python tests_ui/hs_ui.py run SCENARIO [--blender all] [--fresh] [--zip PATH] [--timeout 300]
                                 [--arg key=value ...] [--no-install] [--background]

Each run writes .tmp/ui/<scenario>/<blender>/{console.log,result.json,shots/*.png}.
Exit code 0 only if every selected Blender reported ok=true.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / ".tmp"
HERE = Path(__file__).resolve().parent
SCEN_DIR = HERE / "scenarios"
DRIVER = HERE / "bl_driver.py"
DEFAULT_ZIP = TMP / "build" / "hindsight.zip"

def _env_path(var: str, *parts: str) -> Path | None:
    base = os.environ.get(var)
    return Path(base, *parts) if base else None


CANDIDATE_DIRS = [p for p in (
    _env_path("ProgramFiles", "Blender Foundation"),
    _env_path("ProgramFiles(x86)", "Steam", "steamapps", "common", "Blender"),
    _env_path("ProgramFiles", "Steam", "steamapps", "common", "Blender"),
    _env_path("USERPROFILE", "scoop", "apps", "blender"),
    _env_path("LOCALAPPDATA", "Microsoft", "WinGet", "Packages"),
    TMP / "blenders",  # portable builds unpacked for testing (git-ignored)
) if p is not None]


# ----------------------------------------------------------------------------- discovery
def _version_of(exe: Path) -> str | None:
    try:
        out = subprocess.run([str(exe), "--factory-startup", "--version"], capture_output=True,
                             text=True, timeout=60).stdout
    except Exception:
        return None
    m = re.search(r"Blender (\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def find_blenders() -> list[tuple[str, Path]]:
    seen: dict[str, Path] = {}
    exes: list[Path] = []
    for d in CANDIDATE_DIRS:
        if d.is_dir():
            exes += [p for p in d.rglob("blender.exe") if p.parent.name != "blender_launcher"]
    w = shutil.which("blender")
    if w:
        exes.append(Path(w))
    for exe in exes:
        try:
            key = str(exe.resolve()).lower()
        except OSError:
            continue
        if key in {str(p.resolve()).lower() for p in seen.values()}:
            continue
        v = _version_of(exe)
        if v is None:
            continue
        tag = v
        n = 2
        while tag in seen:  # two installs of the same version (e.g. Steam and installer)
            tag = f"{v}-{n}"
            n += 1
        seen[tag] = exe
    def vkey(item):
        nums = [int(x) for x in re.findall(r"\d+", item[0])]
        return nums
    return sorted(seen.items(), key=vkey)


def select(blenders, which: str):
    if which == "all":
        return [b for b in blenders if tuple(int(x) for x in b[0].split("-")[0].split(".")[:2]) >= (4, 2)]
    pick = [b for b in blenders if b[0] == which or b[0].startswith(which)]
    if not pick:
        sys.exit(f"no Blender matching {which!r}; found: {[b[0] for b in blenders]}")
    return pick[:1] if which != "all" else pick


# ----------------------------------------------------------------------------- sandbox
def profile_dir(tag: str) -> Path:
    return TMP / "profiles" / tag


def sandbox_env(tag: str) -> dict:
    base = profile_dir(tag)
    env = dict(os.environ)
    for key, sub in (("BLENDER_USER_RESOURCES", "resources"), ("BLENDER_USER_CONFIG", "config"),
                     ("BLENDER_USER_SCRIPTS", "scripts"), ("BLENDER_USER_EXTENSIONS", "extensions"),
                     ("BLENDER_USER_DATAFILES", "datafiles")):
        p = base / sub
        p.mkdir(parents=True, exist_ok=True)
        env[key] = str(p)
    tmp = base / "temp"
    tmp.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = env["TMP"] = str(tmp)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HINDSIGHT_SANDBOX"] = str(base)
    return env


def wipe_profile(tag: str) -> None:
    base = profile_dir(tag)
    if base.exists():
        assert TMP in base.parents, base
        shutil.rmtree(base)


# ----------------------------------------------------------------------------- build / install
def build(blenders, out: Path = DEFAULT_ZIP) -> Path:
    exe = blenders[-1][1]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    env = sandbox_env("build")
    cmd = [str(exe), "--factory-startup", "--command", "extension", "build",
           "--source-dir", str(ROOT / "hindsight"), "--output-filepath", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    print(r.stdout.strip())
    if r.returncode != 0 or not out.is_file():
        print(r.stderr)
        sys.exit("build failed")
    v = subprocess.run([str(exe), "--factory-startup", "--command", "extension", "validate", str(out)],
                       capture_output=True, text=True, env=env)
    print(v.stdout.strip())
    if v.returncode != 0:
        print(v.stderr)
        sys.exit("validate failed")
    return out


def cli_install(tag: str, exe: Path, zip_path: Path) -> None:
    env = sandbox_env(tag)
    cmd = [str(exe), "--command", "extension", "install-file", "--repo", "user_default",
           "--enable", str(zip_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        sys.exit(f"install-file failed on {tag}")


# ----------------------------------------------------------------------------- run
def run_one(tag, exe, scenario, args, zip_path, timeout, background, geometry):
    out = TMP / "ui" / scenario / tag
    if out.exists():
        shutil.rmtree(out)
    (out / "shots").mkdir(parents=True)
    env = sandbox_env(tag)
    spec = {"scenario": str(SCEN_DIR / f"s_{scenario}.py"), "out": str(out), "args": args,
            "zip": str(zip_path), "repo": str(ROOT), "tag": tag}
    spec_path = out / "spec.json"
    spec_path.write_text(json.dumps(spec, indent=1))
    cmd = [str(exe)]
    if background:
        cmd += ["--background"]
    else:
        x, y, w, h = geometry
        cmd += ["--window-geometry", str(x), str(y), str(w), str(h), "--no-window-focus",
                "--enable-event-simulate"]
    blend = args.get("blend")
    if blend:
        cmd += [blend]
    cmd += ["--python-exit-code", "3", "--python", str(DRIVER), "--", str(spec_path)]
    t0 = time.time()
    with open(out / "console.log", "w", encoding="utf-8", errors="replace") as log:
        try:
            p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=timeout)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    dt = time.time() - t0
    res_path = out / "result.json"
    res = json.loads(res_path.read_text()) if res_path.is_file() else {"ok": False, "error": "no result.json"}
    res["exit_code"] = code
    res["seconds"] = round(dt, 1)
    res_path.write_text(json.dumps(res, indent=1))
    return res, out


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    b = sub.add_parser("build")
    b.add_argument("--out", default=str(DEFAULT_ZIP))
    i = sub.add_parser("install")
    i.add_argument("--blender", default="all")
    i.add_argument("--zip", default=str(DEFAULT_ZIP))
    i.add_argument("--fresh", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("--blender", default="all")
    r.add_argument("--zip", default=str(DEFAULT_ZIP))
    r.add_argument("--fresh", action="store_true", help="wipe the sandbox profile first")
    r.add_argument("--no-install", action="store_true", help="do not CLI-install the zip first")
    r.add_argument("--timeout", type=int, default=600)
    r.add_argument("--background", action="store_true")
    r.add_argument("--geometry", default="40,40,1600,900")
    r.add_argument("--arg", action="append", default=[], help="key=value passed to the scenario")
    a = ap.parse_args(argv)

    blenders = find_blenders()
    if a.cmd == "list":
        for tag, exe in blenders:
            print(f"{tag:10s} {exe}")
        return 0
    if a.cmd == "build":
        build(blenders, Path(a.out))
        return 0
    if a.cmd == "install":
        for tag, exe in select(blenders, a.blender):
            if a.fresh:
                wipe_profile(tag)
            cli_install(tag, exe, Path(a.zip))
            print("installed", tag)
        return 0

    args = {}
    for kv in a.arg:
        k, _, v = kv.partition("=")
        args[k] = v
    geometry = [int(x) for x in a.geometry.split(",")]
    all_ok = True
    for tag, exe in select(blenders, a.blender):
        if a.fresh:
            wipe_profile(tag)
        if not a.no_install:
            cli_install(tag, exe, Path(a.zip))
        res, out = run_one(tag, exe, a.scenario, args, Path(a.zip), a.timeout, a.background, geometry)
        ok = bool(res.get("ok"))
        all_ok &= ok
        fails = [c for c in res.get("checks", []) if not c.get("ok")]
        print(f"{'OK  ' if ok else 'FAIL'} {a.scenario} on {tag} ({res['seconds']} s, exit {res['exit_code']}) "
              f"checks {len(res.get('checks', [])) - len(fails)}/{len(res.get('checks', []))} -> {out}")
        for c in fails[:20]:
            print(f"     x {c['name']}: {c.get('detail', '')}")
        if res.get("error"):
            print("     error:", str(res["error"])[:2000])
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
