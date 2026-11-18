"""Recording one stroke (runtime.commit) at 100k and 1M vertices, strokes of 2% of the vertices,
and the Blender side of a jump (runtime.goto: reading history plus writing the mesh).

    <bpy python> bench/scenarios/commit.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Scenarios: commit_100k, commit_1m (time of runtime.commit, tracemalloc and VmHWM of one commit),
goto_bl_1_100k, goto_bl_300_100k (runtime.goto of 1 and 300 steps in Blender, 100k vertices).
Strokes are simulated (positions written with foreach_set, then commit): sculpt brush operators
cannot run headless. The packer thread is drained before each timed commit, so a commit is not
timed against the packing of the one before.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

opts = K.Opts()
K.use_root(opts.root)
import bpy  # noqa: E402
import tests.harness as H  # noqa: E402
from hindsight.bl import runtime  # noqa: E402
from hindsight.core.types import SETTINGS  # noqa: E402

K.import_checked(opts.root)
BL = K.load_bench_module("bench_bl")
C = bpy.context


def drain():
    if runtime.packer is not None:
        runtime.packer.drain(120)


def commit_case(nv, reps):
    H.fresh()
    SETTINGS.raw_steps = 64
    ob = BL.make_mesh(nv)
    H.enter(ob, "SCULPT")
    sk = BL.Stroker(ob)
    for _ in range(3):                      # warm up: baseline step, first packing, caches
        sk.stroke()
        assert runtime.commit(ob) is not None
    drain()
    ts = []
    for _ in range(reps):
        sk.stroke()
        drain()
        t = time.perf_counter()
        assert runtime.commit(ob) is not None
        ts.append((time.perf_counter() - t) * 1000)
    def one():
        sk.stroke()
        drain()
        return None
    mem = {"tracemalloc_mb": 0.0, "hwm_mb": 0.0}
    for _ in range(max(2, reps // 4)):
        sk.stroke()
        drain()
        with K.Peak() as p:
            assert runtime.commit(ob) is not None
        mem["tracemalloc_mb"] = max(mem["tracemalloc_mb"], round(p.tracemalloc_mb, 3))
        mem["hwm_mb"] = max(mem["hwm_mb"], round(p.hwm_mb, 3))
    return K.stats(ts, vertices=sk.nv, **mem)


def goto_case(nv, steps, jump, reps):
    """runtime.goto over `jump` steps, both ways, from the middle of the history."""
    H.fresh()
    SETTINGS.raw_steps = 64
    ob = BL.make_mesh(nv)
    H.enter(ob, "SCULPT")
    sk = BL.Stroker(ob)
    for i in range(steps):
        sk.stroke()
        assert runtime.commit(ob) is not None
        if i % 128 == 127:
            drain()
    drain()
    h = runtime.history_of(ob)
    lo = max(0, steps // 2 - jump // 2)
    hi = lo + jump
    ts = []
    for _ in range(reps):
        for a, b in ((hi, lo), (lo, hi)):
            if h.cursor != a:
                runtime.goto(C, a)
            t = time.perf_counter()
            runtime.goto(C, b)
            ts.append((time.perf_counter() - t) * 1000)
            assert h.cursor == b
    return K.stats(ts, vertices=nv, steps=steps, jump=jump)


results = {}
q = opts.quick
big, small = (200_000, 20_000) if q else (1_000_000, 100_000)
for name, nv, reps in (("commit_100k", 100_000 if not q else 20_000, opts.n(25, 6)),
                       ("commit_1m", big, opts.n(12, 4))):
    if opts.want(name):
        K.attempt(results, name, lambda nv=nv, reps=reps: commit_case(nv, reps))
for name, jump in (("goto_bl_1_100k", 1), ("goto_bl_300_100k", 300)):
    if opts.want(name):
        steps = 400 if not q else 120
        K.attempt(results, name, lambda j=jump: goto_case(small, steps, min(j, steps // 2), opts.n(6, 3)))
K.emit(results, quick=q, blender=bpy.app.version_string)
