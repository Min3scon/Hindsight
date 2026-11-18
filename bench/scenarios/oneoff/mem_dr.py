"""Memory the recorder keeps per tracked object: ROOT N. Values, bodies, mesh states (tracemalloc)."""
import sys, os, gc, json, tracemalloc
ROOT = sys.argv[1]; sys.path.insert(0, ROOT); os.chdir(ROOT)
import bpy
import tests.harness as H
from hindsight.bl import datarec, idmap, runtime
N = int(sys.argv[2]); C = bpy.context
H.fresh()
for i in range(N):
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=(i, 0, 0))
    if i % 3 == 0:
        C.active_object.constraints.new("COPY_ROTATION")
datarec.FORCE_SCOPE[0] = "EVERYTHING"
for o in list(C.scene.objects):
    datarec.set_tracked(o, True)
datarec.set_tracked(C.scene, True)
datarec.finish_operation("baseline")
R = {}
def measure(label, fn):
    gc.collect(); tracemalloc.start(); s0 = tracemalloc.take_snapshot()
    keep = fn()
    gc.collect(); s1 = tracemalloc.take_snapshot(); tracemalloc.stop()
    R[label] = round(sum(s.size_diff for s in s1.compare_to(s0, "filename")) / N)
    return keep
def vals():
    for st in runtime._STATES.values():
        st.values = {}
    for u in datarec.tracked_uids():
        datarec._state(u).values = datarec.capture(idmap.find(u))
measure("values_B_per_object", vals)
def bodies():
    datarec._BODIES.clear()
    datarec.refresh_bodies(C.scene)
measure("bodies_B_per_object", bodies)
def meshes():
    for o in C.scene.objects:
        runtime.ensure_state(o, refresh=True)
measure("mesh_state_B_per_object(8 verts)", meshes)
print("RESULT " + json.dumps(R))
