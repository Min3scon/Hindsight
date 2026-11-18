"""Save and open of a large history in Blender, history folder (SIDECAR) and Embed, 100k vertices
with 1,000 and 6,000 steps. A thin wrapper over bench/saveload_bench.py (which does the work, one
process per history size), so that the numbers match what that script prints.

    <bpy python> bench/scenarios/saveload.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Scenarios: {save,open}_{folder,embed}_100k_{1000,6000}: time of bpy.ops.wm.save_as_mainfile and
bpy.ops.wm.open_mainfile (min and median over reps), tracemalloc peak and VmHWM over the RSS before.
--quick: 20k vertices, 100 and 300 steps (names unchanged).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

opts = K.Opts()
BENCH = os.path.join(K.BENCH, "saveload_bench.py")
MODES = (("SIDECAR", "folder"), ("EMBED", "embed"))
results = {}
sizes = ((100_000, 1000), (100_000, 6000)) if not opts.quick else ((20_000, 100), (20_000, 300))
for (nv, steps), label in zip(sizes, (1000, 6000)):
    names = [f"{op}_{m}_100k_{label}" for op in ("save", "open") for _s, m in MODES]
    if not opts.want(*names):
        continue
    try:
        p = subprocess.run([sys.executable, BENCH, opts.root, str(nv), str(steps), str(opts.n(3, 2))],
                           capture_output=True, text=True, timeout=3000)
        line = [x for x in p.stdout.splitlines() if x.startswith("RESULT ")]
        if not line:
            raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1][:250])
        raw = json.loads(line[0][7:])
        for mode, m in MODES:
            for op, key in (("save", f"save_{mode}"), ("open", f"load_{mode}")):
                r = raw[key]
                results[f"{op}_{m}_100k_{label}"] = {
                    "min_ms": r["ms_min"], "median_ms": r["ms_med"], "n": opts.n(3, 2),
                    "tracemalloc_mb": r["py_mb"], "hwm_mb": r["rss_mb"], "steps": raw["steps"],
                    "history_mb": raw["history_mb"], "vertices": nv}
    except Exception as e:
        for n in names:
            results[n] = {"error": f"{type(e).__name__}: {e}"[:300]}
K.emit(results, quick=opts.quick, native=None)
