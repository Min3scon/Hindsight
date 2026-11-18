"""Data recorder cost: ROOT N MODE [reps]. MODE: off | one | all. Prints one JSON line.

Times per call (ms, median of reps): idle tick, check_exclusion, on_update (per depsgraph update),
finish_operation with no change, finish after a move, settle commit after a Python change, prime.
Memory: tracemalloc of the recorded values per object after prime.
"""
import sys, os, json, time, statistics, tracemalloc, gc
ROOT = sys.argv[1]
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import bpy
import tests.harness as H
from hindsight.bl import datarec, idmap, runtime

N = int(sys.argv[2]); MODE = sys.argv[3]; REPS = int(sys.argv[4]) if len(sys.argv) > 4 else 7
C = bpy.context
H.fresh()
for i in range(N):
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=((i % 20) * 0.5, (i // 20) * 0.5, 0))
    C.active_object.name = f"N{i}"
    if i % 3 == 0:
        C.active_object.constraints.new("COPY_ROTATION")
obs = list(C.scene.objects)
if MODE == "off":
    datarec.FORCE_SCOPE[0] = "SCULPT"
else:
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    t = time.perf_counter()
    for o in (obs if MODE == "all" else obs[:1]):
        datarec.set_tracked(o, True)
    if MODE == "all":
        datarec.set_tracked(C.scene, True)
    set_ms = (time.perf_counter() - t) * 1000
datarec.finish_operation("baseline")

# on_update timing: wrap it
ON = []
_orig = datarec.on_update
def _wrapped(dg):
    t = time.perf_counter(); _orig(dg); ON.append((time.perf_counter() - t) * 1000)
datarec.on_update = _wrapped


def med(xs):
    return round(statistics.median(xs), 4), round(min(xs), 4)


def timeit(fn, reps=REPS, inner=1):
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        for _ in range(inner):
            fn()
        out.append((time.perf_counter() - t) * 1000 / inner)
    return med(out)


R = {"n": N, "mode": MODE}
datarec._OPS["marker"] = datarec._ops_marker()
R["tick_idle"] = timeit(datarec.tick, inner=200)
R["check_exclusion"] = timeit(datarec.check_exclusion, inner=200)
R["finish_nochange"] = timeit(lambda: datarec.finish_operation("Select"))

a = obs[0]
k = [0]
def move():
    k[0] += 1
    a.location.x += 0.01
    C.view_layer.update()
    datarec.finish_operation("Move")
ON.clear()
R["finish_move"] = timeit(move)
R["on_update"] = med(ON) if ON else None

def settle():
    a.location.y += 0.01
    C.view_layer.update()
    for u in list(datarec._DIRTY):
        datarec._DIRTY[u] -= 10
    datarec._OPS["marker"] = datarec._ops_marker()
    datarec.tick()
R["settle_commit"] = timeit(settle)

def bulk():
    for o in obs:
        o.location.z += 0.01
    C.view_layer.update()
    datarec.finish_operation("Move all")
R["finish_move_all"] = timeit(bulk, reps=3)

# prime as after loading a file
def do_prime():
    for st in runtime._STATES.values():
        st.values = {}
    datarec.forget(); idmap.forget()
    datarec.prime()
R["prime"] = timeit(do_prime, reps=3)

# memory held per tracked object (recorded values + bodies), measured by rebuilding them
gc.collect()
for st in runtime._STATES.values():
    st.values = {}
datarec._BODIES.clear()
tracemalloc.start()
s0 = tracemalloc.take_snapshot()
datarec.prime()
gc.collect()
s1 = tracemalloc.take_snapshot()
tracemalloc.stop()
held = sum(st.size_diff for st in s1.compare_to(s0, "filename"))
nt = max(1, len(datarec.tracked_uids()))
R["held_bytes_per_tracked"] = round(held / nt)
if MODE != "off":
    R["set_tracked_ms"] = round(set_ms, 1)
steps = sum(len(h.entries) for h in runtime.reg().histories.values())
R["steps"] = steps
print("RESULT " + json.dumps(R))
