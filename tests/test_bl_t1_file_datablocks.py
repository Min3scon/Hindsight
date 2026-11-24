"""D154 (#13): with Record everything, making and deleting a file-level datablock (an image, action, palette,
brush, line style, mask, texture...) is taken back like a text's (D152): going back over the making removes
it (when nothing uses it), going back over its deletion makes it again, exactly, from a snapshot of it (the
datablock written and loaded with Blender's library functions); forward the other way round. A change of its
settings between is a step too, put back from the snapshot."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile
import wave

import bpy
import tests.harness as H
from tests.test_bl_t1_loose_data import texts_walk
from hindsight.bl import idmap, runtime


class _Data:
    """bpy.data as it is now (a fresh scene replaces it)."""
    def __getattr__(self, name):
        return getattr(bpy.data, name)


D = _Data()


def _r(v):
    return tuple(round(x, 4) for x in v) if hasattr(v, "__len__") else round(v, 4)


def _tmp(name):
    return os.path.join(tempfile.gettempdir(), name)


def _cache_file():
    """A cache file: an Alembic file written from the scene, opened."""
    abc = _tmp("made.abc")
    if not os.path.exists(abc):
        bpy.ops.wm.alembic_export(filepath=abc)
    bpy.ops.cachefile.open(filepath=abc)
    D.cache_files[-1].name = "Made"


def _sculpt(fn):
    C = bpy.context
    C.view_layer.objects.active = D.objects["A"]
    bpy.ops.object.mode_set(mode="SCULPT")
    fn(C.scene.tool_settings.sculpt)
    bpy.ops.object.mode_set(mode="OBJECT")


def _curve_brush(sculpt):
    """Sculpt Mode's brush: a local brush that strokes along a curve (a linked brush asset's paint curve
    is made in its library: never recorded)."""
    brush = D.brushes.new("CurveBrush", mode="SCULPT")
    brush.stroke_method = "CURVE"
    if bpy.app.version < (4, 3):
        sculpt.brush = brush
        return
    brush.asset_mark()
    try:
        bpy.ops.brush.asset_activate(asset_library_type="LOCAL", relative_asset_identifier="Brush/CurveBrush")
    except RuntimeError:        # 5.0 on Windows
        bpy.ops.brush.asset_activate(asset_library_type="LOCAL", relative_asset_identifier="Brush\\CurveBrush")
    assert sculpt.brush == brush, sculpt.brush


def _paint_curve(sculpt):
    """A paint curve: made in Sculpt Mode for the brush."""
    assert bpy.ops.paintcurve.new() == {"FINISHED"}
    D.paint_curves[-1].name = "Made"


def _sound():
    wav = _tmp("made.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000), w.writeframes(bytes(1600))
    D.sounds.load(wav).name = "Made"


def _movie_clip():
    png = _tmp("made.png")
    img = D.images.new("ClipFrame", 4, 4)
    img.filepath_raw, img.file_format = png, "PNG"
    img.save()
    D.images.remove(img)
    D.movieclips.load(png).name = "Made"


# kind: (make it, read its state, change a setting[, set the scene up before the baseline])
KINDS = {
    "images": (lambda: D.images.new("Made", 8, 8, alpha=True),
               lambda i: (i.size[:], i.generated_type, i.alpha_mode, i.use_fake_user),
               lambda i: setattr(i, "generated_type", "UV_GRID")),
    "actions": (lambda: setattr(D.actions.new("Made"), "use_fake_user", True),
                lambda a: (a.use_frame_range, _r(a.frame_start), _r(a.frame_end), a.use_fake_user),
                lambda a: (setattr(a, "use_frame_range", True), setattr(a, "frame_end", 42.0))),
    "particles": (lambda: setattr(D.particles.new("Made"), "use_fake_user", True),     # no object uses it
                  lambda p: (p.count, _r(p.lifetime), p.use_fake_user),
                  lambda p: (setattr(p, "count", 77), setattr(p, "lifetime", 12.5))),
    "palettes": (lambda: setattr(D.palettes.new("Made").colors.new(), "color", (0.1, 0.2, 0.3)),
                 lambda p: tuple(_r(c.color) for c in p.colors),
                 lambda p: setattr(p.colors[0], "color", (0.7, 0.6, 0.5))),
    "brushes": (lambda: setattr(D.brushes.new("Made"), "strength", 0.25),
                lambda b: (_r(b.strength), b.size),
                lambda b: setattr(b, "size", 77)),
    "linestyles": (lambda: setattr(D.linestyles.new("Made"), "thickness", 2.5),
                   lambda s: (_r(s.thickness), _r(s.color)),
                   lambda s: setattr(s, "color", (0.9, 0.1, 0.2))),
    "masks": (lambda: D.masks.new("Made").layers.new(name="L1"),
              lambda m: (tuple(x.name for x in m.layers), m.frame_end),
              lambda m: setattr(m, "frame_end", 99)),
    "textures": (lambda: setattr(D.textures.new("Made", "CLOUDS"), "noise_scale", 0.5),
                 lambda t: (t.type, _r(t.noise_scale)),
                 lambda t: setattr(t, "noise_scale", 1.75)),
    "cache_files": (_cache_file,
                    lambda c: (os.path.basename(c.filepath), _r(c.scale), _r(c.frame_offset)),
                    lambda c: (setattr(c, "scale", 2.5), setattr(c, "frame_offset", 3.0))),
    "paint_curves": (lambda: _sculpt(_paint_curve),
                     lambda p: (p.name, p.get("note")),
                     lambda p: p.__setitem__("note", "changed"),
                     lambda: _sculpt(_curve_brush)),
    "sounds": (_sound,
               lambda s: (os.path.basename(s.filepath), s.use_mono),
               lambda s: setattr(s, "use_mono", True)),
    "fonts": (lambda: setattr(D.fonts.load(os.path.join(bpy.utils.system_resource("DATAFILES", path="fonts"),
                                                         "DejaVuSansMono.woff2")), "name", "Made"),
              lambda f: (os.path.basename(f.filepath), f.get("note")),
              lambda f: (f.__setitem__("note", "changed"),
                         setattr(f, "filepath", os.path.join(os.path.dirname(f.filepath), "Inter.woff2")))),
    "movieclips": (_movie_clip,
                   lambda m: (os.path.basename(m.filepath), m.frame_start),
                   lambda m: setattr(m, "frame_start", 7)),
}


def _fake_gp():
    g = D.grease_pencils.new("Made")      # 4.2: an annotation; 5.0 on: Grease Pencil data with no object
    g.use_fake_user = True
    g.layers.new("Notes")
    return g


KINDS["grease_pencils"] = (_fake_gp, lambda g: (len(g.layers), _r(g.layers[0].opacity), g.get("note"), g.use_fake_user),
                           lambda g: (g.__setitem__("note", "changed"), setattr(g.layers[0], "opacity", 0.5)))


def _fake(coll, *args):
    """Object data no object uses, kept with a fake user."""
    d = getattr(D, coll).new("Made", *args)
    d.use_fake_user = True
    return d


def _tri():
    _fake("meshes").from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])


def _data_kind(coll, args, attr, value):
    return (lambda: _fake(coll, *args), lambda d: (_r(getattr(d, attr)) if isinstance(value, float)
                                                   else getattr(d, attr), d.use_fake_user),
            lambda d: setattr(d, attr, value))


KINDS["meshes"] = (_tri, lambda m: (len(m.polygons), _r(m.vertices[0].co), m.use_fake_user),
                   lambda m: setattr(m.vertices[0], "co", (0.0, 0.0, 1.0)))
KINDS["pointclouds"] = (lambda: _fake("pointclouds"), lambda p: (p.get("note"), p.use_fake_user),
                        lambda p: p.__setitem__("note", "changed"))
for _k in [("curves", ("CURVE",), "bevel_depth", 0.25), ("metaballs", (), "resolution", 0.5),
           ("lattices", (), "points_u", 4), ("armatures", (), "display_type", "STICK"),
           ("cameras", (), "lens", 85.0), ("lights", ("POINT",), "energy", 250.0),
           ("lightprobes", ("VOLUME",), "influence_distance", 3.0), ("speakers", (), "volume", 0.5),
           ("volumes", (), "frame_offset", 3), ("hair_curves", (), "surface_uv_map", "UV")]:
    KINDS[_k[0]] = _data_kind(*_k)
# D175: a collection no scene uses (kept with a fake user)
KINDS["collections"] = (lambda: _fake("collections"), lambda c: (c.hide_render, c.color_tag, c.use_fake_user),
                        lambda c: (setattr(c, "hide_render", True), setattr(c, "color_tag", "COLOR_03")))


def walk(kind):
    make, state, change, *setup = KINDS[kind]
    coll = lambda: getattr(bpy.data, kind)

    def read():
        idb = coll().get("Made")
        return None if idb is None else state(idb)
    states = texts_walk(read, [("make", make), ("change", lambda: change(coll()["Made"])),
                               ("delete", lambda: bpy.data.batch_remove([coll()["Made"]]))],  # (no remove() for some kinds)
                        *setup)
    assert states[0] is None and states[-1] is None and states[1] != states[2], (kind, states)



def _test(kind):
    def t():
        walk(kind)
    t.__name__ = f"test_{kind}_made_changed_and_deleted"
    return t


TESTS = [_test(k) for k in KINDS]


def test_object_data_its_object_left_with_a_fake_user():
    """A mesh kept with a fake user is snapshotted once its object is deleted: going back over the mesh's
    deletion makes it again, and going back over the object's gives the object that mesh again (one mesh,
    with its id); forward the other way round."""
    def setup():
        D.objects["A"].data.name = "AMesh"
        D.objects["A"].data.use_fake_user = True

    def read():
        ob, m = D.objects.get("A"), D.meshes.get("AMesh")
        return (ob is not None and ob.data == m, m and len(m.vertices), m and idmap.uid_of(m),
                sorted(x.name for x in D.meshes if x.name.startswith("AMesh")))
    states = texts_walk(read, [("delete A", lambda: D.objects.remove(D.objects["A"])),
                               ("delete mesh", lambda: D.meshes.remove(D.meshes["AMesh"]))], setup)
    assert [s[0] for s in states] == [True, False, False] and states[1][1] == 8, states


def test_object_data_its_object_left_with_no_user_is_not_snapshotted():
    """D174: deleting an object leaves its mesh with no user (Blender drops it at save); only object data
    kept with a fake user is snapshotted, or every operation after would write that mesh again."""
    from hindsight.bl.adapters.keys import SNAP_PREFIX
    _states, h = _scene_ops([("delete A", lambda: D.objects.remove(D.objects["A"]))],
                            lambda: "A" in D.objects, lambda: setattr(D.objects["A"].data, "name", "AMesh"))
    snaps = [v[3] for k, v in runtime._STATES[idmap.uid_of(bpy.context.scene)].values.items()
             if k[1].startswith(SNAP_PREFIX) and v[0] == "meshes"]
    assert len(h.entries) == 1 and D.meshes["AMesh"].users == 0 and snaps == [], (len(h.entries), snaps)


def test_object_data_with_no_user_stays_over_a_jump():
    """D174: object data with no user isn't recorded, so no jump takes it away either: it stays until Blender
    drops it at save (going back over its object's deletion gives the object that mesh again)."""
    def read():
        ob, m = D.objects.get("A"), D.meshes.get("AMesh")
        return (ob is not None and ob.data == m, m and len(m.vertices), "Img" in D.images,
                sorted(x.name for x in D.meshes if x.name.startswith("AMesh")))
    texts_walk(read, [("delete A", lambda: D.objects.remove(D.objects["A"])),
                      ("make image", lambda: setattr(D.images.new("Img", 4, 4), "use_fake_user", True))],
               lambda: setattr(D.objects["A"].data, "name", "AMesh"))


def test_a_collection_only_an_instance_uses_is_made_again():
    """D175: a collection no scene holds, only a collection instance uses (with an object in it): going back
    over its deletion makes it again with its id and its object (the file's, not a copy)."""
    def setup():
        c = D.collections.new("Inst")
        c.objects.link(D.objects.new("InInst", None))
        e = D.objects.new("Instancer", None)
        e.instance_type, e.instance_collection = "COLLECTION", c
        bpy.context.scene.collection.objects.link(e)

    def read():
        c = D.collections.get("Inst")
        return (c and [o.name for o in c.objects], c and idmap.uid_of(c),
                sorted(o.name for o in D.objects if o.name.startswith("InInst")))
    states = texts_walk(read, [("delete Inst", lambda: D.collections.remove(D.collections["Inst"]))], setup)
    assert states[0][0] == ["InInst"], states


def test_made_again_keeps_its_id_and_users():
    """What uses a datablock deleted and made again (a texture's image) uses it again, with its id."""
    def make():
        img = D.images.new("Made", 4, 4)
        tex = D.textures.new("UsesIt", "IMAGE")
        tex.image = img
        tex.use_fake_user = True
    uid = {}

    def read():
        t = D.textures.get("UsesIt")
        img = None if t is None else t.image
        if img is not None:
            uid.setdefault("first", idmap.uid_of(img))
            assert idmap.uid_of(img) == uid["first"], (idmap.uid_of(img), uid)
        return None if img is None else img.name
    states = texts_walk(read, [("make", make), ("delete", lambda: D.images.remove(D.images["Made"]))])
    assert states == [None, "Made", None], states
    # going back over the making of both: the image goes too, once the texture that used it has gone
    runtime._goto_history(runtime.history_of(bpy.context.scene), 0, True)
    assert "UsesIt" not in D.textures and "Made" not in D.images, (list(D.textures), list(D.images))


def test_digest_reads_a_pointer_to_data_not_written_as_null():
    """Blender reads a pointer to data the snapshot doesn't hold as null. 5.0 writes such a pointer (an
    image's runtime data) as an address from the counter the blocks' addresses come from, so a custom
    property block before it changes it: two snapshots that differ only there have one digest, and a
    changed data word changes it."""
    from hindsight.bl import multires
    from hindsight.bl.adapters import loose
    raw = bytearray(multires.snapshot_bytes(D.images.new("Digest", 4, 4)))
    blocks = multires.blend_blocks(bytes(raw))
    info = loose._dna_info(bytes(raw), blocks)
    hs, so = loose._heads(raw)
    p = next(p for p, c, _ in blocks if c == b"IM\0\0")
    entry = info[1][int.from_bytes(raw[p + so:p + so + 4], "little")]
    ptrs, run = (set(x.tolist()) for x in entry[1:3])
    run.update(i for a, m in entry[3].tolist() for i in range(a, a + m))    # nor a name or path (read to its end)
    word = lambda o: raw[p + hs + o:p + hs + o + 8]
    at = p + hs + next(o for o in sorted(ptrs) if o % 8 == 0 and not any(word(o)))         # a null pointer
    data = p + hs + next(o for o in range(64, 1600, 8) if o not in ptrs and any(word(o))   # a set data word
                         and not run.intersection(range(o, o + 8)))     # (not an ID's runtime field: D156)
    before = loose._digest(bytes(raw), blocks, info)
    raw[at:at + 8] = (0x76387C9A9BBE7070).to_bytes(8, "little")
    assert loose._digest(bytes(raw), blocks, info) == before, "a pointer to nothing in the snapshot changed the digest"
    raw[data] ^= 1
    assert loose._digest(bytes(raw), blocks, info) != before, "a changed data word kept the digest"


def test_digest_leaves_out_what_custom_properties_hold():
    """Custom property blocks are left out of the digest (the D151 writer puts them back), and so is the data
    they point to: 4.2 writes a text property's buffer with its byte after the end left as it was in memory."""
    from hindsight.bl import multires
    from hindsight.bl.adapters import loose
    img = D.images.new("Digest", 4, 4)
    img["note"] = "walk cycle"
    raw = bytearray(multires.snapshot_bytes(img))
    blocks = multires.blend_blocks(bytes(raw))
    info = loose._dna_info(bytes(raw), blocks)
    before = loose._digest(bytes(raw), blocks, info)
    raw[raw.index(b"walk cycle\0")] ^= 1
    assert loose._digest(bytes(raw), blocks, info) == before, "a custom property's text changed the digest"


def test_digest_leaves_out_what_is_after_the_end_of_a_name():
    """Blender reads a name or a path up to its end. 5.0 writes a brush asset's catalog name with what is
    after its end as it was in memory: that is left out of the digest, and a changed letter changes it."""
    from hindsight.bl import multires
    from hindsight.bl.adapters import loose
    img = D.images.new("Digest", 4, 4)
    raw = bytearray(multires.snapshot_bytes(img))
    blocks = multires.blend_blocks(bytes(raw))
    info = loose._dna_info(bytes(raw), blocks)
    before = loose._digest(bytes(raw), blocks, info)
    at = raw.index(b"IM" + img.name.encode() + b"\0")
    raw[at + len(img.name) + 6] = 0x5A
    assert loose._digest(bytes(raw), blocks, info) == before, "a byte after the end of the name changed the digest"
    raw[at + 3] ^= 1
    assert loose._digest(bytes(raw), blocks, info) != before, "a changed letter kept the digest"


def test_digest_leaves_out_what_is_after_the_end_of_a_string():
    """4.2 writes a string (an F-Curve's path) as a block of its length rounded up to 4 bytes, what is after
    its end as it was in memory, so an action loaded again read as changed (t1_prop corpus 411076cc4a42):
    that is left out of the digest, and a changed letter changes it. (5.x writes the string to its end.)"""
    from hindsight.bl import multires
    from hindsight.bl.adapters import loose
    ob = D.objects.new("Digest", None)
    ob.keyframe_insert("color", index=0)
    raw = bytearray(multires.snapshot_bytes(ob.animation_data.action))
    blocks = multires.blend_blocks(bytes(raw))
    info = loose._dna_info(bytes(raw), blocks)
    hs, _so = loose._heads(raw)
    p, n = next((p, n) for p, c, n in blocks if c == b"DATA" and raw[p + hs:p + hs + 6] == b"color\0")
    assert n - hs == (8 if bpy.app.version < (5, 0) else 6), n - hs
    raw[p + hs + 6:p + n] = bytes(n - hs - 6)
    before = loose._digest(bytes(raw), blocks, info)
    if n - hs == 8:
        raw[p + hs + 6:p + n] = b"er"
        assert loose._digest(bytes(raw), blocks, info) == before, "bytes after the end of a string changed the digest"
    raw[p + hs] ^= 1
    assert loose._digest(bytes(raw), blocks, info) != before, "a changed letter kept the digest"


def test_digest_reads_every_byte_of_a_long_string():
    """A raw data block (SDNA number 0) has no struct layout: it was read as struct 0 (`Link`, two pointers in
    16 bytes), so in a block of 16 bytes or more every word that wasn't a block's address was left out, and an
    F-Curve path changed in its first 8 bytes of a word to another of its length kept the action's digest."""
    from hindsight.bl import multires
    from hindsight.bl.adapters import loose
    ob = D.objects.new("Digest", None)
    path = '["prop_001_with_a_long_name"]'
    ob[path[2:-2]] = 0.0
    ob.keyframe_insert(path)
    raw = bytearray(multires.snapshot_bytes(ob.animation_data.action))
    blocks = multires.blend_blocks(bytes(raw))
    info = loose._dna_info(bytes(raw), blocks)
    hs, _so = loose._heads(raw)
    p = next(p for p, c, _ in blocks if c == b"DATA" and raw[p + hs:p + hs + len(path)] == path.encode())
    before = loose._digest(bytes(raw), blocks, info)
    for at in (2, path.index("1"), 20):       # in words 0, 1 and 2
        raw[p + hs + at] ^= 1
        assert loose._digest(bytes(raw), blocks, info) != before, f"a changed letter {at} kept the digest"
        raw[p + hs + at] ^= 1


def test_digest_leaves_out_the_runtime_fields_of_an_id():
    """D156: an ID's session number and depsgraph tags are written, but given again when it is read (a file
    opened again, a snapshot loaded): they are left out of the digest."""
    from hindsight.bl import multires
    from hindsight.bl.adapters import loose
    img = D.images.new("Digest", 4, 4)
    raw = bytearray(multires.snapshot_bytes(img))
    blocks = multires.blend_blocks(bytes(raw))
    info = loose._dna_info(bytes(raw), blocks)
    hs, _so = loose._heads(raw)
    p = next(p for p, c, _ in blocks if c == b"IM\0\0")
    at = raw.find(img.session_uid.to_bytes(4, "little"), p + hs + 48, p + hs + 400)     # past "IMDigest"
    if at < 0:              # 5.x doesn't write it
        assert bpy.app.version >= (5, 0), "4.2 writes the session number"
        return
    before = loose._digest(bytes(raw), blocks, info)
    raw[at] ^= 1
    assert loose._digest(bytes(raw), blocks, info) == before, "the session number changed the digest"


def test_going_back_over_a_duplicate_takes_its_action_away():
    """D155: going back over duplicating an animated object takes away the copy of its action the duplicate
    made, as Blender's undo does (no position had it as data nothing uses); forward, it is back."""
    from tests import t1_gate_lib as L, t1_ops as T
    from tests.test_bl_t1_loose_data import datarec_resolve
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    T.select("A")
    D.objects["A"].keyframe_insert("color", frame=1)
    L.tick_like("key")
    T.duplicate("A").fn()
    L.tick_like("dup")
    acts = lambda: sorted((a.name, a.users) for a in D.actions)
    after = acts()
    assert len(after) == 2, after
    h = runtime.history_of(bpy.context.scene)
    runtime._goto_history(h, len(h.entries) - 1, True)
    datarec_resolve()
    assert acts() == [after[0]], acts()
    runtime._goto_history(h, len(h.entries), True)
    datarec_resolve()
    assert acts() == after, (acts(), after)


def _back_and_forth(op, read):
    """Run op on object A, then go back over its step and forward again: [read() after, back, forward]."""
    from tests import t1_gate_lib as L, t1_ops as T
    from tests.test_bl_t1_loose_data import datarec_resolve
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    T.select("A")
    op(D.objects["A"])
    L.tick_like("op")
    out = [read()]
    h = runtime.history_of(D.objects["A"])
    for pos in (len(h.entries) - 1, len(h.entries)):
        runtime._goto_history(h, pos, True)
        datarec_resolve()
        out.append(read())
    return out


def test_going_back_over_keying_takes_its_action_away():
    """D162: going back over the first keyframe takes away the action it made, as Blender's undo does; forward,
    it is back with its id and keys."""
    read = lambda: sorted((a.name, idmap.uid_of(a), a.users, len(_fcurves(a)))
                          for a in D.actions)
    after, back, fwd = _back_and_forth(lambda ob: ob.keyframe_insert("color", frame=1), read)
    assert len(after) == 1 and back == [] and fwd == after, (after, back, fwd)


def test_going_back_over_a_slot_takes_the_material_it_made_away():
    """D163: going back over giving an object a new material takes the material away, as Blender's undo does;
    forward, it is back with its id, name and settings."""
    def op(ob):
        m = D.materials.new("Made")
        m.diffuse_color = (0.25, 0.5, 0.75, 1.0)
        m.roughness = 0.125
        ob.data.materials.append(m)
    read = lambda: (lambda m: m and (idmap.uid_of(m), tuple(_r(x) for x in m.diffuse_color), _r(m.roughness),
                                     [s.material == m for s in D.objects["A"].material_slots]))(D.materials.get("Made"))
    after, back, fwd = _back_and_forth(op, read)
    assert after and back is None and fwd == after, (after, back, fwd)


def test_a_linked_duplicate_waits_for_both_materials_its_owner_makes_again():
    """D164: going forward over giving a shared mesh two new materials, a linked duplicate whose step comes
    first writes its slots while both materials are gone; once the owner has made them again, both hold them."""
    from tests import t1_gate_lib as L, t1_ops as T
    from tests.test_bl_t1_loose_data import datarec_resolve
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    T.duplicate("A", True).fn()
    L.tick_like("dup")
    a, b = D.objects["A"], D.objects["A.001"]
    T.select("A")
    for n in ("Made0", "Made1"):
        a.data.materials.append(D.materials.new(n))
    L.tick_like("mats")
    read = lambda: [[s.material and s.material.name for s in ob.material_slots] for ob in (a, b)]
    after = read()
    assert after == [["Made0", "Made1"]] * 2, after
    ha, hb = runtime.history_of(a), runtime.history_of(b)
    for h in (ha, hb):
        runtime._goto_history(h, len(h.entries) - 1, True)
        datarec_resolve()
    assert read() == [[], []] and D.materials.get("Made0") is None, (read(), list(D.materials))
    for h in (hb, ha):                          # the duplicate first: its slots wait for both materials
        runtime._goto_history(h, len(h.entries), True)
        datarec_resolve()
    assert read() == after, (read(), after)
    from hindsight.bl import adapters
    assert not adapters.MISSING_REFS.get(idmap.uid_of(b)), adapters.MISSING_REFS    # D60: forgotten once written


def _fcurves(a):
    """An action's F-Curves (5.0 on: in its layers' channel bags)."""
    if hasattr(a, "fcurves"):
        return list(a.fcurves)
    return [fc for ly in a.layers for st in ly.strips for cb in st.channelbags for fc in cb.fcurves]


def _scene_ops(ops, read, setup=None):
    """A fresh recorded scene (setup() first), then ops (one step each): (states, the scene's history)."""
    from tests import t1_gate_lib as L
    from tests.test_bl_t1_loose_data import run_ops
    L.fresh_scene()
    if setup is not None:
        setup()
    L.track_all()
    L.tick_like("baseline")
    return run_ops(ops, read), runtime.history_of(bpy.context.scene)


def _goto(h, pos):
    from tests.test_bl_t1_loose_data import datarec_resolve
    runtime._goto_history(h, pos, True)
    datarec_resolve()


def test_going_back_over_an_image_setting_keeps_its_paint():
    """#102 review 4: going back over a change of an image's settings replaces it with its snapshot, which
    holds no pixels of an image painted and not saved: the paint is kept."""
    def make():
        D.images.new("Painted", 4, 4).pixels.foreach_set([0.25, 0.5, 0.75, 1.0] * 16)
    read = lambda: (lambda i: i and (i.alpha_mode, _r(tuple(i.pixels[:4]))))(D.images.get("Painted"))
    states, h = _scene_ops([("make", make), ("alpha", lambda: setattr(D.images["Painted"], "alpha_mode", "PREMUL"))],
                           read)
    for pos in (1, 2):
        _goto(h, pos)
        assert read() == states[pos], (pos, read(), states[pos])


def test_going_back_over_keying_keeps_what_no_action_record_holds():
    """#102 review 5: going back over the first keyframe takes its action away (D162); forward, it is made
    again intact, with the F-Curve modifiers, pose markers and frame range no action record holds."""
    def op(ob):
        ob.keyframe_insert("color", frame=1)
        a = ob.animation_data.action
        _fcurves(a)[0].modifiers.new("NOISE")
        a.pose_markers.new("Pose")
        a.use_frame_range, a.frame_end = True, 42.0
    read = lambda: sorted((idmap.uid_of(a), [m.type for fc in _fcurves(a) for m in fc.modifiers],
                           [m.name for m in a.pose_markers], a.use_frame_range, _r(a.frame_end)) for a in D.actions)
    after, back, fwd = _back_and_forth(op, read)
    assert after and after[0][1] == ["NOISE"] and back == [] and fwd == after, (after, back, fwd)


def test_a_datablock_that_cant_be_snapshotted_is_kept():
    """#102 review 2: a datablock whose snapshot fails stays in the inventory, so no jump takes it away
    (there is no snapshot to make it again)."""
    from hindsight.bl import multires
    real = multires.snapshot_bytes

    def snap(idb, *a, **k):
        if idb.name == "Broken":
            raise RuntimeError("can't be written")
        return real(idb, *a, **k)
    multires.snapshot_bytes = snap
    try:
        read = lambda: sorted(i.name for i in D.images if i.name in ("Broken", "Other"))
        _states, h = _scene_ops([("broken", lambda: setattr(D.images.new("Broken", 4, 4), "use_fake_user", True)),
                                 ("other", lambda: setattr(D.images.new("Other", 4, 4), "use_fake_user", True))], read)
        _goto(h, len(h.entries) - 1)
        assert read() == ["Broken"], read()
    finally:
        multires.snapshot_bytes = real


def test_an_inventory_from_before_snapshots_takes_nothing_away():
    """#102 review 7: an inventory recorded before a kind was snapshotted (an older build: texts, materials and
    worlds only) doesn't hold that kind's datablocks; a jump to it takes none of them away."""
    from hindsight.bl.adapters import loose
    from tests import t1_gate_lib as L
    L.fresh_scene()
    D.images.new("Kept", 4, 4).use_fake_user = True
    idmap.claim(D.images["Kept"])
    L.track_all()
    kinds = loose._SNAP_COLLS
    loose._SNAP_COLLS = ()                  # recorded as an older build does
    try:
        L.tick_like("baseline")
        D.texts.new("one")
        L.tick_like("text one")
    finally:
        loose._SNAP_COLLS = kinds
    D.texts.new("two")
    L.tick_like("text two")
    h = runtime.history_of(bpy.context.scene)
    _goto(h, len(h.entries) - 1)
    assert "Kept" in D.images and "two" not in D.texts, (list(D.images), list(D.texts))


def test_going_back_over_deleting_a_world_makes_no_material():
    """#102 review 6: write_material makes a material again for an id it doesn't find (D163), and the scene
    writes its world's record with it: a world gone from the file came back as a Material with its id."""
    from hindsight.bl import adapters
    from hindsight.bl.adapters import materials
    from hindsight.bl.adapters.keys import WORLD_PREFIX
    from tests import t1_gate_lib as L
    L.fresh_scene()
    w = D.worlds.new("Sky")
    u = idmap.claim(w)
    rec = materials.material_record(w, "")
    D.worlds.remove(w)
    adapters.ADAPTERS["scene"].write(bpy.context.scene, {WORLD_PREFIX + u: rec})
    assert idmap.find(u) is None and "Sky" not in D.materials, (idmap.find(u), list(D.materials))


def test_made_again_uses_the_objects_it_used():
    """#102 review 3: a snapshot holds the objects its datablock uses (particle settings' instance object):
    made again, it uses the scene's object, not a copy carrying its id."""
    def make():
        p = D.particles.new("Made")
        p.use_fake_user = True
        p.render_type = "OBJECT"
        p.instance_object = D.objects["A"]
    read = lambda: ((lambda p: p and p.instance_object and p.instance_object.name)(D.particles.get("Made")),
                    sorted(o.name for o in D.objects))
    texts_walk(read, [("make", make), ("delete", lambda: D.particles.remove(D.particles["Made"]))])


def test_a_record_from_another_blender_keeps_the_loose_read_going():
    """#102 review 1: a snapshot record kept as it was recorded by another Blender (D156) has a DNA id this
    Blender never wrote: reading the loose data raised KeyError at every operation."""
    from hindsight.bl.adapters import loose
    from hindsight.bl.adapters.keys import DNA_PREFIX, SNAP_PREFIX
    _states, _h = _scene_ops([("make", lambda: setattr(D.images.new("Made", 4, 4), "use_fake_user", True))],
                             lambda: "Made" in D.images)
    sc = bpy.context.scene
    st = runtime._STATES[idmap.uid_of(sc)]
    u = idmap.uid_of(D.images["Made"])
    rec = list(st.values[("scene", SNAP_PREFIX + u)])
    st.values[("scene", DNA_PREFIX + "other")] = st.values[("scene", DNA_PREFIX + rec[1])]
    rec[1] = "other"
    st.values[("scene", SNAP_PREFIX + u)] = rec
    loose.forget_snapshots()                # as when the file is opened (in another Blender)
    loose._DNA.clear()
    loose._DINFO.pop("other", None)
    out = loose.scene_loose(sc)
    assert u in out[loose.INV_LOOSE] and out.get(DNA_PREFIX + out[SNAP_PREFIX + u][1]), sorted(out)[:6]


REVIEW = [test_going_back_over_deleting_a_world_makes_no_material, test_made_again_uses_the_objects_it_used,
          test_a_record_from_another_blender_keeps_the_loose_read_going,
          test_going_back_over_an_image_setting_keeps_its_paint,
          test_going_back_over_keying_keeps_what_no_action_record_holds,
          test_a_datablock_that_cant_be_snapshotted_is_kept, test_an_inventory_from_before_snapshots_takes_nothing_away]


if __name__ == "__main__":
    for t in TESTS + REVIEW + [test_made_again_keeps_its_id_and_users, test_digest_reads_a_pointer_to_data_not_written_as_null,
                      test_object_data_its_object_left_with_a_fake_user,
                      test_object_data_its_object_left_with_no_user_is_not_snapshotted,
                      test_object_data_with_no_user_stays_over_a_jump,
                      test_a_collection_only_an_instance_uses_is_made_again,
                      test_a_linked_duplicate_waits_for_both_materials_its_owner_makes_again,
                      test_going_back_over_keying_takes_its_action_away,
                      test_going_back_over_a_slot_takes_the_material_it_made_away,
                      test_digest_leaves_out_what_custom_properties_hold,
                      test_going_back_over_a_duplicate_takes_its_action_away,
                      test_digest_leaves_out_the_runtime_fields_of_an_id,
                      test_digest_leaves_out_what_is_after_the_end_of_a_name,
                      test_digest_leaves_out_what_is_after_the_end_of_a_string,
                      test_digest_reads_every_byte_of_a_long_string]:
        H.run(t)
    H.finish()
