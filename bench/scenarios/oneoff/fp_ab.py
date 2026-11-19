import sys, time, json, statistics
ROOT = sys.argv[1]; sys.path.insert(0, ROOT)
import bpy
import tests.harness as H
from hindsight.bl import meshio, runtime
H.fresh()
bpy.ops.mesh.primitive_grid_add(x_subdivisions=1000, y_subdivisions=1000)
ob = bpy.context.active_object
def t(fn):
    s = time.perf_counter(); fn(); return (time.perf_counter() - s) * 1000
first_fp = t(lambda: runtime.mesh_fingerprint(ob))
fps = [t(lambda: runtime.mesh_fingerprint(ob)) for _ in range(6)]
snaps = [t(lambda: meshio.read_snapshot(ob.data)) for _ in range(5)]
print("AB " + json.dumps({"fingerprint_first_ms": round(first_fp, 1), "fingerprint_med_ms": round(statistics.median(fps), 1),
                          "read_snapshot_med_ms": round(statistics.median(snaps), 1)}))
H.finish(0)
