"""Tier 1: changes Hindsight didn't see, headless.

D46: changes made while Record everything was off for an object, while its history was paused, or
in a session without Hindsight weren't recorded: switching back on, resuming or reopening gave a
history whose positions match no real state. Now they become one step (values and mesh) when
recording resumes; on opening, values changed outside Hindsight become one step, and an object whose
mesh changed outside Hindsight (which the history can't rebuild) has its history left out with a
message instead of being played back onto the wrong mesh. Saving and reopening normally, in every
mode, never raises that alarm.

R060: the mesh fingerprints saved are xxh3-128 ("x3:" + 32 hex digits). A file saved by a build before
R060 holds blake2b values without a tag: opened, it behaves exactly as it did (the mesh is hashed the
way the value was made). A tag this build doesn't know is never taken as a change.

Run: blender -b --factory-startup --python tests/test_bl_t1_outside.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import contextlib
import re
import tempfile

import bpy

import hindsight
import tests.harness as H
from tests import oracle as O
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.core.types import SETTINGS
from hindsight.bl import datarec, idmap, runtime
from hindsight.bl import prefs

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


def test_excluded_by_collection_filtering():
    """D54: an object excluded by Collection Filtering records nothing (it used to record object
    steps, not its mesh); when the filter no longer excludes it, what changed meanwhile is one step,
    so every position of its history is a state that existed."""
    setup()
    sh = C.scene.hindsight
    a = bpy.data.objects["A"]
    states = [state()]
    op(T.move_to_collection("A", "Props"))
    states.append(state())
    h = runtime.history_of(a)
    n = len(h.entries)
    sh.filter_mode = "BLACKLIST"
    sh.filter_collections.add().collection = bpy.data.collections["Props"]
    try:
        for o in (T.translate("A", (0, 2, 0)), T.resize("A", 2.0), T.apply_transform("A", "scale")):
            op(o)
            states.append(state())
        assert len(h.entries) == n, "something was recorded for an excluded object"
    finally:
        sh.filter_mode = "NONE"
    L.tick_like("filter changed")
    labels = [e.meta.get("label") for e in h.entries]
    assert labels[-1] == datarec.CHANGES_WHILE_EXCLUDED and len(labels) == n + 1, labels
    op(T.rotate("A", 0.3))
    states.append(state())
    every_position_existed("A", states)


def test_hidden_in_another_view_layer():
    """D56: hidden (H) is per view layer. A hidden in the first view layer, then the window shows a
    second view layer (A visible there) and A is moved: the move is one step holding only the move
    (it also recorded A as shown, read in the second layer), and going back over it leaves A hidden
    in the first layer and shown in the second. Going back over the hiding shows A in both."""
    setup()
    vl1 = C.scene.view_layers[0]
    vl2 = C.scene.view_layers.new("Second")
    a = bpy.data.objects["A"]
    with C.temp_override(view_layer=vl1):
        a.hide_set(True)
        L.tick_like("hide A")
    h = runtime.history_of(a)
    n = len(h.entries)
    with C.temp_override(view_layer=vl2):
        a.location.x += 1.0
        L.tick_like("move A")
        assert len(h.entries) == n + 1, [e.meta.get("label") for e in h.entries]
        keys = {(r.adapter, p) for r in h.entries[-1].load().records for p in r.changes}
        assert keys == {("rna", "location")}, keys
        runtime._goto_history(h, n, True)
        assert a.hide_get(view_layer=vl1) and not a.hide_get(view_layer=vl2)
        runtime._goto_history(h, n - 1, True)
        assert not a.hide_get(view_layer=vl1) and not a.hide_get(view_layer=vl2)
        runtime._goto_history(h, n + 1, True)
        assert a.hide_get(view_layer=vl1) and not a.hide_get(view_layer=vl2)


def blake2b_print(ob):
    """The fingerprint a build before R060 saved: meshio.fingerprint(..., algo="blake2b"), no tag."""
    return runtime.mesh_fingerprint(ob, "blake2b")


@contextlib.contextmanager
def saved_prints_as(make):
    """Saves inside the block write make(ob) as each recorded mesh's fingerprint (the form another build
    wrote) instead of this build's value."""
    real = runtime.saved_prints

    def other_build():
        out = real()
        for u, rec in out.items():
            if isinstance(rec, dict) and "mesh" in rec:
                rec["mesh"] = make(idmap.find(u) or runtime.find_object(u))
        return out
    runtime.saved_prints = other_build
    try:
        yield
    finally:
        runtime.saved_prints = real


def loaded_prints(form):
    """The fingerprints the opened file holds (runtime.reg().saved) all have this form."""
    vals = [r["mesh"] for r in runtime.reg().saved.values() if isinstance(r, dict) and "mesh" in r]
    assert vals and all(re.fullmatch(form, v) for v in vals), (form, vals)


OLD_FORM, X3_FORM = r"[0-9a-f]{32}", r"x3:[0-9a-f]{32}"


def save_and_change_outside(path, change, keep_backup=True, prints=None):
    """Save with Hindsight, then without it change the file and save again, then open it with
    Hindsight. keep_backup=False: Blender's backup copy (.blend1, D107) is removed first. prints: the
    first save writes prints(ob) as each mesh's fingerprint (see saved_prints_as)."""
    SETTINGS.save_mode = "SIDECAR"
    T.select()
    with saved_prints_as(prints) if prints else contextlib.nullcontext():
        bpy.ops.wm.save_as_mainfile(filepath=path)
    hindsight.unregister()
    H._registered[0] = False
    change()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    outside = state()
    if not keep_backup and os.path.isfile(path + "1"):
        os.remove(path + "1")
    hindsight.register()
    H._registered[0] = True
    bpy.ops.wm.open_mainfile(filepath=path)
    prefs.FORCE["record_scope"] = "EVERYTHING"
    return outside


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


def test_mesh_changed_without_hindsight():
    setup()
    for o in (T.translate("A", (1, 0, 0)), T.edit_ops("A", 2)[0], T.leave_edit("A")):
        op(o)
    path = os.path.join(tempfile.mkdtemp(prefix="hs_outside_"), "outside_mesh.blend")

    outside = save_and_change_outside(path, move_a_vertex, keep_backup=False)
    a = bpy.data.objects["A"]
    assert runtime.history_of(a) is None, "a history that can't match the mesh is left out"
    assert any(runtime.OUTSIDE_MESH_TEXT.format(name="A") == t for _s, t in runtime.load_notes), runtime.load_notes
    assert state() == outside


def bridged_case_states():
    """A fresh scene with three steps on A; the mesh at every position, the cursor left at the end."""
    setup()
    for o in (T.translate("A", (1, 0, 0)), T.edit_ops("A", 2)[0], T.leave_edit("A")):
        op(o)
    states = [state()]
    h = runtime.history_of(bpy.data.objects["A"])
    for pos in range(len(h.entries) + 1):
        runtime._goto_history(h, pos, True)
        states.append(state())
    runtime._goto_history(h, len(h.entries), True)
    return states


def move_a_vertex():
    a = bpy.data.objects["A"]
    a.data.vertices[0].co.x += 1.0
    a.data.update()


def test_mesh_changed_without_hindsight_bridged_by_the_backup():
    """D107: Blender's backup copy (.blend1) holds the mesh exactly as Hindsight saved it, so the change
    made without Hindsight becomes one step and the history goes on; every position existed."""
    states = bridged_case_states()
    path = os.path.join(tempfile.mkdtemp(prefix="hs_outside_"), "outside_bridged.blend")

    outside = save_and_change_outside(path, move_a_vertex)
    a = bpy.data.objects["A"]
    h = runtime.history_of(a)
    assert h is not None and h.entries[-1].load().label == runtime.OUTSIDE_LABEL, "one step for the change"
    assert state() == outside
    every_position_existed("A", states + [outside])


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
        prefs.FORCE["record_scope"] = "EVERYTHING"
        after = {h.uid: len(h.entries) for h in runtime.reg().histories.values()}
        assert after == before, (mode, before, after)
        assert not any("wasn't running" in t for _s, t in runtime.load_notes), (mode, runtime.load_notes)
    SETTINGS.save_mode = "SIDECAR"


def test_blake2b_prints_saved_normally_never_raise_the_alarm():
    """R060: a file saved by a build before R060 (fingerprints blake2b, no tag) in Object, Sculpt and Edit
    Mode, opened with this build: nothing is left out, no step is added. Saved again by this build, the
    values are "x3:", and opening that is quiet too."""
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
        path = os.path.join(tempfile.mkdtemp(prefix="hs_oldprints_"), f"old_prints_{mode.lower()}.blend")
        with saved_prints_as(blake2b_print):
            bpy.ops.wm.save_as_mainfile(filepath=path)
        for form in (OLD_FORM, X3_FORM):
            bpy.ops.wm.open_mainfile(filepath=path)
            prefs.FORCE["record_scope"] = "EVERYTHING"
            loaded_prints(form)
            after = {h.uid: len(h.entries) for h in runtime.reg().histories.values()}
            assert after == before, (mode, form, before, after)
            assert not any("wasn't running" in t for _s, t in runtime.load_notes), (mode, form, runtime.load_notes)
            bpy.ops.wm.save_as_mainfile(filepath=path)        # this build's values from here on
    SETTINGS.save_mode = "SIDECAR"


def test_mesh_changed_without_hindsight_against_blake2b_prints():
    """R060: as test_mesh_changed_without_hindsight, the file saved by a build before R060."""
    setup()
    for o in (T.translate("A", (1, 0, 0)), T.edit_ops("A", 2)[0], T.leave_edit("A")):
        op(o)
    path = os.path.join(tempfile.mkdtemp(prefix="hs_outside_"), "outside_mesh_old.blend")

    outside = save_and_change_outside(path, move_a_vertex, keep_backup=False, prints=blake2b_print)
    loaded_prints(OLD_FORM)
    a = bpy.data.objects["A"]
    assert runtime.history_of(a) is None, "a history that can't match the mesh is left out"
    assert any(runtime.OUTSIDE_MESH_TEXT.format(name="A") == t for _s, t in runtime.load_notes), runtime.load_notes
    assert state() == outside


def test_bridged_by_the_backup_against_blake2b_prints():
    """R060: as test_mesh_changed_without_hindsight_bridged_by_the_backup, the file saved by a build
    before R060: the backup copy's mesh is hashed with blake2b too, matches, and the change is one step."""
    states = bridged_case_states()
    path = os.path.join(tempfile.mkdtemp(prefix="hs_outside_"), "outside_bridged_old.blend")

    outside = save_and_change_outside(path, move_a_vertex, prints=blake2b_print)
    loaded_prints(OLD_FORM)
    a = bpy.data.objects["A"]
    h = runtime.history_of(a)
    assert h is not None and h.entries[-1].load().label == runtime.OUTSIDE_LABEL, "one step for the change"
    assert any(runtime.OUTSIDE_BRIDGED_TEXT.format(name="A") == t for _s, t in runtime.load_notes), runtime.load_notes
    assert state() == outside
    every_position_existed("A", states + [outside])


def test_an_unknown_tag_is_never_a_change():
    """R060: a fingerprint with a tag this build doesn't know (written by a newer build) is taken as
    unchanged: the history is kept and nothing is said, also when the mesh did change (it can't be told)."""
    for changed in (False, True):
        setup()
        for o in (T.translate("A", (1, 0, 0)), T.edit_ops("A", 2)[0], T.leave_edit("A")):
            op(o)
        n = len(runtime.history_of(bpy.data.objects["A"]).entries)
        path = os.path.join(tempfile.mkdtemp(prefix="hs_outside_"), f"unknown_tag_{changed}.blend")

        def change():
            if changed:
                a = bpy.data.objects["A"]
                a.data.vertices[0].co.x += 1.0
                a.data.update()
        outside = save_and_change_outside(path, change, keep_backup=False, prints=lambda ob: "zz:" + "0" * 32)
        loaded_prints(r"zz:0{32}")
        h = runtime.history_of(bpy.data.objects["A"])
        assert h is not None and len(h.entries) == n, (changed, h and [e.meta.get("label") for e in h.entries])
        assert not any("wasn't running" in t for _s, t in runtime.load_notes), (changed, runtime.load_notes)
        assert state() == outside


for fn in (test_record_everything_switched_off_and_on, test_paused_and_resumed, test_values_changed_without_hindsight,
           test_mesh_changed_without_hindsight, test_mesh_changed_without_hindsight_bridged_by_the_backup,
           test_saving_normally_never_raises_the_alarm,
           test_excluded_by_collection_filtering, test_hidden_in_another_view_layer,
           test_blake2b_prints_saved_normally_never_raise_the_alarm, test_mesh_changed_without_hindsight_against_blake2b_prints,
           test_bridged_by_the_backup_against_blake2b_prints, test_an_unknown_tag_is_never_a_change):
    H.run(fn)
H.finish()
