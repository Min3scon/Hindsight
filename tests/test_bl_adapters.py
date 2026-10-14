"""Data adapters inside Blender (headless): recording, playback, files, ids, scope, time budget.

Run: blender -b --factory-startup --python tests/test_bl_adapters.py (or tests/run_tests.py).
Operation boundaries and Blender's undo need the real interface: see tests_ui scenarios.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import math
import random
import struct
import tempfile
import time

import bpy
import numpy as np

import tests.harness as H
from hindsight.bl import adapters, datarec, idmap, runtime
from hindsight.core import persist
from hindsight.core.types import DATA, DELTA

C = bpy.context
F = struct.Struct("<f")


def xform(ob):
    return ([*ob.location], ob.rotation_mode, [*ob.rotation_euler], [*ob.rotation_quaternion], [*ob.scale],
            [*ob.delta_location])


def same_bits(a, b):
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(same_bits(x, y) for x, y in zip(a, b))
    if isinstance(a, float):
        return F.pack(a) == F.pack(b)
    return a == b


def setup(scope="EVERYTHING"):
    H.fresh()
    datarec.FORCE_SCOPE[0] = scope
    ob = H.make_mesh(800, name="A")
    bpy.ops.object.select_all(action="DESELECT")
    C.view_layer.objects.active = ob
    ob.select_set(True)
    return ob


def move(ob, rng):
    ob.location = [float(x) for x in rng.standard_normal(3)]
    ob.rotation_euler = [float(x) for x in rng.standard_normal(3)]
    ob.scale = [float(x) for x in rng.uniform(0.5, 2.0, 3)]
    if rng.random() < 0.2:
        ob.rotation_mode = "QUATERNION" if ob.rotation_mode != "QUATERNION" else "XYZ"
    if rng.random() < 0.2:
        ob.delta_location = (float(rng.random()), -0.0, 1e-38)


def test_records_transforms_and_plays_back():
    ob = setup()
    datarec.set_tracked(ob, True)
    rng = np.random.default_rng(1)
    states = [xform(ob)]
    mesh = [H.snapshot(ob)]
    uid = runtime.obj_uid(ob)
    for i in range(30):
        if i % 4 == 3:                                   # a sculpt stroke now and then
            H.enter(ob, "SCULPT")
            H.fake_stroke(ob, i)
            assert H.commit(ob) is not None
            H.enter(ob, "OBJECT")
        else:
            move(ob, rng)
            steps = datarec.commit(label="Move", uids=[uid])
            assert len(steps) == 1 and steps[0].kind == DATA, steps
        states.append(xform(ob))
        mesh.append(H.snapshot(ob))
    h = runtime.history_of(ob)
    assert len(h.entries) == 30 and h.cursor == 30, len(h.entries)
    kinds = [e.kind for e in h.entries]
    assert kinds.count("DATA") == 23 and kinds.count(DELTA) == 7, kinds
    order = list(range(31))
    random.Random(4).shuffle(order)
    for pos in order + [0, 30, 15]:
        runtime.goto(C, pos)
        assert h.cursor == pos
        assert same_bits(xform(ob), states[pos]), (pos, xform(ob), states[pos])
        H.assert_same(H.snapshot(ob), mesh[pos], f"mesh at {pos}")
    # nothing is recorded by the jumps themselves
    assert not datarec.commit(uids=[uid]) and len(h.entries) == 30


def test_packed_and_saved():
    ob = setup()
    datarec.set_tracked(ob, True)
    rng = np.random.default_rng(2)
    uid = runtime.obj_uid(ob)
    states = [xform(ob)]
    for i in range(12):
        move(ob, rng)
        datarec.commit(label="Move", uids=[uid])
        states.append(xform(ob))
    h = runtime.history_of(ob)
    for e in h.entries:
        e.pack("zlib")
    for pos in (0, 12, 5, 7, 1):
        runtime.goto(C, pos)
        assert same_bits(xform(ob), states[pos]), pos
    runtime.goto(C, 12)
    d = tempfile.mkdtemp(prefix="hs_adapt_")
    path = os.path.join(d, "a.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    side = persist.sidecar_file(path)
    with open(side, "rb") as f:
        assert persist.registry_from_bytes(f.read()).load_report["format"] == 4
    bpy.ops.wm.open_mainfile(filepath=path)
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    ob = bpy.data.objects["A"]
    C.view_layer.objects.active = ob
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 12 and uid in runtime.reg().everything
    for pos in (0, 6, 12):
        runtime.goto(C, pos)
        assert same_bits(xform(ob), states[pos]), ("after reload", pos)


def test_scope_and_opt_in():
    ob = setup(scope="SCULPT")
    datarec.set_tracked(ob, True)                       # switched on, but the preference says sculpt only
    ob.location = (1, 2, 3)
    assert not datarec.commit(uids=[runtime.obj_uid(ob)])
    assert runtime.history_of(ob) is None
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    other = H.make_mesh(300, name="B")
    other.location = (5, 5, 5)                          # never switched on
    assert not datarec.commit(uids=[idmap.claim(other)])
    datarec.set_tracked(ob, False)
    ob.location = (0, 0, 0)
    assert not datarec.commit(uids=[runtime.obj_uid(ob)])


def test_ids_survive_copies():
    ob = setup()
    u = idmap.claim(ob)
    dup = ob.copy()
    C.scene.collection.objects.link(dup)
    assert dup.get(idmap.UID_PROP) == u                 # Blender copied the property
    u2 = idmap.claim(dup)
    assert u2 != u and idmap.claim(ob) == u and idmap.find(u) == ob and idmap.find(u2) == dup
    mat = bpy.data.materials.new("M")
    um = idmap.claim(mat)
    assert idmap.find(um) == mat


def test_capture_budget():
    """The generic adapter must read an object's transforms well inside its budget."""
    setup()
    obs = [H.make_mesh(50, name=f"O{i}", location=(i, 0, 0)) for i in range(200)]
    a = adapters.ADAPTERS["rna"]
    for ob in obs:
        a.capture(ob)
    t = time.perf_counter()
    for ob in obs:
        a.capture(ob)
    per = (time.perf_counter() - t) * 1000 / len(obs)
    print(f"  rna capture: {per:.3f} ms per object (budget {a.budget_ms} ms)")
    assert per < a.budget_ms, per


def test_animation_is_not_recorded():
    """Animated and driven properties change with the frame, not the user: never recorded."""
    ob = setup()
    datarec.set_tracked(ob, True)
    uid = runtime.obj_uid(ob)
    ob.keyframe_insert("location", frame=1)
    ob.location = (3, 2, 1)
    ob.keyframe_insert("location", frame=20)
    fc = ob.driver_add("scale", 0)
    fc.driver.expression = "frame / 10"
    datarec.baseline(ob, uid, refresh=True)
    for f in (1, 5, 12, 20):
        C.scene.frame_set(f)
        assert not datarec.commit(uids=[uid]), f
    assert "location" not in adapters.ADAPTERS["rna"].capture(ob)
    assert "scale" not in adapters.ADAPTERS["rna"].capture(ob)
    ob.rotation_euler = (0.5, 0, 0)                      # a property that isn't animated still records
    steps = datarec.commit(uids=[uid])
    assert len(steps) == 1 and set(steps[0].records[0].changes) == {"rotation_euler"}, steps


def test_awkward_values_round_trip():
    ob = setup()
    datarec.set_tracked(ob, True)
    uid = runtime.obj_uid(ob)
    vals = [(-0.0, 0.0, 1e-45), (math.inf, -math.inf, 3.4028234663852886e38), (1e-38, -1e-40, 0.1)]
    stored = []
    for v in vals:
        ob.location = v
        stored.append(list(ob.location))        # what Blender keeps (it clamps infinities to the float range)
        datarec.commit(uids=[uid])
    assert F.pack(stored[0][0]) == F.pack(-0.0) and stored[0][2] != 0.0      # -0 and a denormal survive
    for i in list(range(len(vals))) * 2:
        runtime.goto(C, i + 1)
        assert same_bits(list(ob.location), stored[i]), (i, list(ob.location), stored[i])


def test_rotation_mode_switches():
    """Regression (Step 3B): setting rotation_mode makes Blender convert the rotation, so a jump back
    across a mode switch must rewrite every rotation value, not only the ones that step changed."""
    ob = setup()
    datarec.set_tracked(ob, True)
    uid = runtime.obj_uid(ob)
    rng = np.random.default_rng(7)
    want = [xform(ob)]
    for i in range(40):
        ob.rotation_euler = [float(x) for x in rng.standard_normal(3)]
        if rng.random() < 0.3:
            ob.rotation_mode = "QUATERNION" if ob.rotation_mode != "QUATERNION" else "XYZ"
        if rng.random() < 0.15:
            ob.rotation_mode = "AXIS_ANGLE" if ob.rotation_mode != "AXIS_ANGLE" else "ZYX"
        datarec.commit(uids=[uid])
        want.append(xform(ob))
    n = len(runtime.history_of(ob).entries)
    for pos in list(range(n, -1, -1)) + list(range(n + 1)) + [0, n, n // 2]:
        runtime.goto(C, pos)
        assert same_bits(xform(ob), want[pos]), (pos, xform(ob), want[pos])


for t in (test_records_transforms_and_plays_back, test_rotation_mode_switches, test_packed_and_saved, test_scope_and_opt_in,
          test_ids_survive_copies, test_capture_budget, test_animation_is_not_recorded,
          test_awkward_values_round_trip):
    H.run(t)
datarec.FORCE_SCOPE[0] = None
H.finish()
