"""Tier 1, Edit Mode on meshes with more than geometry, headless.

D43: restoring a topology step rebuilds the mesh. It used to drop vertex group weights and leave shape
keys with the old number of points (lost for good after the next Edit Mode visit). On an object
recorded with Record everything, weights and shape key positions are part of every step now:
scrubbing over Edit Mode steps that change the topology is exact both ways for them too (checked by
the independent oracle, which reads weights vertex by vertex and every shape key with its settings).

Run: blender -b --factory-startup --python tests/test_bl_t1_meshdata.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from hindsight.bl import idmap, runtime

C = bpy.context


def rich_scene():
    """The usual scene, the grid E with two vertex groups (painted weights), three shape keys
    (one relative to another, one muted) and two materials on alternating faces."""
    L.fresh_scene()
    e = bpy.data.objects["E"]
    n = len(e.data.vertices)
    g1, g2 = e.vertex_groups.new(name="Left"), e.vertex_groups.new(name="Ring")
    for i in range(n):
        if i % 3:
            g1.add([i], (i % 17) / 16.0, 'REPLACE')
        if i % 5 == 0:
            g2.add([i], 0.25 + (i % 7) / 10.0, 'REPLACE')
    e.shape_key_add(name="Basis")
    up = e.shape_key_add(name="Up")
    for k, d in enumerate(up.data):
        d.co.z += 0.1 * (k % 4)
    side = e.shape_key_add(name="Side")
    for d in side.data:
        d.co.x += 0.3
    side.relative_key = up
    side.mute = True
    up.value = 0.4
    for name in ("Red", "Blue"):
        e.data.materials.append(bpy.data.materials.new(name))
    for i, p in enumerate(e.data.polygons):
        p.material_index = i % 2


def edit_sequence():
    ops = T.edit_ops("E", 11)
    by = {o.label.split(": ")[1]: o for o in ops}
    return [by["grab"], by["extrude"], by["bevel"], by["inset"], by["delete"], by["subdivide"], T.leave_edit("E")]


@covers("obj.editsteps", "EXACT", "ORACLE")
def test_weights_and_shape_keys_survive_topology_steps():
    r = L.Run(edit_sequence(), setup=rich_scene)
    e = bpy.data.objects["E"]
    assert e.vertex_groups and e.data.shape_keys is not None
    end = O.object_state(e, derived=False)
    assert len({len(kb.data) for kb in e.data.shape_keys.key_blocks}) == 1
    L.check_object_histories(r, full=True)
    L.check_global(r, full=True)
    h = runtime.reg().history_for(idmap.uid_of(e))
    runtime._goto_history(h, 0, True)
    T.select("E")
    bpy.ops.object.mode_set(mode="EDIT")                # an Edit Mode round trip keeps it all
    bpy.ops.object.mode_set(mode="OBJECT")
    runtime.check_mode_transition()
    runtime._goto_history(h, len(h.entries), True)
    assert O.object_state(e, derived=False) == end, O.diff(end, O.object_state(e, derived=False))[:4]
    # Blender's bevel and inset leave tiny negative weights; they come back clamped (Blender's API
    # takes 0 to 1 only), the sidebar says so, and the clamping is never recorded as a change
    n = len(h.entries)
    L.tick_like("nothing")
    assert len(h.entries) == n, "a restore that clamped weights was recorded as a change"
    from hindsight.bl import warnings as W
    if idmap.uid_of(e) in runtime.WEIGHTS_CLAMPED:
        assert any(t == W.TXT_EV_WEIGHTS for _s, t in W.everything_notes(C, e))


for fn in (test_weights_and_shape_keys_survive_topology_steps,):
    H.run(fn)
H.finish()
