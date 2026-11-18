"""Edit Mode step by step: ROOT NVERTS. ms per finish_operation after moving some vertices (median of 5),
split into the data commit and the Edit Mode step."""
import sys, os, json, time, statistics
ROOT = sys.argv[1]; sys.path.insert(0, ROOT); os.chdir(ROOT)
import bpy, bmesh
import tests.harness as H
from hindsight.bl import datarec, runtime
N = int(sys.argv[2])
H.fresh()
ob = H.make_mesh(N, name="M")
datarec.FORCE_SCOPE[0] = "EVERYTHING"
datarec.set_tracked(ob, True)
datarec.finish_operation("baseline")
H.enter(ob, "EDIT")
bm = bmesh.from_edit_mesh(ob.data)
for v in bm.verts:
    v.select = v.index % 50 == 0
bmesh.update_edit_mesh(ob.data)
datarec.finish_operation("select")
tot, com, edit = [], [], []
oc, oe = datarec.commit, datarec._edit_step
def tc(*a, **k):
    t = time.perf_counter(); r = oc(*a, **k); com.append((time.perf_counter() - t) * 1000); return r
def te(*a, **k):
    t = time.perf_counter(); r = oe(*a, **k); edit.append((time.perf_counter() - t) * 1000); return r
datarec.commit, datarec._edit_step = tc, te
for k in range(5):
    bpy.ops.transform.translate(value=(0, 0, 0.01))
    t = time.perf_counter()
    datarec.finish_operation("Move")
    tot.append((time.perf_counter() - t) * 1000)
h = runtime.history_of(ob)
print("RESULT " + json.dumps({"n": len(ob.data.vertices), "finish_ms": round(statistics.median(tot), 2),
      "commit_ms": round(statistics.median(com), 2), "edit_step_ms": round(statistics.median(edit), 2),
      "steps": len(h.entries)}))
