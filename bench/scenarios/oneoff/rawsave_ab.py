"""R066 A/B: what a save spends building the history it writes (persist.registry_parts) with the newest 64
steps of a mesh still unpacked (70 strokes of 2%). One JSON line: median and min of 7 builds."""
import json, os, sys, time, statistics
ROOT = sys.argv[1]; V = int(sys.argv[2]); sys.path.insert(0, ROOT); sys.path.insert(1, os.path.join(ROOT, "bench", "scenarios"))
import bpy
import tests.harness as H
import _common as K
BL = K.load_bench_module("bench_bl")
from hindsight.bl import runtime
from hindsight.core import persist
H.fresh()
ob = BL.make_mesh(V, name="M")
H.enter(ob, "SCULPT"); sk = BL.Stroker(ob)
for i in range(70):
    sk.stroke_commit()
if runtime.packer is not None:
    runtime.packer.drain(60)
h = runtime.history_of(ob)
raw = [e for e in h.entries if not e.is_packed()]
ts = []
for _ in range(7):
    t = time.perf_counter(); parts = persist.registry_parts(runtime.reg(), "auto"); ts.append((time.perf_counter() - t) * 1000)
print("AB " + json.dumps({"vertices": V, "raw_steps": len(raw), "raw_mb": round(sum(e.nbytes() for e in raw) / 2**20, 1),
                          "parts_min_ms": round(min(ts), 1), "parts_med_ms": round(statistics.median(ts), 1)}), flush=True)
H.finish(0)
