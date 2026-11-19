"""R071 A/B: a texture paint stroke on a 2K float image (67 MB of pixels) and a sculpt stroke on a 1M mesh,
committed (runtime.commit), with the engine's diff on one thread or by its own rule (two threads from 4 MB).

    PYTHONPATH=<engine folder> <bpy python> bench/scenarios/oneoff/diff_threads_ab.py CODE_ROOT [ROUNDS] [--inproc]

Separate processes (alternate the two roots): one line "AB {...}" with the median over ROUNDS (8) of
  tex2k_commit, tex2k_diff     the commit of one texture stroke, and the time inside the engine's diff in it
  m1_commit, m1_diff           the same for one stroke on a 1M-vertex mesh (bench_bl.Stroker)
--inproc (needs delta.DIFF_THREADS, R071): each round runs both settings, one thread (A) and the engine's
rule (B), in alternating order, and the line has A_ and B_ of each value. Times in ms."""
import json, os, statistics, sys, time
ROOT = sys.argv[1]
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 8
INPROC = "--inproc" in sys.argv
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, "bench", "scenarios"))
import bpy
import numpy as np
import tests.harness as H
import _common as K
import hindsight_native
from hindsight.bl import runtime
from hindsight.core import delta
assert runtime.__file__.startswith(ROOT), runtime.__file__
BL = K.load_bench_module("bench_bl")
C = bpy.context

spent = [0.0]
_real_diff = delta._N.diff


class _Timed:
    """delta._N with diff timed (the rest passed through)."""
    def __getattr__(self, name):
        return getattr(hindsight_native, name)

    @staticmethod
    def diff(*a):
        t = time.perf_counter()
        try:
            return _real_diff(*a)
        finally:
            spent[0] += time.perf_counter() - t


delta._N = _Timed()


def drain():
    if runtime.packer is not None:
        runtime.packer.drain(60)


H.fresh()
# the 2K texture: as tests/test_bl_texture_paint.py, side 2048, float pixels
canvas = H.make_mesh(800, name="Canvas")
mat = bpy.data.materials.new("Paint")
mat.use_nodes = True
canvas.data.materials.append(mat)
img = bpy.data.images.new("Tex", 2048, 2048, alpha=True, float_buffer=True)
mat.node_tree.nodes.new("ShaderNodeTexImage").image = img
runtime.FORCE_TEXTURE[0] = True
H.enter(canvas, "TEXTURE_PAINT")
px = np.empty(2048 * 2048 * 4, np.float32)
img.pixels.foreach_get(px)
rng = np.random.default_rng(71)


def tex_stroke():
    """A dab of 160 x 160 pixels somewhere on the image, then the commit."""
    a = px.reshape(2048, 2048, 4)
    y, x = (int(v) for v in rng.integers(0, 2048 - 160, 2))
    a[y:y + 160, x:x + 160] = rng.random((160, 160, 4), dtype=np.float32)
    img.pixels.foreach_set(px)
    img.update()
    drain()
    spent[0] = 0.0
    t = time.perf_counter()
    assert runtime.commit(canvas) is not None
    return (time.perf_counter() - t) * 1000, spent[0] * 1000


tex_stroke()                                   # the first stroke reads the baseline
H.enter(canvas, "OBJECT")
mesh = BL.make_mesh(1_000_000, name="M")
H.enter(mesh, "SCULPT")
sk = BL.Stroker(mesh)


def m1_stroke():
    sk.stroke()
    drain()
    spent[0] = 0.0
    t = time.perf_counter()
    assert runtime.commit(mesh) is not None
    return (time.perf_counter() - t) * 1000, spent[0] * 1000


m1_stroke()
H.enter(mesh, "OBJECT")


def round_of(setting):
    if INPROC:
        delta.DIFF_THREADS[0] = setting
    H.enter(canvas, "TEXTURE_PAINT")
    tc, td = tex_stroke()
    H.enter(mesh, "SCULPT")
    mc, md = m1_stroke()
    H.enter(mesh, "OBJECT")
    return {"tex2k_commit": tc, "tex2k_diff": td, "m1_commit": mc, "m1_diff": md}


sides = {"A": 1, "B": None} if INPROC else {"": None}
got = {s: [] for s in sides}
round_of(None)                                 # warm both paths once
for r in range(ROUNDS):
    order = list(sides) if r % 2 == 0 else list(reversed(sides))
    for s in order:
        got[s].append(round_of(sides[s]))
out = {"engine_api": hindsight_native.API, "rounds": ROUNDS}
for s, rs in got.items():
    for k in rs[0]:
        out[(s + "_" if s else "") + k] = round(statistics.median(r[k] for r in rs), 3)
out["load"] = open("/proc/loadavg").read().split()[:3]
print("AB " + json.dumps(out), flush=True)
H.finish(0)
