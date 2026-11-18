"""D105: names given back after a jump when the name is free again.

Two recorded objects swap names (Left and Right). Going back over it in the global history wrote one
name while the other object still held it, so Blender added .001: depending on the order the result
was Left / Right.001 or Left.001 / Right, and the tab said a name was taken though nothing held it
once the jump was done. A chain of renames (A to B, B to C) the same. A name held by an object the
jump didn't touch is still left alone (it's never renamed), with the note.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.core import globaltl
from hindsight.bl import datarec, runtime, adapters

C = bpy.context


def glen():
    return globaltl.global_length(runtime.reg(), runtime._visible_uids())


def rename(ob, name):
    ob.name = name
    L.tick_like(f"rename {name}")


def names():
    return sorted(o.name for o in C.scene.objects)


def test_swap_comes_back():
    L.fresh_scene()
    L.track_all()
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    rename(a, "Left")
    rename(b, "Right")
    start = (a.name, b.name)
    a.name = "tmp"                                    # one operation (a script, a rename add-on)
    b.name = "Left"
    a.name = "Right"
    L.tick_like("swap names")
    end = (a.name, b.name)
    assert end == ("Right", "Left")
    for _ in range(2):
        runtime.global_goto(C, glen() - 1, True)
        assert (a.name, b.name) == start, (a.name, b.name)
        assert not adapters.NAME_TAKEN, adapters.NAME_TAKEN
        runtime.global_goto(C, glen(), True)
        assert (a.name, b.name) == end, (a.name, b.name)


def test_name_held_by_an_untouched_object_stays():
    L.fresh_scene()
    L.track_all()
    a = bpy.data.objects["A"]
    rename(a, "Hero")
    rename(a, "Other")
    c = bpy.data.objects["C"]
    datarec.set_tracked(c, False)
    c.name = "Hero"                                   # not recorded: the jump doesn't touch it
    h = runtime.history_of(a)
    runtime._goto_history(h, h.cursor - 1, True)
    datarec.resolve_missing_refs()
    assert c.name == "Hero", "never renamed"
    assert a.name != "Hero" and adapters.name_taken(a) == "Hero", (a.name, adapters.NAME_TAKEN)


if __name__ == "__main__":
    for t in (test_swap_comes_back, test_name_held_by_an_untouched_object_stays):
        H.run(t)
    H.finish()
