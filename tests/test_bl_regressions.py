"""Regression tests for bugs found in real Blender (beta 2 job). Each test names the bug it pins."""
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.harness as H  # noqa: E402

import bpy  # noqa: E402

from hindsight.bl import guard, runtime, scrubber  # noqa: E402


def test_handlers_survive_file_open():
    """Beta 1: recorder handlers were not @persistent, so File > Open silently stopped recording."""
    H.fresh()
    ob = H.make_mesh(500)
    path = os.path.join(tempfile.mkdtemp(prefix="hs_reg_"), "f.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    assert all(runtime.handlers_installed().values()), runtime.handlers_installed()
    bpy.ops.wm.read_homefile(use_empty=True)
    assert all(runtime.handlers_installed().values()), runtime.handlers_installed()


def test_handlers_reinstalled_if_removed():
    """Another add-on clearing bpy.app.handlers must not stop recording for good."""
    H.fresh()
    bpy.app.handlers.depsgraph_update_post.remove(runtime.on_depsgraph)
    assert runtime.ensure_handlers() == ["depsgraph_update_post"]
    assert all(runtime.handlers_installed().values())


def _region(type, x, y, w, h, align):
    return SimpleNamespace(type=type, x=x, y=y, width=w, height=h, alignment=align)


def test_slider_clears_asset_shelf_and_side_panels():
    """Beta 1: in Sculpt Mode on Blender 4.3+ the slider was drawn under the brush asset shelf."""
    win = _region("WINDOW", 2, 87, 884, 787, "NONE")
    area = SimpleNamespace(regions=[
        win,
        _region("ASSET_SHELF", 2, 87, 884, 59, "BOTTOM"),
        _region("ASSET_SHELF_HEADER", 2, 146, 884, 24, "BOTTOM"),
        _region("TOOLS", 2, 170, 56, 652, "LEFT"),
        _region("UI", 614, 170, 272, 652, "RIGHT"),
        _region("HUD", 58, 170, 1, 1, "FLOAT"),
    ])
    left, right, bottom = scrubber.region_insets(area, win)
    assert bottom == 170 - 87, bottom
    assert left == 56 and right == 2 + 884 - 614, (left, right)
    lay = scrubber.layout(win.width, win.height, 1.0, None, (left, right, bottom))
    x0, y0, x1, y1 = lay["bar"]
    assert y0 >= bottom, lay["bar"]
    assert lay["dot"][0] - lay["dot"][2] > left, lay["dot"]
    assert x1 < win.width - right, lay["bar"]
    # hidden regions (1 px) are ignored
    area.regions[1].height = area.regions[2].height = 1
    assert scrubber.region_insets(area, win)[2] == 0


def test_slider_shows_empty_track_before_first_stroke():
    """Beta 1: the slider only appeared after the first recorded step, so new users saw nothing."""
    H.fresh()
    ob = H.make_mesh(300)
    H.enter(ob, "SCULPT")
    info = scrubber.slider_info(bpy.context)
    assert info is not None and info.get("empty") and info["len"] == 0, info
    H.enter(ob, "OBJECT")
    assert scrubber.slider_info(bpy.context) is None  # Object Mode without history: nothing drawn
    H.enter(ob, "SCULPT")
    runtime.turn_off(bpy.context, ob)
    assert scrubber.slider_info(bpy.context) is None  # recording paused: no empty track


def test_guard_switches_off_one_feature_and_logs_once():
    guard.reset()
    calls = []

    @guard.guarded("overlay")
    def boom():
        calls.append(1)
        raise RuntimeError("x")
    for _ in range(5):
        boom()
    assert calls == [1], calls          # after the first failure the feature is off
    assert [n for n, *_ in guard.failed()] == ["overlay"]
    assert guard.ok("recorder")
    guard.reset("overlay")
    assert guard.ok("overlay")


def test_registration_survives_a_broken_module():
    """A module that fails to register must not take the sidebar tab down with it."""
    import importlib
    import hindsight
    H.fresh()
    hindsight.unregister()
    real = importlib.import_module("hindsight.bl.movie").register

    def broken():
        raise RuntimeError("injected")
    importlib.import_module("hindsight.bl.movie").register = broken
    try:
        hindsight.register()
        assert [n for n, _, _ in hindsight._errors] == ["movie"], hindsight._errors
        assert hasattr(bpy.types, "VIEW3D_PT_hindsight")
        assert hasattr(bpy.types, "HINDSIGHT_OT_self_check")
    finally:
        importlib.import_module("hindsight.bl.movie").register = real
        hindsight.unregister()
        guard.reset()
        hindsight.register()
        H._registered[0] = True


def test_reversing_change_is_recorded_not_mistaken_for_undo():
    """Round C, beta 2: filling a mask and then clearing it was taken for a native undo of the
    fill, so the clear was never recorded and later jumps showed the wrong mask."""
    H.fresh()
    ob = H.make_mesh(800)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    H.commit(ob)
    me = ob.data
    a = me.attributes.new(".sculpt_mask", "FLOAT", "POINT")
    a.data.foreach_set("value", [0.4] * len(me.vertices))
    me.update()
    H.commit(ob)                               # fill mask
    me.attributes.remove(me.attributes[".sculpt_mask"])   # Blender removes the layer on clear
    me.update()
    H.commit(ob)                               # clear mask: a new step, not an undo
    h = runtime.history_of(ob)
    assert len(h.entries) == 3 and h.cursor == 3, (len(h.entries), h.cursor)
    runtime.goto(bpy.context, 2)
    assert ".sculpt_mask" in me.attributes
    runtime.goto(bpy.context, 3)
    assert ".sculpt_mask" not in me.attributes


def test_edit_mode_alone_starts_no_history():
    """Round E, beta 2: a file saved in Edit Mode got an 'Edit Mode' step (and a history) as soon
    as the user left Edit Mode, without editing anything."""
    H.fresh()
    ob = H.make_mesh(600)
    H.enter(ob, "EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.transform.translate(value=(0.05, 0, 0))   # even a real edit does not start one
    H.enter(ob, "OBJECT")
    assert runtime.history_of(ob) is None, "Edit Mode must not start a history"
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 3)
    H.commit(ob)
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 1, "the first stroke starts it, with one step"


for fn in (test_edit_mode_alone_starts_no_history, test_reversing_change_is_recorded_not_mistaken_for_undo, test_handlers_survive_file_open, test_handlers_reinstalled_if_removed,
           test_slider_clears_asset_shelf_and_side_panels, test_slider_shows_empty_track_before_first_stroke,
           test_guard_switches_off_one_feature_and_logs_once, test_registration_survives_a_broken_module):
    H.run(fn)
H.finish()
