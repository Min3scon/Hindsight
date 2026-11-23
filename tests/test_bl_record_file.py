"""L2: "Everything in this file" switches Record everything on for the scene and every local object
in one click (objects added later follow, D36), shows as on only when all of them are, and switches
them all off again. Hindsight's own Movie Mode objects are never switched on."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile
import time
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.bl import datarec, runtime
from hindsight.bl import prefs

C = bpy.context


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
    path = os.path.join(tempfile.gettempdir(), "fresh_install", "plain.blend")
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


if __name__ == "__main__":
    H.run(test_one_switch_for_the_file)
    H.run(test_switch_is_quick_with_many_objects)
    H.run(test_collections_made_and_deleted_with_the_file_switch)
    H.run(test_a_fresh_install_records_everything_in_a_file)
    H.finish()
