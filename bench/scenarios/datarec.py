"""Data recorder cost with 500 objects all recorded (scope Everything; a third have a constraint).

    <bpy python> bench/scenarios/datarec.py CODE_ROOT [--quick] [--only a,b] [--reps N]

Scenarios (ms per call; min and median over reps):
  dr_finish_nochange_500  operator finish (finish_operation) with nothing changed
  dr_finish_move_500      operator finish after moving one object
  dr_tick_idle_500        the recorder's timer tick, idle (200 calls per sample)
  dr_on_update_500        the depsgraph update handler (one call per update; during the move runs)
  dr_prime_500            prime at load: rebuild the recorded values of every object
tracemalloc of the values the recorder holds after prime is in dr_prime_500 (held_bytes_per_tracked).
--quick: 50 objects (names unchanged).
"""
import gc
import os
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as K  # noqa: E402

opts = K.Opts()
K.use_root(opts.root)
import bpy  # noqa: E402
import tests.harness as H  # noqa: E402
from hindsight.bl import datarec, idmap, runtime  # noqa: E402

K.import_checked(opts.root)
C = bpy.context
N = 500 if not opts.quick else 50
REPS = opts.n(9, 3)

H.fresh()
for i in range(N):
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=((i % 20) * 0.5, (i // 20) * 0.5, 0))
    C.active_object.name = f"N{i}"
    if i % 3 == 0:
        C.active_object.constraints.new("COPY_ROTATION")
obs = list(C.scene.objects)
datarec.FORCE_SCOPE[0] = "EVERYTHING"
for o in obs:
    datarec.set_tracked(o, True)
datarec.set_tracked(C.scene, True)
datarec.finish_operation("baseline")

ON = []
_orig = datarec.on_update


def _wrapped(dg):
    t = time.perf_counter()
    _orig(dg)
    ON.append((time.perf_counter() - t) * 1000)


datarec.on_update = _wrapped
datarec._OPS["marker"] = datarec._ops_marker()
a = obs[0]
results = {}


def move():
    a.location.x += 0.01
    C.view_layer.update()
    datarec.finish_operation("Move")


if opts.want("dr_tick_idle_500"):
    K.attempt(results, "dr_tick_idle_500",
              lambda: K.stats(K.time_it(datarec.tick, REPS, inner=200), objects=N))
if opts.want("dr_finish_nochange_500"):
    K.attempt(results, "dr_finish_nochange_500",
              lambda: K.stats(K.time_it(lambda: datarec.finish_operation("Select"), REPS), objects=N))
if opts.want("dr_finish_move_500", "dr_on_update_500"):
    def fm():
        ON.clear()
        ts = K.time_it(move, REPS)
        r = K.stats(ts, objects=N)
        r.update(K.mem_of(move, reps=2))
        if ON:
            results["dr_on_update_500"] = K.stats(ON, objects=N, calls=len(ON))
        return r
    K.attempt(results, "dr_finish_move_500", fm)
    if "dr_on_update_500" not in results and "error" not in results["dr_finish_move_500"]:
        results["dr_on_update_500"] = {"error": "the depsgraph handler never ran"}


def prime_case():
    def do_prime():
        for st in runtime._STATES.values():
            st.values = {}
        datarec.forget()
        idmap.forget()
        datarec.prime()
    ts = K.time_it(do_prime, opts.n(5, 3))
    gc.collect()
    for st in runtime._STATES.values():
        st.values = {}
    datarec._BODIES.clear()
    tracemalloc.start()
    s0 = tracemalloc.take_snapshot()
    datarec.prime()
    gc.collect()
    s1 = tracemalloc.take_snapshot()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    held = sum(s.size_diff for s in s1.compare_to(s0, "filename"))
    nt = max(1, len(datarec.tracked_uids()))
    return K.stats(ts, objects=N, held_bytes_per_tracked=round(held / nt),
                   tracemalloc_mb=round(peak / 2 ** 20, 3))


if opts.want("dr_prime_500"):
    K.attempt(results, "dr_prime_500", prime_case)
K.emit(results, quick=opts.quick, blender=bpy.app.version_string)
