"""Data adapters: actions, drivers and NLA (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
from .. import idmap, log
from .core import _UI_PROPS, _fcurves, set_struct_values, struct_values
from .keys import ACT_PREFIX, _ACT_CACHE


_AD_SKIP = frozenset(_UI_PROPS | {"action_tweak_storage", "action_slot_handle_tweak_storage", "use_tweak_mode",
                                  "use_pin", "action_slot_handle", "last_slot_identifier", "action_slot"})
_KEY_2 = ("co", "handle_left", "handle_right")
_KEY_INT = ("interpolation", "type", "handle_left_type", "handle_right_type", "easing")
_KEY_1 = ("back", "amplitude", "period")
_OWN_PATHS = ("hindsight.",)   # Hindsight's own animated properties (Movie Mode keys the scene's step)
_SLOT_TYPES = {"OB": "OBJECT", "ME": "MESH", "MA": "MATERIAL", "WO": "WORLD", "SC": "SCENE", "CA": "CAMERA",
               "LA": "LIGHT", "KE": "KEY", "NT": "NODETREE", "CU": "CURVE", "AR": "ARMATURE"}


_TARGET_SKIP = frozenset(_UI_PROPS | {"id_type"})
_STRIP_SKIP = frozenset(_UI_PROPS | {"select", "frame_start_raw", "frame_end_raw", "frame_start_ui", "frame_end_ui",
                                     "strip_time", "action_slot_handle", "last_slot_identifier", "action_slot",
                                     "name"})


def _keys_of(fc) -> dict:
    kp = fc.keyframe_points
    n = len(kp)
    r = {}
    for f in _KEY_2:
        buf = [0.0] * (2 * n)
        kp.foreach_get(f, buf)
        r[f] = buf
    for f in _KEY_INT:
        buf = [0] * n
        kp.foreach_get(f, buf)
        r[f] = buf
    for f in _KEY_1:
        buf = [0.0] * n
        kp.foreach_get(f, buf)
        r[f] = buf
    return r


def _set_keys(fc, r) -> None:
    kp = fc.keyframe_points
    n = len(r.get("interpolation") or ())
    if len(kp) != n:
        kp.clear()
        kp.add(n)
    if n:
        for f in _KEY_2 + _KEY_INT + _KEY_1:
            kp.foreach_set(f, r[f])
    fc.update()


def _driver_values(fc) -> dict:
    d = fc.driver
    out = {"path": fc.data_path, "index": int(fc.array_index), "mute": bool(fc.mute),
           "type": d.type, "expression": d.expression, "use_self": bool(d.use_self), "keys": _keys_of(fc)}
    out["vars"] = [{"name": v.name, "type": v.type,
                    "targets": [dict(struct_values(t, _TARGET_SKIP), id_type=t.id_type) for t in v.targets]}
                   for v in d.variables]
    return out


def _nla_values(ad) -> list:
    out = []
    for t in ad.nla_tracks:
        strips = []
        for st in t.strips:
            v = struct_values(st, _STRIP_SKIP)
            slot = getattr(st, "action_slot", None)
            v["slot"] = slot.identifier if slot is not None else None
            strips.append([st.name, v])
        out.append({"name": t.name, "mute": bool(t.mute), "is_solo": bool(t.is_solo), "lock": bool(t.lock),
                    "strips": strips})
    return out


def anim_values(ob):
    """How the object plays its action, its drivers and its NLA tracks, or None when it has no
    animation data."""
    ad = ob.animation_data
    if ad is None:
        return None
    v = struct_values(ad, _AD_SKIP)
    slot = getattr(ad, "action_slot", None)
    v["slot"] = slot.identifier if slot is not None else None
    if len(ad.drivers):
        v["drivers"] = [_driver_values(fc) for fc in ad.drivers]
    if len(ad.nla_tracks):
        v["nla"] = _nla_values(ad)
    return v


def _write_drivers(ob, ad, want: list) -> None:
    if [(_d["path"], _d["index"]) for _d in want] != [(f.data_path, f.array_index) for f in ad.drivers]:
        for fc in list(ad.drivers):
            ad.drivers.remove(fc)
        for w in want:
            try:
                fc = ob.driver_add(w["path"], w["index"]) if w["index"] >= 0 else ob.driver_add(w["path"])
            except Exception:
                try:
                    fc = ob.driver_add(w["path"])
                except Exception as e:
                    log.error_once(("driver_add", str(e)), f"could not restore a driver: {e}", e)
                    continue
            if isinstance(fc, list):
                fc = fc[0]
    by = {(f.data_path, f.array_index): f for f in ad.drivers}
    for w in want:
        fc = by.get((w["path"], w["index"]))
        if fc is None:
            continue
        cur = _driver_values(fc)
        if cur == w:
            continue
        d = fc.driver
        fc.mute = w.get("mute", False)
        d.type = w["type"]
        d.expression = w.get("expression", "")
        d.use_self = w.get("use_self", False)
        while len(d.variables):
            d.variables.remove(d.variables[0])
        for vw in w.get("vars") or ():
            v = d.variables.new()
            v.name = vw["name"]
            v.type = vw["type"]
            for t, tw in zip(v.targets, vw.get("targets") or ()):
                try:
                    if t.id_type != tw.get("id_type", t.id_type):
                        t.id_type = tw["id_type"]
                except Exception:
                    pass
                set_struct_values(t, {k: x for k, x in tw.items() if k != "id_type"})
        _set_keys(fc, w.get("keys") or {})


def _write_nla(ad, want: list) -> None:
    shape = [(t["name"], [n for n, _v in t["strips"]]) for t in want]
    if shape != [(t.name, [s.name for s in t.strips]) for t in ad.nla_tracks]:
        for t in list(ad.nla_tracks):
            ad.nla_tracks.remove(t)
        for tw in want:
            t = ad.nla_tracks.new()
            t.name = tw["name"]
            for name, sv in tw["strips"]:
                ref = (sv.get("action") or {}).get("ref")
                act = idmap.find(ref) if ref else None
                if act is None:
                    continue
                try:
                    st = t.strips.new(name, int(sv.get("frame_start", 1)), act)
                except Exception as e:
                    log.error_once(("nla_strip", str(e)), f"could not restore an NLA strip: {e}", e)
    for t, tw in zip(ad.nla_tracks, want):
        t.mute, t.lock = tw.get("mute", False), tw.get("lock", False)
        if t.is_solo != tw.get("is_solo", False):
            t.is_solo = tw.get("is_solo", False)
        for st, (name, sv) in zip(t.strips, tw["strips"]):
            set_struct_values(st, {k: x for k, x in sv.items() if k != "slot"})
            ident = sv.get("slot")
            if ident and hasattr(st, "action_slot") and st.action is not None:
                slot = _slot_of(st.action, ident)
                if slot is not None and st.action_slot != slot:
                    st.action_slot = slot


def _slot_of(action, ident):
    if not ident or not hasattr(action, "slots"):
        return None
    return next((x for x in action.slots if x.identifier == ident), None)


def action_record(action, ident) -> dict:
    per = _ACT_CACHE.get(action.session_uid)
    if per is not None and ident in per:
        return per[ident]
    rows = []
    for fc in _fcurves(action, _slot_of(action, ident)):
        if fc.data_path.startswith(_OWN_PATHS):
            continue                    # Hindsight's own keys (Movie Mode's step) are never recorded
        r = {"path": fc.data_path, "index": int(fc.array_index),
             "group": fc.group.name if fc.group is not None else "", "mute": bool(fc.mute),
             "extrapolation": fc.extrapolation, "lock": bool(fc.lock)}
        r.update(_keys_of(fc))
        rows.append(r)
    rec = {"name": action.name, "fcurves": rows}
    _ACT_CACHE.setdefault(action.session_uid, {})[ident] = rec
    return rec


def action_owner_map() -> dict:
    """(action pointer, slot identifier) -> uid of the object that records that action (the first
    recorded object by name that plays it). Kept for one recorder frame."""
    fc = idmap.frame_cache()
    if fc is not None and "act_owner" in fc:
        return fc["act_owner"]
    from .. import datarec
    users = []
    for u in datarec.tracked_uids():
        ob = idmap.find(u)
        ad = getattr(ob, "animation_data", None) if isinstance(ob, bpy.types.Object) else None
        if ad is not None and ad.action is not None and ad.action.library is None:
            slot = getattr(ad, "action_slot", None)
            users.append((ob.name, u, ad.action.as_pointer(), slot.identifier if slot is not None else None))
    out = {}
    for _n, u, ap, ident in sorted(users, key=lambda t: t[0]):
        out.setdefault((ap, ident), u)
    if fc is not None:
        fc["act_owner"] = out
    return out


def owned_action(ob) -> dict:
    ad = ob.animation_data
    if ad is None or ad.action is None or ad.action.library is not None:
        return {}
    a = ad.action
    slot = getattr(ad, "action_slot", None)
    ident = slot.identifier if slot is not None else None
    if a.users > 1 + bool(a.use_fake_user):
        if action_owner_map().get((a.as_pointer(), ident)) != idmap.uid_of(ob):
            return {}
    au = idmap.claim(a)
    if not au:
        return {}
    return {ACT_PREFIX + au + "|" + (ident or ""): action_record(a, ident)}


def _fcurve_coll(action, ident, create: bool):
    if hasattr(action, "slots"):                       # Blender 4.4+: layered actions, a slot's channels
        from bpy_extras import anim_utils
        slot = _slot_of(action, ident)
        if slot is None:
            if not create or not ident:
                return None, None
            slot = action.slots.new(id_type=_SLOT_TYPES.get(ident[:2], "OBJECT"), name=ident[2:])
        cb = (anim_utils.action_ensure_channelbag_for_slot(action, slot) if create
              else anim_utils.action_get_channelbag_for_slot(action, slot))
        return (cb.fcurves if cb is not None else None), cb
    return action.fcurves, None


def write_action(key: str, rec) -> None:
    if not isinstance(rec, dict):
        return
    au, _sep, ident = key[len(ACT_PREFIX):].partition("|")
    ident = ident or None
    a = idmap.find(au)
    try:
        if a is None:
            # gone from the file (nothing used it when it was saved): made again under its id
            a = bpy.data.actions.new(rec.get("name") or "Action")
            a[idmap.UID_PROP] = au
            idmap.note(a, au)
        _ACT_CACHE.pop(a.session_uid, None)
        coll, cb = _fcurve_coll(a, ident, create=True)
        if coll is None:
            return
        want = {(r["path"], r["index"]): r for r in rec.get("fcurves") or ()}
        for fc in [f for f in coll if (f.data_path, f.array_index) not in want
                   and not f.data_path.startswith(_OWN_PATHS)]:
            coll.remove(fc)
        for r in rec.get("fcurves") or ():
            fc = coll.find(r["path"], index=r["index"])
            if fc is None:
                if cb is None:
                    fc = coll.new(r["path"], index=r["index"], action_group=r.get("group") or "")
                else:
                    fc = coll.new(r["path"], index=r["index"])
                    if r.get("group"):
                        g = cb.groups.get(r["group"]) or cb.groups.new(r["group"])
                        fc.group = g
            fc.mute = r.get("mute", False)
            fc.lock = r.get("lock", False)
            if fc.extrapolation != r.get("extrapolation", fc.extrapolation):
                fc.extrapolation = r["extrapolation"]
            _set_keys(fc, r)
        _ACT_CACHE.pop(a.session_uid, None)
    except Exception as e:
        log.error_once(("action", str(e)), f"could not restore the animation {rec.get('name')}: {e}", e)


def write_anim(ob, want) -> None:
    try:
        if want is None:
            if ob.animation_data is not None:      # animated later than this: it had none then
                ob.animation_data_clear()
            return
        ad = ob.animation_data or ob.animation_data_create()
        ref = (want.get("action") or {}).get("ref")
        act = idmap.find(ref) if ref else None
        if ad.action != act:
            ad.action = act
        ident = want.get("slot")
        if act is not None and ident and hasattr(ad, "action_slot"):
            slot = _slot_of(act, ident)
            if slot is not None and ad.action_slot != slot:
                ad.action_slot = slot
        set_struct_values(ad, {k: v for k, v in want.items() if k not in ("action", "slot", "drivers", "nla")})
        if want.get("drivers") or len(ad.drivers):
            _write_drivers(ob, ad, want.get("drivers") or [])
        if want.get("nla") or len(ad.nla_tracks):
            _write_nla(ad, want.get("nla") or [])
    except Exception as e:
        log.error_once(("anim", str(e)), f"could not restore which animation {ob.name} plays: {e}", e)
