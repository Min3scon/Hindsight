"""D145: Dyntopo strokes are steps (preference "Record Dyntopo", off by default; meshes up to
runtime.DYNTOPO_TRIS_MAX faces). While Dyntopo is on the mesh is a BMesh: a commit writes it back
(ed.flush_edits) and compares everything; going back leaves Sculpt Mode, writes the mesh, comes back and
turns Dyntopo on again (Blender 4.2 doesn't by itself).

Brush strokes, Detail Flood Fill, Symmetrize and the mesh filter crash or do nothing headless, so the
changes here are turning Dyntopo on (it triangulates the mesh) and edits made in Object Mode between two
visits with Dyntopo on (a subdivision and a move). Real strokes: tests_ui/scenarios/s_dyntopo.py."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hashlib

import bpy
import bmesh
import numpy as np
import tests.harness as H
from hindsight.bl import meshio, runtime
from hindsight.bl import warnings as W

C = bpy.context


def state(ob):
    me = ob.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    cv = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", cv)
    return (len(me.vertices), len(me.polygons), hashlib.sha1(np.round(co, 5).tobytes() + cv.tobytes()).hexdigest()[:12])


def read(ob):
    """The mesh as Dyntopo has it (written back first)."""
    if runtime.dyntopo_active(ob):
        bpy.ops.ed.flush_edits()
    return state(ob)


def dyntopo(ob, on=True):
    if ob.use_dynamic_topology_sculpting != on:
        bpy.ops.sculpt.dynamic_topology_toggle()
    assert ob.use_dynamic_topology_sculpting == on


def setup():
    H.fresh()
    runtime.FORCE_DYNTOPO[0] = True
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    ob = C.active_object
    ob.name = "Dyn"
    H.enter(ob, "SCULPT")
    runtime._tick_once()
    return ob


def in_object_mode(ob, fn):
    """A change made in Object Mode between two Sculpt Mode visits with Dyntopo on (hidden from Hindsight)."""
    with runtime.suspended():
        bpy.ops.object.mode_set(mode="OBJECT")
        fn(ob.data)
        bpy.ops.object.mode_set(mode="SCULPT")
        dyntopo(ob)


def subdivide(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:12], cuts=1, use_grid_fill=True)
    bm.to_mesh(me)
    bm.free()


def move(me):
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co[2::3] += 0.05
    me.vertices.foreach_set("co", co)
    me.update()


def recorded_session():
    ob = setup()
    states = [read(ob)]
    dyntopo(ob)
    runtime.commit(ob)                       # turning Dyntopo on triangulates the mesh
    states.append(read(ob))
    for fn in (subdivide, move):
        in_object_mode(ob, fn)
        runtime.commit(ob)
        states.append(read(ob))
    return ob, states


def test_off_by_default_pauses_as_before():
    ob = setup()
    runtime.FORCE_DYNTOPO[0] = None
    assert not runtime.dyntopo_on()
    dyntopo(ob)
    assert W.blocking(C, ob), "Dyntopo must still pause recording when the preference is off"
    assert runtime.commit(ob) is None
    from hindsight.bl import prefs
    assert prefs.HINDSIGHT_AP_prefs.__annotations__["record_dyntopo"].keywords["default"] is False
    print("PASS test_off_by_default_pauses_as_before")


def test_each_change_is_a_step_and_exact():
    ob, states = recorded_session()
    assert len(set(states)) == len(states), states
    assert states[1][1] > states[0][1], "Dyntopo didn't triangulate the mesh"
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == len(states) - 1, (h and [e.meta.get("label") for e in h.entries])
    for pos in (len(states) - 1, 0, 2, 1, len(states) - 1, 0):
        runtime.goto(C, pos)
        assert h.cursor == pos
        # before the toggle (position 0) Dyntopo was off: the quads come back and it stays off, as with
        # Blender's own undo; at every later position it is on again (4.2 too)
        assert ob.mode == "SCULPT" and ob.use_dynamic_topology_sculpting == (pos > 0), ("Dyntopo state", pos)
        assert read(ob) == states[pos], (pos, read(ob), states[pos])
    print("PASS test_each_change_is_a_step_and_exact")


def test_a_change_after_going_back_replaces_the_later_steps():
    ob, states = recorded_session()
    runtime.goto(C, 1)
    in_object_mode(ob, move)
    runtime.commit(ob)
    h = runtime.history_of(ob)
    assert len(h.entries) == 2 and h.cursor == 2, (len(h.entries), h.cursor)
    now = read(ob)
    runtime.goto(C, 1)
    assert read(ob) == states[1]
    runtime.goto(C, 2)
    assert read(ob) == now
    print("PASS test_a_change_after_going_back_replaces_the_later_steps")


def test_big_mesh_still_pauses():
    ob = setup()
    old = runtime.DYNTOPO_TRIS_MAX
    runtime.DYNTOPO_TRIS_MAX = 10
    try:
        dyntopo(ob)
        assert W.blocking(C, ob)
        txt = W.TXT_DYNTOPO_BIG.format(n=10)
        assert any(t == txt for _lvl, t in W._errors(C, ob)), W._errors(C, ob)
    finally:
        runtime.DYNTOPO_TRIS_MAX = old
    print("PASS test_big_mesh_still_pauses")


def test_dyntopo_layers_are_not_recorded():
    ob = setup()
    dyntopo(ob)
    bpy.ops.ed.flush_edits()
    snap = meshio.read_snapshot(ob.data, ob=ob)
    assert not [k for k in snap.arrays if ".sculpt_dyntopo" in k], list(snap.arrays)
    print("PASS test_dyntopo_layers_are_not_recorded")


if __name__ == "__main__":
    for t in (test_off_by_default_pauses_as_before, test_each_change_is_a_step_and_exact,
              test_a_change_after_going_back_replaces_the_later_steps, test_big_mesh_still_pauses,
              test_dyntopo_layers_are_not_recorded):
        H.run(t)
    runtime.FORCE_DYNTOPO[0] = None
    H.finish()
