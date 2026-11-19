"""Morph History: time per dab (apply_morph_at) on an N-vertex grid sphere, 40 dabs along a path, radius ~3% of verts.
Usage: python morph_dab.py ROOT N [prof]. Prints AB {json}."""
import os, sys, time, statistics, json, cProfile, pstats, io
ROOT = sys.argv[1]; N = int(sys.argv[2]); PROF = len(sys.argv) > 3
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.argv = [sys.argv[0]]
import bpy, numpy as np
from mathutils import Vector
import tests.harness as H
from hindsight.bl import runtime, meshio, morph as BM
from hindsight.core.types import POSITION_KEY
C = bpy.context

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
    me.polygons.foreach_set("loop_start", np.arange(0, faces.size, 4, dtype=np.int32))
    me.polygons.foreach_set("loop_total", np.full(len(faces), 4, np.int32))
    me.update(calc_edges=True)
    ob = bpy.data.objects.new("Big", me); bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob; ob.select_set(True)
    return ob
H.fresh()
ob = grid(N)
H.enter(ob, "SCULPT")
H.fake_stroke(ob, 1, n=200); assert H.commit(ob) is not None
runtime.set_morph_target(C)
for s in (2, 3, 4):
    H.fake_stroke(ob, s, n=N * 20); assert H.commit(ob) is not None
co = meshio.read_channel(ob.data, POSITION_KEY).reshape(-1, 3).astype(np.float64)
nv = len(co)
rng = np.random.default_rng(5)
c0 = co[int(rng.integers(0, nv))]
d = np.sqrt(((co - c0) ** 2).sum(1)); r = float(np.sort(d)[int(nv * 0.03)])
path = [co[int(i)] for i in np.argsort(d)[:: max(1, nv // 4000)][:40]]
ts = []; tb = []; moved = 0
pr = cProfile.Profile()
for rep in range(3):
    t = time.perf_counter(); assert BM.begin(ob), BM.last_message[0]; tb.append((time.perf_counter() - t) * 1000)
    for i, p in enumerate(path):
        t = time.perf_counter()
        if PROF: pr.enable()
        moved += BM.apply_morph_at(ob, Vector(p), r, 0.5)
        if PROF: pr.disable()
        ts.append((time.perf_counter() - t) * 1000)
    BM.end(ob)
print("AB " + json.dumps({"n": nv, "dab_med_ms": round(statistics.median(ts), 2), "dab_min_ms": round(min(ts), 2),
                          "begin_med_ms": round(statistics.median(tb), 2), "moved": moved}))
if PROF:
    s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(14)
    for line in s.getvalue().splitlines():
        if "/" in line or "{" in line: print("  ", line.strip()[:160])
H.finish(0)
