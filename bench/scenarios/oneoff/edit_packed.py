"""R069: an Edit Mode operator that keeps the counts (Smooth Vertices), Record everything, 300k vertices:
finish_operation with the baseline's topology packed (compared by checksum) and unpacked (by arrays)."""
import os, sys, time, statistics, json
ROOT = sys.argv[1]; sys.path.insert(0, ROOT)
import bpy, bmesh
import tests.harness as H
from hindsight.bl import datarec, runtime
sys.argv = [sys.argv[0]]
import tests.test_bl_topo_pack as T
H.fresh()
datarec.FORCE_SCOPE[0] = "EVERYTHING"
ob = H.make_mesh(300_000, name="E")
datarec.set_tracked(ob, True)
datarec.finish_operation("baseline")
H.enter(ob, "EDIT")
datarec.finish_operation("edit")
bm = bmesh.from_edit_mesh(ob.data)
for v in bm.verts:
    v.select = v.index % 50 == 0
bmesh.update_edit_mesh(ob.data)
st = runtime._STATES[runtime.obj_uid(ob)]
res = {"packed": [], "unpacked": []}
for r in range(16):
    for mode in (("packed", "unpacked") if r % 2 else ("unpacked", "packed")):
        if mode == "packed":
            runtime.pack_idle_states(wait=True)
            assert st.is_packed()
        else:
            _ = st.arrays
            assert not st.is_packed()
        bpy.ops.mesh.vertices_smooth(factor=0.3)
        t = time.perf_counter(); T._end_operator("MESH_OT_vertices_smooth", 10 + 2 * r + (mode == "packed")); dt = time.perf_counter() - t
        res[mode].append(dt * 1000)
print("AB " + json.dumps({k: {"min": round(min(v), 2), "med": round(statistics.median(v), 2)} for k, v in res.items()}))
H.finish(0)
