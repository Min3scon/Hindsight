"""R069: what a recorded mesh's baseline holds, before and after its topology is packed."""
import os, sys, time, json
ROOT = sys.argv[1]; KIND = sys.argv[2]; sys.path.insert(0, ROOT); sys.path.insert(1, os.path.join(ROOT, "bench", "scenarios"))
import bpy
import tests.harness as H
import _common as K
BL = K.load_bench_module("bench_bl")
from hindsight.bl import runtime
H.fresh()
if KIND == "grid1m":
    ob = BL.make_mesh(1_000_000, name="M")
else:
    bpy.ops.mesh.primitive_monkey_add()
    ob = bpy.context.active_object
    ob.data.remesh_voxel_size = 0.0045
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.voxel_remesh()
H.enter(ob, "SCULPT"); sk = BL.Stroker(ob)
for i in range(2):
    sk.stroke_commit()
st = runtime._STATES[runtime.obj_uid(ob)]
before = st.nbytes()
can = hasattr(runtime, "pack_idle_states")
t = time.perf_counter()
if can:
    runtime.pack_idle_states(wait=True)
tp = time.perf_counter() - t
after = st.nbytes() if hasattr(st, "nbytes") else before
tu = None
if can and st.is_packed():
    t = time.perf_counter(); _ = st.arrays; tu = time.perf_counter() - t
print("AB " + json.dumps({"mesh": KIND, "vertices": len(ob.data.vertices), "baseline_mb": round(before / 2**20, 1),
                          "packed_baseline_mb": round(after / 2**20, 1), "pack_ms_off_main_thread": round(tp * 1000, 1),
                          "unpack_ms": None if tu is None else round(tu * 1000, 1)}))
H.finish(0)
