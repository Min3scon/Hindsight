"""D151: with Record everything, datablocks that no object and no scene uses are recorded in the scene's
history (as node groups no object uses are, D133): texts (what they say and their settings), materials
and worlds nothing uses (kept with a fake user, an add-on's library), and the custom properties and
add-on data of any other datablock of the file (images, actions, palettes, brushes...). Going back and
forward puts each state back. A material an object uses stays in that object's history only. With
many unused materials an operation reads a bounded number of them, so the cost doesn't grow with the
file (a changed one is found within a few operations).
D152: making or deleting a text, an unused material or world or a node group (D133) is taken back too: going
back over the making removes the datablock (when nothing uses it), going back over its deletion makes it
again with what was recorded; forward the other way round. Images, actions, palettes, brushes... are made
again from a snapshot (D154, test_bl_t1_file_datablocks); their properties are recorded as before."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests.addons import hs_fake_addon as FAKE
from hindsight.bl import adapters, runtime
from hindsight.bl import prefs

C = bpy.context
_on = [False]


def addon_on():
    if not _on[0]:
        FAKE.register()
        _on[0] = True


def _r(v):
    return round(v, 4)


def scene_walk(states, read):
    h = runtime.history_of(C.scene)
    assert h is not None and len(h.entries) >= len(states) - 1, (h and len(h.entries), len(states))
    for pos in list(range(h.cursor, -1, -1)) + list(range(h.cursor + 1)):
        runtime._goto_history(h, pos, True)
        assert read() in states, (pos, read(), states)
    runtime._goto_history(h, h.cursor - h.cursor, True)
    runtime._goto_history(h, 0, True)
    assert read() == states[0], (read(), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


def run_ops(ops, read):
    states = [read()]
    for label, fn in ops:
        fn()
        L.tick_like(label)
        states.append(read())
    assert all(x != y for x, y in zip(states, states[1:])), states
    return states


def texts_walk(read, ops, setup=None):
    """Run `ops` on the scene (setup() first, before the baseline), then walk the scene's history over every
    position both ways."""
    L.fresh_scene()
    if setup is not None:
        setup()
    L.track_all()
    L.tick_like("baseline")
    states = run_ops(ops, read)
    h = runtime.history_of(C.scene)
    assert len(h.entries) == len(states) - 1, (len(h.entries), len(states))     # one step per operation
    for pos in list(range(len(states) - 1, -1, -1)) + list(range(len(states))):
        runtime._goto_history(h, pos, True)
        datarec_resolve()
        assert read() == states[pos], (pos, read(), states[pos])
    return states


def test_text_made_and_deleted():
    def make():
        t = bpy.data.texts.new("made.txt")
        t.write("hello\n")
        t.use_module = True
    read = lambda: (lambda t: None if t is None else (t.as_string(), t.use_module))(bpy.data.texts.get("made.txt"))
    states = texts_walk(read, [("make", make),
                               ("edit", lambda: bpy.data.texts["made.txt"].write("more\n")),
                               ("delete", lambda: bpy.data.texts.remove(bpy.data.texts["made.txt"]))])
    assert states[0] is None and states[-1] is None and states[1] == ("hello\n", True)


def test_unused_material_made_and_deleted():
    def make():
        m = bpy.data.materials.new("MadeMat")
        m.use_fake_user = True
        m.diffuse_color = (0.2, 0.4, 0.6, 1.0)
        m.roughness = 0.3

    def read():
        m = bpy.data.materials.get("MadeMat")
        return None if m is None else (tuple(_r(x) for x in m.diffuse_color), _r(m.roughness), m.use_fake_user)
    states = texts_walk(read, [("make", make),
                               ("rough", lambda: setattr(bpy.data.materials["MadeMat"], "roughness", 0.8)),
                               ("delete", lambda: bpy.data.materials.remove(bpy.data.materials["MadeMat"]))])
    assert states[0] is None and states[1][1] == 0.3 and states[-1] is None


def test_unused_world_made_and_deleted():
    def make():
        w = bpy.data.worlds.new("MadeWorld")
        w.use_fake_user = True
        w.color = (0.1, 0.2, 0.3)

    def read():
        w = bpy.data.worlds.get("MadeWorld")
        return None if w is None else (tuple(_r(x) for x in w.color), w.use_fake_user)
    texts_walk(read, [("make", make),
                      ("colour", lambda: setattr(bpy.data.worlds["MadeWorld"], "color", (0.9, 0.8, 0.7))),
                      ("delete", lambda: bpy.data.worlds.remove(bpy.data.worlds["MadeWorld"]))])


def test_going_back_over_a_making_keeps_what_is_used_now():
    """Never take away a datablock an object (or the scene) uses now, and say so."""
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    a = bpy.data.objects["A"]
    m = bpy.data.materials.new("Later")
    m.use_fake_user = True
    L.tick_like("make material")
    bpy.data.texts.new("later.txt")
    L.tick_like("make text")
    a.data.materials.append(m)
    L.tick_like("assign")
    hs = runtime.history_of(C.scene)
    runtime._goto_history(hs, 0, True)
    datarec_resolve()
    assert bpy.data.materials.get("Later") is not None and bpy.data.materials["Later"].users >= 1, "a used material was removed"
    assert bpy.data.texts.get("later.txt") is None, "an unused text made after position 0 is still there"
    runtime._goto_history(hs, len(hs.entries), True)
    datarec_resolve()
    assert bpy.data.texts.get("later.txt") is not None and bpy.data.materials.get("Later") is not None


def datarec_resolve():
    from hindsight.bl import datarec
    datarec.resolve_missing_refs()


def test_loose_making_on_the_global_timeline():
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    seqs = [L.max_seq()]
    read = lambda: (bpy.data.texts.get("g.txt") is not None, bpy.data.materials.get("GMat") is not None,
                    tuple(round(x, 3) for x in bpy.data.objects["A"].location))
    states = [read()]
    bpy.data.texts.new("g.txt")
    L.tick_like("text")
    states.append(read()); seqs.append(L.max_seq())
    m = bpy.data.materials.new("GMat")
    m.use_fake_user = True
    L.tick_like("material")
    states.append(read()); seqs.append(L.max_seq())
    bpy.data.objects["A"].location.x += 1.0
    L.tick_like("move")
    states.append(read()); seqs.append(L.max_seq())
    bpy.data.texts.remove(bpy.data.texts["g.txt"])
    L.tick_like("delete text")
    states.append(read()); seqs.append(L.max_seq())
    for i in list(range(len(states) - 1, -1, -1)) + list(range(len(states))):
        L.goto_after(seqs[i])
        assert read() == states[i], (i, read(), states[i])


def test_loose_making_survives_save_reload_and_recovery():
    import shutil, tempfile
    from hindsight.core.types import SETTINGS
    tmp = tempfile.mkdtemp(prefix="hs_loose152_")
    try:
        SETTINGS.save_mode = "SIDECAR"
        SETTINGS.crash_recovery = "INTERRUPTED"
        L.fresh_scene()
        L.track_all()
        L.tick_like("baseline")
        read = lambda: ((lambda t: None if t is None else t.as_string())(bpy.data.texts.get("r.txt")),
                        bpy.data.materials.get("RMat") is not None)
        states = [read()]
        t = bpy.data.texts.new("r.txt"); t.write("kept\n")
        L.tick_like("text")
        states.append(read())
        path = os.path.join(tmp, "f.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        # steps after the save: only the journal has them (the session is interrupted)
        m = bpy.data.materials.new("RMat"); m.use_fake_user = True
        L.tick_like("material")
        states.append(read())
        bpy.data.texts.remove(bpy.data.texts["r.txt"])
        L.tick_like("delete text")
        states.append(read())
        for reopen_how in ("interrupted", "saved"):
            if reopen_how == "saved":
                bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            prefs.FORCE["record_scope"] = "EVERYTHING"
            h = runtime.history_of(C.scene)
            assert h is not None and len(h.entries) >= 3, (reopen_how, h and len(h.entries))
            for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
                runtime._goto_history(h, pos, True)
                assert read() in states, (reopen_how, pos, read(), states)
            runtime._goto_history(h, 0, True)
            assert read() == states[0], (reopen_how, read())
            runtime._goto_history(h, len(h.entries), True)
            assert read() == states[-1], (reopen_how, read())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_extra_work_without_loose_datablocks():
    """D152 costs nothing when the file has none of these datablocks: capture reads two empty inventories
    and nothing else, and a jump that doesn't change an inventory looks at no datablock."""
    from hindsight.bl import idmap
    L.fresh_scene()
    for t in list(bpy.data.texts):
        bpy.data.texts.remove(t)
    for m in list(bpy.data.materials):
        if m.users == 0:
            bpy.data.materials.remove(m)
    for g in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(g)
    L.track_all()
    L.tick_like("baseline")
    sc = C.scene
    assert adapters.scene_loose(sc) == {adapters.INV_LOOSE: []}, adapters.scene_loose(sc)
    assert adapters.scene_groups(sc).get(adapters.INV_GROUPS) == [] and \
        not [k for k in adapters.scene_groups(sc) if k.startswith(("@ng:", "@lx:"))]
    calls = [0]
    real = idmap.uid_of

    def spy(idb, *a, **k):
        calls[0] += 1
        return real(idb, *a, **k)
    idmap.uid_of = spy
    try:
        adapters._queue_loose_removals(sc, {"settings": {}, "collections": []})
        adapters._make_loose_again({"settings": {}}, None)
        adapters.flush_loose_removals()
    finally:
        idmap.uid_of = real
    assert calls[0] == 0 and not adapters._LOOSE_GONE, calls


def test_text_content_and_settings():
    L.fresh_scene()
    t = bpy.data.texts.new("notes.txt")
    t.write("one\n")
    L.track_all()
    L.tick_like("baseline")
    read = lambda: (t.as_string(), t.use_module, t.indentation)
    states = run_ops([("write", lambda: t.write("two\n")),
                      ("replace", lambda: t.from_string("rewritten")),
                      ("module", lambda: setattr(t, "use_module", True)),
                      ("indent", lambda: setattr(t, "indentation", "SPACES" if t.indentation == "TABS" else "TABS"))],
                     read)
    scene_walk(states, read)
    # Hindsight's own history text (Embed save) is never recorded
    h = runtime.history_of(C.scene)
    n = len(h.entries)
    from hindsight.bl.persistence import TEXT_NAME
    own = bpy.data.texts.get(TEXT_NAME) or bpy.data.texts.new(TEXT_NAME)
    own.from_string("stored history")
    L.tick_like("own text")
    assert len(h.entries) == n, "Hindsight's own text was recorded"


def test_unused_material_and_world():
    L.fresh_scene()
    mat = bpy.data.materials.new("Library")
    mat.use_fake_user = True
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    w = bpy.data.worlds.new("Spare")
    w.use_fake_user = True
    assert C.scene.world != w
    L.track_all()
    L.tick_like("baseline")
    read = lambda: (tuple(_r(x) for x in mat.diffuse_color), _r(mat.roughness),
                    _r(bsdf.inputs["Roughness"].default_value), tuple(_r(x) for x in w.color))
    states = run_ops([("colour", lambda: setattr(mat, "diffuse_color", (0.8, 0.1, 0.1, 1.0))),
                      ("roughness", lambda: setattr(mat, "roughness", 0.25)),
                      ("node input", lambda: setattr(bsdf.inputs["Roughness"], "default_value", 0.9)),
                      ("world", lambda: setattr(w, "color", (0.1, 0.2, 0.3)))], read)
    scene_walk(states, read)


def test_addon_data_on_any_datablock():
    addon_on()
    L.fresh_scene()
    img = bpy.data.images.new("Painted", 4, 4)
    img.use_fake_user = True
    act = bpy.data.actions.new("Library")
    act.use_fake_user = True
    pal = bpy.data.palettes.new("Colours")
    txt = bpy.data.texts.new("addon.cfg")
    L.track_all()
    L.tick_like("baseline")
    read = lambda: (_r(img.hsfake.strength), act.get("note"), pal.get("k"), txt.hsfake.mode, img.hsfake.sub.level)
    states = run_ops([("image add-on value", lambda: setattr(img.hsfake, "strength", 0.9)),
                      ("action custom property", lambda: act.__setitem__("note", "walk cycle")),
                      ("palette custom property", lambda: pal.__setitem__("k", 2)),
                      ("text add-on enum", lambda: setattr(txt.hsfake, "mode", "HARD")),
                      ("image add-on group", lambda: setattr(img.hsfake.sub, "level", 7))], read)
    scene_walk(states, read)


def test_material_an_object_uses_stays_with_the_object():
    L.fresh_scene()
    a = bpy.data.objects["A"]
    mat = bpy.data.materials.new("Used")
    a.data.materials.append(mat)
    L.track_all()
    L.tick_like("baseline")
    hs = runtime.history_of(C.scene)
    ns = len(hs.entries) if hs is not None else 0
    mat.roughness = 0.1
    L.tick_like("roughness")
    hs = runtime.history_of(C.scene)
    assert (len(hs.entries) if hs is not None else 0) == ns, "a used material was recorded by the scene too"
    ha = runtime.history_of(a)
    assert ha is not None and ha.entries, "the object didn't record its material"


def test_many_unused_materials_bounded_cost():
    L.fresh_scene()
    mats = []
    for i in range(160):
        m = bpy.data.materials.new(f"Lib{i:03d}")
        m.use_fake_user = True
        mats.append(m)
    L.track_all()
    L.tick_like("baseline")
    calls = [0]
    real = adapters.materials.material_record

    def spy(m, owner=None):
        if m.session_uid not in adapters._MAT_CACHE:
            calls[0] += 1
        return real(m, owner)
    adapters.materials.material_record = spy
    try:
        L.tick_like("nothing")
    finally:
        adapters.materials.material_record = real
    assert calls[0] <= adapters.LOOSE_READ_MAX, (calls[0], adapters.LOOSE_READ_MAX)
    # a change of the one read last is still found within a few operations
    mats[-1].roughness = 0.33
    count = lambda: len(runtime.history_of(C.scene).entries) if runtime.history_of(C.scene) else 0
    n = count()
    for k in range(len(mats) // adapters.LOOSE_READ_MAX + 2):
        L.tick_like(f"op {k}")
        if count() > n:
            break
    assert count() == n + 1, (count(), n)
    h = runtime.history_of(C.scene)
    runtime._goto_history(h, h.cursor - 1, True)
    assert round(mats[-1].roughness, 4) != 0.33, mats[-1].roughness


if __name__ == "__main__":
    for t in (test_text_made_and_deleted, test_unused_material_made_and_deleted, test_unused_world_made_and_deleted,
              test_going_back_over_a_making_keeps_what_is_used_now, test_loose_making_on_the_global_timeline,
              test_loose_making_survives_save_reload_and_recovery, test_no_extra_work_without_loose_datablocks,
              test_text_content_and_settings, test_unused_material_and_world, test_addon_data_on_any_datablock,
              test_material_an_object_uses_stays_with_the_object, test_many_unused_materials_bounded_cost):
        H.run(t)
    if _on[0]:
        FAKE.unregister()
    H.finish()
