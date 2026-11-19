import sys, time, json, statistics
ROOT = sys.argv[1]; sys.path.insert(0, ROOT)
import bpy, numpy as np
import tests.harness as H
from hindsight.bl import camrig
H.fresh()
bpy.ops.mesh.primitive_grid_add(x_subdivisions=1000, y_subdivisions=1000)
ob = bpy.context.active_object
m = ob.modifiers.new("D", "SIMPLE_DEFORM"); m.deform_method = "TWIST"; m.angle = 0.5
dg = bpy.context.evaluated_depsgraph_get()
me_e = ob.evaluated_get(dg).data
ev = np.empty(len(me_e.vertices) * 3, np.float32); me_e.vertices.foreach_get("co", ev); ev = ev.reshape(-1, 3)
bs = np.empty(len(ob.data.vertices) * 3, np.float32); ob.data.vertices.foreach_get("co", bs); bs = bs.reshape(-1, 3)
ts = []; val = None
for _ in range(7):
    t = time.perf_counter(); val = camrig._stretch(ob, ev, bs); ts.append((time.perf_counter() - t) * 1000)
print("AB " + json.dumps({"stretch": val, "min_ms": round(min(ts), 1), "med_ms": round(statistics.median(ts), 1)}))
H.finish(0)
