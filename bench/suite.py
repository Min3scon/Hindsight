#!/usr/bin/env python3
"""The Hindsight benchmark suite: a fixed list of scenarios, one JSON file of results.

    python3 bench/suite.py [--root PATH] [--only a,b] [--quick] [--py ~/venvs/bpy53/bin/python]
                           [--label NAME] [--out FILE]

  --root PATH   the code under test (default: this checkout)
  --only a,b    run only the groups and scenarios whose name contains one of these
  --quick       small sizes, a smoke run (numbers are not comparable with a full run)
  --py PYTHON   the interpreter with bpy (default ~/venvs/bpy53/bin/python)
  --label NAME  result name (default: the git short hash of the root, "-dirty" if it has changes)
  --out FILE    default bench/results/<label>.json

Each group of scenarios runs in its own process (bench/scenarios/<group>.py, see bench/README.md).
The Rust engine used is the one shipped in the ROOT's own hindsight/wheels (unpacked to a cache
folder and put first on PYTHONPATH), so an old root runs the engine it was released with, not
whatever the venv holds (the add-on has no Python fallback; tests/reference holds the Python versions).
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCEN = os.path.join(HERE, "scenarios")
DEFAULT_PY = os.path.expanduser("~/venvs/bpy53/bin/python")

T = ("100k", "1m")
GROUPS = {
    "commit": ["commit_100k", "commit_1m", "goto_bl_1_100k", "goto_bl_300_100k"],
    "jumps": [f"jump_{n}_{d}_{t}" for t in T for n in (1, 30, 300, 3000) for d in ("fwd", "back")]
             + [f"drag_5000_{d}_{t}" for t in T for d in ("fwd", "back")],
    "saveload": [f"{op}_{m}_100k_{s}" for s in (1000, 6000) for op in ("save", "open") for m in ("folder", "embed")],
    "editmode": ["edit_step_100k", "edit_step_300k"],
    "datarec": ["dr_finish_nochange_500", "dr_finish_move_500", "dr_tick_idle_500", "dr_on_update_500",
                "dr_prime_500"],
    "draw": [f"redraw_{k}_{c}" for c in ("1obj_object", "1obj_global", "50obj_object", "50obj_global",
                                          "50obj_object_filtered", "50obj_global_filtered")
             for k in ("viewport", "panel")] + ["drag_5000"],
    "movie": ["movie_addon_ms_per_frame_100k", "movie_frame_set_ms_per_frame_100k"],
    "addon": ["addon_import", "addon_register_cold", "addon_unregister_cold", "addon_register_warm",
              "addon_unregister_warm"],
    "idle": ["idle_60s_1obj", "idle_60s_50obj", "depsgraph_handler_1obj", "depsgraph_handler_50obj"],
}
ALL_NAMES = [n for g in GROUPS.values() for n in g]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def loadavg():
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):  # Windows has no getloadavg
        return None


def git_label(root):
    try:
        h = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"], capture_output=True,
                           text=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=30).stdout.strip()
        if h:
            return h + ("-dirty" if dirty else "")
    except Exception:
        pass
    return os.path.basename(os.path.abspath(root))


def select_groups(only):
    """Groups to run for --only patterns (a pattern matches a group name or any scenario name)."""
    if not only:
        return list(GROUPS)
    return [g for g, names in GROUPS.items() if any(p in g or any(p in n for n in names) for p in only)]


def native_env(root, base_env=None):
    """Environment that makes the root's own shipped Rust wheel the one that gets imported."""
    env = dict(base_env or os.environ)
    tags = {("linux", "x86_64"): ("manylinux", "x86_64"), ("win32", "AMD64"): ("win_amd64",)}.get(
        (sys.platform, platform.machine()))
    if not tags:
        return env
    wd = os.path.join(root, "hindsight", "wheels")
    try:
        wheels = [w for w in os.listdir(wd) if w.startswith("hindsight_native") and all(t in w for t in tags)]
    except OSError:
        return env
    if not wheels:
        return env
    wheel = sorted(wheels)[-1]
    dest = os.path.join(tempfile.gettempdir(), "hs_bench_native", wheel[:-4])
    if not os.path.isdir(os.path.join(dest, "hindsight_native")):
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(os.path.join(wd, wheel)) as z:
            z.extractall(dest)
    env["PYTHONPATH"] = dest + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def last_json(text):
    for line in reversed(text.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def facts(py, root, env):
    p = subprocess.run([py, os.path.join(SCEN, "facts.py"), root], capture_output=True, text=True,
                       env=env, timeout=300)
    d = last_json(p.stdout)
    return d or {"error": (p.stderr or p.stdout)[-300:]}


def run_group(py, root, group, quick=False, only=None, env=None, timeout=3600):
    """Run one scenario group in its own process. Returns ({name: result}, seconds).
    A group that fails as a whole gives {"error": ...} for each of its scenarios."""
    cmd = [py, os.path.join(SCEN, group + ".py"), root]
    if quick:
        cmd.append("--quick")
    if only and any(o in group for o in only):
        only = None          # the whole group was asked for (its name matches a pattern)
    if only:
        cmd += ["--only", ",".join(only)]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        d = last_json(p.stdout)
        if d is None:
            tail = (p.stderr or p.stdout).strip().splitlines()
            raise RuntimeError(f"rc={p.returncode}: " + (tail[-1] if tail else "no output")[:250])
        res = d["results"]
        for n in GROUPS[group]:
            if n not in res and (not only or any(o in n for o in only)):
                res[n] = {"error": "no result reported"}
        return res, round(time.time() - t0, 1)
    except Exception as e:
        return {n: {"error": f"group {group} failed: {type(e).__name__}: {e}"[:300]}
                for n in GROUPS[group] if not only or any(o in n for o in only)}, round(time.time() - t0, 1)


def fmt(r):
    if "error" in r:
        return "not available: " + r["error"][:70]
    return f"min {r['min_ms']:.4g} med {r['median_ms']:.4g} ms"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.dirname(HERE))
    ap.add_argument("--only", default="")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--py", default=DEFAULT_PY)
    ap.add_argument("--label", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    only = [x for x in a.only.split(",") if x]
    label = a.label or git_label(root)
    env = native_env(root)
    load0 = loadavg()
    out = {"label": label, "root": root, "quick": a.quick, "only": only,
           "started": time.strftime("%Y-%m-%d %H:%M:%S"), "scenarios": {}, "group_seconds": {}}
    f = facts(a.py, root, env)
    for g in select_groups(only):
        log(f"[{label}] {g} ...")
        res, secs = run_group(a.py, root, g, a.quick, only, env)
        out["scenarios"].update(res)
        out["group_seconds"][g] = secs
        for n, r in res.items():
            log(f"    {n:40s} {fmt(r)}")
    out["machine"] = {"cores": os.cpu_count(), "load_average_start": load0, "load_average_end": loadavg(),
                      "host_python": platform.python_version(), "platform": platform.platform(), **f}
    path = a.out or os.path.join(HERE, "results", label + (".quick" if a.quick else "") + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=False)
    log("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
