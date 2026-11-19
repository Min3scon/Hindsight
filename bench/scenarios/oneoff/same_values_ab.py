"""R070 in-process A/B: the data recorder's "nothing changed" check with and without the engine's
same_values, alternating in one process (the scene of bench/scenarios/datarec.py: 500 cubes, a third with
a constraint, all recorded with Record everything).

    <bpy python> bench/scenarios/oneoff/same_values_ab.py CODE_ROOT [ROUNDS]

A = datarec._same_values as before R070 (the marshal compare alone, copied here), B = the current one.
Each round times 5 calls of each case on each side, in the order A B or B A (alternating). Prints one
line "AB {...}": per case the median of the round medians and the best, in ms, for A and B, and the
_same_values share alone (the function timed around its 501 calls in one operation)."""
import json, marshal, os, statistics, sys, time
ROOT = sys.argv[1]
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 15
sys.path.insert(0, ROOT)
import bpy
import tests.harness as H
from hindsight.bl import datarec
assert datarec.__file__.startswith(ROOT), datarec.__file__
C = bpy.context


def before_r070(old, new):
    if len(old) != len(new):
        return False
    try:
        if old.keys() != new.keys():
            return False
        ks = [k for k, v in new.items() if old[k] is not v]
        return marshal.dumps([old[k] for k in ks], 2) == marshal.dumps([new[k] for k in ks], 2)
    except Exception:
        return False


H.fresh()
for i in range(500):
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
datarec._OPS["marker"] = datarec._ops_marker()
a = obs[0]
SIDES = {"A": before_r070, "B": datarec._same_values}


def nochange():
    datarec.finish_operation("Select")


def move():
    a.location.x += 0.01
    C.view_layer.update()
    datarec.finish_operation("Move")


share = []


def timed_share(fn):
    def w(o, n):
        t = time.perf_counter()
        r = fn(o, n)
        share[-1] += time.perf_counter() - t
        return r
    return w


res = {k: {"A": [], "B": []} for k in ("nochange", "move", "same_values_share")}
for r in range(ROUNDS):
    for side in ("AB" if r % 2 == 0 else "BA"):
        datarec._same_values = SIDES[side]
        for name, fn in (("nochange", nochange), ("move", move)):
            ts = []
            for _ in range(5):
                t = time.perf_counter()
                fn()
                ts.append((time.perf_counter() - t) * 1000)
            res[name][side].append(statistics.median(ts))
        datarec._same_values = timed_share(SIDES[side])
        share.append(0.0)
        nochange()
        res["same_values_share"][side].append(share[-1] * 1000)
datarec._same_values = SIDES["B"]
out = {}
for name, d in res.items():
    ma, mb = statistics.median(d["A"]), statistics.median(d["B"])
    out[name] = {"A_med": round(ma, 3), "B_med": round(mb, 3), "A_min": round(min(d["A"]), 3),
                 "B_min": round(min(d["B"]), 3), "ratio_med": round(mb / ma, 3)}
out["rounds"] = ROUNDS
out["load"] = open("/proc/loadavg").read().split()[:3]
print("AB " + json.dumps(out), flush=True)
H.finish(0)
