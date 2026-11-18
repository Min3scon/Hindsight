"""Writing recorded constraints back (adapters._write_constraints), headless.

Regression: when one recorded constraint can't be made again (a type this Blender doesn't have), the
stack was zipped with the recorded list, so every later constraint got the values of the one before
it. Each constraint made must get its own values.

Run: blender -b --factory-startup --python tests/test_bl_datarec_fast_writes.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bpy

import tests.harness as H
from hindsight.bl import adapters

C = bpy.context


def _object():
    H.fresh()
    ob = bpy.data.objects.new("o", bpy.data.meshes.new("m"))
    C.scene.collection.objects.link(ob)
    return ob


def test_unknown_constraint_type_leaves_the_others_their_own_values():
    ob = _object()
    want = [{"type": "COPY_LOCATION", "name": "A", "values": {"use_x": False, "influence": 0.25}},
            {"type": "NOT_A_TYPE", "name": "B", "values": {"use_x": True, "influence": 0.5}},
            {"type": "COPY_ROTATION", "name": "C", "values": {"use_x": False, "influence": 0.75}}]
    adapters._write_constraints(ob, want)
    got = [(c.type, c.name, c.use_x, round(c.influence, 3)) for c in ob.constraints]
    assert got == [("COPY_LOCATION", "A", False, 0.25), ("COPY_ROTATION", "C", False, 0.75)], got


def test_matching_stack_is_only_given_its_values():
    ob = _object()
    a = ob.constraints.new("COPY_LOCATION")
    a.name = "A"
    b = ob.constraints.new("COPY_ROTATION")
    b.name = "B"
    ptrs = [c.as_pointer() for c in ob.constraints]
    want = [{"type": "COPY_LOCATION", "name": "A", "values": {"influence": 0.1}},
            {"type": "COPY_ROTATION", "name": "B", "values": {"influence": 0.9}}]
    adapters._write_constraints(ob, want)
    assert [c.as_pointer() for c in ob.constraints] == ptrs      # the stack wasn't rebuilt
    assert [round(c.influence, 3) for c in ob.constraints] == [0.1, 0.9]


for fn in (test_unknown_constraint_type_leaves_the_others_their_own_values,
           test_matching_stack_is_only_given_its_values):
    H.run(fn)
H.finish()
