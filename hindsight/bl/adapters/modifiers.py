"""Data adapters: modifiers (see ADAPTERS.md)."""
from __future__ import annotations

import bpy
from ...core.types import MISSING
from .. import idmap, log
from .core import _UI_PROPS, animated_paths, set_struct_values, struct_values
from .idprops import _gn_inputs, _nested_values, _set_gn_inputs, _set_id_props
from .particles import _particles, _set_physics_modifier
from .settings import _id_props


# ----------------------------------------------------------------------------- modifiers (D110)
MOD_PREFIX = "mod:"
_MOD_SKIP = frozenset(_UI_PROPS | {"execution_time", "persistent_uid", "is_override_data", "show_expanded",
                                   "is_active", "use_pin_to_last"}) - {"show_in_editmode", "show_on_cage"}
_NOT_REMADE = {"MULTIRES"}       # its subdivision data can't be made again from settings


def modifier_values(ob) -> dict:
    """The stack as [[type, name], ...] and each modifier's settings (its own values, the settings it
    keeps in structs, Geometry Nodes inputs, custom properties), one path per modifier."""
    mods = list(ob.modifiers)
    out = {"modifiers": [[m.type, m.name] for m in mods]}
    anim = animated_paths(ob)
    for m in mods:
        try:
            rec = {"values": struct_values(m, _MOD_SKIP, anim=anim)}
            nested = _nested_values(m)
            if nested:
                rec["nested"] = nested
            gn = _gn_inputs(m)
            if gn is not None:
                rec["gn"] = gn
            custom = _id_props(m)
            if custom:
                rec["custom"] = custom
            pr = _particles(m)
            if pr is not None:
                rec["particles"] = {"seed": pr["seed"], "settings": pr["settings"]}
            out[MOD_PREFIX + m.name] = rec
        except Exception:
            continue
    return out


def write_modifiers(ob, values: dict, recorded=None) -> None:
    """Make the stack what it was recorded as: modifiers removed and added by name, moved into order
    (never all removed and added again: bind data and caches of the ones that stay are kept), then
    each changed modifier's settings put back."""
    layout = values.get("modifiers")
    if layout is MISSING:
        layout = None
    try:
        if isinstance(layout, list):
            want = [(t, n) for t, n in layout]
            names = [n for _t, n in want]
            for m in [m for m in ob.modifiers if m.name not in names or
                      (m.name, m.type) not in {(n, t) for t, n in want}]:
                ob.modifiers.remove(m)
            for t, n in want:
                if ob.modifiers.get(n) is None and t not in _NOT_REMADE:
                    m = ob.modifiers.new(n, t)
                    if m is not None and m.name != n:
                        m.name = n
                    rec = values.get(MOD_PREFIX + n)
                    if rec is None and recorded is not None:
                        rec = recorded(MOD_PREFIX + n)
                    if isinstance(rec, dict) and m is not None:
                        _set_modifier(ob, m, rec)
            present = [n for _t, n in want if ob.modifiers.get(n) is not None]
            for i, n in enumerate(present):
                cur = [m.name for m in ob.modifiers].index(n)
                if cur != i:
                    ob.modifiers.move(cur, i)
    except Exception as e:
        log.error_once(("modifier_stack", str(e)), f"could not restore the modifier stack: {e}", e)
    for path, rec in values.items():
        if not path.startswith(MOD_PREFIX) or not isinstance(rec, dict):
            continue
        m = ob.modifiers.get(path[len(MOD_PREFIX):])
        if m is not None:
            try:
                _set_modifier(ob, m, rec)
            except Exception as e:
                log.error_once(("modifier_values", str(e)), f"could not restore {m.name}: {e}", e)


def _set_modifier(ob, m, rec: dict) -> None:
    vals = rec.get("values") or {}
    if "node_group" in vals:                    # the node group first: the inputs are its sockets
        set_struct_values(m, {"node_group": vals["node_group"]}, ob, f'modifiers["{m.name}"].')
    set_struct_values(m, vals, ob, f'modifiers["{m.name}"].')
    _set_id_props(m, rec.get("custom"))
    _set_gn_inputs(m, rec.get("gn"))
    _set_physics_modifier(ob, m, {"nested": rec.get("nested"), "particles": None})
    pr = rec.get("particles")
    ps = getattr(m, "particle_system", None)
    if isinstance(pr, dict) and ps is not None:
        want = idmap.find(pr["settings"]) if pr.get("settings") else None
        if want is not None and ps.settings != want:
            made = ps.settings
            ps.settings = want
            if made is not None and made.users == 0:
                bpy.data.particles.remove(made)
        if ps.seed != int(pr.get("seed", ps.seed)):
            ps.seed = int(pr.get("seed", ps.seed))
