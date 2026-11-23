"""Multires in a real window (Record Multires, off by default until this passes).

Real strokes on a Multires sphere (level 2) during one Sculpt Mode visit are one step, taken when the
visit ends. Dragging the on-screen slider back in a later visit puts the displacement back exactly
(the evaluated mesh), dragging it forward brings the strokes back, and a visit with jumps only is no
step. A second visit with strokes is a second step.
"""
import hashlib

import bpy

import common as C


def _hash(d):
    """The evaluated mesh (every vertex of the shown level), in Object Mode."""
    with d.override():
        ob = bpy.data.objects["Blob"]
        dg = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        m = ev.to_mesh()
        buf = [0.0] * (3 * len(m.vertices))
        m.vertices.foreach_get("co", buf)
        n = len(m.vertices)
        ev.to_mesh_clear()
    return hashlib.sha1(repr([round(x, 5) for x in buf]).encode()).hexdigest()[:12], n


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    C.mod("prefs").get_prefs().record_scope = "SCULPT"    # #62: what this proves is about sculpting only
    yield 0.3
    ob = C.fresh_sphere(d, segments=32, rings=16)
    C.frame_view(d)
    with d.override():
        ob.modifiers.new("Multires", "MULTIRES")
        for _ in range(2):
            bpy.ops.object.multires_subdivide(modifier="Multires", mode="CATMULL_CLARK")
    C.mod("prefs").get_prefs().record_multires = True
    yield 0.4
    h0 = _hash(d)

    C.set_mode(d, "SCULPT")
    yield 0.9                                  # the visit's baseline settles (0.25 s) before the first stroke
    for k in range(2):
        yield from C.real_stroke(d, k)
    C.set_mode(d, "OBJECT")
    yield 0.8
    h1 = _hash(d)
    i = C.info(d)
    d.check("two strokes in one visit: one step when the visit ends", i is not None and i["len"] == 1
            and h1 != h0, (i, h0, h1))
    d.shot("multires_after_visit")

    C.set_mode(d, "SCULPT")
    yield 0.9
    yield from C.drag_slider_to(d, 0)
    yield 0.6
    C.set_mode(d, "OBJECT")
    yield 0.8
    d.check("slider back: the displacement before the strokes", _hash(d) == h0 and C.info(d)["len"] == 1,
            (_hash(d), h0, C.info(d)))

    C.set_mode(d, "SCULPT")
    yield 0.9
    yield from C.drag_slider_to(d, 1)
    yield 0.6
    C.set_mode(d, "OBJECT")
    yield 0.8
    d.check("slider forward: the strokes come back, a visit with jumps only is no step",
            _hash(d) == h1 and C.info(d)["len"] == 1, (_hash(d), h1, C.info(d)))

    C.set_mode(d, "SCULPT")
    yield 0.9
    yield from C.real_stroke(d, 3)
    C.set_mode(d, "OBJECT")
    yield 0.8
    h2 = _hash(d)
    d.check("a second visit with a stroke is a second step", C.info(d)["len"] == 2 and h2 != h1,
            (C.info(d), h1, h2))
    d.check("Multires modifier and its levels kept", [m.type for m in ob.modifiers] == ["MULTIRES"]
            and ob.modifiers[0].total_levels == 2, [(m.type, getattr(m, "total_levels", None)) for m in ob.modifiers])
