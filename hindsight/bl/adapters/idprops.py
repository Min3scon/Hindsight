"""Data adapters: ID properties (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
import contextlib
from ...core.types import MISSING
from .. import idmap, log
from . import core
from .animation import action_record, anim_values, write_action, write_anim
from .core import struct_values, to_rna
from .keys import (
    ACT_PREFIX, ANIMATED, ANIM_KEY, COMP_TREE, DATA_PREFIX, IDPROPS_KEY, INV_GROUPS, INV_LOOSE, MATA_PREFIX,
    MATP_PREFIX, MAT_PREFIX, NGA_PREFIX, NG_PREFIX, PART_PREFIX, REF_KEY, SK_PREFIX, SYSPROPS_KEY,
    WORLDP_PREFIX, WORLD_PREFIX)


_NO_ANIM = {"anim": None}


def anim_of(x) -> dict:
    """{ANIM_KEY: how x plays its animation} and, when x is the only user of its action, the action
    (D120 for data other than objects)."""
    if x.animation_data is None:
        return _NO_ANIM
    out = {ANIM_KEY: anim_values(x)}
    ad = x.animation_data
    if ad is not None and ad.action is not None and ad.action.library is None:
        a = ad.action
        if a.users <= 1 + bool(a.use_fake_user):
            slot = getattr(ad, "action_slot", None)
            ident = slot.identifier if slot is not None else None
            au = idmap.claim(a)
            if au:
                out[ACT_PREFIX + au + "|" + (ident or "")] = action_record(a, ident)
    return out


def write_anim_of(x, vals: dict) -> None:
    for path, v in vals.items():
        if path.startswith(ACT_PREFIX) and v is not MISSING:
            write_action(path, v)
    if ANIM_KEY in vals and vals[ANIM_KEY] is not MISSING:
        write_anim(x, vals[ANIM_KEY])


def assignment_path(path: str) -> bool:
    """A recorded "which animation it plays" value (an object's, its data's, its shape keys', a
    material's node tree's)."""
    return path == ANIM_KEY or path.endswith(("." + ANIM_KEY, "|" + ANIM_KEY))


def owned_path(adapter_id: str, path: str) -> bool:
    """A value an object records only while it owns the data (D112, D114)."""
    if adapter_id == "scene":
        # (the scene's loose node groups are not: they coming and going is a step, D152, like D151's data)
        # (the inventories coming and going is no step either: before #104 Movie Mode stopped reading them)
        return path in ("settings", COMP_TREE, INV_LOOSE, INV_GROUPS) or path.startswith((WORLD_PREFIX, WORLDP_PREFIX))
    return adapter_id == "object" and path.startswith((DATA_PREFIX, MAT_PREFIX, MATP_PREFIX, ACT_PREFIX,
                                                       SK_PREFIX, MATA_PREFIX, NG_PREFIX, NGA_PREFIX, PART_PREFIX))


def _owns_data(ob, d) -> bool:
    """The object that records its data's properties: the only user, or for a shared mesh the object
    that records the mesh (D39)."""
    if d.users <= 1 + bool(getattr(d, "use_fake_user", False)):
        return True
    if ob.type != "MESH":
        return False
    from .. import datarec
    return datarec._MESH_OWNER.get(d.as_pointer()) == idmap.uid_of(ob)


def _own_prop(k: str) -> bool:
    """Hindsight's own properties (ids, the camera rig mark, its scene settings) are never part of
    what is recorded."""
    return k == "hindsight" or k.startswith("_hindsight") or k.startswith("hindsight_")


def _skipped(idb, k: str) -> bool:
    """Not recorded: Hindsight's own properties, and the "cycles" group Blender writes on a camera at each
    save (Blender 5: in its add-on data), a copy of its panorama settings for older versions (#104: a
    reopen read it as a change). The whole group is skipped, so anything an add-on keeps under that key
    on a camera isn't recorded either."""
    return _own_prop(k) or (k == "cycles" and isinstance(idb, bpy.types.Camera) and not hasattr(idb, "cycles"))


def _sys_group(idb, create: bool = False):
    """Blender 5: the group add-ons' properties are stored in, or None (none yet, or Blender 4)."""
    get = getattr(idb, "bl_system_properties_get", None)
    if get is None:
        return None
    try:
        pg = get(do_create=create)
        return pg.id_properties_ensure() if pg is not None else None
    except Exception:
        return None


def _idp_json(v):
    """An ID property value (from to_dict) as a JSON value: a datablock becomes {REF_KEY: its id}. An
    empty group is left out: it reads as the defaults, like no group (Blender 4 makes one when a panel
    only reads an add-on's settings, which isn't a change)."""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            x = _idp_json(x)
            if not (isinstance(x, dict) and not x):
                out[k] = x
        return out
    if isinstance(v, list):
        return [_idp_json(x) for x in v]
    if isinstance(v, bpy.types.ID):
        return {REF_KEY: idmap.ref_of(v)}
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return core.normalise(v)


def _idp_py(v, cur=MISSING):
    """What _idp_json gave, ready to store again; ANIMATED parts take the value stored now (cur)."""
    if v == ANIMATED:
        return cur
    if isinstance(v, dict):
        if set(v) == {REF_KEY}:
            return idmap.find(v[REF_KEY]) if v[REF_KEY] else None
        c = cur if isinstance(cur, dict) else {}
        return {k: _idp_py(x, c.get(k, MISSING)) for k, x in v.items()
                if not (x == ANIMATED and c.get(k, MISSING) is MISSING)}
    if isinstance(v, list):
        c = cur if isinstance(cur, list) and len(cur) == len(v) else [MISSING] * len(v)
        return [_idp_py(x, y) for x, y in zip(v, c)]
    return v


def _group_json(g, owner=None) -> dict:
    """owner: the datablock g belongs to, when g is its add-on group (Blender 5)."""
    out = {}
    if g is None:
        return out
    try:
        keys = list(g.keys())
    except Exception:
        return out
    owner = g if owner is None else owner
    for k in keys:
        if _skipped(owner, k):
            continue
        try:
            v = g[k]
            v = _idp_json(v.to_dict() if hasattr(v, "to_dict") else
                          v.to_list() if hasattr(v, "to_list") else v)
        except Exception:
            continue
        if not (isinstance(v, dict) and not v):
            out[k] = v
    return out


def _mark_animated(d: dict, anim) -> None:
    """Paths an animation or driver sets ('["prop"]', 'addon.value') become ANIMATED in d."""
    for p in anim:
        if p.startswith('["'):
            k = p[2:p.find('"]', 2)]
            rest = p[p.find('"]', 2) + 2:]
        else:
            head = p.split(".", 1)
            k, rest = head[0], ("." + head[1] if len(head) > 1 else "")
            k = k.split("[", 1)[0]
        if k not in d:
            continue
        node = d
        key = k
        ok = True
        for part in [x for x in rest.split(".") if x]:
            if "[" in part or not isinstance(node.get(key), dict):
                ok = False
                break
            node, key = node[key], part
        if ok and isinstance(node, dict) and key in node:
            node[key] = ANIMATED
        else:
            d[k] = ANIMATED


_NONE = {}                       # a shared empty group: never changed, so a value that is the same
                                 # object on both sides of a compare is skipped (datarec._same_values)
_ID_EMPTY4 = {IDPROPS_KEY: _NONE}
_ID_EMPTY5 = {IDPROPS_KEY: _NONE, SYSPROPS_KEY: _NONE}


def id_props(idb, anim=None) -> dict:
    """{IDPROPS_KEY: the datablock's own ID properties, SYSPROPS_KEY: Blender 5's add-on group}. Both
    are always there (empty when nothing is set): a value that disappears would read as a kind that
    isn't recorded, not as properties taken off."""
    own = _group_json(idb) or _NONE
    five = hasattr(idb, "bl_system_properties_get")
    sysd = (_group_json(_sys_group(idb), idb) or _NONE) if five else None
    if own is _NONE and not sysd:
        return _ID_EMPTY5 if five else _ID_EMPTY4
    out = {IDPROPS_KEY: own}
    if five:
        out[SYSPROPS_KEY] = sysd
    if anim:
        if own is not _NONE:
            _mark_animated(own, anim)
        if sysd:
            _mark_animated(sysd, anim)
    return out


def _write_group(g, want: dict, owner=None) -> None:
    if g is None:
        return
    want = want or {}
    owner = g if owner is None else owner
    for k in [k for k in g.keys() if not _skipped(owner, k) and k not in want]:
        try:
            del g[k]                     # not set then: back to the add-on's default
        except Exception:
            pass
    for k, v in want.items():
        if _skipped(owner, k):
            continue
        try:
            cur = g[k] if k in g else MISSING
            curv = cur.to_dict() if hasattr(cur, "to_dict") else cur.to_list() if hasattr(cur, "to_list") else cur
            if cur is not MISSING and _idp_json(curv) == v:
                continue
            val = _idp_py(v, curv)
            if val is MISSING:
                continue
            if isinstance(v, dict) and set(v) == {REF_KEY} and val is None and v[REF_KEY]:
                continue                 # the datablock it points to is deleted now: left as it is
            if k in g:
                del g[k]                 # Blender won't store a dict over an existing group
            g[k] = val
        except Exception as e:
            log.error_once(("idprop", k, str(e)), f"could not put back the property {k}: {e}", e)


def write_id_props(idb, values: dict) -> None:
    if IDPROPS_KEY in values and values[IDPROPS_KEY] is not MISSING:
        _write_group(idb, values[IDPROPS_KEY])
    elif IDPROPS_KEY in values:          # recorded as none: remove what is set now
        _write_group(idb, {})
    if SYSPROPS_KEY in values:
        want = values[SYSPROPS_KEY]
        g = _sys_group(idb, create=want is not MISSING and bool(want))
        _write_group(g, {} if want is MISSING else want, idb)


def _gn_inputs(m):
    """A Geometry Nodes modifier's input values on Blender 5.2 and later, where they are RNA
    (modifier.properties.inputs.<identifier>: value, type, attribute_name) instead of custom properties.
    None where the modifier keeps them as custom properties (read by _id_props)."""
    props = getattr(m, "properties", None)
    inputs = getattr(props, "inputs", None) if props is not None else None
    ng = getattr(m, "node_group", None)
    if inputs is None or ng is None:
        return None
    out = {}
    for it in ng.interface.items_tree:
        if getattr(it, "item_type", "SOCKET") != "SOCKET" or getattr(it, "in_out", "") != "INPUT":
            continue
        x = getattr(inputs, it.identifier, None)
        if x is None:
            continue
        rec = {}
        for a in ("type", "attribute_name", "value"):
            if not hasattr(x, a):
                continue
            try:
                v = getattr(x, a)
            except Exception:
                continue
            if isinstance(v, bpy.types.ID):
                rec[a] = {"ref": idmap.ref_of(v)}
                continue
            if v is None:
                rec[a] = None
                continue
            try:
                v = core.normalise(v)
            except Exception:
                continue
            if isinstance(v, (int, float, str, list, bool)):
                rec[a] = v
        out[it.identifier] = rec
    return out


def _set_gn_inputs(m, recs) -> None:
    props = getattr(m, "properties", None)
    inputs = getattr(props, "inputs", None) if props is not None else None
    if inputs is None or not isinstance(recs, dict):
        return
    for ident, rec in recs.items():
        x = getattr(inputs, ident, None)
        if x is None or not isinstance(rec, dict):
            continue
        for a in ("type", "attribute_name", "value"):      # the type first: it decides what is used
            if a not in rec:
                continue
            v = rec[a]
            if isinstance(v, dict) and "ref" in v:
                v = idmap.find(v["ref"]) if v["ref"] else None
            try:
                setattr(x, a, to_rna(v) if not isinstance(v, bpy.types.ID) else v)
            except Exception as e:
                log.error_once(("gn_input", ident, a, str(e)), f"could not restore input {ident}.{a}: {e}", e)


def _set_id_props(owner, props: dict) -> None:
    for k, v in (props or {}).items():
        if isinstance(v, dict) and set(v) == {REF_KEY}:
            v = idmap.find(v[REF_KEY]) if v[REF_KEY] else None
            if v is None:
                continue                              # deleted now: left unset, like other references
        try:
            owner[k] = v
        except Exception:
            pass


def _hidden(ob) -> dict:
    """Hidden (H) in each view layer of the scenes it is in."""
    out = {}
    for sc in bpy.data.scenes:
        for vl in sc.view_layers:
            try:
                out[f"{sc.name}/{vl.name}"] = bool(ob.hide_get(view_layer=vl))
            except Exception:
                continue
    return out


def _set_hidden(ob, hidden) -> None:
    """Put back what _hidden read. A single True/False (recorded before D56) is for the view layer the
    window shows."""
    if isinstance(hidden, bool):
        with contextlib.suppress(Exception):
            ob.hide_set(hidden)
        return
    for key, h in (hidden or {}).items():
        sc_name, _sep, vl_name = key.partition("/")
        sc = bpy.data.scenes.get(sc_name)
        vl = sc.view_layers.get(vl_name) if sc is not None else None
        if vl is not None:
            try:
                if ob.hide_get(view_layer=vl) != bool(h):
                    ob.hide_set(bool(h), view_layer=vl)
            except Exception:
                pass


# Settings a physics modifier keeps in structs of its own (struct_values leaves nested structs out).
_MOD_STRUCTS = {"CLOTH": ("settings", "collision_settings"), "SOFT_BODY": ("settings",),
                "FLUID": ("domain_settings", "flow_settings", "effector_settings")}


def _nested_values(m) -> dict:
    out = {}
    for name in _MOD_STRUCTS.get(m.type, ()):
        st = getattr(m, name, None)
        if st is not None:
            try:
                out[name] = struct_values(st)
            except Exception:
                continue
    return out
