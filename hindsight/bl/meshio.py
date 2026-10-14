"""Read and write Hindsight channels on Blender meshes (numpy + foreach, no bmesh)."""
from __future__ import annotations

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


def read_topology(me) -> dict:
    c = counts(me)
    ev = np.empty(c["EDGE"] * 2, np.int32)
    me.edges.foreach_get("vertices", ev)
    cv = np.empty(c["CORNER"], np.int32)
    me.loops.foreach_get("vertex_index", cv)
    ce = np.empty(c["CORNER"], np.int32)
    me.loops.foreach_get("edge_index", ce)
    fo = np.empty(c["FACE"] + 1, np.int32)
    me.polygons.foreach_get("loop_start", fo[:-1])
    fo[-1] = c["CORNER"]
    return {EDGE_VERTS_KEY: ev, CORNER_VERT_KEY: cv, CORNER_EDGE_KEY: ce, FACE_OFFSETS_KEY: fo}


def read_snapshot(me, sculpt_only: bool = False) -> Snapshot:
    arrays = read_topology(me)
    for key in channel_keys(me, sculpt_only):
        a = read_channel(me, key)
        if a is not None:
            arrays[key] = a
    return Snapshot(counts(me), arrays)


def topology_equal(me, snap_counts: dict, snap_arrays: dict, deep: bool = False) -> bool:
    if counts(me) != snap_counts:
        return False
    if not deep:
        return True
    cv = np.empty(snap_counts["CORNER"], np.int32)
    me.loops.foreach_get("vertex_index", cv)
    ref = snap_arrays.get(CORNER_VERT_KEY)
    if ref is None or not np.array_equal(cv, ref):
        return False
    ev = np.empty(snap_counts["EDGE"] * 2, np.int32)
    me.edges.foreach_get("vertices", ev)
    ref = snap_arrays.get(EDGE_VERTS_KEY)
    return ref is not None and np.array_equal(ev, ref)


def write_snapshot(me, snap: Snapshot) -> None:
    """Rebuild the whole mesh from a Snapshot. Object must NOT be in Edit/Sculpt mode."""
    c = snap.counts
    a = snap.arrays
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
        if key in TOPO_KEYS or key == POSITION_KEY:
            continue
        try:
            write_channel(me, key, arr)
        except Exception:
            pass
    me.update()


def write_changes(me, arrays: dict, keys, removed=()) -> None:
    for key in keys:
        if key in TOPO_KEYS or key == "__TOPO__":
            continue
        if key in removed:
            write_channel(me, key, None)
        elif key in arrays:
            write_channel(me, key, arrays[key])
    for key in removed:
        if key not in keys:
            write_channel(me, key, None)
    me.update()
