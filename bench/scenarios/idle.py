"""Idle cost: what the add-on's timers burn while nothing happens, with 1 and 50 recorded objects.

    <bpy python> bench/scenarios/idle.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Scenarios:
  idle_60s_1obj, idle_60s_50obj   CPU time (K.cpu: process_time; wall time on Windows, ms) of 60 simulated seconds of our timers
        (runtime.tick, which also runs the data recorder and camera ticks, and slider_prop's range
        timer): each timer function is called, in virtual time, at the interval it returned last.
        Object Mode, nothing changing. The samples are repeated simulations. extra: timer calls.
  depsgraph_handler_1obj, depsgraph_handler_50obj   runtime.on_depsgraph with a geometry update
        of the active object in Object Mode, ms per 1,000 calls (CPU)
Recorded objects: small meshes (2,000 vertices) with 3 steps each; one of them active.
Blender's own timer scheduling is not included. --quick: fewer repeats.
"""
import heapq
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

opts = K.Opts()
K.use_root(opts.root)
import bpy  # noqa: E402
import tests.harness as H  # noqa: E402
from hindsight.bl import runtime, slider_prop  # noqa: E402

K.import_checked(opts.root)
BL = K.load_bench_module("bench_bl")
C = bpy.context


def world(nobj):
    H.fresh()
    obs = []
    for i in range(nobj):
        ob = BL.make_mesh(2000, name=f"X{i}")
        ob.location.x = 3 + i
        H.enter(ob, "SCULPT")
        for s in range(3):
            H.fake_stroke(ob, s + 1)
            assert runtime.commit(ob) is not None
        H.enter(ob, "OBJECT")
        obs.append(ob)
    if runtime.packer is not None:
        runtime.packer.drain(60)
    return obs


def simulate(seconds=60.0):
    """Call each timer at the interval it returned; returns (cpu ms, number of calls)."""
    heap = [(0.5, 0, runtime.tick), (0.2, 1, slider_prop._timer)]
    heapq.heapify(heap)
    n = 0
    t0 = K.cpu()
    while heap[0][0] <= seconds:
        due, k, fn = heapq.heappop(heap)
        nxt = fn()
        n += 1
        if nxt is not None:
            heapq.heappush(heap, (due + nxt, k, fn))
    return (K.cpu() - t0) * 1000, n


class _U:
    def __init__(self, idv):
        self.id = idv
        self.is_updated_geometry = True
        self.is_updated_transform = False


def depsgraph_cost(ob, n=1000):
    class DG:
        updates = [_U(ob), _U(ob.data)]
    dg = DG()
    t0 = K.cpu()
    for _ in range(n):
        runtime.on_depsgraph(C.scene, dg)
    ms = (K.cpu() - t0) * 1000
    runtime._DIRTY.clear()
    runtime._STROKE_SEEN.clear()
    return ms


results = {}
for nobj in (1, 50):
    names = (f"idle_60s_{nobj}obj", f"depsgraph_handler_{nobj}obj")
    if not opts.want(*names):
        continue
    try:
        obs = world(nobj)
        for _ in range(3):
            runtime.tick()
        if opts.want(names[0]):
            cpu, calls = [], 0
            for _ in range(opts.n(7, 3)):
                c, calls = simulate()
                cpu.append(c)
            results[names[0]] = K.stats(cpu, timer_calls=calls, recorded_objects=nobj, simulated_s=60)
        if opts.want(names[1]):
            ts = [depsgraph_cost(obs[0]) for _ in range(opts.n(7, 3))]
            results[names[1]] = K.stats(ts, calls_per_sample=1000, recorded_objects=nobj)
    except Exception as e:
        import traceback
        traceback.print_exc()
        for n in names:
            results.setdefault(n, {"error": f"{type(e).__name__}: {e}"[:300]})
K.emit(results, quick=opts.quick, blender=bpy.app.version_string)
