"""Tier 1: changes Hindsight didn't see, headless.

D46: changes made while Record everything was off for an object, while its history was paused, or
in a session without Hindsight weren't recorded: switching back on, resuming or reopening gave a
history whose positions match no real state. Now they become one step (values and mesh) when
recording resumes; on opening, values changed outside Hindsight become one step, and an object whose
mesh changed outside Hindsight (which the history can't rebuild) has its history left out with a
message instead of being played back onto the wrong mesh. Saving and reopening normally, in every
mode, never raises that alarm.

Run: blender -b --factory-startup --python tests/test_bl_t1_outside.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile

import bpy

import hindsight
import tests.harness as H
from tests import oracle as O
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from hindsight.core.types import SETTINGS
from hindsight.bl import datarec, idmap, runtime

C = bpy.context


def setup():
    L.fresh_scene()
    L.track_all()
    runtime.reg().preserve_modes = False


def op(o):
    o.run()
    L.tick_like(o.label)


def state(name="A"):
    return O.object_state(bpy.data.objects[name], derived=False)


def every_position_existed(name, states):
    h = runtime.history_of(bpy.data.objects[name])
    for pos in range(len(h.entries) + 1):
        runtime._goto_history(h, pos, True)
        assert state(name) in states, f"{name} at position {pos} is a state that never existed"
    runtime._goto_history(h, len(h.entries), True)


@covers("obj.transforms", "STEPS", "EXACT")
def test_record_everything_switched_off_and_on():
    setup()
    a = bpy.data.objects["A"]
    states = [state()]
    for o in (T.translate("A", (1, 0, 0)), T.resize("A", 1.5)):
        op(o)
        states.append(state())
    datarec.set_tracked(a, False)
    for o in (T.translate("A", (0, 3, 0)), T.apply_transform("A", "scale")):
        op(o)
        states.append(state())
    datarec.set_tracked(a, True)
    L.tick_like("switched on")
    op(T.rotate("A", 0.5))
    states.append(state())
    labels = [e.meta.get("label") for e in runtime.history_of(a).entries]
    assert labels[2] == datarec.CHANGES_WHILE_OFF and len(labels) == 4, labels
    every_position_existed("A", states)


@covers("obj.transforms", "STEPS", "EXACT")
def test_paused_and_resumed():
    setup()
    a = bpy.data.objects["A"]
    states = [state()]
    for o in (T.translate("A", (1, 0, 0)), T.resize("A", 1.5)):
        op(o)
        states.append(state())
    runtime.turn_off(C, a)
    for o in (T.translate("A", (0, 3, 0)), T.apply_transform("A", "scale")):
        op(o)
        states.append(state())
    runtime.turn_on(C, a)
    L.tick_like("resumed")
    op(T.rotate("A", 0.5))
    states.append(state())
    every_position_existed("A", states)


def save_and_change_outside(path, change):
    """Save with Hindsight, then without it change the file and save again, then open it with
    Hindsight."""
    SETTINGS.save_mode = "SIDECAR"
    T.select()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    hindsight.unregister()
    H._registered[0] = False
    change()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    outside = state()
    hindsight.register()
    H._registered[0] = True
    bpy.ops.wm.open_mainfile(filepath=path)
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    return outside


@covers("obj.transforms", "RELOAD.SIDECAR", "WARN")
def test_values_changed_without_hindsight():
    setup()
    for o in (T.translate("A", (1, 0, 0)), T.resize("A", 1.5)):
        op(o)
    path = os.path.join(tempfile.mkdtemp(prefix="hs_outside_"), "outside.blend")

    def change():
        T.select("A")
        bpy.ops.transform.translate(value=(0, 5, 0))
    outside = save_and_change_outside(path, change)
    a = bpy.data.objects["A"]
    assert state() == outside
    h = runtime.history_of(a)
    assert [e.meta.get("label") for e in h.entries][-1] == runtime.OUTSIDE_LABEL
    runtime._goto_history(h, 0, True)
    runtime._goto_history(h, len(h.entries), True)
    assert state() == outside, "back to the start and forward again keeps the change made outside Hindsight"


@covers("obj.editsteps", "RELOAD.SIDECAR", "WARN")
def test_mesh_changed_without_hindsight():
    setup()
    for o in (T.translate("A", (1, 0, 0)), T.edit_ops("A", 2)[0], T.leave_edit("A")):
        op(o)
    path = os.path.join(tempfile.mkdtemp(prefix="hs_outside_"), "outside_mesh.blend")

    def change():
        a = bpy.data.objects["A"]
        a.data.vertices[0].co.x += 1.0
        a.data.update()
    outside = save_and_change_outside(path, change)
    a = bpy.data.objects["A"]
    assert runtime.history_of(a) is None, "a history that can't match the mesh is left out"
    assert any(runtime.OUTSIDE_MESH_TEXT.format(name="A") == t for _s, t in runtime.load_notes), runtime.load_notes
    assert state() == outside


@covers("obj.transforms", "RELOAD.SIDECAR")
@covers("obj.editsteps", "RELOAD.EMBED")
def test_saving_normally_never_raises_the_alarm():
    """Saved and reopened with Hindsight in Object, Sculpt and Edit Mode (with a sculpt history, Edit
    Mode steps and recorded values): nothing is left out, no step is added."""
    for mode, save_mode in (("OBJECT", "SIDECAR"), ("SCULPT", "EMBED"), ("EDIT", "BOTH")):
        setup()
        op(T.translate("A", (1, 0, 0)))
        sculpt = H.make_mesh(800, name="Clay")
        H.enter(sculpt, "SCULPT")
        H.fake_stroke(sculpt, 3)
        H.commit(sculpt)
        H.enter(sculpt, "OBJECT")
        for o in T.edit_ops("E", 4)[:2]:
            op(o)
        T.select()
        target = {"OBJECT": None, "SCULPT": "Clay", "EDIT": "E"}[mode]
        if target:
            T.select(target)
            bpy.ops.object.mode_set(mode=mode)
        before = {h.uid: len(h.entries) for h in runtime.reg().histories.values()}
        SETTINGS.save_mode = save_mode
        path = os.path.join(tempfile.mkdtemp(prefix="hs_normal_"), f"normal_{mode.lower()}.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        datarec.FORCE_SCOPE[0] = "EVERYTHING"
        after = {h.uid: len(h.entries) for h in runtime.reg().histories.values()}
        assert after == before, (mode, before, after)
        assert not any("wasn't running" in t for _s, t in runtime.load_notes), (mode, runtime.load_notes)
    SETTINGS.save_mode = "SIDECAR"


for fn in (test_record_everything_switched_off_and_on, test_paused_and_resumed, test_values_changed_without_hindsight,
           test_mesh_changed_without_hindsight, test_saving_normally_never_raises_the_alarm):
    H.run(fn)
H.finish()
