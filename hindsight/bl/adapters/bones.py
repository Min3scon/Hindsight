"""Data adapters: armature bones and the pose (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
from .. import log
from .core import _UI_PROPS, animated_slots, set_struct_values, struct_values
from .idprops import _group_json, _write_group
from .keys import ANIMATED, _BONES_CACHE
from .membership import _write_constraints


_BONE_SKIP = frozenset(_UI_PROPS | {"name", "hide", "hide_select", "select", "select_head", "select_tail"})


def bones_of(arm) -> dict:
    """An armature's bones as Edit Mode makes them: name, parent, head, tail, orientation (roll),
    connected or not, their settings and bone collections. Read from the armature's bones (Edit Mode
    changes reach them when Edit Mode ends)."""
    k = arm.session_uid
    rec = _BONES_CACHE.get(k)
    if rec is not None:
        return rec
    rows = []
    for b in arm.bones:
        rows.append({"name": b.name, "parent": b.parent.name if b.parent is not None else "",
                     "head": list(b.head_local), "tail": list(b.tail_local),
                     "matrix": [list(r) for r in b.matrix_local], "connect": bool(b.use_connect),
                     "values": struct_values(b, _BONE_SKIP),
                     "colls": sorted(c.name for c in getattr(b, "collections", ()))})
    colls = [[c.name, bool(getattr(c, "is_visible", True))] for c in getattr(arm, "collections_all", ())]
    rec = _BONES_CACHE[k] = {"bones": rows, "collections": colls}
    return rec


def _bone_shape(arm) -> list:
    return [(b.name, b.parent.name if b.parent is not None else "", [round(x, 6) for x in b.head_local],
             [round(x, 6) for x in b.tail_local], [round(x, 6) for r in b.matrix_local for x in r],
             bool(b.use_connect)) for b in arm.bones]


def write_bones(ob, rec) -> None:
    arm = ob.data
    if not isinstance(rec, dict) or arm is None:
        return
    _BONES_CACHE.pop(arm.session_uid, None)
    want = rec.get("bones") or []
    shape = [(w["name"], w["parent"], [round(x, 6) for x in w["head"]], [round(x, 6) for x in w["tail"]],
              [round(x, 6) for r in w["matrix"] for x in r], w["connect"]) for w in want]
    try:
        for name, vis in rec.get("collections") or ():       # bone collections first (by name)
            if arm.collections_all.get(name) is None:
                arm.collections.new(name)
        for c in list(getattr(arm, "collections_all", ())):
            if c.name not in {n for n, _v in rec.get("collections") or ()}:
                arm.collections.remove(c)
        for name, vis in rec.get("collections") or ():
            c = arm.collections_all.get(name)
            if c is not None and c.is_visible != vis:
                c.is_visible = vis
        if _bone_shape(arm) != shape:
            _edit_bones(ob, want)
        for w in want:
            b = arm.bones.get(w["name"])
            if b is None:
                continue
            set_struct_values(b, w.get("values") or {})
            have = {c.name for c in b.collections}
            for cn in set(w.get("colls") or ()) - have:
                c = arm.collections_all.get(cn)
                if c is not None:
                    c.assign(b)
            for cn in have - set(w.get("colls") or ()):
                c = arm.collections_all.get(cn)
                if c is not None:
                    c.unassign(b)
    except Exception as e:
        log.error_once(("bones", str(e)), f"could not restore the bones of {ob.name}: {e}", e)
    _BONES_CACHE.pop(arm.session_uid, None)


def _edit_bones(ob, want) -> None:
    """Put the bones back through a short Edit Mode visit (bones can only be made, moved and parented
    there), then back to the mode the object was in."""
    from mathutils import Matrix
    ctx = bpy.context
    vl = ctx.view_layer
    prev_active = vl.objects.active
    prev_mode = ob.mode
    with ctx.temp_override(active_object=ob, object=ob, selected_objects=[ob], selected_editable_objects=[ob]):
        vl.objects.active = ob
        if prev_mode != "EDIT":
            bpy.ops.object.mode_set(mode="EDIT")
        try:
            eb = ob.data.edit_bones
            names = {w["name"] for w in want}
            for b in [b for b in eb if b.name not in names]:
                eb.remove(b)
            for w in want:
                b = eb.get(w["name"]) or eb.new(w["name"])
                b.use_connect = False
                b.head, b.tail = w["head"], w["tail"]
                b.matrix = Matrix(w["matrix"])
                b.tail = w["tail"]
            for w in want:
                b = eb.get(w["name"])
                b.parent = eb.get(w["parent"]) if w["parent"] else None
                b.use_connect = w["connect"]
        finally:
            if prev_mode != "EDIT":
                bpy.ops.object.mode_set(mode=prev_mode if prev_mode in ("OBJECT", "POSE") else "OBJECT")
    if prev_active is not None and prev_active != ob:
        vl.objects.active = prev_active
_POSE_FIELDS = (("location", 3), ("rotation_quaternion", 4), ("rotation_euler", 3),
                ("rotation_axis_angle", 4), ("scale", 3))


def pose_values(ob) -> dict:
    """An armature object's pose: each pose bone's transform and rotation mode, its constraint stack
    and its custom properties. What an animation or driver sets is ANIMATED."""
    pose = ob.pose
    if pose is None:
        return {}
    bones = pose.bones
    n = len(bones)
    flat = {}
    for f, k in _POSE_FIELDS:
        buf = [0.0] * (n * k)
        bones.foreach_get(f, buf)
        flat[f] = buf
    anim = animated_slots(ob) if ob.animation_data is not None else {}
    out = {}
    for i, pb in enumerate(bones):
        name = pb.name
        row = {}
        pre = f'pose.bones["{bpy.utils.escape_identifier(name)}"].' if anim else ""
        for f, k in _POSE_FIELDS:
            v = flat[f][i * k:(i + 1) * k]
            idx = anim.get(pre + f) if anim else None
            if idx:
                v = [ANIMATED if j in idx else x for j, x in enumerate(v)]
            row[f] = v
        row["rotation_mode"] = pb.rotation_mode
        if len(pb.constraints):
            row["constraints"] = [{"type": c.type, "name": c.name,
                                   "values": struct_values(c, anim=anim or None)} for c in pb.constraints]
        props = _group_json(pb)
        if props:
            row["props"] = props
        out[name] = row
    return out


def write_pose(ob, want: dict) -> None:
    pose = ob.pose
    if pose is None or not isinstance(want, dict):
        return
    for pb in pose.bones:
        row = want.get(pb.name)
        if row is None:
            continue
        try:
            if pb.rotation_mode != row.get("rotation_mode", pb.rotation_mode):
                pb.rotation_mode = row["rotation_mode"]
            for f, _k in _POSE_FIELDS:
                v = row.get(f)
                if not v:
                    continue
                cur = list(getattr(pb, f))
                new = [c if x == ANIMATED else x for x, c in zip(v, cur)]
                if new != cur:
                    setattr(pb, f, new)
            if row.get("constraints") or len(pb.constraints):
                _write_constraints(ob, row.get("constraints") or [], holder=pb,
                                   base=f'pose.bones["{bpy.utils.escape_identifier(pb.name)}"].')
            _write_group(pb, row.get("props") or {})
        except Exception as e:
            log.error_once(("pose", str(e)), f"could not restore the pose of {pb.name}: {e}", e)
