"""Weight Paint, Vertex Paint and Texture Paint in a real window (D140, D141, V2).

One real stroke in each mode (simulated mouse input in the viewport) must be exactly one step,
and dragging the on-screen slider back must put the weights, the colours and the image back;
dragging it forward again must bring the stroke back. Texture painting is recorded only with the
preference Record texture painting, which this scenario turns on.
"""
import bpy

import common as C


def _weights(ob):
    return round(sum(g.weight for v in ob.data.vertices for g in v.groups), 3)


def _colours(ob):
    a = ob.data.color_attributes["Col"]
    buf = [0.0] * (4 * len(a.data))
    a.data.foreach_get("color", buf)
    return round(sum(buf), 2)


def _pixels(img):
    buf = [0.0] * (4 * img.size[0] * img.size[1])
    img.pixels.foreach_get(buf)
    return round(sum(buf), 2)


def _back_and_forth(d, label, read):
    """Generator: slider to the step before the last one and to the last one again, checking read()."""
    i = C.info(d)
    after = read()
    yield from C.drag_slider_to(d, i["pos"] - 1)
    yield 0.4
    back = read()
    j = C.info(d)
    d.check(f"{label}: slider back one step puts it back", j is not None and j["pos"] == i["pos"] - 1
            and back != after, (i, j, after, back))
    yield from C.drag_slider_to(d, i["pos"])
    yield 0.4
    k = C.info(d)
    d.check(f"{label}: slider forward brings the stroke back", k is not None and k["pos"] == i["pos"]
            and read() == after, (k, after, read()))
    return back


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    ob = C.fresh_sphere(d, segments=48, rings=24)
    C.frame_view(d)
    me = ob.data
    vg = ob.vertex_groups.new(name="Group")
    vg.add(list(range(len(me.vertices))), 0.0, "REPLACE")
    col = me.color_attributes.new("Col", "FLOAT_COLOR", "POINT")
    col.data.foreach_set("color", [0.5, 0.5, 0.5, 1.0] * len(col.data))     # any brush colour changes grey
    me.color_attributes.active_color = col
    img = bpy.data.images.new("Canvas", 256, 256, alpha=False)
    img.pixels.foreach_set([0.5, 0.5, 0.5, 1.0] * (256 * 256))
    img.update()
    prefs = C.mod("prefs").get_prefs()
    prefs.record_texture_painting = True
    yield 0.3

    # ------------------------------------------------------------------ Weight Paint
    C.set_mode(d, "WEIGHT_PAINT")
    yield 0.6
    w0 = _weights(ob)
    yield from C.real_stroke(d, 0)
    i = C.info(d)
    d.check("weight paint: one stroke, one step", i is not None and i["len"] == 1 and _weights(ob) != w0,
            (i, w0, _weights(ob)))
    d.shot("weight_paint_stroke")
    back = yield from _back_and_forth(d, "weight paint", lambda: _weights(ob))
    d.check("weight paint: back is the weights before the stroke", back == w0, (back, w0))

    # ------------------------------------------------------------------ Vertex Paint
    C.set_mode(d, "VERTEX_PAINT")
    yield 0.6
    n = C.info(d)["len"]
    c0 = _colours(ob)
    yield from C.real_stroke(d, 1)
    i = C.info(d)
    d.check("vertex paint: one stroke, one step", i["len"] == n + 1 and _colours(ob) != c0,
            (n, i, c0, _colours(ob)))
    d.shot("vertex_paint_stroke")
    back = yield from _back_and_forth(d, "vertex paint", lambda: _colours(ob))
    d.check("vertex paint: back is the colours before the stroke", back == c0, (back, c0))

    # ------------------------------------------------------------------ Texture Paint
    C.set_mode(d, "TEXTURE_PAINT")
    ip = bpy.context.scene.tool_settings.image_paint
    ip.mode = "IMAGE"
    ip.canvas = img
    yield 0.6
    n = C.info(d)["len"]
    p0 = _pixels(img)
    yield from C.real_stroke(d, 2)
    yield 0.4
    i = C.info(d)
    d.check("texture paint: one stroke, one step", i["len"] == n + 1 and _pixels(img) != p0,
            (n, i, p0, _pixels(img)))
    d.shot("texture_paint_stroke")
    back = yield from _back_and_forth(d, "texture paint", lambda: _pixels(img))
    d.check("texture paint: back is the image before the stroke", back == p0, (back, p0))
