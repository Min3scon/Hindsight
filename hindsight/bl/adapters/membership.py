"""Data adapters: collection membership and constraints (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
from .. import idmap, log
from .core import set_struct_values


_UC_DIRECT = 4      # objects read with Object.users_collection in a frame before the table is built


def users_collection(ob) -> tuple:
    """ob.users_collection: the collections it is in, in the same order (bpy.data.collections, then
    each scene's own collection). Blender's version reads every collection's objects for each object
    it is asked about; in a frame (idmap.frame) that is done once for all of them."""
    fc = idmap.frame_cache()
    if fc is None:
        return ob.users_collection
    m = fc.get("users_collection")
    if m is None:
        n = fc["users_collection_n"] = fc.get("users_collection_n", 0) + 1
        if n <= _UC_DIRECT:
            return ob.users_collection      # a few objects: cheaper than reading everything
        m = {}
        for c in bpy.data.collections:
            for o in c.objects:
                m.setdefault(o.as_pointer(), []).append(c)
        for sc in bpy.data.scenes:
            c = sc.collection
            for o in c.objects:
                m.setdefault(o.as_pointer(), []).append(c)
        fc["users_collection"] = m
    return tuple(m.get(ob.as_pointer(), ()))


def _coll_ref(c) -> str:
    """A collection as a value: its Hindsight id, or "scene:<id>" for a scene's own collection."""
    for sc in bpy.data.scenes:
        if sc.collection == c:
            return "scene:" + (idmap.claim(sc) or "")
    return idmap.claim(c) or ""


def _coll_from_ref(ref: str):
    if ref.startswith("scene:"):
        sc = idmap.find(ref[6:])
        return sc.collection if sc is not None else None
    return idmap.find(ref)


def _write_constraints(ob, want: list, holder=None, base: str = "") -> None:
    """Put a constraint stack back on ob (or on holder, a pose bone of ob, whose path is base)."""
    st = holder if holder is not None else ob
    have = [(c.type, c.name) for c in st.constraints]
    if have == [(w["type"], w["name"]) for w in want]:
        pairs = list(zip(st.constraints, want))
    else:
        for c in list(st.constraints):
            st.constraints.remove(c)
        # each constraint made gets its own recorded values: one that can't be made is left out
        # (zipping the stack with the recorded list gave every later one the wrong values)
        pairs = []
        for w in want:
            try:
                c = st.constraints.new(w["type"])
            except Exception as e:
                log.error_once(("constraint_new", str(e)), f"could not restore a constraint: {e}", e)
                continue
            try:
                c.name = w["name"]
            except Exception as e:
                log.error_once(("constraint_name", str(e)), f"could not name a restored constraint: {e}", e)
            pairs.append((c, w))
    for c, w in pairs:
        set_struct_values(c, w.get("values") or {}, ob, f'{base}constraints["{c.name}"].')
