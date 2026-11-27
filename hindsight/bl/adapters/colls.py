"""Data adapters: collection settings and the collection tree (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
from .. import idmap, log
from .core import _UI_PROPS, set_struct_values, struct_values
from .keys import COLL_NAMES, _RECREATED
from .meshlists import _give_name
from .settings import _ID_SKIP


_COLL_SKIP = frozenset(_UI_PROPS | _ID_SKIP | {"active_exporter_index"})
_LC_FLAGS = ("exclude", "hide_viewport", "holdout", "indirect_only")


def _walk_colls(c, out):
    for ch in c.children:
        out.append(ch)
        _walk_colls(ch, out)


def collection_settings(sc) -> dict:
    """Every collection's own settings (hide in viewport and render, selectable, colour tag, Line
    Art, instance offset) and, per view layer, each collection's Exclude, Hide in Viewport, Holdout
    and Indirect Only."""
    colls = []
    _walk_colls(sc.collection, colls)
    vals = {}
    for c in colls:
        if c.library is not None:
            continue
        cu = idmap.claim(c)
        if cu:
            vals[cu] = struct_values(c, _COLL_SKIP)
    layers = {}
    for vl in sc.view_layers:
        rows = {}
        stack = list(vl.layer_collection.children)
        while stack:
            lc = stack.pop()
            cu = idmap.uid_of(lc.collection)
            if cu:
                rows[cu] = [bool(getattr(lc, f)) for f in _LC_FLAGS]
            stack.extend(lc.children)
        layers[vl.name] = rows
    return {"colls": vals, "layers": layers}


def write_collection_settings(sc, rec) -> None:
    if not isinstance(rec, dict):
        return
    try:
        for cu, v in (rec.get("colls") or {}).items():
            c = idmap.find(cu)
            if c is not None and isinstance(v, dict):
                set_struct_values(c, v)
        for vl in sc.view_layers:
            rows = (rec.get("layers") or {}).get(vl.name)
            if not rows:
                continue
            if vl.layer_collection is None:
                vl.update()                       # #110: a scene just made has none until then
            stack = list(vl.layer_collection.children)
            while stack:
                lc = stack.pop()
                want = rows.get(idmap.uid_of(lc.collection))
                if want:
                    for f, w in zip(_LC_FLAGS, want):     # Exclude first: it changes the others
                        if bool(getattr(lc, f)) != w:
                            setattr(lc, f, w)
                stack.extend(lc.children)
    except Exception as e:
        log.error_once(("coll_settings", str(e)), f"could not restore collection settings: {e}", e)


def collection_tree(scene) -> list:
    """The scene's collections as [[id, name, parent id or "scene"], ...] in tree order."""
    rows = []

    def walk(c, parent):
        for ch in c.children:
            u = idmap.claim(ch)
            rows.append([u, ch.name, parent])
            if u:
                COLL_NAMES[u] = ch.name
            walk(ch, u)
    walk(scene.collection, "scene")
    return rows


def _write_collection_tree(scene, want: list) -> None:
    """Make the scene's collection tree match: collections taken out are only unlinked (their
    objects and the datablock stay, so an object step can still put objects back in them),
    collections that no longer exist are made again with their id and name."""
    if collection_tree(scene) == want:
        return

    def coll(ref):
        return scene.collection if ref == "scene" else idmap.find(ref)
    want_links = {(u, p) for u, _n, p in want}
    for u, _n, p in collection_tree(scene)[::-1]:            # children before parents
        if (u, p) not in want_links:
            par, ch = coll(p), idmap.find(u)
            if par is not None and ch is not None and ch.name in par.children:
                par.children.unlink(ch)
    names = {u: name for u, name, _p in want}
    for u, name, p in want:
        ch = idmap.find(u)
        if ch is None:
            ch = bpy.data.collections.new(name)
            ch[idmap.UID_PROP] = u
            idmap.note(ch, u)
            _RECREATED.add(u)
        _give_name(ch, name, names)
        par = coll(p)
        if par is not None and ch.name not in par.children:
            par.children.link(ch)
    # child order: relink the children of a parent whose order differs
    order = {}
    for u, _n, p in want:
        order.setdefault(p, []).append(u)
    for p, kids in order.items():
        par = coll(p)
        if par is None:
            continue
        have = [idmap.uid_of(c) for c in par.children]
        if [k for k in have if k in kids] != kids:
            for k in kids:
                c = idmap.find(k)
                if c is not None and c.name in par.children:
                    par.children.unlink(c)
                    par.children.link(c)
