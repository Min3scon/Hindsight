"""L2: "Everything in this file" switches Record everything on for the scene and every local object
in one click (objects added later follow, D36), shows as on only when all of them are, and switches
them all off again. Hindsight's own Movie Mode objects are never switched on."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile
import time
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.bl import datarec, runtime
from hindsight.bl import prefs

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hs_record_file_")    # each run its own folder: suites run side by side


def test_one_switch_for_the_file():
    L.fresh_scene()
    prefs.FORCE["record_scope"] = "EVERYTHING"
    assert not datarec.file_tracked()
    bpy.ops.hindsight.record_file(on=True)
    assert datarec.file_tracked()
    assert datarec.is_tracked(C.scene)
    assert all(datarec.is_tracked(o) for o in bpy.data.objects)
    T.add_primitive("cube", (0, -5, 0)).run()
    L.tick_like("add")
    assert datarec.is_tracked(bpy.data.objects["Cube"]), "added later: recorded too (D36)"
    assert datarec.file_tracked()
    datarec.set_tracked(bpy.data.objects["B"], False)
    assert not datarec.file_tracked(), "one object off: the switch shows off"
    bpy.ops.hindsight.record_file(on=False)
    assert not any(datarec.is_tracked(o) for o in bpy.data.objects) and not datarec.is_tracked(C.scene)
    a = bpy.data.objects["A"]
    bpy.ops.hindsight.record_file(on=True)
    a.location.x += 1
    L.tick_like("move")
    assert runtime.history_of(a) is not None and runtime.history_of(a).entries


def test_switch_is_quick_with_many_objects():
    H.fresh()
    prefs.FORCE["record_scope"] = "EVERYTHING"
    me = bpy.data.meshes.new("M")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    for i in range(300):
        ob = bpy.data.objects.new(f"O{i}", me.copy())
        C.scene.collection.objects.link(ob)
    t = time.perf_counter()
    datarec.set_file_tracked(True)
    dt = time.perf_counter() - t
    assert datarec.file_tracked()
    assert dt < 3.0, dt                 # one walk over the objects, not one per object
    print(f"  300 objects switched on in {dt * 1000:.0f} ms")


def test_collections_made_and_deleted_with_the_file_switch():
    """L13: with the one switch for the file, making a collection, moving an object into it and deleting a
    collection are recorded (in the scene's history) and come back both ways."""
    L.fresh_scene()
    prefs.FORCE["record_scope"] = "EVERYTHING"
    bpy.ops.hindsight.record_file(on=True)
    L.tick_like("baseline")
    sc = C.scene
    read = lambda: (tuple(sorted(c.name for c in sc.collection.children_recursive)),
                    tuple(sorted(c.name for c in bpy.data.objects["A"].users_collection)))
    states = [read()]

    assert "Made" not in bpy.data.collections and "Spare" not in bpy.data.collections

    def make():
        sc.collection.children.link(bpy.data.collections.new("Made"))

    def move():
        a = bpy.data.objects["A"]
        for c in list(a.users_collection):
            c.objects.unlink(a)
        bpy.data.collections["Made"].objects.link(a)

    def extra():
        sc.collection.children.link(bpy.data.collections.new("Spare"))

    def delete():
        bpy.data.collections.remove(bpy.data.collections["Spare"])
    for label, fn in (("make", make), ("move", move), ("extra", extra), ("delete", delete)):
        fn()
        L.tick_like(label)
        states.append(read())
    assert all(x != y for x, y in zip(states, states[1:])), states
    assert runtime.history_of(sc) is not None, "the scene has no history"
    sc.hindsight.use_global = True              # the object's membership and the scene's collections: one timeline
    pos0 = runtime.scrub_info(C)["pos"]
    n = len(states) - 1
    for k in list(range(1, n + 1)) + list(range(n - 1, -1, -1)):
        runtime.goto(C, pos0 - k)
        assert read() == states[n - k], (k, read(), states[n - k])
    sc.hindsight.use_global = False
    bpy.ops.hindsight.record_file(on=False)
    prefs.FORCE["record_scope"] = None
    print("PASS test_collections_made_and_deleted_with_the_file_switch")


def test_a_fresh_install_records_everything_in_a_file():
    """#60: with the default preferences Record is Everything and "Everything in this file" is on in
    a file where nobody switched it off: a change outside sculpting becomes a step, no switch turned on."""
    L.fresh_scene()
    assert prefs.HINDSIGHT_AP_prefs.__annotations__["record_scope"].keywords["default"] == "EVERYTHING"
    path = os.path.join(TMP, "fresh_install", "plain.blend")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    assert datarec.scope_everything()
    assert datarec.file_tracked(), "Everything in this file is on"
    a = bpy.data.objects["A"]
    a.location.x += 1
    L.tick_like("move")
    h = runtime.history_of(a)
    assert h is not None and h.entries, "moving an object is a step"
    a.modifiers.new("Bevel", "BEVEL")
    L.tick_like("add modifier")
    assert len(h.entries) == 2, len(h.entries)


def _stored_prefs():
    """The add-on's entry in Blender's preferences, as an installed Hindsight has it (headless runs
    register Hindsight without one). The caller removes it."""
    a = C.preferences.addons.new()
    a.module = prefs._base_package
    return a


def test_a_stored_sculpting_only_choice_survives_the_update():
    """#61: preferences that store Record = Sculpting only (a user's choice before the update) still
    say Sculpting only after it: opening a file switches nothing on and only sculpt strokes are
    recorded. Preferences that store no Record choice get Everything."""
    L.fresh_scene()
    a = _stored_prefs()
    try:
        prefs.get_prefs().record_scope = "SCULPT"      # what choosing it in the old build stored
        path = os.path.join(TMP, "stored_choice", "plain.blend")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        p = prefs.get_prefs()
        assert p.is_property_set("record_scope") and p.record_scope == "SCULPT", p.record_scope
        assert not datarec.scope_everything() and not datarec.file_tracked()
        ob = bpy.data.objects["A"]
        ob.location.x += 1
        L.tick_like("move")
        h = runtime.history_of(ob)
        assert h is None or not h.entries, "a move is not recorded with Sculpting only"
        H.enter(ob, "SCULPT")
        H.fake_stroke(ob, 1)
        assert H.commit(ob) is not None, "a sculpt stroke is recorded"
        bpy.ops.object.mode_set(mode="OBJECT")
        p.property_unset("record_scope")               # stored preferences without a Record choice
        assert p.record_scope == "EVERYTHING" and datarec.scope_everything()
    finally:
        C.preferences.addons.remove(a)


def test_a_file_saved_with_everything_switched_off_opens_with_it_off():
    """#61: switching "Everything in this file" off is a choice the file keeps. Saved and opened
    again, it is still off and a move is not recorded (only a file nobody switched gets the default)."""
    L.fresh_scene()
    path = os.path.join(TMP, "switched_off", "off.blend")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    assert datarec.file_tracked(), "the default: on"
    bpy.ops.hindsight.record_file(on=False)
    assert not datarec.file_tracked()
    bpy.ops.wm.save_mainfile()
    bpy.ops.wm.open_mainfile(filepath=path)
    assert not datarec.file_tracked() and not runtime.reg().everything, "still off after reopening"
    a = bpy.data.objects["A"]
    a.location.x += 1
    L.tick_like("move")
    h = runtime.history_of(a)
    assert h is None or not h.entries, "nothing is recorded"


def test_a_file_whose_history_did_not_load_keeps_its_choice():
    """Review of #105: a file saved with "Everything in this file" switched off, whose history can't
    be read when it opens (here its history folder is gone), doesn't get the default: the choice is
    in that history, so nothing is switched on."""
    from hindsight.core import persist
    from hindsight.core.types import SETTINGS
    L.fresh_scene()
    was, SETTINGS.save_mode = SETTINGS.save_mode, "SIDECAR"
    try:
        path = os.path.join(TMP, "unread", "off.blend")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        bpy.ops.hindsight.record_file(on=False)
        a = bpy.data.objects["A"]
        a.location.x += 1
        L.tick_like("move")
        bpy.ops.wm.save_mainfile()
        assert os.path.isdir(persist.sidecar_dir(path))
        shutil.rmtree(persist.sidecar_dir(path))
        bpy.ops.wm.open_mainfile(filepath=path)
        assert runtime.missing_message[0], "the history didn't load"
        assert not runtime.reg().everything and not datarec.file_tracked(), "nothing switched on"
    finally:
        SETTINGS.save_mode = was


def test_a_crash_keeps_everything_switched_off():
    """#104 (#61 review): "Everything in this file" switched off, then Blender ends without a save. The
    crash journal holds the choice, so the recovered file keeps it off, though the history it saved
    never had one (it had the default)."""
    from hindsight.bl import persistence
    from hindsight.core.types import SETTINGS
    L.fresh_scene()
    was = SETTINGS.save_mode, SETTINGS.crash_recovery
    SETTINGS.save_mode, SETTINGS.crash_recovery = "SIDECAR", "INTERRUPTED"
    try:
        path = os.path.join(TMP, "crash_off", "off.blend")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        a = bpy.data.objects["A"]
        a.location.x += 1
        L.tick_like("move")
        bpy.ops.wm.save_mainfile()              # a saved history with the default: no choice in it
        bpy.ops.hindsight.record_file(on=False)
        assert not datarec.file_tracked()
        a.location.x += 1
        L.tick_like("move while off")
        persistence._close_journal()            # the session ends here, unsaved
        bpy.ops.wm.open_mainfile(filepath=path)
        h = runtime.history_of(bpy.data.objects["A"])
        assert h is not None and len(h.entries) == 1, "the saved step came back"
        assert not runtime.reg().everything and not datarec.file_tracked(), "still off"
    finally:
        SETTINGS.save_mode, SETTINGS.crash_recovery = was


if __name__ == "__main__":
    H.run(test_one_switch_for_the_file)
    H.run(test_switch_is_quick_with_many_objects)
    H.run(test_collections_made_and_deleted_with_the_file_switch)
    H.run(test_a_fresh_install_records_everything_in_a_file)
    H.run(test_a_stored_sculpting_only_choice_survives_the_update)
    H.run(test_a_file_saved_with_everything_switched_off_opens_with_it_off)
    H.run(test_a_file_whose_history_did_not_load_keeps_its_choice)
    H.run(test_a_crash_keeps_everything_switched_off)
    H.finish()
