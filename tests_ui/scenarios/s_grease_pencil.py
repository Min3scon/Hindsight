"""Grease Pencil drawn in a real window (D131; recorded headless before).

With Everything in this file on, each stroke drawn with the mouse in Draw mode is one step on the global
timeline; dragging the slider back takes the strokes away one by one and forward brings them back, with
their points. Blender 4.3 and later draw Grease Pencil v3 (grease_pencil), 4.2 the legacy kind (gpencil).
"""
import bpy

import common as C

V3 = bpy.app.version >= (4, 3, 0)


def _strokes(ob):
    """(number of strokes, number of points) on the active layer's current frame."""
    d = ob.data
    if V3:
        layer = d.layers.active
        fr = layer.current_frame() if layer is not None else None
        if fr is None or fr.drawing is None:
            return (0, 0)
        st = fr.drawing.strokes
        return (len(st), sum(len(s.points) for s in st))
    layer = d.layers.active
    fr = layer.active_frame if layer is not None else None
    if fr is None:
        return (0, 0)
    return (len(fr.strokes), sum(len(s.points) for s in fr.strokes))


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    with d.override():
        for ob in list(bpy.data.objects):
            if ob.type == "MESH":
                bpy.data.objects.remove(ob)
        if V3:
            bpy.ops.object.grease_pencil_add(type="EMPTY", location=(0, 0, 0))
        else:
            bpy.ops.object.gpencil_add(type="EMPTY", location=(0, 0, 0))
        gp = bpy.context.active_object
        gp.name = "Drawing"
        bpy.ops.view3d.view_axis(type="FRONT")
        bpy.ops.view3d.view_all()
    C.mod("prefs").get_prefs().record_scope = "EVERYTHING"
    with d.override():
        bpy.ops.hindsight.record_file(on=True)
    bpy.context.scene.hindsight.use_global = True
    yield 0.6
    C.set_mode(d, "PAINT_GREASE_PENCIL" if V3 else "PAINT_GPENCIL")
    yield 0.8
    n0 = (C.info(d) or {}).get("len", 0)
    states = [_strokes(gp)]
    win, area, reg, ui, space = d.view3d()
    for k in range(3):
        y = reg.height * (0.35 + 0.15 * k)
        pts = [(reg.width * 0.3 + i * reg.width * 0.4 / 19, y + (i % 4) * 3) for i in range(20)]
        for j in range(3):
            d.ev("MOUSEMOVE", x=reg.x + pts[0][0] + j, y=reg.y + pts[0][1])
        yield 0.2
        yield from d.drag(reg, pts, step_wait=0.02)
        yield 1.0
        states.append(_strokes(gp))
    i = C.info(d)
    d.check("three strokes drawn, three steps", i is not None and i["len"] == n0 + 3
            and [s[0] for s in states] == [states[0][0] + k for k in range(4)], (n0, i, states))
    d.shot("grease_pencil_strokes")
    for k in (2, 0, 1, 3):
        yield from C.drag_slider_to(d, n0 + k)
        yield 0.6
        d.check(f"slider to {n0 + k}: the strokes of that step, with their points",
                C.info(d)["pos"] == n0 + k and _strokes(gp) == states[k], (k, C.info(d), _strokes(gp), states[k]))
