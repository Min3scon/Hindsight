"""A Dyntopo step at about 100k triangles: median time of runtime.commit (main thread). Prints AB {json}.
Usage: python dyn_commit.py ROOT  (with bpy importable)."""
import os, sys, time, statistics, cProfile
ROOT = sys.argv[1]; sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.argv = [sys.argv[0]]
import bpy, bmesh
import tests.harness as H
from hindsight.bl import runtime
from hindsight.bl import prefs
import test_bl_dyntopo as T
C = bpy.context
H.fresh()
prefs.FORCE["record_dyntopo"] = True
bpy.ops.mesh.primitive_uv_sphere_add(segments=256, ring_count=200)
ob = C.active_object; ob.name = "Dyn"
H.enter(ob, "SCULPT"); runtime._tick_once()
T.dyntopo(ob); runtime.commit(ob)
print("tris", sum(len(p.vertices) - 2 for p in ob.data.polygons))
def change(me):
    bm = bmesh.new(); bm.from_mesh(me)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:12], cuts=1, use_grid_fill=True)
    bm.to_mesh(me); bm.free()
ts = []
pr = cProfile.Profile()
for k in range(8):
    T.in_object_mode(ob, change)
    if k >= 4: pr.enable()
    t = time.perf_counter(); s = runtime.commit(ob); ts.append((time.perf_counter() - t) * 1000)
    if k >= 4: pr.disable()
    assert s is not None
import json
print("AB " + json.dumps({"dyntopo_commit_med_ms": round(statistics.median(ts), 1), "min_ms": round(min(ts), 1)}))

H.finish(0)
