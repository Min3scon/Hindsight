"""Data adapters: particles and physics (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
from .. import idmap, log
from . import settings
from .core import _UI_PROPS, set_struct_values, struct_values
from .keys import PART_PREFIX
from .settings import _ID_SKIP


_PART_SKIP = frozenset(_UI_PROPS | _ID_SKIP | {"active_texture_index", "active_instanceweight_index"})


def particle_owner_map() -> dict:
    """particle settings pointer -> uid of the object that records them (the first recorded object
    by name with a particle system using them). Kept for one recorder frame."""
    fc = idmap.frame_cache()
    if fc is not None and "part_owner" in fc:
        return fc["part_owner"]
    from .. import datarec
    users = []
    for u in datarec.tracked_uids():
        ob = idmap.find(u)
        if isinstance(ob, bpy.types.Object) and len(getattr(ob, "particle_systems", ())):
            users.append((ob.name, u, ob))
    out = {}
    for _n, u, ob in sorted(users, key=lambda t: t[0]):
        for ps in ob.particle_systems:
            if ps.settings is not None:
                out.setdefault(ps.settings.as_pointer(), u)
    if fc is not None:
        fc["part_owner"] = out
    return out


def owned_particles(ob) -> dict:
    systems = getattr(ob, "particle_systems", None)
    if not systems or not len(systems):
        return {}
    uid = idmap.uid_of(ob)
    out = {}
    owners = None
    for ps in systems:
        st = ps.settings
        if st is None or st.library is not None:
            continue
        if st.users > 1 + bool(st.use_fake_user):
            if owners is None:
                owners = particle_owner_map()
            if owners.get(st.as_pointer()) != uid:
                continue
        su = idmap.claim(st)
        if su:
            out[PART_PREFIX + su] = settings.settings_of(st, _PART_SKIP)
    return out


def _particles(m):
    """A particle system modifier: its settings datablock (by id, and its values in case it is gone
    when the object comes back) and its seed. Baked particles are not part of it."""
    ps = getattr(m, "particle_system", None) if m.type == "PARTICLE_SYSTEM" else None
    if ps is None or ps.settings is None:
        return None
    return {"seed": int(ps.seed), "settings": idmap.claim(ps.settings),
            "values": struct_values(ps.settings)}


def _physics(ob) -> dict:
    """Physics settings that live on the object: collision, force field, rigid body."""
    out = {}
    for name, used in (("collision", any(m.type == "COLLISION" for m in ob.modifiers)),
                       ("field", getattr(getattr(ob, "field", None), "type", "NONE") != "NONE"),
                       ("rigid_body", getattr(ob, "rigid_body", None) is not None)):
        st = getattr(ob, name, None)
        if used and st is not None:
            try:
                out[name] = struct_values(st)
            except Exception:
                continue
    return out


def _set_physics_modifier(ob, m, w) -> None:
    for name, vals in (w.get("nested") or {}).items():
        st = getattr(m, name, None)
        if st is not None:
            set_struct_values(st, vals, ob, f'modifiers["{m.name}"].{name}.')
    pr = w.get("particles")
    ps = getattr(m, "particle_system", None)
    if isinstance(pr, dict) and ps is not None:
        made = ps.settings
        want = idmap.find(pr["settings"]) if pr.get("settings") else None
        if want is not None and want != made:
            ps.settings = want
            if made is not None and made.users == 0:
                bpy.data.particles.remove(made)
        elif want is None and ps.settings is not None:
            set_struct_values(ps.settings, pr.get("values") or {})
        ps.seed = int(pr.get("seed", ps.seed))


def _set_physics(ob, phys) -> None:
    if not isinstance(phys, dict):
        return
    if phys.get("collision") and getattr(ob, "collision", None) is not None:
        set_struct_values(ob.collision, phys["collision"])
    f = phys.get("field")
    if f and getattr(ob, "field", None) is not None:
        if "type" in f:                           # the type first: it decides which settings exist
            set_struct_values(ob.field, {"type": f["type"]})
        set_struct_values(ob.field, f)
    rb = phys.get("rigid_body")
    if rb:
        try:
            if ob.rigid_body is None:
                with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
                    bpy.ops.rigidbody.object_add()
            if ob.rigid_body is not None:
                if "type" in rb:
                    set_struct_values(ob.rigid_body, {"type": rb["type"]})
                set_struct_values(ob.rigid_body, rb)
        except Exception as e:
            log.error_once(("rigid_body", str(e)), f"could not bring back a rigid body: {e}", e)
