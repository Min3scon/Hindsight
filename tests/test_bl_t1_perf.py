"""Tier 1 gate, cost and memory (gate item PERF), headless.

Recording cost (the work after one operation: comparing the recorded objects and the scene, writing
the step), restoring cost (one step of an object's history, a jump across the whole global timeline)
and memory per step (the packed step), on three scenes:
  small   6 objects, all switched on, scene recorded
  medium  60 objects (a third with a constraint), 20 switched on, scene recorded
  heavy   500 objects (a third with a constraint), 50 switched on, scene recorded
plus the Object Mode mesh check (Shade Smooth) on 10k, 100k and 1M vertices.

Budgets are about 3 times the medians measured on the development PC (PERF_LOG.md, "Tier 1"), so a
real slowdown fails while normal machine noise doesn't. Each timing is the median of 5 runs.

Run: blender -b --factory-startup --python tests/test_bl_t1_perf.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import statistics
import time

import bpy

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from hindsight.bl import datarec, idmap, runtime

C = bpy.context
ROWS = ("obj.transforms", "obj.parenting", "obj.constraints", "obj.collections", "obj.addremove", "obj.editsteps")

# ms (recording after one operation), by scene
RECORD_MS = {"small": {"move": 8, "relations": 10, "add": 12},
             "medium": {"move": 15, "relations": 35, "add": 40},
             "heavy": {"move": 45, "relations": 250, "add": 260}}
RESTORE_ONE_MS = 2.0
GLOBAL_JUMP_MS = {"small": 5, "medium": 10, "heavy": 60}
STEP_BYTES = {"move": 512, "constraint": 1024, "add_object": 48_000}
OBJECT_MESH_MS = {10_000: 20, 100_000: 90, 1_000_000: 2000}


def scene(n, tracked):
    H.fresh()
    T.build_scene()
    for i in range(n - 6):
        bpy.ops.mesh.primitive_cube_add(size=0.3, location=((i % 20) * 0.5, (i // 20) * 0.5, -3))
        C.active_object.name = f"N{i}"
        if i % 3 == 0:
            C.active_object.constraints.new("COPY_ROTATION")
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    for o in list(C.scene.objects)[:tracked]:
        datarec.set_tracked(o, True)
    datarec.set_tracked(C.scene, True)
    L.tick_like("baseline")


def timed(op):
    op.run()
    t = time.perf_counter()
    L.tick_like(op.label)
    return (time.perf_counter() - t) * 1000


def median(fn, n=5):
    return statistics.median(fn(k) for k in range(n))


def packed(uid):
    h = runtime.reg().history_for(uid)
    return len(h.entries[-1].to_bytes(runtime.SETTINGS.compression, 0))


SCENES = (("small", 6, 6), ("medium", 60, 20), ("heavy", 500, 50))
RESULTS = {}


@covers(ROWS, "PERF")
def test_recording_and_restoring_cost():
    bad = []
    for label, n, tracked in SCENES:
        scene(n, tracked)
        move = median(lambda k: timed(T.translate("A", (0.05 * (k + 1), 0, 0))))
        rel = median(lambda k: timed(T.parent("B", "A", True)) if k % 2 == 0 else timed(T.unparent("B", "CLEAR")))
        add = median(lambda k: timed(T.add_primitive("cube", (k, -6, 0))))
        h = runtime.reg().history_for(idmap.uid_of(bpy.data.objects["A"]))
        t = time.perf_counter()
        for k in range(20):
            runtime._goto_history(h, len(h.entries) - 1 - (k % 2), True)
        one = (time.perf_counter() - t) * 1000 / 20
        sp = C.scene.hindsight
        sp.use_global = True
        t = time.perf_counter()
        runtime.global_goto(C, 0)
        runtime.global_goto(C, 10 ** 6)
        gj = (time.perf_counter() - t) * 1000 / 2
        sp.use_global = False
        RESULTS[label] = {"move_ms": round(move, 1), "relations_ms": round(rel, 1), "add_ms": round(add, 1),
                          "restore_one_ms": round(one, 3), "global_jump_ms": round(gj, 1)}
        b = RECORD_MS[label]
        for what, got, limit in (("move", move, b["move"]), ("parent / unparent", rel, b["relations"]),
                                 ("add an object", add, b["add"]), ("restore one step", one, RESTORE_ONE_MS),
                                 ("global jump", gj, GLOBAL_JUMP_MS[label])):
            if got > limit:
                bad.append(f"{label}: {what} {got:.1f} ms > {limit} ms")
    print("PERF", RESULTS)
    assert not bad, bad


@covers(ROWS, "PERF")
def test_memory_per_step():
    scene(6, 6)
    T.translate("A", (1, 0, 0)).run()
    L.tick_like("move")
    au = idmap.uid_of(bpy.data.objects["A"])
    move = packed(au)
    T.add_constraint("A", "COPY_LOCATION").run()
    L.tick_like("constraint")
    con = packed(au)
    T.add_primitive("monkey", (0, -6, 0)).run()
    L.tick_like("add")
    add = packed(idmap.uid_of(C.scene))
    print("BYTES", {"move": move, "constraint": con, "add_object": add})
    assert move <= STEP_BYTES["move"] and con <= STEP_BYTES["constraint"] and add <= STEP_BYTES["add_object"], \
        (move, con, add)


@covers(["obj.transforms", "obj.editsteps"], "PERF")
def test_object_mode_mesh_check_cost():
    bad, got = [], {}
    for n, limit in OBJECT_MESH_MS.items():
        H.fresh()
        ob = H.make_mesh(n, name="M")
        datarec.FORCE_SCOPE[0] = "EVERYTHING"
        datarec.set_tracked(ob, True)
        T.select("M")
        times = []
        for k in range(3):
            (bpy.ops.object.shade_smooth if k % 2 == 0 else bpy.ops.object.shade_flat)()
            t = time.perf_counter()
            L.tick_like("shade")
            times.append((time.perf_counter() - t) * 1000)
        got[n] = round(statistics.median(times), 1)
        assert len(runtime.history_of(ob).entries) == 3
        if got[n] > limit:
            bad.append(f"{n} vertices: {got[n]} ms > {limit} ms")
    print("MESH", got)
    assert not bad, bad


for fn in (test_recording_and_restoring_cost, test_memory_per_step, test_object_mode_mesh_check_cost):
    H.run(fn)
H.finish()
