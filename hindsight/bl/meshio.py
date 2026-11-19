"""Read and write Hindsight channels on Blender meshes (numpy + foreach, no bmesh)."""
from __future__ import annotations

import hashlib

import bpy

import numpy as np

from ..core.types import (
    CHANNEL_TYPES, POSITION_KEY, EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY,
    Snapshot, make_key, split_key,
)

TOPO_KEYS = (EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY)
SCULPT_NAMES = {".sculpt_mask", ".sculpt_face_set", ".hide_vert", ".hide_edge", ".hide_poly"}
_SKIP_PREFIX = (".select", ".vs.", ".es.", ".pn.", ".uv_select", ".edge_verts", ".corner_vert",
                ".corner_edge")

_bufs: dict = {}


def _buf(key: str, size: int, dtype) -> np.ndarray:
    """Reusable read buffer per channel (never handed out as a baseline)."""
    b = _bufs.get(key)
    if b is None or b.size != size or b.dtype != np.dtype(dtype):
        b = np.empty(size, dtype)
        _bufs[key] = b
    return b


def keep_buffers(keys) -> None:
    """Let go of the read buffers of channels not in keys (the last commit's channels): without it
    every attribute name ever read kept a buffer the size of its mesh for the whole session."""
    for k in [k for k in _bufs if k not in keys]:
        del _bufs[k]


def counts(me) -> dict:
    return {"POINT": len(me.vertices), "EDGE": len(me.edges),
            "FACE": len(me.polygons), "CORNER": len(me.loops)}


def _recordable(attr, sculpt_only: bool) -> bool:
    if attr.data_type not in CHANNEL_TYPES or attr.domain not in ("POINT", "EDGE", "FACE", "CORNER"):
        return False
    name = attr.name
    if name == "position":
        return False
    if name in SCULPT_NAMES:
        return True
    if name.startswith(_SKIP_PREFIX):
        return False
    if sculpt_only:
        return attr.data_type in ("FLOAT_COLOR", "BYTE_COLOR")
    return True


def channel_keys(me, sculpt_only: bool) -> list:
    keys = [POSITION_KEY]
    for a in me.attributes:
        if _recordable(a, sculpt_only):
            keys.append(make_key(a.name, a.domain, a.data_type))
    return keys


def read_channel(me, key: str, out: np.ndarray | None = None, reuse: bool = False) -> np.ndarray | None:
    """Read a channel as a flat native array. None if the attribute does not exist."""
    name, domain, dtype_name = split_key(key)
    attr = me.attributes.get(name)
    if attr is None or attr.data_type != dtype_name or attr.domain != domain:
        return None
    native, width, prop = CHANNEL_TYPES[dtype_name]
    n = len(attr.data) * width
    if out is None:
        out = _buf(key, n, native) if reuse else np.empty(n, native)
    attr.data.foreach_get(prop, out)
    return out


def write_channel(me, key: str, arr: np.ndarray | None) -> None:
    """Write a channel. arr None -> remove the attribute."""
    name, domain, dtype_name = split_key(key)
    attr = me.attributes.get(name)
    if arr is None:
        if attr is not None and name != "position":
            me.attributes.remove(attr)
        return
    if attr is not None and (attr.data_type != dtype_name or attr.domain != domain):
        me.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = me.attributes.new(name, dtype_name, domain)
        if dtype_name in ("FLOAT_COLOR", "BYTE_COLOR") and domain in ("POINT", "CORNER"):
            ca = me.color_attributes
            # Blender 5.0 forgets the active and default colour when their layer is removed (later
            # versions keep the name): a layer put back is them again, as when it was first added
            if not ca.active_color_name:
                ca.active_color_name = name
            if not ca.default_color_name:
                ca.default_color_name = name
    prop = CHANNEL_TYPES[dtype_name][2]
    attr.data.foreach_set(prop, arr)


def _int_attr(me, name: str, domain: str, dtype: str, count: int):
    """The mesh's own topology attribute (".edge_verts", ".corner_vert", ".corner_edge") if it is
    there as expected, else None. Reading or writing it is one copy of the array; the legacy
    collections (me.edges "vertices", me.loops "vertex_index") go element by element through
    RNA, 70 to 150 times slower (95 ms against 1.4 ms for the edges of a 1M-vertex mesh)."""
    a = me.attributes.get(name)
    if a is not None and a.domain == domain and a.data_type == dtype and len(a.data) == count:
        return a
    return None


def _corner_ints(me, attr_name: str, legacy: str, n: int) -> np.ndarray:
    """Corner vertex or edge indices: the attribute (".corner_vert", ".corner_edge"), the loops
    collection as the fallback."""
    out = np.empty(n, np.int32)
    a = _int_attr(me, attr_name, "CORNER", "INT", n)
    if a is not None:
        a.data.foreach_get("value", out)
    else:
        me.loops.foreach_get(legacy, out)
    return out


def _edge_verts(me, n_edges: int) -> np.ndarray:
    """The two vertices of every edge: the ".edge_verts" attribute, me.edges as the fallback."""
    out = np.empty(n_edges * 2, np.int32)
    a = _int_attr(me, ".edge_verts", "EDGE", "INT32_2D", n_edges)
    if a is not None:
        a.data.foreach_get("value", out)
    else:
        me.edges.foreach_get("vertices", out)
    return out


def _set_ints(me, name: str, domain: str, dtype: str, count: int, arr, legacy, legacy_prop: str) -> None:
    """Write topology indices through the attribute when it is there, else the legacy collection."""
    a = _int_attr(me, name, domain, dtype, count)
    if a is not None:
        a.data.foreach_set("value", arr)
    else:
        legacy.foreach_set(legacy_prop, arr)


def _topology_readers(me, c: dict) -> dict:
    def ev():
        return _edge_verts(me, c["EDGE"])

    def fo():
        out = np.empty(c["FACE"] + 1, np.int32)
        me.polygons.foreach_get("loop_start", out[:-1])
        out[-1] = c["CORNER"]
        return out
    return {EDGE_VERTS_KEY: ev,
            CORNER_VERT_KEY: lambda: _corner_ints(me, ".corner_vert", "vertex_index", c["CORNER"]),
            CORNER_EDGE_KEY: lambda: _corner_ints(me, ".corner_edge", "edge_index", c["CORNER"]),
            FACE_OFFSETS_KEY: fo}


def read_topology(me) -> dict:
    return {k: read() for k, read in _topology_readers(me, counts(me)).items()}


def read_snapshot(me, sculpt_only: bool = False, ob=None, extras: bool = False) -> Snapshot:
    """The mesh as a Snapshot. With extras (an object recorded with Record everything), also its
    vertex group weights and shape key positions (read_extras)."""
    arrays = read_topology(me)
    for key in channel_keys(me, sculpt_only):
        a = read_channel(me, key)
        if a is not None:
            arrays[key] = a
    if extras and ob is not None:
        arrays.update(read_extras(ob, me))
    return Snapshot(counts(me), arrays)


# ----------------------------------------------------------------------------- D43: weights, shape keys
# Restoring a topology step rebuilds the mesh, which drops vertex group weights and leaves shape
# keys with the old number of points. For objects recorded with Record everything they are channels
# too: one per vertex group (the weight, NaN where a vertex isn't in the group) and one per shape key
# (its positions, in key order). Groups and keys are written back by name; which groups and keys
# exist (and the keys' settings) isn't changed here. Blender's API only writes weights from 0 to 1;
# a weight outside that (Blender's bevel and inset can leave tiny negative ones) comes back clamped.
VG_PREFIX = "vg:"
SK_PREFIX = "sk:"
IMG_PREFIX = "img:"       # texture painting: the pixels of one image (float RGBA), see read_images
TEXTURE_MAX_PIXELS = 2048 * 2048    # larger images are not recorded: at 4K one stroke reads 268 MB (60 to 90 ms)


def is_extra(key: str) -> bool:
    return key.startswith(VG_PREFIX) or key.startswith(SK_PREFIX) or key.startswith(IMG_PREFIX)


def img_key(name: str) -> str:
    return make_key(IMG_PREFIX + name, "POINT", "FLOAT_COLOR")


def paint_images(ob) -> list:
    """The images a texture painting stroke on `ob` can change: those of its materials' texture nodes
    and the canvas of the image paint tool, as far as Hindsight can record them (up to
    TEXTURE_MAX_PIXELS, with pixel data)."""
    seen, out = set(), []

    def take(img):
        if img is None or img.name in seen:
            return
        seen.add(img.name)
        w, h = img.size
        if w * h and w * h <= TEXTURE_MAX_PIXELS and img.source in ("FILE", "GENERATED") and img.has_data:
            out.append(img)

    for slot in getattr(ob, "material_slots", ()):
        mat = slot.material
        if mat is not None:
            for img in getattr(mat, "texture_paint_images", ()) or ():
                take(img)
    try:
        take(bpy.context.scene.tool_settings.image_paint.canvas)
    except Exception:
        pass
    return out


def read_images(ob) -> dict:
    """{key: float32 RGBA pixels, flat} per paintable image (reused buffers, never handed out as a baseline)."""
    return read_images_named(ob, paint_images(ob))


def read_images_named(ob, images) -> dict:
    """read_images for some of the paintable images."""
    out = {}
    for img in images:
        key = img_key(img.name)
        n = img.size[0] * img.size[1] * 4
        buf = _buf(key, n, np.float32)
        img.pixels.foreach_get(buf)
        out[key] = buf
    return out


def _write_image(name: str, arr) -> None:
    img = bpy.data.images.get(name)
    if img is None or arr is None or img.size[0] * img.size[1] * 4 != arr.size:
        return
    img.pixels.foreach_set(arr)
    img.update()
    img.update_tag()


def vg_key(name: str) -> str:
    return make_key(VG_PREFIX + name, "POINT", "FLOAT")


def sk_key(index: int, name: str) -> str:
    return make_key(f"{SK_PREFIX}{index:03d}:{name}", "POINT", "FLOAT_VECTOR")


def _sk_name(key: str) -> str:
    return split_key(key)[0][len(SK_PREFIX) + 4:]


def read_weights(ob, me) -> dict:
    """One channel per vertex group: the weight, NaN where a vertex is not in the group (so a member
    at weight 0 differs from a non-member). Blender has no bulk read for weights: this loop over the
    vertices is the fastest exact one measured (a third quicker than reading `v.index` and the
    group list per vertex; bmesh is no quicker on 4.2 and 5.3)."""
    out = {}
    names = [g.name for g in getattr(ob, "vertex_groups", ())]
    n = len(me.vertices)
    if names:
        w = np.full((len(names), n), np.nan, np.float32)
        ng = len(names)
        for i, v in enumerate(me.vertices):
            for g in v.groups:
                gi = g.group
                if gi < ng:
                    w[gi, i] = g.weight
        for gi, name in enumerate(names):
            out[vg_key(name)] = w[gi].copy()
    return out


def read_extras(ob, me) -> dict:
    out = read_weights(ob, me)
    sk = getattr(me, "shape_keys", None)
    if sk is not None:
        for i, kb in enumerate(sk.key_blocks):
            co = np.empty(len(kb.data) * 3, np.float32)
            kb.data.foreach_get("co", co)
            out[sk_key(i, kb.name)] = co
    return out


def clamp_weights(arr) -> bool:
    """Clamp a weight channel to what Blender can write (0 to 1), in place. True if anything changed."""
    out = (arr < 0) | (arr > 1)          # NaN (not a member) compares False
    if out.any():
        np.clip(arr, 0.0, 1.0, out=arr, where=out)
        return True
    return False


def _write_vgroup(g, n: int, arr) -> None:
    g.remove(list(range(n)))
    members = np.nonzero(~np.isnan(arr))[0]
    if not len(members):
        return
    vals, inv = np.unique(np.clip(arr[members], 0.0, 1.0), return_inverse=True)
    for k, val in enumerate(vals):
        g.add(members[inv == k].tolist(), float(val), 'REPLACE')


def write_extras(ob, me, arrays: dict, removed=(), rebuilt: bool = False) -> None:
    """Weights and shape key positions back onto ob's mesh (rebuilt: the mesh was just rebuilt, so
    every shape key is made again at its new size, with its settings kept)."""
    n = len(me.vertices)
    for key, arr in arrays.items():
        name = split_key(key)[0]
        if name.startswith(IMG_PREFIX):
            _write_image(name[len(IMG_PREFIX):], arr)
            continue
        if name.startswith(VG_PREFIX):
            g = ob.vertex_groups.get(name[len(VG_PREFIX):])
            if g is not None and arr is not None and arr.size == n:
                _write_vgroup(g, n, arr)
    for key in removed:
        name = split_key(key)[0]
        if name.startswith(VG_PREFIX):
            g = ob.vertex_groups.get(name[len(VG_PREFIX):])
            if g is not None:
                g.remove(list(range(n)))
    sk_arrays = {_sk_name(k): a for k, a in arrays.items() if split_key(k)[0].startswith(SK_PREFIX)}
    key = me.shape_keys
    if key is None:
        return
    blocks = list(key.key_blocks)
    if rebuilt or any(len(kb.data) != n for kb in blocks):
        pos = np.empty(n * 3, np.float32)
        me.attributes["position"].data.foreach_get("vector", pos)
        keep = [(kb.name, kb.value, kb.slider_min, kb.slider_max, kb.mute, kb.relative_key.name,
                 kb.vertex_group, kb.interpolation) for kb in blocks]
        use_relative = key.use_relative
        ob.shape_key_clear()
        made = {}
        for name, *_rest in keep:
            kb = ob.shape_key_add(name=name, from_mix=False)
            kb.name = name
            co = sk_arrays.get(name)
            kb.data.foreach_set("co", co if co is not None and co.size == n * 3 else pos)
            made[name] = kb
        for name, value, smin, smax, mute, rel, vgroup, interp in keep:
            kb = made[name]
            kb.slider_min, kb.slider_max = smin, smax
            kb.value, kb.mute = value, mute
            if rel in made:
                kb.relative_key = made[rel]
            kb.vertex_group, kb.interpolation = vgroup, interp
        me.shape_keys.use_relative = use_relative
        return
    for kb in blocks:
        co = sk_arrays.get(kb.name)
        if co is not None and co.size == n * 3:
            kb.data.foreach_set("co", co)


def fingerprint(me, ob=None) -> str:
    """A digest of the whole mesh as Hindsight records it (counts, topology, every recorded
    channel; with ob, its weights and shape keys too). Used to tell which recorded state Blender's
    undo put back after a Hindsight jump."""
    # the same digest as hashing read_snapshot(me, ob=ob, extras=ob is not None), but each array is
    # read, hashed and let go in turn, so the whole mesh is never copied at once
    c = counts(me)
    readers = _topology_readers(me, c)
    for key in channel_keys(me, False):
        readers[key] = lambda key=key: read_channel(me, key)
    if ob is not None:
        readers.update({k: (lambda a=a: a) for k, a in read_extras(ob, me).items()})
    h = hashlib.blake2b(digest_size=16)
    h.update(repr(sorted(c.items())).encode())
    for key in sorted(readers):
        a = readers[key]()
        if a is None:
            continue
        a = np.ascontiguousarray(a)
        h.update(key.encode() + b"\0" + str(a.dtype).encode() + b"\0")
        h.update(a.view(np.uint8))
        a = None                   # let it go before the next one is read
    return h.hexdigest()


def topology_equal(me, snap_counts: dict, snap_arrays: dict, deep=False) -> bool:
    """Same element counts; with deep, also the same corner vertices, and unless deep is "corners",
    the same edges."""
    if counts(me) != snap_counts:
        return False
    if not deep:
        return True
    cv = _corner_ints(me, ".corner_vert", "vertex_index", snap_counts["CORNER"])
    ref = snap_arrays.get(CORNER_VERT_KEY)
    if ref is None or not np.array_equal(cv, ref):
        return False
    if deep == "corners":
        return True
    ev = _edge_verts(me, snap_counts["EDGE"])
    ref = snap_arrays.get(EDGE_VERTS_KEY)
    return ref is not None and np.array_equal(ev, ref)


def write_snapshot(me, snap: Snapshot, ob=None) -> None:
    """Rebuild the whole mesh from a Snapshot. Object must NOT be in Edit/Sculpt mode. With ob, its
    vertex group weights and shape keys are put back too (D43)."""
    c = snap.counts
    a = snap.arrays
    # clear_geometry also drops the vertex group list (Blender keeps it on the mesh): keep the groups
    groups = [(g.name, g.lock_weight) for g in ob.vertex_groups] if ob is not None else []
    active_group = ob.vertex_groups.active_index if groups else 0
    me.clear_geometry()
    me.vertices.add(c["POINT"])
    me.edges.add(c["EDGE"])
    me.loops.add(c["CORNER"])
    me.polygons.add(c["FACE"])
    me.attributes["position"].data.foreach_set("vector", a[POSITION_KEY])
    if c["EDGE"]:
        _set_ints(me, ".edge_verts", "EDGE", "INT32_2D", c["EDGE"], a[EDGE_VERTS_KEY], me.edges, "vertices")
    if c["CORNER"]:
        _set_ints(me, ".corner_vert", "CORNER", "INT", c["CORNER"], a[CORNER_VERT_KEY], me.loops, "vertex_index")
        _set_ints(me, ".corner_edge", "CORNER", "INT", c["CORNER"], a[CORNER_EDGE_KEY], me.loops, "edge_index")
    if c["FACE"]:
        me.polygons.foreach_set("loop_start", a[FACE_OFFSETS_KEY][:-1])
    for key, arr in a.items():
        if key in TOPO_KEYS or key == POSITION_KEY or is_extra(key):
            continue
        try:
            write_channel(me, key, arr)
        except Exception:
            pass
    if ob is not None:
        for name, lock in groups:
            if ob.vertex_groups.get(name) is None:
                g = ob.vertex_groups.new(name=name)
                g.lock_weight = lock
        if groups:
            ob.vertex_groups.active_index = active_group
        write_extras(ob, me, {k: v for k, v in a.items() if is_extra(k)}, rebuilt=True)
    me.update()


def write_changes(me, arrays: dict, keys, removed=(), ob=None) -> None:
    extra, extra_removed = {}, []
    for key in keys:
        if key in TOPO_KEYS or key == "__TOPO__":
            continue
        if is_extra(key):
            if key in removed:
                extra_removed.append(key)
            elif key in arrays:
                extra[key] = arrays[key]
            continue
        if key in removed:
            write_channel(me, key, None)
        elif key in arrays:
            write_channel(me, key, arrays[key])
    for key in removed:
        if key not in keys:
            if is_extra(key):
                extra_removed.append(key)
            else:
                write_channel(me, key, None)
    if ob is not None and (extra or extra_removed):
        write_extras(ob, me, extra, extra_removed)
    me.update()
