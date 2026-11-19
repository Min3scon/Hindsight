"""D151: with Record everything, datablocks that no object and no scene uses are recorded in the scene's
history (as node groups no object uses are, D133): texts (what they say and their settings), materials
and worlds nothing uses (kept with a fake user, an add-on's library), and the custom properties and
add-on data of any other datablock of the file (images, actions, palettes, brushes...). Going back and
forward puts each state back. A material an object uses stays in that object's history only. With
many unused materials an operation reads a bounded number of them, so the cost doesn't grow with the
file (a changed one is found within a few operations).
Making or deleting such a datablock is not taken back (the same limit as for node groups)."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests.addons import hs_fake_addon as FAKE
from hindsight.bl import adapters, runtime

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
    real = adapters.material_record

    def spy(m, owner=None):
        if m.session_uid not in adapters._MAT_CACHE:
            calls[0] += 1
        return real(m, owner)
    adapters.material_record = spy
    try:
        L.tick_like("nothing")
    finally:
        adapters.material_record = real
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
    for t in (test_text_content_and_settings, test_unused_material_and_world, test_addon_data_on_any_datablock,
              test_material_an_object_uses_stays_with_the_object, test_many_unused_materials_bounded_cost):
        H.run(t)
    if _on[0]:
        FAKE.unregister()
    H.finish()
