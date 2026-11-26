import os, sys, time, json, statistics
ROOT = sys.argv[1]; sys.path.insert(0, ROOT); sys.path.insert(1, os.path.join(ROOT, "bench", "scenarios"))
import tests.harness as H
import _common as K
BL = K.load_bench_module("bench_bl")
from hindsight.bl import meshio
from hindsight.core.history import MeshState
H.fresh()
ob = BL.make_mesh(1_000_000, name="S")
snap = meshio.read_snapshot(ob.data)
ts = []
for _ in range(9):
    st = MeshState({}, {})
    t = time.perf_counter(); st.set_snapshot(snap); ts.append((time.perf_counter() - t) * 1000)
held = sum(a.nbytes for k, a in st.light.items() if not any(a is b for b in snap.arrays.values()))
print("AB " + json.dumps({"set_snapshot_med_ms": round(statistics.median(ts), 1), "set_snapshot_min_ms": round(min(ts), 1),
                          "baseline_own_mb": round(held / 2**20, 1)}))
H.finish(0)
