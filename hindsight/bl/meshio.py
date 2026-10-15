"""Read and write Hindsight channels on Blender meshes (numpy + foreach, no bmesh)."""
from __future__ import annotations

import hashlib

import numpy as np

from ..core.types import (
    CHANNEL_TYPES, POSITION_KEY, MASK_KEY, FACE_SET_KEY, HIDE_VERT_KEY, HIDE_EDGE_KEY,
    HIDE_POLY_KEY, EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY,
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
    prop = CHANNEL_TYPES[dtype_name][2]
    attr.data.foreach_set(prop, arr)


def _corner_ints(me, attr_name: str, legacy: str, n: int) -> np.ndarray:
    """Corner vertex or edge indices. The attribute (".corner_vert", ".corner_edge") reads about
    twice as fast as the loops collection; the loops collection is the fallback."""
    out = np.empty(n, np.int32)
    a = me.attributes.get(attr_name)
    if a is not None and a.domain == "CORNER" and a.data_type == "INT" and len(a.data) == n:
        a.data.foreach_get("value", out)
    else:
        me.loops.foreach_get(legacy, out)
    return out


def read_topology(me) -> dict:
    c = counts(me)
    ev = np.empty(c["EDGE"] * 2, np.int32)
    me.edges.foreach_get("vertices", ev)
    cv = _corner_ints(me, ".corner_vert", "vertex_index", c["CORNER"])
    ce = _corner_ints(me, ".corner_edge", "edge_index", c["CORNER"])
    fo = np.empty(c["FACE"] + 1, np.int32)
    me.polygons.foreach_get("loop_start", fo[:-1])
    fo[-1] = c["CORNER"]
    return {EDGE_VERTS_KEY: ev, CORNER_VERT_KEY: cv, CORNER_EDGE_KEY: ce, FACE_OFFSETS_KEY: fo}


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


def is_extra(key: str) -> bool:
    return key.startswith(VG_PREFIX) or key.startswith(SK_PREFIX)


def vg_key(name: str) -> str:
    return make_key(VG_PREFIX + name, "POINT", "FLOAT")


def sk_key(index: int, name: str) -> str:
    return make_key(f"{SK_PREFIX}{index:03d}:{name}", "POINT", "FLOAT_VECTOR")


def _sk_name(key: str) -> str:
    return split_key(key)[0][len(SK_PREFIX) + 4:]


def read_extras(ob, me) -> dict:
    out = {}
    names = [g.name for g in getattr(ob, "vertex_groups", ())]
    n = len(me.vertices)
    if names:
        w = np.full((len(names), n), np.nan, np.float32)
        for v in me.vertices:
            gs = v.groups
            if len(gs):
                i = v.index
                for g in gs:
                    if g.group < len(names):
                        w[g.group, i] = g.weight
        for gi, name in enumerate(names):
            out[vg_key(name)] = w[gi].copy()
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
    snap = read_snapshot(me, ob=ob, extras=ob is not None)
    h = hashlib.blake2b(digest_size=16)
    h.update(repr(sorted(snap.counts.items())).encode())
    for key in sorted(snap.arrays):
        a = np.ascontiguousarray(snap.arrays[key])
        h.update(key.encode() + b"\0" + str(a.dtype).encode() + b"\0")
        h.update(a.view(np.uint8))
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
    ev = np.empty(snap_counts["EDGE"] * 2, np.int32)
    me.edges.foreach_get("vertices", ev)
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
        me.edges.foreach_set("vertices", a[EDGE_VERTS_KEY])
    if c["CORNER"]:
        me.loops.foreach_set("vertex_index", a[CORNER_VERT_KEY])
        me.loops.foreach_set("edge_index", a[CORNER_EDGE_KEY])
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
