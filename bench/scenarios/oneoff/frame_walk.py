"""framing.history_extents_multi over STEPS injected strokes at N vertices (Movie Mode framing walk)."""
import os, sys, time, cProfile, pstats, io, json
ROOT = sys.argv[1]; N = sys.argv[2]; STEPS = sys.argv[3]; PROF = len(sys.argv) > 4
sys.argv = [sys.argv[0], ROOT, N, STEPS, "1"]
sys.path.insert(0, os.path.join(ROOT, "bench"))
if os.environ.get("HS_ENGINE"): sys.path.insert(0, os.environ["HS_ENGINE"])
import saveload_bench as B
import numpy as np
from hindsight.bl import runtime
from hindsight.core import framing
B.setup()
ob = [o for o in B.bpy.data.objects if o.type == "MESH"][0]
uid = runtime.obj_uid(ob); h = runtime.history_of(ob); st = runtime.ensure_state(ob)
bases = np.stack([framing.view_basis(a, 0.3) for a in (0.0, 1.0, 2.0)]); tans = np.array([(0.4, 0.3)] * 3)
h.cursor = len(h.entries) // 2
ts = []
pr = cProfile.Profile()
for rep in range(3):
    t = time.perf_counter()
    if PROF and rep == 2: pr.enable()
    ext = framing.history_extents_multi(h, st, uid, bases, tans)
    if PROF and rep == 2: pr.disable()
    ts.append((time.perf_counter() - t) * 1000)
print("AB " + json.dumps({"n": int(N), "steps": len(h.entries), "walk_ms": round(min(ts), 1), "ext0": [round(float(x), 6) for x in ext[0]]}))
if PROF:
    s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(14)
    for line in s.getvalue().splitlines():
        if "/" in line or "{" in line: print("  ", line.strip()[:160])
B.H.finish(0)
