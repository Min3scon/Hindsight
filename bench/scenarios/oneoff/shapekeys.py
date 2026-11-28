"""Record everything on a 40k-vertex mesh with K shape keys: moving one shape key's slider (Object Mode), then the
operator's end (finish_operation): ms, and cProfile."""
import sys, time, statistics, cProfile
ROOT = sys.argv[1]; K = int(sys.argv[2]); sys.path.insert(0, ROOT)
import bpy, numpy as np
import tests.harness as H
from hindsight.bl import datarec, runtime
from hindsight.bl import prefs
H.fresh()
ob = H.make_mesh(40_000, name="Face")
ob.shape_key_add(name="Basis")
rng = np.random.default_rng(1)
n = len(ob.data.vertices)
for k in range(K):
    sk = ob.shape_key_add(name=f"K{k:03d}")
    co = np.empty(n * 3, np.float32); sk.data.foreach_get("co", co)
    co[rng.integers(0, n * 3, 500)] += 0.01
    sk.data.foreach_set("co", co)
prefs.FORCE["record_scope"] = "EVERYTHING"
datarec.set_tracked(ob, True)
datarec.finish_operation("baseline")
ts = []
pr = cProfile.Profile()
for r in range(8):
    kb = ob.data.shape_keys.key_blocks[1 + r % K]
    kb.value = (r % 5) / 5.0
    bpy.context.view_layer.update()
    t = time.perf_counter(); datarec.finish_operation("Value"); ts.append((time.perf_counter() - t) * 1000)
import json
print("AB " + json.dumps({"shape_keys": K, "min_ms": round(min(ts), 1), "med_ms": round(statistics.median(ts), 1),
                          "steps": len(runtime.history_of(ob).entries) if runtime.history_of(ob) else 0}))
H.finish(0)
