"""Movie Mode: the recorder ignores Hindsight's own camera and lights (INTERFACES.md section 9).

Objects marked with rigmark.RIG_PROP are never recorded, never get `_hindsight_uid`, never appear in
a step, and the global history never removes or re-creates them. An unmarked camera added the same
way is the control: it is recorded, goes away and comes back.

Run: blender -b --factory-startup --python tests/test_bl_rig_ignored.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile
from types import SimpleNamespace

import bpy

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.core import globaltl, persist
from hindsight.core.types import SETTINGS
from hindsight.bl import datarec, idmap, log, rigmark, runtime, ui_panel

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hs_rig_ignored_")


def setup():
    L.fresh_scene()
    L.track_all()


def rig(role):
    """One of Hindsight's own Movie Mode objects, made the way camrig makes them (bpy.data only)."""
    if role == "camera":
        name = "Hindsight Camera"
        data = bpy.data.cameras.new(name)
    else:
        name = "Hindsight " + role.capitalize()
        data = bpy.data.lights.new(name, "SUN")
    ob = bpy.data.objects.new(name, data)
    C.scene.collection.objects.link(ob)
    rigmark.mark(ob, role)
    ob.location = (1.5, -7.25, 3.0) if role == "camera" else (-4.0, 2.0, 6.5)
    ob.rotation_euler = (1.1, 0.0, 0.3)
    return ob


def control_camera():
    ob = bpy.data.objects.new("Control Cam", bpy.data.cameras.new("Control Cam"))
    C.scene.collection.objects.link(ob)
    ob.location = (0.0, -9.0, 1.0)
    return ob


def step_count():
    return sum(len(h.entries) for h in runtime.reg().histories.values())


def glen():
    return globaltl.global_length(runtime.reg(), runtime._visible_uids())


def no_uid(ob):
    return idmap.UID_PROP not in ob.keys()


def state(name):
    ob = bpy.data.objects.get(name)
    if ob is None or name not in C.scene.objects:
        return None
    return (ob.name, tuple(tuple(round(v, 6) for v in r) for r in ob.matrix_world),
            ob.data.name if ob.data is not None else None, rigmark.role_of(ob), no_uid(ob))


def all_steps_text():
    out = []
    for h in runtime.reg().histories.values():
        for e in h.entries:
            st = e.load()
            out.append(repr(st.records) + repr(sorted((st.extra or {}).keys())) + repr(e.meta))
    return "\n".join(out)


def test_added_rig_records_nothing():
    setup()
    n0 = step_count()
    cam, key = rig("camera"), rig("key")
    L.tick_like("add Movie Mode objects")
    assert step_count() == n0, "adding Hindsight's own objects is not a step"
    assert no_uid(cam) and no_uid(key), "they never get a Hindsight id"
    ctrl = control_camera()                       # the same thing, unmarked: recorded
    L.tick_like("add a camera")
    assert step_count() == n0 + 1, "an ordinary camera added the same way is a step"
    assert not no_uid(ctrl)
    assert not datarec.is_tracked(cam) and not datarec.is_tracked(key)
    assert datarec.is_tracked(ctrl), "an ordinary object added while the scene is recorded is recorded"


def test_global_history_keeps_rig():
    setup()
    rig("camera"), rig("key")
    L.tick_like("add Movie Mode objects")
    want = {n: state(n) for n in ("Hindsight Camera", "Hindsight Key")}
    control_camera()
    L.tick_like("add a camera")
    T.add_primitive("cube", (0, -5, 0)).run()
    L.tick_like("add a cube")
    T.translate("Cube", (1, 0, 0)).run()
    L.tick_like("move the cube")
    a = bpy.data.objects["A"]
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 7)
    assert H.commit(a) is not None, "sculpting A is recorded"
    H.enter(a, "OBJECT")
    n = glen()
    assert n >= 4, n
    ctrl_end = state("Control Cam")
    assert ctrl_end is not None
    for g in list(range(n, -1, -1)) + list(range(1, n + 1)):
        runtime.global_goto(C, g, True)
        for name, w in want.items():
            assert state(name) == w, (g, name, state(name), w)
        if g == 0:
            assert state("Control Cam") is None, "the recorded camera goes away at the start"
            assert "Cube" not in C.scene.objects
    assert state("Control Cam") == ctrl_end, "the recorded camera comes back as it was"
    assert "Cube" in C.scene.objects


def test_rig_never_in_a_step():
    setup()
    rig("camera"), rig("key"), rig("fill")
    L.tick_like("add Movie Mode objects")
    control_camera()
    L.tick_like("add a camera")
    T.add_primitive("cube", (0, -5, 0)).run()
    L.tick_like("add a cube")
    for name in ("Hindsight Camera", "Hindsight Key", "Hindsight Fill"):
        bpy.data.objects[name].location.x += 1.0
    T.translate("Cube", (1, 0, 0)).run()
    L.tick_like("move the cube")
    text = all_steps_text()
    assert "Control Cam" in text and "Cube" in text, "control: recorded objects are in the steps"
    assert "Hindsight " not in text, "no step mentions Hindsight's own objects"
    for name in ("Hindsight Camera", "Hindsight Key", "Hindsight Fill"):
        assert no_uid(bpy.data.objects[name]), name
    objs = datarec._state(idmap.uid_of(C.scene)).values.get(("scene", "objects"))
    assert objs is not None and None not in objs and len(objs) == len(set(objs))
    assert len(objs) == len([o for o in C.scene.objects if not rigmark.is_rig(o)]), objs


def test_set_tracked_refuses_rig():
    setup()
    cam = rig("camera")
    said = []
    orig = log.warn
    log.warn = lambda text: said.append(text)
    try:
        datarec.set_tracked(cam, True)
    finally:
        log.warn = orig
    assert said == [datarec.RIG_TEXT], said
    assert not datarec.is_tracked(cam) and no_uid(cam)
    assert not any(idmap.find(u) == cam for u in runtime.reg().everything)


def test_rig_is_excluded():
    setup()
    cam = rig("camera")
    assert runtime.is_excluded(cam) is True
    assert runtime.history_of(cam) is None
    assert runtime.can_record(cam) is False
    # a marked mesh (the mark is what counts, not the type): no sculpt history either
    m = bpy.data.objects["A"]
    datarec.set_tracked(m, False)
    rigmark.mark(m, "fill")
    assert runtime.is_excluded(m) is True and runtime.can_record(m) is False
    H.enter(m, "SCULPT")
    H.fake_stroke(m, 3)
    assert H.commit(m) is None, "no sculpt step for a marked object"
    H.enter(m, "OBJECT")
    assert runtime.history_of(m) is None
    # control: the unmarked twin is recorded
    b = bpy.data.objects["B"]
    assert runtime.is_excluded(b) is False and runtime.can_record(b) is True


def test_deleting_rig_records_nothing():
    setup()
    cam, key = rig("camera"), rig("key")
    L.tick_like("add Movie Mode objects")
    n0 = step_count()
    bpy.data.objects.remove(cam)
    bpy.data.objects.remove(key)
    L.tick_like("Movie Mode off")
    assert step_count() == n0, "removing Hindsight's own objects is not a step"


def test_copy_of_rig_is_ignored():
    setup()
    rig("camera")
    L.tick_like("add Movie Mode objects")
    n0 = step_count()
    T.duplicate("Hindsight Camera", False).run()
    L.tick_like("duplicate")
    copy = C.active_object
    assert copy is not None and copy.name != "Hindsight Camera" and rigmark.is_rig(copy)
    assert step_count() == n0 and no_uid(copy), "a copy keeps the mark and is ignored like the original"


class _Layout:
    def __init__(self, log=None):
        self.__dict__["log"] = log if log is not None else []

    def __setattr__(self, k, v):
        self.__dict__[k] = v

    def __getattr__(self, name):
        def fn(*args, **kw):
            self.log.append((name, args, kw))
            return _Layout(self.log)
        return fn

    def text(self):
        return " ".join(kw.get("text", "") for n, _a, kw in self.log if n == "label")


def test_sidebar_says_hindsight_own():
    setup()
    cam = rig("camera")
    ctx = SimpleNamespace(scene=C.scene, active_object=cam, view_layer=C.view_layer,
                          preferences=C.preferences, window_manager=C.window_manager, region=None, area=None)
    for scope in ("EVERYTHING", "SCULPT"):
        datarec.FORCE_SCOPE[0] = scope
        for units in (None, 13):
            lay = _Layout()
            ui_panel.draw_main(lay, ctx, units)
            t = lay.text()
            assert "Movie Mode" in t and "Hindsight's own" in t, (scope, t)
            assert "Collection Filtering" not in t and "Select a mesh" not in t, (scope, t)
            assert not any(n == "operator" and a and a[0] == "hindsight.record_everything"
                           for n, a, _kw in lay.log), "no switch to record it"
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    assert len(datarec.RIG_TEXT.split()) <= 40


def test_rig_ignored_after_reload():
    setup()
    rig("camera"), rig("key")
    L.tick_like("add Movie Mode objects")
    T.add_primitive("cube", (0, -5, 0)).run()
    L.tick_like("add a cube")
    want = {n: state(n) for n in ("Hindsight Camera", "Hindsight Key")}
    n_before = glen()
    path = os.path.join(TMP, "rig.blend")
    SETTINGS.save_mode = "SIDECAR"
    T.select()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    assert os.path.isdir(persist.sidecar_dir(path))
    bpy.ops.wm.open_mainfile(filepath=path)
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    assert glen() == n_before, "the history came back with the file"
    for name, w in want.items():
        assert state(name) == w, (name, state(name), w)
    n0 = step_count()
    bpy.data.objects["Hindsight Camera"].location.z += 2.0
    L.tick_like("move the Movie Mode camera")
    assert step_count() == n0, "still ignored after the reload"
    want["Hindsight Camera"] = state("Hindsight Camera")
    for g in (0, n_before):
        runtime.global_goto(C, g, True)
        for name, w in want.items():
            assert state(name) == w, (g, name, state(name), w)
        assert ("Cube" in C.scene.objects) == (g > 0), g
    assert "Cube" in C.scene.objects


if __name__ == "__main__":
    for fn in (test_added_rig_records_nothing, test_global_history_keeps_rig, test_rig_never_in_a_step,
               test_set_tracked_refuses_rig, test_rig_is_excluded, test_deleting_rig_records_nothing,
               test_copy_of_rig_is_ignored, test_sidebar_says_hindsight_own, test_rig_ignored_after_reload):
        H.run(fn)
    H.finish()
