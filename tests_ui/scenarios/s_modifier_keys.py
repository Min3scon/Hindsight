"""Modifiers changed with the keyboard in a real window (D110; recorded headless before).

With Everything in this file on: Ctrl+1 on a mesh adds a Subdivision Surface modifier, Ctrl+3 raises its
level, Ctrl+0 sets it to 0, each one step; the global slider puts the modifier stack and its levels back,
both ways.
"""
import bpy

import common as C


def _stack(ob):
    return tuple((m.type, getattr(m, "levels", None)) for m in ob.modifiers)


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    ob = C.fresh_sphere(d, segments=16, rings=8)
    C.frame_view(d)
    C.mod("prefs").get_prefs().record_scope = "EVERYTHING"
    with d.override():
        bpy.ops.hindsight.record_file(on=True)
    bpy.context.scene.hindsight.use_global = True
    yield 0.6
    n0 = (C.info(d) or {}).get("len", 0)
    states = [_stack(ob)]
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    d.ev("MOUSEMOVE", x=cx, y=cy)
    yield 0.2
    for key in ("ONE", "THREE", "ZERO"):
        d.key(key, ctrl=True, x=cx, y=cy)
        yield 1.0
        states.append(_stack(ob))
    i = C.info(d)
    d.check("Ctrl+1, Ctrl+3, Ctrl+0: three steps, each changing the modifier stack",
            i is not None and i["len"] == n0 + 3 and len(set(states)) == 4, (n0, i, states))
    for k in (2, 0, 1, 3):
        yield from C.drag_slider_to(d, n0 + k)
        yield 0.6
        d.check(f"slider to {n0 + k}: the modifier stack of that step", C.info(d)["pos"] == n0 + k
                and _stack(ob) == states[k], (k, C.info(d), _stack(ob), states[k]))
