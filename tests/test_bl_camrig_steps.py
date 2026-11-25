"""#112 (23.2): the time lapse camera holds every object at every step of the history: objects moved,
rotated, scaled or reparented, objects added late or deleted midway, and objects that aren't meshes
whose shape changed. Each case is checked at every step of the global timeline: everything that
renders there is inside the frame, and the content reaches the margin at one step at least."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import math

import bpy
import numpy as np

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.test_bl_camrig_types import check_in_frame
from hindsight.core import globaltl
from hindsight.bl import camrig, rigmark, runtime

C = bpy.context


def glen():
    return globaltl.global_length(runtime.reg(), runtime._visible_uids())


def _start(*cubes):
    """Fresh scene, one cube per (name, location, scale), every object recorded, global history on."""
    H.fresh()
    for name, loc, scale in cubes:
        bpy.ops.mesh.primitive_cube_add(location=loc)
        C.active_object.name = name
        C.active_object.scale = scale
    L.track_all()
    runtime.set_global(C, True)


def _run(*ops):
    for op in ops:
        op.run()
        L.tick_like(op.label)


def _rendered_points():
    """World points of everything that renders now (meshes and curves as evaluated)."""
    dg = C.evaluated_depsgraph_get()
    out = []
    for ob in C.scene.objects:
        if rigmark.is_rig(ob) or ob.hide_render or ob.type not in ("MESH", "CURVE"):
            continue
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            co = np.empty(len(me.vertices) * 3, np.float64)
            me.vertices.foreach_get("co", co)
        finally:
            ev.to_mesh_clear()
        m = np.array([list(r) for r in ob.matrix_world], np.float64)
        out.append(co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])
    return np.vstack(out) if out else np.empty((0, 3))


def _where():
    """The scene's own objects, every cursor, the active object, the selection and the mode."""
    act = C.view_layer.objects.active
    return (sorted(o.name for o in C.scene.objects if not rigmark.is_rig(o)),
            [(h.uid, h.cursor) for h in runtime.reg().histories.values()],
            act.name if act else None, sorted(o.name for o in C.selected_objects), C.mode)


def _frame_and_check_every_step():
    """Frame from the end of the history, then check what renders at every step against that camera.
    Framing leaves the scene and every cursor as they were."""
    sc = C.scene
    n = glen()
    assert n >= 2, n
    before = _where()
    sc.hindsight.movie_mode = True                      # makes the camera and frames
    assert _where() == before, (before, _where())
    assert camrig.frame(sc)
    assert _where() == before, (before, _where())
    pts = []
    for g in range(n + 1):
        runtime.global_goto(C, g, True)
        here = _rendered_points()
        check_in_frame(here, tight=False, msg=f"step {g}")
        pts.append(here)
    runtime.global_goto(C, n, True)
    check_in_frame(np.vstack(pts), tight=True, msg="every step")


def test_moved_far_away_and_back():
    _start(("A", (0, 0, 0), (1, 1, 1)))
    _run(T.translate("A", (60, 0, 0)), T.translate("A", (-60, 0, 0)))
    _frame_and_check_every_step()


def test_rotated():
    _start(("A", (0, 0, 0), (6, 0.5, 0.5)))           # a long bar, lying along X, standing up at the end
    _run(T.rotate("A", math.radians(90), "Y"))
    _run(T.translate("A", (0, 0, 1)))
    _frame_and_check_every_step()


def test_scaled_up_and_down():
    _start(("A", (0, 0, 0), (1, 1, 1)))
    _run(T.resize("A", 8.0), T.resize("A", 0.125))
    _frame_and_check_every_step()


def test_reparented():
    """A big cube follows the small parent it is given far away, then is let go back where it was."""
    _start(("A", (0, 0, 0), (3, 3, 3)), ("P", (0, 0, 0), (0.2, 0.2, 0.2)))
    _run(T.parent("A", "P", False), T.translate("P", (40, 0, 0)), T.unparent("A", "CLEAR"))
    assert bpy.data.objects["A"].parent is None
    _frame_and_check_every_step()


def test_added_late():
    _start(("A", (0, 0, 0), (1, 1, 1)))
    _run(T.translate("A", (0, 0, 1)), T.add_primitive("cube", (30, 0, 0)))
    late = C.active_object.name
    _run(T.translate(late, (-30, 3, 0)))
    _frame_and_check_every_step()


def test_added_late_and_sculpted():
    """Framing goes back over its making and forward again: it is still active and in Sculpt Mode."""
    _start(("A", (0, 0, 0), (1, 1, 1)))
    _run(T.translate("A", (0, 0, 1)))
    late = H.make_mesh(400, "Late", (12, 0, 0))
    L.tick_like("add a sphere")
    H.enter(late, "SCULPT")
    H.fake_stroke(late, 1)
    assert H.commit(late) is not None
    _frame_and_check_every_step()


def test_deleted_midway_and_brought_back():
    """Deleted at the end: going back over the deletion brings it back, and it is held there."""
    _start(("A", (0, 0, 0), (1, 1, 1)), ("B", (35, 0, 0), (2, 2, 2)))
    _run(T.translate("A", (0, 0, 1)), T.translate("B", (0, 1, 0)), T.delete(["B"]))
    assert "B" not in C.scene.objects
    _frame_and_check_every_step()
    assert "B" not in C.scene.objects
    runtime.global_goto(C, 1, True)
    assert "B" in C.scene.objects, "going back over the deletion brings it back"


def test_two_objects_100_m_apart():
    """100 m apart at the start, side by side at the end."""
    _start(("A", (0, 0, 0), (1, 1, 1)), ("B", (100, 0, 0), (1, 1, 1)))
    _run(T.translate("B", (-97, 0, 0)), T.translate("A", (0, 2, 0)))
    _frame_and_check_every_step()


def test_a_curve_whose_shape_changed():
    """An object that isn't a mesh: its bevel grows and shrinks back."""
    _start()
    cu = bpy.data.curves.new("Rope", "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(1)
    sp.points[1].co = (4, 0, 0, 1)
    cu.bevel_depth = 0.1
    ob = bpy.data.objects.new("Rope", cu)
    C.scene.collection.objects.link(ob)
    L.track_all()
    for depth in (3.0, 0.1):
        bpy.data.curves["Rope"].bevel_depth = depth
        L.tick_like("bevel")
    _frame_and_check_every_step()


def test_the_movie_never_walks_the_history():
    """A frame of the movie that takes the user's light away makes the lamps again and places them:
    from that step only, never by walking the history inside the frame change (where a mode switch
    crashes Blender) or a render. One jump per frame (#104's scene)."""
    from hindsight.bl import datarec
    H.fresh()
    sc = C.scene
    light = bpy.data.objects.new("Light", bpy.data.lights.new("Light", "POINT"))
    sc.collection.objects.link(light)
    datarec.record_file_default()
    bpy.data.objects.remove(light)
    L.tick_like("Delete")
    H.make_mesh(1200, "A")
    L.tick_like("add A")
    runtime.set_global(C, True)
    sh = sc.hindsight
    sh.movie_camera = sh.movie_lighting = True
    sh.movie_mode = True
    sh.movie_step = runtime.scrub_info(C)["len"]
    sh.frames_per_step = 1
    sc.frame_set(sc.frame_start)
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}
    jumps = []
    real = runtime.global_goto
    runtime.global_goto = lambda *a, **k: (jumps.append(a[1]), real(*a, **k))[1]
    try:
        for f in (sc.frame_start + 2, sc.frame_start, sc.frame_start + 1):
            jumps.clear()
            sc.frame_set(f)
            assert set(camrig.LIGHT_ROLES) & set(camrig.rig(sc)) or camrig.scene_has_own_light(sc), f
            assert len(jumps) <= 1, (f, jumps)
    finally:
        runtime.global_goto = real


if __name__ == "__main__":
    H.run(test_moved_far_away_and_back)
    H.run(test_rotated)
    H.run(test_scaled_up_and_down)
    H.run(test_reparented)
    H.run(test_added_late)
    H.run(test_added_late_and_sculpted)
    H.run(test_deleted_midway_and_brought_back)
    H.run(test_two_objects_100_m_apart)
    H.run(test_a_curve_whose_shape_changed)
    H.run(test_the_movie_never_walks_the_history)
    H.finish()
