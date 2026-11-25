"""#113: Create time lapse keys the history, then adds the camera and lights; nothing of them before.

Before it is clicked there is no Hindsight camera or light, no frame handler and no camera and lighting
timer. It switches Movie Mode on, keys the global history over the frames and only then places the
camera (and the key, fill and rim lamps when the scene has no light) for the whole history. Clicking
it again after more work places them for the longer history. The panel's Movie Mode switch can only
switch Movie Mode off.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile
from types import SimpleNamespace

import bpy
import numpy as np

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests import test_bl_camrig_steps as S
from tests.test_bl_camrig_types import check_in_frame
from hindsight.bl import camrig, movie, runtime, ui_panel

C = bpy.context
ROLES = {"camera", "key", "fill", "rim"}


class _Layout:
    """Takes any UILayout call and logs it (test_bl_panels' FakeLayout; that module runs its tests on import)."""
    def __init__(self, log=None):
        self.__dict__["log"] = [] if log is None else log

    def __getattr__(self, name):
        return lambda *a, **kw: (self.log.append((name, a, kw)), _Layout(self.log))[1]


def draw_panel(cls, context, header=False):
    lay = _Layout()
    (cls.draw_header if header else cls.draw)(SimpleNamespace(layout=lay), context)
    return lay


def _lights():
    return sorted(o.name for o in C.scene.objects if o.type == "LIGHT")


def _nothing_yet(msg):
    sc = C.scene
    assert not sc.hindsight.movie_mode, msg
    assert camrig.rig(sc) == {} and sc.camera is None and _lights() == [], (msg, camrig.rig(sc), _lights())
    assert movie.on_frame_change not in bpy.app.handlers.frame_change_post, f"{msg}: frame handler"
    assert not bpy.app.timers.is_registered(camrig._timer), f"{msg}: camera and lighting timer"


def _running(msg):
    assert movie.on_frame_change in bpy.app.handlers.frame_change_post, f"{msg}: frame handler"
    assert bpy.app.timers.is_registered(camrig._timer), f"{msg}: camera and lighting timer"


def _keyed_to(n):
    fc = movie._step_fcurve(C.scene)
    assert fc is not None, "no Step keys"
    assert [round(k.co[1]) for k in fc.keyframe_points] == [0, n], [tuple(k.co) for k in fc.keyframe_points]


def _every_step_in_frame(msg):
    """Played on the timeline, the camera as Create time lapse placed it holds what renders at every
    step, tightly over all; the scene's camera and frame range stay the time lapse's."""
    sc = C.scene
    n = S.glen()
    f0, per = sc.frame_start, sc.hindsight.frames_per_step
    end = sc.frame_end
    assert end >= f0 + n * per, (msg, end, f0 + n * per)
    assert sc.camera is camrig.rig(sc)["camera"], (msg, sc.camera)
    pts = []
    for g in range(n + 1):
        sc.frame_set(f0 + g * per)
        assert runtime.scrub_info(C)["pos"] == g, (msg, g, runtime.scrub_info(C)["pos"])
        assert sc.frame_end == end, (msg, g, sc.frame_end)
        here = S._rendered_points()
        check_in_frame(here, tight=False, msg=f"{msg}: step {g}")
        pts.append(here)
    check_in_frame(np.vstack(pts), tight=True, msg=f"{msg}: every step")


def test_nothing_before_then_everything_placed_for_the_whole_history():
    S._start(("A", (0, 0, 0), (1, 1, 1)))
    S._run(T.translate("A", (0, 0, 1)), T.add_primitive("cube", (12, 0, 0)))   # a scene step too
    S._run(T.translate(C.active_object.name, (0, 4, 0)))     # D181: one added and never changed isn't framed
    _nothing_yet("history made, before Create time lapse")
    assert bpy.ops.hindsight.create_time_lapse() == {'FINISHED'}
    sc = C.scene
    assert sc.hindsight.movie_mode, "Create time lapse plays the time lapse"
    assert set(camrig.rig(sc)) == ROLES, camrig.rig(sc)
    _keyed_to(S.glen())
    _running("after Create time lapse")
    _every_step_in_frame("first time lapse")


def test_again_after_more_work_places_them_for_the_longer_history():
    S._start(("A", (0, 0, 0), (1, 1, 1)))
    S._run(T.translate("A", (0, 0, 1)))
    assert bpy.ops.hindsight.create_time_lapse() == {'FINISHED'}
    sc = C.scene
    sc.hindsight.movie_mode = False                     # back to work
    assert camrig.rig(sc) == {} and _lights() == [], "the time lapse's camera and lighting go with Movie Mode"
    S._run(T.add_primitive("cube", (40, 0, 0)))         # more work, far away
    late = C.active_object.name
    S._run(T.translate(late, (0, 5, 0)))
    assert camrig.rig(sc) == {}, "nothing comes back before Create time lapse is clicked again"
    assert bpy.ops.hindsight.create_time_lapse() == {'FINISHED'}
    assert set(camrig.rig(sc)) == ROLES
    _keyed_to(S.glen())
    _every_step_in_frame("longer time lapse")


def test_the_scenes_own_light_is_left_as_it_is():
    H.fresh()
    sc = C.scene
    lamp = bpy.data.objects.new("MyLamp", bpy.data.lights.new("MyLamp", "POINT"))     # there all along
    sc.collection.objects.link(lamp)
    lamp.location = (3, -4, 5)
    bpy.ops.mesh.primitive_cube_add()
    C.active_object.name = "A"
    L.track_all()
    runtime.set_global(C, True)
    S._run(T.translate("A", (0, 0, 1)), T.add_primitive("cube", (6, 0, 0)))
    C.view_layer.update()

    def state():
        d = lamp.data
        return (_lights(), [tuple(r) for r in lamp.matrix_world], d.type, d.energy, tuple(d.color),
                lamp.hide_render, lamp.hide_viewport, lamp.hide_get())
    before = state()
    assert bpy.ops.hindsight.create_time_lapse() == {'FINISHED'}
    assert set(camrig.rig(sc)) == {"camera"}, camrig.rig(sc)
    assert state() == before, (before, state())


def test_nothing_to_key_adds_nothing():
    H.fresh()
    runtime.set_global(C, True)
    assert bpy.ops.hindsight.create_time_lapse() == {'CANCELLED'}
    _nothing_yet("no history")


def test_a_saved_time_lapse_starts_again_when_loaded():
    tmp = tempfile.mkdtemp(prefix="hs_time_lapse_")
    plain, made = os.path.join(tmp, "plain.blend"), os.path.join(tmp, "made.blend")
    S._start(("A", (0, 0, 0), (1, 1, 1)))
    S._run(T.translate("A", (0, 0, 1)))
    bpy.ops.wm.save_as_mainfile(filepath=plain)
    assert bpy.ops.hindsight.create_time_lapse() == {'FINISHED'}
    bpy.ops.wm.save_as_mainfile(filepath=made)
    bpy.ops.wm.open_mainfile(filepath=plain)
    _nothing_yet("a file without a time lapse")
    bpy.ops.wm.open_mainfile(filepath=made)
    assert C.scene.hindsight.movie_mode and set(camrig.rig(C.scene)) == ROLES
    _running("a file saved with its time lapse")
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def test_panel_switch_only_switches_movie_mode_off():
    H.fresh()
    sc = C.scene
    ctx = SimpleNamespace(scene=sc, mode="OBJECT", region=None, area=None)
    head = draw_panel(ui_panel.VIEW3D_PT_hindsight_movie, ctx, header=True)
    assert head.__dict__.get("enabled") is False, "off: the switch can't switch Movie Mode on"
    body = draw_panel(ui_panel.VIEW3D_PT_hindsight_movie, ctx)
    ops = [a[0] for n, a, kw in body.log if n == "operator"]
    assert "hindsight.create_time_lapse" in ops and "hindsight.quick_auto_key" not in ops, ops
    sc.hindsight.movie_mode = True
    head = draw_panel(ui_panel.VIEW3D_PT_hindsight_movie, ctx, header=True)
    assert head.__dict__.get("enabled", True) is True, "on: the switch switches it off"


if __name__ == "__main__":
    H.run(test_nothing_before_then_everything_placed_for_the_whole_history)
    H.run(test_again_after_more_work_places_them_for_the_longer_history)
    H.run(test_the_scenes_own_light_is_left_as_it_is)
    H.run(test_nothing_to_key_adds_nothing)
    H.run(test_a_saved_time_lapse_starts_again_when_loaded)
    H.run(test_panel_switch_only_switches_movie_mode_off)
    H.finish()
