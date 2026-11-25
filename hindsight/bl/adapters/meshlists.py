"""Data adapters: mesh lists: vertex groups, shape keys, UV and attribute lists (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
from ...core.types import MISSING
from .. import idmap, log
from . import core
from .core import animated_slots, note_name
from .keys import ANIMATED, _RECREATED


# ----------------------------------------------------------------------------- mesh lists (D108)
_SK_FIELDS = ("value", "mute", "slider_min", "slider_max", "vertex_group", "interpolation")


def mesh_lists(ob) -> dict:
    """The vertex group list, the UV map list and the shape key list of a mesh object, with their
    settings (the weights, UV coordinates and key positions are mesh channels). Read cheaply: a few
    attributes per item."""
    me = ob.data
    out = {"vgroups": [[g.name, bool(g.lock_weight)] for g in ob.vertex_groups] + [
        int(ob.vertex_groups.active_index)]}
    uvs = me.uv_layers
    out["uvmaps"] = [[u.name, bool(u.active), bool(u.active_render)] for u in uvs]
    key = me.shape_keys
    if key is None:
        out["shapekeys"] = None
    else:
        blocks = []
        anim = animated_slots(key) if key.animation_data is not None else {}
        for kb in key.key_blocks:
            row = [kb.name] + [core.normalise(getattr(kb, f)) for f in _SK_FIELDS]
            if anim:
                pre = f'key_blocks["{bpy.utils.escape_identifier(kb.name)}"].'
                for j, f in enumerate(_SK_FIELDS):
                    if pre + f in anim:
                        row[1 + j] = ANIMATED     # D124: keyed or driven, it follows the frame
            row.append(kb.relative_key.name if kb.relative_key is not None else "")
            blocks.append(row)
        out["shapekeys"] = {"blocks": blocks, "use_relative": bool(key.use_relative),
                            "active": int(ob.active_shape_key_index),
                            "only": bool(ob.show_only_shape_key)}
    return out


_PENDING_UV: dict = {}       # object pointer -> UV map flags to set once the mesh is written


def write_mesh_lists(ob, values: dict) -> None:
    """Make the lists what they were recorded as: groups and shape keys removed, added, renamed and
    reordered by position, settings put back. The weights and key positions are written with the mesh
    right after (runtime._goto_history writes data before the mesh), into these lists."""
    made = []
    vg = values.get("vgroups")
    if isinstance(vg, list) and vg:
        try:
            made += _write_vgroups(ob, vg[:-1], vg[-1])
        except Exception as e:
            log.error_once(("vgroups", str(e)), f"could not restore the vertex group list: {e}", e)
    if "shapekeys" in values and values["shapekeys"] is not MISSING:
        try:
            made += _write_shapekeys(ob, values["shapekeys"])
        except Exception as e:
            log.error_once(("shapekeys", str(e)), f"could not restore the shape key list: {e}", e)
    if made:
        _fill_made(ob, made)
    uv = values.get("uvmaps")
    if isinstance(uv, list):
        _PENDING_UV[ob.as_pointer()] = uv


def after_mesh_write(ob) -> None:
    """UV maps are attributes of the mesh, written after the data: their flags go on now."""
    uv = _PENDING_UV.pop(ob.as_pointer(), None)
    if not uv or ob.data is None:
        return
    layers = ob.data.uv_layers
    try:
        for name, active, render in uv:
            lay = layers.get(name)
            if lay is None:
                continue
            if active and not lay.active:
                layers.active = lay
            if render and not lay.active_render:
                lay.active_render = True
    except Exception as e:
        log.error_once(("uvmaps", str(e)), f"could not restore which UV map is active: {e}", e)


def _write_vgroups(ob, want: list, active: int) -> list:
    made = []
    groups = ob.vertex_groups
    names = [n for n, _l in want]
    have = [g.name for g in groups]
    if have != names:
        # keep groups whose names are wanted (their weights stay with them), then rename by position
        for g in [g for g in groups if g.name not in names]:
            # a group at a position where another name is wanted and that name is missing: a rename
            i = list(groups).index(g)
            if i < len(names) and groups.get(names[i]) is None:
                g.name = names[i]
            else:
                groups.remove(g)
        for n in names:
            if groups.get(n) is None:
                groups.new(name=n)
                made.append(("vg", n))
        _reorder(ob, [g.name for g in groups], names, "vertex_group_move")
    for (n, lock) in want:
        g = groups.get(n)
        if g is not None and bool(g.lock_weight) != lock:
            g.lock_weight = lock
    if 0 <= active < len(groups) and groups.active_index != active:
        groups.active_index = active
    return made


def _write_shapekeys(ob, rec) -> list:
    made = []
    me = ob.data
    if rec is None:
        if me.shape_keys is not None:
            ob.shape_key_clear()
        return made
    blocks = rec.get("blocks") or []
    names = [b[0] for b in blocks]
    if me.shape_keys is None and names:
        ob.shape_key_add(name=names[0], from_mix=False)
        made.append(("sk", names[0]))
    key = me.shape_keys
    have = [kb.name for kb in key.key_blocks]
    if have != names:
        for kb in [kb for kb in key.key_blocks if kb.name not in names]:
            i = list(key.key_blocks).index(kb)
            if i < len(names) and key.key_blocks.get(names[i]) is None:
                kb.name = names[i]
            else:
                ob.shape_key_remove(kb)
        for n in names:
            if key.key_blocks.get(n) is None:
                ob.shape_key_add(name=n, from_mix=False)
                made.append(("sk", n))
        _reorder(ob, [kb.name for kb in key.key_blocks], names, "shape_key_move")
    for b in blocks:
        kb = key.key_blocks.get(b[0])
        if kb is None:
            continue
        for f, v in zip(_SK_FIELDS, b[1:1 + len(_SK_FIELDS)]):
            if v == ANIMATED:
                continue                      # D124: the animation sets it
            try:
                if getattr(kb, f) != v:
                    setattr(kb, f, v)
            except Exception:
                pass
        rel = b[1 + len(_SK_FIELDS)] if len(b) > 1 + len(_SK_FIELDS) else ""
        if rel and key.key_blocks.get(rel) is not None and kb.relative_key.name != rel:
            kb.relative_key = key.key_blocks[rel]
    if key.use_relative != rec.get("use_relative", True):
        key.use_relative = rec.get("use_relative", True)
    a = int(rec.get("active", 0))
    if 0 <= a < len(key.key_blocks) and ob.active_shape_key_index != a:
        ob.active_shape_key_index = a
    if ob.show_only_shape_key != bool(rec.get("only", False)):
        ob.show_only_shape_key = bool(rec.get("only", False))
    return made


def _fill_made(ob, made) -> None:
    """A group or shape key the list write made again gets its weights or positions from the object's
    mesh state (Blender 4.2 can record a removal and its weights in two steps, so the mesh write of
    the jump doesn't always carry them)."""
    from .. import meshio, runtime
    st = runtime._STATES.get(idmap.uid_of(ob))
    if st is None:
        return
    me = ob.data
    n = len(me.vertices)
    for kind, name in made:
        if kind == "vg":
            arr = st.light.get(meshio.vg_key(name))
            g = ob.vertex_groups.get(name)
            if arr is not None and g is not None and arr.size == n:
                meshio._write_vgroup(g, n, arr)
        else:
            key = me.shape_keys
            kb = key.key_blocks.get(name) if key is not None else None
            if kb is None:
                continue
            for k, arr in st.light.items():
                if k.startswith(meshio.SK_PREFIX) and meshio._sk_name(k) == name and arr.size == n * 3:
                    meshio.sk_points(kb).foreach_set("co", arr)
                    break


def _reorder(ob, have: list, want: list, op: str) -> None:
    """Move items until the order is `want` (the operators move the active item one place at a time)."""
    if have == want or sorted(have) != sorted(want):
        return
    is_vg = op == "vertex_group_move"
    for target, name in enumerate(want):
        cur = [g.name for g in ob.vertex_groups] if is_vg else [k.name for k in ob.data.shape_keys.key_blocks]
        i = cur.index(name)
        if i == target:
            continue
        if is_vg:
            ob.vertex_groups.active_index = i
        else:
            ob.active_shape_key_index = i
        with bpy.context.temp_override(object=ob, active_object=ob):
            for _ in range(i - target):
                if is_vg:
                    bpy.ops.object.vertex_group_move(direction="UP")
                else:
                    bpy.ops.object.shape_key_move(type="UP")


def _give_name(ch, name: str, names: dict) -> None:
    """D55: a collection gets its recorded name back from one that isn't in any scene now (taken out
    by this jump, or left over) or that gets another name in this same jump; one still in use keeps
    its name and this one gets '.001' (the sidebar says so)."""
    if ch.name != name and ch.library is None:
        holder = bpy.data.collections.get(name)
        if (holder is not None and holder != ch and holder.library is None
                and (holder.users == 0 or names.get(idmap.uid_of(holder), name) != name)):
            if hasattr(ch, "rename"):
                ch.rename(name, mode="ALWAYS")
            else:
                holder.name = name + ".001"
                ch.name = name
        else:
            ch.name = name
    note_name(ch, name)


def drop_recreated_orphans() -> None:
    """D55: a collection Hindsight made again that a later jump took out of the scene, with nothing
    left in it, is removed again, as it was when it was deleted (collections the user made stay, so
    their settings are kept when they come back)."""
    for u in list(_RECREATED):
        c = idmap.find(u)
        if c is None:
            _RECREATED.discard(u)
            continue
        if c.users == 0 and not c.objects and not c.children and c.library is None:
            _RECREATED.discard(u)
            idmap.unnote(c)
            bpy.data.collections.remove(c)
