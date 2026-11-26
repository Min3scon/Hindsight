"""#110: with Record everything, making and deleting a scene is taken back. Going back over its making
removes it (and the objects only it held); going back over its deletion makes it again exactly (its
settings, view layers, world, collections and objects) with its id, so its own history carries on;
forward the other way round. The window layout (workspaces, screens) is never recorded: Blender's
Python API can't put a screen's areas back, and Blender's own undo leaves the layout alone too."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile

import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import idmap, log, prefs, runtime


def _r(v):
    return tuple(round(x, 4) for x in v)


def read():
    """What a user sees of the scene "Made" (None: there is none)."""
    sc = bpy.data.scenes.get("Made")
    if sc is None:
        return None
    tri = bpy.data.objects.get("MadeTri")
    return (idmap.uid_of(sc), sc.frame_end, sc.render.resolution_x, sc.get("note"),
            sc.world.name if sc.world else None,
            tuple((v.name, v.use_pass_z) for v in sc.view_layers),
            tuple(sorted((o.name, _r(o.location)) for o in sc.objects)),
            tuple(c.name for c in sc.collection.children),
            tuple(sorted(o.name for o in sc.collection.objects)),
            tuple(_r(v.co) for v in tri.data.vertices) if tri is not None and tri.users else None)


def make():
    sc = bpy.data.scenes.new("Made")
    sc.frame_end = 50
    sc.render.resolution_x = 640
    sc["note"] = "hi"
    sc.world = bpy.data.worlds.new("MadeWorld")
    sc.view_layers[0].use_pass_z = True
    ob = bpy.data.objects.new("MadeEmpty", None)
    ob.location = (1, 2, 3)
    sc.collection.objects.link(ob)
    c = bpy.data.collections.new("MadeColl")
    sc.collection.children.link(c)
    me = bpy.data.meshes.new("MadeMesh")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    c.objects.link(bpy.data.objects.new("MadeTri", me))


def change():
    sc = bpy.data.scenes["Made"]
    sc.frame_end = 77
    sc.view_layers.new("Second")
    bpy.data.objects["MadeEmpty"].location.x = 5


def delete():
    bpy.data.scenes.remove(bpy.data.scenes["Made"])


OPS = (("New Scene", make), ("change", change), ("Delete Scene", delete))


def _run(ops=OPS):
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    states, seqs = [read()], [L.max_seq()]
    for label, fn in ops:
        fn()
        L.tick_like(label)
        states.append(read())
        seqs.append(L.max_seq())
    return states, seqs


def _walk(states, seqs, where=""):
    errors, keep = [], log.error_once
    log.error_once = lambda key, text, exc=None: errors.append(text)
    try:
        for i in list(range(len(states) - 1, -1, -1)) + list(range(len(states))):
            L.goto_after(seqs[i])
            assert read() == states[i], (where, i, read(), states[i])
    finally:
        log.error_once = keep
    assert not errors, errors


def test_a_scene_made_changed_and_deleted():
    states, seqs = _run()
    assert states[0] is None and states[-1] is None and None not in states[1:3] and states[1] != states[2], states
    assert len(set(seqs)) == len(seqs), seqs          # making and deleting are steps
    _walk(states, seqs)


def test_a_scene_made_again_keeps_its_history():
    """Going back over the deletion makes the scene again with its id: its history goes on from there."""
    states, seqs = _run()
    uid = states[1][0]
    h = runtime.reg().history_for(uid)
    assert h is not None, uid
    L.goto_after(seqs[2])
    assert read() == states[2], (read(), states[2])
    assert runtime.reg().history_for(idmap.uid_of(bpy.data.scenes["Made"])) is h
    bpy.data.scenes["Made"].frame_end = 99
    L.tick_like("change again")
    assert h.cursor == len(h.entries) and read()[1] == 99, (h.cursor, len(h.entries), read())
    L.goto_after(seqs[2])
    assert read() == states[2], (read(), states[2])


def test_the_first_scene_deleted():
    """Deleting the file's first scene ("Scene", which held the data no object uses) and going back."""
    def delete_first():
        bpy.context.window.scene = bpy.data.scenes["Made"] if bpy.context.window else None
        bpy.data.scenes.remove(bpy.data.scenes["Scene"])
    def seen():
        sc = bpy.data.scenes.get("Scene")
        return (None if sc is None else (idmap.uid_of(sc), sc.frame_end,
                                         tuple(sorted((o.name, _r(o.location)) for o in sc.objects))), read())
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    make()
    L.tick_like("New Scene")
    states, seqs = [seen()], [L.max_seq()]
    delete_first()
    L.tick_like("Delete Scene")
    states.append(seen())
    seqs.append(L.max_seq())
    assert states[0][0][2] and states[1][0] is None and states[0][1] == states[1][1], states
    for i in (0, 1, 0, 1):
        L.goto_after(seqs[i])
        assert seen() == states[i], (i, seen(), states[i])


def test_a_deleted_scene_comes_back_after_save_and_reload():
    """The file is saved after the deletion (Blender drops what only the scene held) and opened again."""
    tmp = tempfile.mkdtemp(prefix="hs_scenes110_")
    try:
        states, seqs = _run()
        path = os.path.join(tmp, "f.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        prefs.FORCE["record_scope"] = "EVERYTHING"
        assert bpy.data.objects.get("MadeEmpty") is None, "Blender keeps what the scene alone held"
        _walk(states, seqs, "reopened")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_going_over_a_step_of_an_object_in_another_scene():
    """The step crossed is of an object the window's scene doesn't hold: Follow object leaves the selection."""
    def move():
        bpy.data.objects["MadeEmpty"].location.x = 7
    _walk(*_run(OPS[:1] + (("move", move),)))


def test_blenders_undo_and_redo_of_a_making():
    """Blender's own undo of New Scene takes the scene away and its redo brings it back: the history's
    cursor follows, and no deletion (or second making) is recorded."""
    states, _seqs = _run(OPS[:1])
    h = runtime.reg().history_for(states[1][0])
    for direction, want in (("undo", 0), ("redo", 1), ("undo", 0)):
        L._emulate_blender_undo([(h.entries[0].meta["seq"], h.uid, 0)], direction)
        assert h.cursor == want and read() == states[want], (direction, h.cursor, read())
        L.tick_like("next")
        assert len(h.entries) == 1 and h.cursor == want, [e.meta["label"] for e in h.entries]


def test_a_scene_and_its_view_layer_renamed():
    """Renaming a scene or one of its view layers is a step: going back gives the old names back (to the same
    view layer, which keeps what isn't recorded of it)."""
    def rename():
        sc = bpy.data.scenes["Made"]
        sc.view_layers[0].name = "Renamed"
        sc.name = "Kept"

    def names():
        sc = bpy.data.scenes.get("Made") or bpy.data.scenes["Kept"]
        return sc.name, [v.name for v in sc.view_layers], sc.view_layers[0].as_pointer()
    states, seqs = [], []
    for label, fn in (("New Scene", make), ("rename", rename)):
        if not states:
            L.fresh_scene()
            L.track_all()
            L.tick_like("baseline")
        fn()
        L.tick_like(label)
        states.append(names())
        seqs.append(L.max_seq())
    assert seqs[1] > seqs[0] and states[0][0] != states[1][0], (seqs, states)
    for i in (0, 1, 0, 1):
        L.goto_after(seqs[i])
        assert names() == states[i], (i, names(), states[i])


def test_the_tooltip_names_the_window_layout():
    text = bpy.ops.hindsight.record_file.get_rna_type().description
    assert "the window layout (workspaces, screens)" in text, text


def test_the_window_layout_cant_be_put_back():
    """Why the window layout isn't recorded (#110): Python can't make or remove a screen, and can't set an
    area's place or size or split and join areas (only operators can, in a real window, at mouse positions).
    If this fails, Blender has gained that API: look at recording the layout again."""
    assert not hasattr(bpy.data.screens, "new") and not hasattr(bpy.data.screens, "remove")
    assert not hasattr(bpy.data.workspaces, "new") and not hasattr(bpy.data.window_managers, "new")
    area = bpy.types.Area.bl_rna.properties
    assert all(area[k].is_readonly for k in ("x", "y", "width", "height"))
    assert bpy.types.Screen.bl_rna.properties["areas"].is_readonly


if __name__ == "__main__":
    H.run(test_a_scene_made_changed_and_deleted)
    H.run(test_a_scene_made_again_keeps_its_history)
    H.run(test_the_first_scene_deleted)
    H.run(test_a_deleted_scene_comes_back_after_save_and_reload)
    H.run(test_going_over_a_step_of_an_object_in_another_scene)
    H.run(test_blenders_undo_and_redo_of_a_making)
    H.run(test_a_scene_and_its_view_layer_renamed)
    H.run(test_the_tooltip_names_the_window_layout)
    H.run(test_the_window_layout_cant_be_put_back)
    H.finish()
