import os, sys, time
ROOT = sys.argv[1]; sys.path.insert(0, ROOT); sys.path.insert(1, os.path.join(ROOT, "bench", "scenarios"))
import bpy, bmesh
import tests.harness as H
import _common as K
BL = K.load_bench_module("bench_bl")
from hindsight.bl import runtime
H.fresh()
ob = BL.make_mesh(1_000_000, name="R")
H.enter(ob, "SCULPT"); sk = BL.Stroker(ob); sk.stroke_commit()
H.enter(ob, "OBJECT")
bm = bmesh.new(); bm.from_mesh(ob.data)
bmesh.ops.subdivide_edges(bm, edges=bm.edges[:40], cuts=1)
bm.to_mesh(ob.data); bm.free(); ob.data.update()
from hindsight.bl import persistence
import tempfile, json
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(tempfile.mkdtemp(prefix="hs_t1m_"), "t.blend"))
ob = bpy.data.objects["R"]
n0 = len(runtime.history_of(ob).entries)
t = time.perf_counter(); H.enter(ob, "SCULPT"); runtime._tick_once(); s = runtime.commit(ob); dt = (time.perf_counter() - t) * 1000
d = getattr(persistence, "drain_journal_writer", None)
if d: d()
dt2 = (time.perf_counter() - t) * 1000
print("AB " + json.dumps({"steps": [n0, len(runtime.history_of(ob).entries)], "main_thread_ms": round(dt), "until_journaled_ms": round(dt2)}))

H.finish(0)
