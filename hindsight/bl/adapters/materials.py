"""Data adapters: materials and node groups (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
from .. import idmap, log
from . import core
from .core import _UI_PROPS, _note_ref, animated_paths, set_struct_values, struct_values, to_rna
from .idprops import anim_of, id_props
from .keys import (
    ANIMATED, ANIM_KEY, MATA_PREFIX, MATP_PREFIX, MAT_PREFIX, NGA_PREFIX, NG_PREFIX, _ACT_CACHE,
    _BONES_CACHE, _EXT_CACHE, _EXT_DEPS, _GEOM_CACHE, _MAT_CACHE, _OBMAT_CACHE, _VAL_CACHE)


_MAT_SKIP = frozenset(_UI_PROPS | {"preview_render_type", "use_preview_world", "paint_active_slot",
                                   "use_nodes", "pass_index"}) - {"pass_index"}
_NODE_SKIP = frozenset(_UI_PROPS | {"location", "location_absolute", "width", "height", "dimensions",
                                    "select", "show_options", "show_preview", "show_texture", "hide",
                                    "is_active_output", "internal_links", "parent", "warning_propagation",
                                    "use_custom_color", "color", "name", "bl_idname", "bl_label",
                                    "bl_description", "bl_icon", "bl_static_type", "bl_width_default",
                                    "bl_width_min", "bl_width_max", "bl_height_default", "bl_height_min",
                                    "bl_height_max", "type"})
_MAT_OWNER_OF: dict = {}     # material session_uid -> uid of the object that records it
_TREE_MAT: dict = {}         # node tree session_uid -> material session_uid


def material_changed(idb) -> str | None:
    """A Material or a node tree changed: forget its record. Returns the uid of the object that
    records it (to be compared again), or None."""
    try:
        k = idb.session_uid
    except Exception:
        return None
    if isinstance(idb, bpy.types.NodeTree):
        _MAT_GROUPS.clear()
        k = _TREE_MAT.get(k, k)          # a material's own tree, or a node group (D126)
    _MAT_CACHE.pop(k, None)
    return _MAT_OWNER_OF.get(k)


def forget_materials() -> None:
    _EXT_CACHE.clear()
    _EXT_DEPS.clear()
    _OBMAT_CACHE.clear()
    _BONES_CACHE.clear()
    _MAT_GROUPS.clear()
    _VAL_CACHE.clear()
    _GEOM_CACHE.clear()
    _ACT_CACHE.clear()
    _MAT_CACHE.clear()
    _MAT_OWNER_OF.clear()
    _TREE_MAT.clear()
_IFACE_SKIP = frozenset(_UI_PROPS | {"name", "socket_type", "select", "bl_socket_idname", "identifier",
                                     "item_type", "in_out", "index", "position", "parent", "is_panel_toggle"})


def _groups_in_tree(nt, out: list, seen: set) -> None:
    for n in nt.nodes:
        g = getattr(n, "node_tree", None)
        if g is not None and g.library is None and g.as_pointer() not in seen:
            seen.add(g.as_pointer())
            out.append(g)
            _groups_in_tree(g, out, seen)


def groups_of(ob) -> list:
    """Node groups this object's Geometry Nodes modifiers and its materials use (nested ones too)."""
    if not len(bpy.data.node_groups):
        return []
    out, seen = [], set()
    for m in getattr(ob, "modifiers", ()):
        g = getattr(m, "node_group", None)
        if g is not None and g.library is None and g.as_pointer() not in seen:
            seen.add(g.as_pointer())
            out.append(g)
            _groups_in_tree(g, out, seen)
    for sl in getattr(ob, "material_slots", ()):
        mt = sl.material
        nt = getattr(mt, "node_tree", None) if mt is not None else None
        if nt is None:
            continue
        lst = _MAT_GROUPS.get(nt.session_uid)
        if lst is None:                  # kept until a node tree changes (walking every node is slow)
            lst = []
            _groups_in_tree(nt, lst, set())
            _MAT_GROUPS[nt.session_uid] = lst
        for g in lst:
            if g.as_pointer() not in seen:
                seen.add(g.as_pointer())
                out.append(g)
    return out


_MAT_GROUPS: dict = {}       # material node tree session_uid -> node groups it uses (nested too)


def group_owner_map() -> dict:
    """node group pointer -> uid of the object that records it (the first recorded object by name
    that uses it). Kept for one recorder frame."""
    fc = idmap.frame_cache()
    if fc is not None and "ng_owner" in fc:
        return fc["ng_owner"]
    from .. import datarec
    users = []
    for u in datarec.tracked_uids():
        ob = idmap.find(u)
        if isinstance(ob, bpy.types.Object):
            users.append((ob.name, u, ob))
    out = {}
    for _n, u, ob in sorted(users, key=lambda t: t[0]):
        for g in groups_of(ob):
            out.setdefault(g.as_pointer(), u)
    if fc is not None:
        fc["ng_owner"] = out
    return out


def interface_items(ng) -> list:
    iface = getattr(ng, "interface", None)
    if iface is None:
        return []
    rows = []
    for it in iface.items_tree:
        if it.item_type == "SOCKET":
            rows.append(["SOCKET", it.in_out, it.name, it.socket_type, it.identifier,
                         struct_values(it, _IFACE_SKIP)])
        else:
            rows.append(["PANEL", "", it.name, "", "", struct_values(it, _IFACE_SKIP)])
    return rows


def group_record(ng, owner=None) -> dict:
    k = ng.session_uid
    if owner is not None:
        _MAT_OWNER_OF[k] = owner
    rec = _MAT_CACHE.get(k)
    if rec is None:
        rec = _MAT_CACHE[k] = {"tree": capture_tree(ng), "iface": interface_items(ng)}
    return rec


def owned_groups(ob) -> dict:
    groups = groups_of(ob)
    if not groups:
        return {}
    uid = idmap.uid_of(ob)
    owners = None
    out = {}
    for g in groups:
        if g.users > 1 + bool(g.use_fake_user):
            if owners is None:
                owners = group_owner_map()
            if owners.get(g.as_pointer()) != uid:
                continue
        gu = idmap.claim(g)
        if gu:
            try:
                out[NG_PREFIX + gu] = group_record(g, uid)
                out[NGA_PREFIX + gu] = anim_of(g)            # D146: always there (None without animation)
            except Exception:
                continue
    return out


def write_interface(ng, rows) -> None:
    iface = getattr(ng, "interface", None)
    if iface is None or not isinstance(rows, list):
        return
    want_ids = {r[4] for r in rows if r[0] == "SOCKET"}
    for it in [x for x in iface.items_tree if x.item_type == "SOCKET" and x.identifier not in want_ids]:
        iface.remove(it)
    have = {x.identifier: x for x in iface.items_tree if x.item_type == "SOCKET"}
    for r in rows:
        if r[0] != "SOCKET" or r[4] in have:
            continue
        try:
            it = iface.new_socket(r[2], in_out=r[1], socket_type=r[3])
            have[r[4]] = it              # (a socket made again gets a new identifier: Blender's)
        except Exception as e:
            log.error_once(("iface", str(e)), f"could not restore a group input or output: {e}", e)
    order = [have[r[4]] for r in rows if r[0] == "SOCKET" and r[4] in have]
    for pos, it in enumerate(order):
        try:
            if it.position != pos:
                iface.move(it, pos)
        except Exception:
            pass
    for r in rows:
        it = have.get(r[4]) if r[0] == "SOCKET" else None
        if it is None:
            continue
        try:
            if it.name != r[2]:
                it.name = r[2]
            set_struct_values(it, r[5] or {})
        except Exception:
            pass


def write_group(gu: str, rec) -> None:
    ng = idmap.find(gu)
    if ng is None or not isinstance(rec, dict):
        return
    _MAT_CACHE.pop(ng.session_uid, None)
    try:
        write_interface(ng, rec.get("iface"))
        if rec.get("tree"):
            write_tree(ng, rec["tree"])
    except Exception as e:
        log.error_once(("group", str(e)), f"could not restore the node group {ng.name}: {e}", e)
    _MAT_CACHE.pop(ng.session_uid, None)


def material_owner_map() -> dict:
    """material pointer -> uid of the tracked object that records it: the first by name among the
    tracked objects that use it (as for shared meshes, D39). Kept for one recorder frame."""
    fc = idmap.frame_cache()
    if fc is not None and "mat_owner" in fc:
        return fc["mat_owner"]
    from .. import datarec
    out = {}
    users = []
    for u in datarec.tracked_uids():
        ob = idmap.find(u)
        if isinstance(ob, bpy.types.Object) and ob.data is not None and hasattr(ob.data, "materials"):
            users.append((ob.name, u, ob))
    for _n, u, ob in sorted(users):
        for sl in ob.material_slots:
            m = sl.material
            if m is not None and m.library is None:
                out.setdefault(m.as_pointer(), u)
    if fc is not None:
        fc["mat_owner"] = out
    return out


def owned_materials(ob) -> dict:
    uid = idmap.uid_of(ob)
    out = {}
    seen = set()
    owners = None
    single = ob.data is not None and ob.data.users == 1
    for sl in ob.material_slots:
        m = sl.material
        if m is None or m.library is not None or m.as_pointer() in seen:
            continue
        seen.add(m.as_pointer())
        # used by this object alone (its only user is this slot): no need to look at the others
        if not (m.users == 1 and (single or sl.link == "OBJECT")):
            if owners is None:
                owners = material_owner_map()
            if owners.get(m.as_pointer()) != uid:
                continue
        mu = idmap.claim(m)
        if mu:
            try:
                out[MAT_PREFIX + mu] = material_record(m, uid)
            except Exception:
                continue
    return out


def _owned_refs(ob, owned) -> list:
    """[(material id, material)] for the owned records (kept with them: no search every time)."""
    if not owned:
        return []
    by_id = {}
    for sl in ob.material_slots:
        m = sl.material
        if m is not None:
            u = idmap.uid_of(m)
            if u is not None:
                by_id[u] = m
    return [(k[len(MAT_PREFIX):], by_id[k[len(MAT_PREFIX):]]) for k in owned
            if k[len(MAT_PREFIX):] in by_id]


def material_props(refs) -> dict:
    """D114/D125: the owned materials' custom properties and add-on data, and their node trees'
    animation, read every time (setting m["name"] = value doesn't tell the depsgraph)."""
    out = {}
    for mu, m in refs:
        out[MATP_PREFIX + mu] = id_props(m, animated_paths(m) if m.animation_data is not None else None)
        nt = getattr(m, "node_tree", None)
        if nt is not None:
            for k, v in anim_of(nt).items():
                out[(MATA_PREFIX + mu + "|" + k) if k == ANIM_KEY else k] = v
    return out


def material_record(m, owner=None) -> dict:
    k = m.session_uid
    if owner is not None:
        _MAT_OWNER_OF[k] = owner
    rec = _MAT_CACHE.get(k)
    if rec is not None:
        return rec
    vals = struct_values(m, _MAT_SKIP)
    for p in animated_paths(m):     # (a Material has no path_from_id: struct_values can't mark them)
        if p in vals:
            vals[p] = ANIMATED
    rec = {"values": vals}
    nt = getattr(m, "node_tree", None)
    if nt is not None:
        rec["tree"] = capture_tree(nt)
        _TREE_MAT[nt.session_uid] = k
    _MAT_CACHE[k] = rec
    return rec


def _socket_value(sock):
    if not hasattr(sock, "default_value"):
        return None
    v = sock.default_value
    if isinstance(v, bpy.types.ID):
        return {"ref": idmap.ref_of(v)}
    try:
        return core.normalise(v)
    except Exception:
        return None


def capture_tree(nt) -> dict:
    """A node tree: its nodes (name, type), each node's own settings and input values, and its links.
    A node's position isn't part of it (moving a node isn't a step)."""
    nodes = []
    vals = {}
    anim = animated_paths(nt)       # animated values change with the frame: left out
    for n in nt.nodes:
        nodes.append([n.name, n.bl_idname])
        pre = f'nodes["{bpy.utils.escape_identifier(n.name)}"].' if anim else ""
        rec = {"props": struct_values(n, _NODE_SKIP, anim=anim)}
        ins = []
        for i, sock in enumerate(n.inputs):
            if anim and f"{pre}inputs[{i}].default_value" in anim:
                continue
            v = _socket_value(sock)
            if v is not None:
                ins.append([sock.identifier, v])
        if ins:
            rec["in"] = ins
        if n.bl_idname in ("ShaderNodeValue", "ShaderNodeRGB") and n.outputs:
            ov = _socket_value(n.outputs[0])
            if ov is not None:
                rec["out"] = ov
        items = _node_items(n)                  # D130: zone and switch items (their sockets)
        if items:
            rec["items"] = items
        po = getattr(n, "paired_output", None)  # D130: a zone's input node and its output node
        if po is not None:
            rec["pair"] = po.name
        vals[n.name] = rec
    # sockets by identifier, and by position for item sockets made again (new identifiers)
    links = [[l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier,
              _sock_index(l.from_node.outputs, l.from_socket), _sock_index(l.to_node.inputs, l.to_socket)]
             for l in nt.links]
    return {"nodes": nodes, "node": vals, "links": links}


_ITEM_COLLS_SKIP = frozenset({"inputs", "outputs", "internal_links", "panel_states"})
_ITEM_SKIP = frozenset(_UI_PROPS | {"color"})


def _sock_index(socks, sock) -> int:
    for i, x in enumerate(socks):
        if x == sock:
            return i
    return -1


def _node_items(n) -> dict:
    out = {}
    for p in n.bl_rna.properties:
        if p.type != "COLLECTION" or p.identifier in _ITEM_COLLS_SKIP:
            continue
        coll = getattr(n, p.identifier, None)
        if coll is None or not hasattr(coll, "new"):
            continue
        out[p.identifier] = [struct_values(it, _ITEM_SKIP) for it in coll]
    return out


def _item_key(v) -> tuple:
    return (v.get("name"), v.get("socket_type") or v.get("data_type"))


def _write_items(node, items: dict) -> None:
    for cname, want in items.items():
        coll = getattr(node, cname, None)
        if coll is None:
            continue
        have = [_item_key(struct_values(it, _ITEM_SKIP)) for it in coll]
        if have != [_item_key(w) for w in want]:
            coll.clear()
            for w in want:
                t = w.get("socket_type") or w.get("data_type")
                name = w.get("name") or ""
                for args in ((t, name), (name,), ()):
                    if args and args[0] is None:
                        continue
                    try:
                        coll.new(*args)
                        break
                    except Exception:
                        continue
        for it, w in zip(coll, want):
            set_struct_values(it, {k: x for k, x in w.items() if k not in ("socket_type", "data_type")})


def _make_material_again(muid: str, rec: dict, coll: str):
    """D163: a material made with a slot that a jump took away going back, or (#123) a scene's world that a save
    left out with the deleted scene that alone used it: made again under its id (the record, written next, holds
    its name and settings)."""
    name = (rec.get("values") or {}).get("name")
    if not isinstance(name, str):
        return None
    try:
        m = getattr(bpy.data, coll).new(name)
        if rec.get("tree") is not None and getattr(m, "node_tree", None) is None:
            m.use_nodes = True              # Blender 4.2: a new material has no node tree
        m[idmap.UID_PROP] = muid
        idmap.note(m, muid)
        return m
    except Exception as e:
        log.error_once(("mat_new", str(e)), f"could not make the material {name} again: {e}", e)
        return None


def write_material(muid: str, rec: dict, make: str = "") -> None:
    """make: where a missing one is made again: "materials" for a material slot's (D163), "worlds" for a scene's
    world (#123). A loose record makes none here (_make_loose_again does)."""
    m = idmap.find(muid)
    if m is None:
        m = _make_material_again(muid, rec, make) if make else None
        if m is None:
            return
    _MAT_CACHE.pop(m.session_uid, None)
    _OBMAT_CACHE.clear()
    try:
        set_struct_values(m, rec.get("values") or {})
        tree = rec.get("tree")
        if tree and getattr(m, "node_tree", None) is not None:
            write_tree(m.node_tree, tree)
    except Exception as e:
        log.error_once(("material", str(e)), f"could not restore material {m.name}: {e}", e)


def _set_socket(sock, v) -> None:
    if isinstance(v, dict) and "ref" in v:
        v = idmap.find(v["ref"]) if v["ref"] else None
    cur = _socket_value(sock)
    if cur == v or (isinstance(v, bpy.types.ID) and sock.default_value == v):
        return
    sock.default_value = to_rna(v) if not isinstance(v, bpy.types.ID) else v


def write_tree(nt, tree: dict) -> None:
    want = [(n, t) for n, t in tree.get("nodes") or ()]
    names = {n for n, _t in want}
    for node in [x for x in nt.nodes if x.name not in names or (x.name, x.bl_idname) not in set(want)]:
        nt.nodes.remove(node)
    for n, t in want:
        if nt.nodes.get(n) is None:
            node = nt.nodes.new(t)
            node.name = n
    for n, rec in (tree.get("node") or {}).items():
        node = nt.nodes.get(n)
        if node is None:
            continue
        set_struct_values(node, rec.get("props") or {})
        if rec.get("items"):
            try:
                _write_items(node, rec["items"])
            except Exception as e:
                log.error_once(("node_items", str(e)), f"could not restore the items of {node.name}: {e}", e)
        by_id = {s.identifier: s for s in node.inputs}
        for ident, v in rec.get("in") or ():
            sock = by_id.get(ident)
            if sock is not None:
                try:
                    _set_socket(sock, v)
                except Exception:
                    pass
        if "out" in rec and node.outputs:
            try:
                _set_socket(node.outputs[0], rec["out"])
            except Exception:
                pass
    for n, rec in (tree.get("node") or {}).items():     # D130: zones paired once both nodes exist
        node, out = nt.nodes.get(n), nt.nodes.get(rec.get("pair") or "")
        if node is not None and out is not None and hasattr(node, "pair_with_output"):
            po = getattr(node, "paired_output", None)
            if po is None or po.name != out.name:
                try:
                    node.pair_with_output(out)
                except Exception:
                    pass
    want_links = [tuple(l) for l in tree.get("links") or ()]
    have = [(l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier,
             _sock_index(l.from_node.outputs, l.from_socket), _sock_index(l.to_node.inputs, l.to_socket))
            for l in nt.links]
    if [h[:len(w)] for h, w in zip(have, want_links)] != want_links or len(have) != len(want_links):
        nt.links.clear()
        for row in want_links:
            fn, fs, tn, ts = row[:4]
            fi, ti = (row[4], row[5]) if len(row) >= 6 else (-1, -1)
            a, b = nt.nodes.get(fn), nt.nodes.get(tn)
            if a is None or b is None:
                continue
            so = next((x for x in a.outputs if x.identifier == fs), None)
            si = next((x for x in b.inputs if x.identifier == ts), None)
            if so is None and 0 <= fi < len(a.outputs):
                so = a.outputs[fi]          # an item socket made again: same place, new identifier
            if si is None and 0 <= ti < len(b.inputs):
                si = b.inputs[ti]
            if so is not None and si is not None:
                nt.links.new(so, si)


# ----------------------------------------------------------------------------- material slots (D111)
def material_slots(ob) -> list:
    """[[link, material id], ...] and, last, the active slot's index."""
    rows = []
    mats = ob.data.materials
    for i, sl in enumerate(ob.material_slots):
        dm = mats[i] if i < len(mats) else None
        om = sl.material if sl.link == "OBJECT" else None
        rows.append([sl.link, ref_of_material(dm), ref_of_material(om)])
    return rows + [int(ob.active_material_index)]


def ref_of_material(m):
    return idmap.ref_of(m) if m is not None else None


def write_material_slots(ob, rec) -> None:
    if not isinstance(rec, list) or not rec:
        return
    want, active = rec[:-1], rec[-1]
    data = ob.data
    try:
        mats = data.materials
        if len(mats) != len(want) or len(ob.material_slots) != len(want):
            # pop() leaves the object's own slot count behind (Blender 4.2 to 5.3); clear() doesn't.
            # The mesh's materials are set again below with each slot
            keep = [m for m in mats]
            mats.clear()
            for i in range(len(want)):
                mats.append(keep[i] if i < len(keep) else None)
        for i, row in enumerate(want):
            link, dref = row[0], row[1]
            oref = row[2] if len(row) > 2 else None
            sl = ob.material_slots[i]
            dm = idmap.find(dref) if dref else None
            _note_ref(ob, f"data.materials[{i}]", dref, dm, "object", "slots")   # D60: noted, or forgotten
            if mats[i] != dm:
                mats[i] = dm                      # the mesh's material (also behind an object link)
            if sl.link != link:
                sl.link = link
            if link == "OBJECT":
                om = idmap.find(oref) if oref else None
                _note_ref(ob, f"material_slots[{i}].material", oref, om, "object", "slots")
                if sl.material != om:
                    sl.material = om
        if 0 <= int(active) < len(ob.material_slots) and ob.active_material_index != int(active):
            ob.active_material_index = int(active)
    except Exception as e:
        log.error_once(("material_slots", str(e)), f"could not restore the material slots: {e}", e)
