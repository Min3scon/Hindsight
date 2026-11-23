"""D154 (#13): with Record everything, making and deleting a file-level datablock (an image, action, palette,
brush, line style, mask, texture...) is taken back like a text's (D152): going back over the making removes
it (when nothing uses it), going back over its deletion makes it again, exactly, from a snapshot of it (the
datablock written and loaded with Blender's library functions); forward the other way round. A change of its
settings between is a step too, put back from the snapshot."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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


# kind: (make it, read its state, change a setting)
KINDS = {
    "images": (lambda: D.images.new("Made", 8, 8, alpha=True),
               lambda i: (i.size[:], i.generated_type, i.alpha_mode, i.use_fake_user),
               lambda i: setattr(i, "generated_type", "UV_GRID")),
    "actions": (lambda: setattr(D.actions.new("Made"), "use_fake_user", True),
                lambda a: (a.use_frame_range, _r(a.frame_start), _r(a.frame_end), a.use_fake_user),
                lambda a: (setattr(a, "use_frame_range", True), setattr(a, "frame_end", 42.0))),
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
}


def walk(kind):
    make, state, change = KINDS[kind]
    coll = lambda: getattr(bpy.data, kind)

    def read():
        idb = coll().get("Made")
        return None if idb is None else state(idb)
    states = texts_walk(read, [("make", make), ("change", lambda: change(coll()["Made"])),
                               ("delete", lambda: coll().remove(coll()["Made"]))])
    assert states[0] is None and states[-1] is None and states[1] != states[2], (kind, states)



def _test(kind):
    def t():
        walk(kind)
    t.__name__ = f"test_{kind}_made_changed_and_deleted"
    return t


TESTS = [_test(k) for k in KINDS]


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
    ptrs = set(info[1][int.from_bytes(raw[p + so:p + so + 4], "little")][1].tolist())
    word = lambda o: raw[p + hs + o:p + hs + o + 8]
    at = p + hs + next(o for o in sorted(ptrs) if o % 8 == 0 and not any(word(o)))         # a null pointer
    data = p + hs + next(o for o in range(64, 1600, 8) if o not in ptrs and any(word(o)))   # a set data word
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


if __name__ == "__main__":
    for t in TESTS + [test_made_again_keeps_its_id_and_users, test_digest_reads_a_pointer_to_data_not_written_as_null,
                      test_digest_leaves_out_what_custom_properties_hold]:
        H.run(t)
    H.finish()
