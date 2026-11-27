"""#110: with Record everything, making and deleting a scene is taken back. Going back over its making
removes it (what only it held stays in the file, as Blender's own deletion leaves it); going back over its
deletion makes it again exactly (its settings, view layers, world, collections and objects) with its id, so
its own history carries on; forward the other way round. Hindsight's own scene settings (Use Global,
Preserve Modes, Movie Mode) aren't recorded, so a scene made again has them at their defaults. The window
layout (workspaces, screens) is never recorded: Blender's Python API can't put a screen's areas back, and
Blender's own undo leaves the layout alone too."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile

import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.core import globaltl
from hindsight.bl import adapters, datarec, idmap, log, prefs, runtime


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


def _run(ops=OPS, look=read):
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    states, seqs = [look()], [L.max_seq()]
    for label, fn in ops:
        fn()
        L.tick_like(label)
        states.append(look())
        seqs.append(L.max_seq())
    return states, seqs


def _walk(states, seqs, where="", look=read):
    errors, keep = [], log.error_once
    log.error_once = lambda key, text, exc=None: errors.append(text)
    try:
        for i in list(range(len(states) - 1, -1, -1)) + list(range(len(states))):
            L.goto_after(seqs[i])
            assert look() == states[i], (where, i, look(), states[i])
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
    """Deleting the file's first scene ("First": it sorts before "Made", so it holds the data no object uses,
    adapters.scene_loose) and going back: a text nothing uses keeps its contents at every step."""
    def edit():
        bpy.data.texts["Loose"].from_string("after")

    def delete_first():
        if bpy.context.window:
            bpy.context.window.scene = bpy.data.scenes["Made"]
        bpy.data.scenes.remove(bpy.data.scenes["First"])

    def seen():
        sc, t = bpy.data.scenes.get("First"), bpy.data.texts.get("Loose")
        return (None if sc is None else (idmap.uid_of(sc), sc.frame_end,
                                         tuple(sorted((o.name, _r(o.location)) for o in sc.objects))), read(),
                t and t.as_string())
    L.fresh_scene()
    bpy.data.scenes["Scene"].name = "First"
    bpy.data.texts.new("Loose").from_string("before")
    L.track_all()
    L.tick_like("baseline")
    make()
    L.tick_like("New Scene")
    assert adapters.scene_loose(bpy.data.scenes["First"]) and not adapters.scene_loose(bpy.data.scenes["Made"])
    states, seqs = [seen()], [L.max_seq()]
    for label, fn in (("edit the text", edit), ("Delete Scene", delete_first)):
        fn()
        L.tick_like(label)
        states.append(seen())
        seqs.append(L.max_seq())
    assert sorted(s.name for s in bpy.data.scenes) == ["Made"] and states[0][2] == "before", states
    assert states[0][0][2] and states[2][0] is None and states[0][1] == states[2][1], states
    assert states[1][2] == states[2][2] == "after" and len(set(seqs)) == 3, (states, seqs)
    for i in (0, 2, 1, 0, 2, 1, 2):
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


def test_a_scene_made_again_while_another_holds_its_name():
    """#123: after the deletion the user makes another "Made" while Record everything is off, so no jump
    takes it away. Going back over the deletion makes the scene again as "Made.001" (with its id), and the
    next commit records nothing: Blender's name is taken as the recorded one (D47)."""
    states, seqs = _run()
    prefs.FORCE["record_scope"] = "SCULPT"
    other = bpy.data.scenes.new("Made")
    L.tick_like("other")
    prefs.FORCE["record_scope"] = "EVERYTHING"
    L.tick_like("on again")
    L.goto_after(seqs[2])
    back = bpy.data.scenes.get("Made.001")
    assert back is not None and idmap.uid_of(back) == states[2][0] and bpy.data.scenes["Made"] == other, \
        [s.name for s in bpy.data.scenes]
    s = L.max_seq()
    L.tick_like("next")
    assert L.max_seq() == s and back.name == "Made.001", (L.max_seq(), s, back.name)


def test_an_object_another_scene_uses_comes_back_in_its_collection():
    """#123: MadeTri is also in the first scene, so it stays in the file after save and reopen while the
    collection only "Made" had is dropped. Going back over the deletion puts it in that collection made
    again, not in the scene's own collection."""
    def share():
        bpy.data.scenes["Scene"].collection.objects.link(bpy.data.objects["MadeTri"])

    def look():
        sc = bpy.data.scenes.get("Made")
        return (read(), sorted(o.name for o in bpy.data.scenes["Scene"].collection.objects),
                sc and tuple((c.name, tuple(o.name for o in c.objects)) for c in sc.collection.children))
    tmp = tempfile.mkdtemp(prefix="hs_scenes123_")
    try:
        states, seqs = _run(OPS[:1] + (("share", share), OPS[2]), look)
        path = os.path.join(tmp, "f.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        prefs.FORCE["record_scope"] = "EVERYTHING"
        assert bpy.data.objects.get("MadeTri") and not bpy.data.collections.get("MadeColl"), "what Blender kept"
        assert states[2][2] == (("MadeColl", ("MadeTri",)),) and "MadeTri" in states[3][1], states
        _walk(states, seqs, "reopened", look)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_an_object_still_in_the_file_is_linked_where_it_was_recorded():
    """#123: making a scene again links an object still in the file into the collections it was recorded in,
    not into the scene's own collection (the test above passes only because resolve_missing_refs moved it
    afterwards)."""
    L.fresh_scene()
    sc, c = bpy.data.scenes.new("Made"), bpy.data.collections.new("MadeColl")
    sc.collection.children.link(c)
    first = bpy.data.scenes["Scene"]
    ob = bpy.data.objects.new("Shared", None)
    first.collection.objects.link(ob)
    body = {"collections": [idmap.claim(c), "scene:" + idmap.claim(first)]}
    assert adapters._recreate_object(sc, idmap.claim(ob), body, None) is ob
    assert sorted(x.name for x in ob.users_collection) == ["MadeColl", "Scene Collection"], ob.users_collection
    assert ob.name not in sc.collection.objects


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


def test_blenders_undo_and_redo_of_global_jumps_over_a_making_and_a_deletion():
    """#123: a global jump back over the deletion and the change makes the scene again, one forward over them
    takes it away. Blender's undo of both and its redo of both follow the jumps (several steps each, so a
    one-step match is not enough): every cursor is where the jump left it, and nothing is recorded."""
    states, seqs = _run()
    reg = runtime.reg()

    def cursors():
        return {hid: h.cursor for hid, h in reg.histories.items()}

    def lengths():
        return {hid: len(h.entries) for hid, h in reg.histories.items()}

    def pos(seq):
        return sum(1 for g in globaltl.global_groups(reg, runtime._visible_uids()) if g[-1][0] <= seq)
    want = {3: cursors()}
    datarec.PUSH_IN_BACKGROUND[0] = True                      # headless: the jumps go on Blender's undo
    try:
        L.goto_after(seqs[1])
        want[1] = cursors()
        L.goto_after(seqs[3])
    finally:
        datarec.PUSH_IN_BACKGROUND[0] = False
    assert cursors() == want[3] and want[1] != want[3] and len(datarec._JUMPS) == 2, (cursors(), want)
    n = lengths()
    for direction, s in (("undo", 1), ("undo", 3), ("redo", 1), ("redo", 3)):
        at, saved = cursors(), {}
        for h in reg.histories.values():
            saved.update(L._saved_states(h))
        runtime.global_goto(bpy.context, pos(seqs[s]), False)   # the data Blender puts back
        for hid, c in at.items():
            reg.histories[hid].cursor = c
        L._restore_states(saved)
        runtime.UNDO_DIRECTION[0] = direction
        datarec.on_undo(direction)
        assert cursors() == want[s] and read() == states[s], (direction, s, cursors(), want[s], read())
        L.tick_like("next")
        assert lengths() == n and cursors() == want[s], (direction, s, lengths(), n)


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
    H.run(test_a_scene_made_again_while_another_holds_its_name)
    H.run(test_an_object_another_scene_uses_comes_back_in_its_collection)
    H.run(test_an_object_still_in_the_file_is_linked_where_it_was_recorded)
    H.run(test_going_over_a_step_of_an_object_in_another_scene)
    H.run(test_blenders_undo_and_redo_of_a_making)
    H.run(test_blenders_undo_and_redo_of_global_jumps_over_a_making_and_a_deletion)
    H.run(test_a_scene_and_its_view_layer_renamed)
    H.run(test_the_tooltip_names_the_window_layout)
    H.run(test_the_window_layout_cant_be_put_back)
    H.finish()
