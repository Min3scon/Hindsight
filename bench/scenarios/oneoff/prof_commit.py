import os, sys, time, cProfile, pstats, io
sys.path.insert(0, "/home/user/Hindsight")
import bpy, numpy as np
import tests.harness as H
from hindsight.bl import runtime
from hindsight.core import native
N = int(sys.argv[1]); STEPS = int(sys.argv[2])

def grid(n):
    seg = int(n ** 0.5); rings = n // seg
    u = np.linspace(0, 2*np.pi, seg, endpoint=False); v = np.linspace(0.05, np.pi-0.05, rings)
    uu, vv = np.meshgrid(u, v)
    co = np.stack([np.sin(vv)*np.cos(uu), np.sin(vv)*np.sin(uu), np.cos(vv)], -1).reshape(-1, 3).astype(np.float32)
    idx = np.arange(rings*seg).reshape(rings, seg)
    a = idx[:-1, :]; b = np.roll(idx, -1, 1)[:-1, :]; c = np.roll(idx, -1, 1)[1:, :]; d = idx[1:, :]
    faces = np.stack([a, b, c, d], -1).reshape(-1, 4)
    me = bpy.data.meshes.new("big"); me.vertices.add(len(co)); me.loops.add(faces.size); me.polygons.add(len(faces))
    me.vertices.foreach_set("co", co.ravel()); me.loops.foreach_set("vertex_index", faces.ravel().astype(np.int32))
    me.polygons.foreach_set("loop_start", np.arange(0, faces.size, 4, dtype=np.int32)); me.polygons.foreach_set("loop_total", np.full(len(faces), 4, np.int32))
    me.update(calc_edges=True)
    ob = bpy.data.objects.new("Big", me); bpy.context.scene.collection.objects.link(ob); return ob

H.fresh()
ob = grid(N); H.enter(ob, "SCULPT")
a = ob.data.attributes["position"]; nv = len(a.data); co = np.empty(nv*3, np.float32)
rng = np.random.default_rng(1); k = nv // 50
def stroke():
    a.data.foreach_get("vector", co); s = int(rng.integers(0, nv - k)); co.reshape(-1, 3)[s:s+k] *= 1.002; a.data.foreach_set("vector", co)
for i in range(5): stroke(); runtime.commit(ob)        # warm up
ts = []
pr = cProfile.Profile()
for i in range(STEPS):
    stroke()
    t = time.perf_counter(); pr.enable(); runtime.commit(ob); pr.disable(); ts.append(time.perf_counter() - t)
ts = np.array(ts) * 1000
print(f"native={native.AVAILABLE} verts={nv} commits={STEPS} median={np.median(ts):.3f} ms p90={np.percentile(ts,90):.3f} ms load={os.getloadavg()[0]:.1f}")
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(16); 
for line in s.getvalue().splitlines():
    if "/" in line or "{" in line: print("  ", line.strip()[:150])
H.finish(0)
