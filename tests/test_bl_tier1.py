"""Tier 1 (objects) inside Blender, headless: parenting, constraints, collections, visibility, names,
the per-kind switches, and a random mix of all of them checked at every history position.

Run: blender -b --factory-startup --python tests/test_bl_tier1.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import random
import struct

import bpy
import numpy as np

import tests.harness as H
from hindsight.bl import adapters, datarec, idmap, runtime

C = bpy.context
F = struct.Struct("<f")


def fb(v):
    return tuple(F.pack(x) for x in v)


def state(ob):
    """Everything Tier 1 records on an object, exactly."""
    cons = []
    for c in ob.constraints:
        vals = adapters.struct_values(c)
        cons.append((c.type, c.name, repr(sorted(vals.items()))))
    return (ob.name, ob.parent.name if ob.parent else None, ob.parent_type,
            tuple(fb(r) for r in ob.matrix_parent_inverse), fb(ob.location), fb(ob.rotation_euler), fb(ob.scale),
            tuple(sorted(c.name for c in ob.users_collection)), ob.hide_viewport, ob.hide_render,
            ob.hide_select, ob.hide_get(), tuple(cons))


def setup():
    H.fresh()
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    datarec.FORCE_KINDS[0] = None
    a = H.make_mesh(200, name="A", location=(2, 0, 0))
    b = H.make_mesh(200, name="B", location=(0, 1, 0))
    c = H.make_mesh(200, name="Target", location=(0, 0, 3))
    for o in C.view_layer.objects:
        o.select_set(False)
    C.view_layer.objects.active = b
    datarec.set_tracked(b, True)
    return a, b, c


def rec(ob, label="Change"):
    return datarec.commit(label=label, uids=[runtime.obj_uid(ob)])


def check_all_positions(ob, states, seed=3):
    h = runtime.history_of(ob)
    n = len(h.entries)
    assert n == len(states) - 1, (n, len(states))
    order = list(range(n + 1))
    random.Random(seed).shuffle(order)
    for pos in order + [0, n]:
        C.view_layer.objects.active = find(ob_uid[0])
        runtime.goto(C, pos)
        got = state(find(ob_uid[0]))
        assert got == states[pos], (pos, got, states[pos])


ob_uid = [None]


def find(uid):
    return idmap.find(uid)


def test_parenting():
    a, b, c = setup()
    ob_uid[0] = runtime.obj_uid(b)
    states = [state(b)]
    b.parent = a
    b.matrix_parent_inverse = a.matrix_world.inverted()
    assert rec(b, "Parent")
    states.append(state(b))
    b.parent_type = "OBJECT"
    a.location = (5, 5, 5)                                  # moving the parent isn't a change of B
    assert not rec(b)
    states[-1] = state(b)
    mw = b.matrix_world.copy()
    b.parent = None
    b.matrix_world = mw                                     # clear parent, keep transform
    assert rec(b, "Clear Parent")
    states.append(state(b))
    check_all_positions(b, states)


def test_constraints():
    a, b, c = setup()
    ob_uid[0] = runtime.obj_uid(b)
    states = [state(b)]
    k = b.constraints.new("COPY_LOCATION")
    k.target = c
    assert rec(b, "Add Constraint")
    states.append(state(b))
    k.influence = 0.375
    k.use_offset = True
    assert rec(b, "Influence")
    states.append(state(b))
    t = b.constraints.new("TRACK_TO")
    t.target = a
    t.track_axis = "TRACK_NEGATIVE_Z"
    assert rec(b, "Add Constraint")
    states.append(state(b))
    b.constraints.move(1, 0)
    assert rec(b, "Move Constraint")
    states.append(state(b))
    t.name = "Look"
    assert rec(b, "Rename Constraint")
    states.append(state(b))
    b.constraints.remove(b.constraints["Copy Location"])
    assert rec(b, "Delete Constraint")
    states.append(state(b))
    check_all_positions(b, states)


def test_collections_visibility_names():
    a, b, c = setup()
    ob_uid[0] = runtime.obj_uid(b)
    states = [state(b)]
    coll = bpy.data.collections.new("Props")
    C.scene.collection.children.link(coll)
    coll.objects.link(b)
    assert rec(b, "Link")
    states.append(state(b))
    C.scene.collection.objects.unlink(b)
    assert rec(b, "Move to Collection")
    states.append(state(b))
    b.hide_render = True
    b.hide_select = True
    assert rec(b, "Hide")
    states.append(state(b))
    b.hide_set(True)
    assert rec(b, "Hide in viewport")
    states.append(state(b))
    b.hide_set(False)
    b.name = "Renamed"
    assert rec(b, "Rename")
    states.append(state(b))
    check_all_positions(b, states)


def test_kinds_switch_off_and_on():
    a, b, c = setup()
    uid = runtime.obj_uid(b)
    datarec.FORCE_KINDS[0] = {"transforms"}
    datarec.rebaseline()
    k = b.constraints.new("COPY_ROTATION")
    assert not rec(b), "constraints are switched off"
    b.location = (1, 1, 1)
    steps = rec(b)
    assert len(steps) == 1 and {p for r in steps[0].records for p in r.changes} == {"location"}, steps
    datarec.FORCE_KINDS[0] = None
    datarec.rebaseline()
    assert not rec(b), "switching a kind back on records nothing by itself"
    k.influence = 0.5
    steps = rec(b)
    assert len(steps) == 1 and {p for r in steps[0].records for p in r.changes} == {"constraints"}, steps


def test_random_mix():
    a, b, c = setup()
    ob_uid[0] = runtime.obj_uid(b)
    coll = bpy.data.collections.new("Extra")
    C.scene.collection.children.link(coll)
    rng = np.random.default_rng(9)
    states = [state(b)]
    for i in range(60):
        r = rng.random()
        if r < 0.2:
            b.location = [float(x) for x in rng.standard_normal(3)]
        elif r < 0.3:
            if b.parent is None:
                b.parent = a if rng.random() < 0.5 else c
                b.matrix_parent_inverse = b.parent.matrix_world.inverted()
            else:
                b.parent = None
        elif r < 0.45:
            if len(b.constraints) < 3 or rng.random() < 0.5:
                k = b.constraints.new(str(rng.choice(["COPY_LOCATION", "LIMIT_SCALE", "TRACK_TO"])))
                if hasattr(k, "target"):
                    k.target = c
            else:
                b.constraints.remove(b.constraints[int(rng.integers(len(b.constraints)))])
        elif r < 0.55 and len(b.constraints):
            b.constraints[int(rng.integers(len(b.constraints)))].influence = float(rng.random())
        elif r < 0.65:
            if coll in b.users_collection and len(b.users_collection) > 1:
                coll.objects.unlink(b)
            elif coll not in b.users_collection:
                coll.objects.link(b)
            else:
                continue
        elif r < 0.8:
            b.hide_viewport = not b.hide_viewport
        elif r < 0.9:
            b.name = f"B{i}"
        else:
            b.scale = [float(x) for x in rng.uniform(0.2, 3, 3)]
        if rec(b):
            states.append(state(b))
        else:
            states[-1] = state(b)
    check_all_positions(b, states, seed=11)


for t in (test_parenting, test_constraints, test_collections_visibility_names, test_kinds_switch_off_and_on,
          test_random_mix):
    H.run(t)
datarec.FORCE_SCOPE[0] = None
datarec.FORCE_KINDS[0] = None
H.finish()
