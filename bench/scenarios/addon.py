"""Add-on load cost: import of the package, register and unregister.

    <bpy python> bench/scenarios/addon.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Each sample is a fresh interpreter (bpy and numpy already imported, so only our cost is timed):
  addon_import                `import hindsight` and every module register() loads (hindsight.bl.*,
                              and what they pull in of hindsight.core and the native engine)
  addon_register_cold         the first hindsight.register() (modules already imported)
  addon_unregister_cold       the first hindsight.unregister()
  addon_register_warm         later register() calls in the same process (modules imported)
  addon_unregister_warm       later unregister() calls (median of 4 cycles per interpreter)
min and median are over interpreters. The page cache is warm (a cold disk is not measured).
"""
import json
import os
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

NAMES = ("addon_import", "addon_register_cold", "addon_unregister_cold", "addon_register_warm",
         "addon_unregister_warm")


def child(root):
    sys.path.insert(0, root)
    os.chdir(root)
    import bpy  # noqa: F401
    import numpy  # noqa: F401
    t = time.perf_counter()
    import hindsight
    import importlib
    for name in getattr(hindsight, "_MODULES", ()):
        importlib.import_module(f"hindsight.bl.{name}")
    t_imp = time.perf_counter() - t
    assert os.path.abspath(hindsight.__file__).startswith(root), hindsight.__file__
    t = time.perf_counter()
    hindsight.register()
    t_reg = time.perf_counter() - t
    t = time.perf_counter()
    hindsight.unregister()
    t_unreg = time.perf_counter() - t
    rw, uw = [], []
    for _ in range(4):
        t = time.perf_counter()
        hindsight.register()
        rw.append(time.perf_counter() - t)
        t = time.perf_counter()
        hindsight.unregister()
        uw.append(time.perf_counter() - t)
    out = dict(zip(NAMES, (t_imp * 1000, t_reg * 1000, t_unreg * 1000, statistics.median(rw) * 1000,
                           statistics.median(uw) * 1000)))
    errs = list(getattr(hindsight, "_errors", []))
    out["errors"] = [e[0] for e in errs]
    sys.stdout.write("\n" + json.dumps(out) + "\n")
    sys.stdout.flush()
    os._exit(0)


if "--child" in sys.argv:
    child(os.path.abspath(sys.argv[1]))

opts = K.Opts()
rows = []
err = None
for _ in range(opts.n(7, 3)):
    p = subprocess.run([sys.executable, os.path.abspath(__file__), opts.root, "--child"],
                       capture_output=True, text=True, timeout=300)
    line = [x for x in p.stdout.splitlines() if x.startswith("{")]
    if not line:
        err = (p.stderr or p.stdout).strip().splitlines()[-1][:250] if (p.stderr or p.stdout).strip() else "no output"
        break
    rows.append(json.loads(line[-1]))
results = {}
for n in NAMES:
    if not opts.want(n):
        continue
    if err or not rows:
        results[n] = {"error": err or "no samples"}
    else:
        results[n] = K.stats([r[n] for r in rows], module_errors=rows[0]["errors"])
K.emit(results, quick=opts.quick, native=None)
