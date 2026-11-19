"""Painting in an Image Editor's Paint mode, in a real window (D144).

With Record texture painting on, a stroke painted in a 2D Image Editor on an image of the active
object's material is a Texture Paint step of that object, which stays in Object Mode. Going back puts
the image back, going forward brings the stroke back. An image of no object isn't recorded.
"""
import bpy

import common as C


def _pixels(img):
    buf = [0.0] * (4 * img.size[0] * img.size[1])
    img.pixels.foreach_get(buf)
    return round(sum(buf), 2)


def _goto(d, pos):
    with d.override():
        C.mod("runtime").goto(bpy.context, pos)


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    ob = C.fresh_sphere(d, segments=32, rings=16)
    C.frame_view(d)
    img = bpy.data.images.new("Canvas", 256, 256, alpha=False)
    img.pixels.foreach_set([0.5, 0.5, 0.5, 1.0] * (256 * 256))
    img.update()
    mat = bpy.data.materials.new("Paint")
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    mat.node_tree.nodes.new("ShaderNodeTexImage").image = img
    ob.data.materials.append(mat)
    loose = bpy.data.images.new("Loose", 128, 128, alpha=False)
    loose.pixels.foreach_set([0.5, 0.5, 0.5, 1.0] * (128 * 128))
    C.mod("prefs").get_prefs().record_texture_painting = True

    win = d.window
    area = next(a for a in win.screen.areas if a.type == "PROPERTIES")
    area.type = "IMAGE_EDITOR"
    yield 0.3
    sp = area.spaces.active
    sp.image = img
    if hasattr(sp, "ui_mode"):
        sp.ui_mode = "PAINT"
    else:
        sp.mode = "PAINT"
    yield 0.4
    reg = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=reg):
        bpy.ops.image.view_all(fit_view=True)
    yield 0.6
    d.note("image_editor", {"mode": sp.mode, "size": (reg.width, reg.height)})

    def stroke(k):
        cx, cy = reg.width * 0.5, reg.height * (0.4 + 0.1 * k)
        pts = [(cx - reg.width * 0.3 + i * reg.width * 0.6 / 19, cy) for i in range(20)]
        for j in range(3):                     # hover first: a fresh area can eat the first press
            d.ev("MOUSEMOVE", x=reg.x + pts[0][0] + j, y=reg.y + pts[0][1])
        yield 0.2
        yield from d.drag(reg, pts, step_wait=0.02)
        yield 0.6

    p0 = _pixels(img)
    yield from stroke(0)
    p1 = _pixels(img)
    h = C.mod("runtime").history_of(ob)
    d.check("a stroke in the Image Editor is a step of the object, which stays in Object Mode",
            h is not None and len(h.entries) == 1 and p1 != p0 and ob.mode == "OBJECT",
            (h and [e.meta.get("label") for e in h.entries], p0, p1, ob.mode))
    d.shot("image_editor_stroke", area=area)
    yield from stroke(1)
    p2 = _pixels(img)
    d.check("a second stroke, a second step", h is not None and len(h.entries) == 2 and p2 != p1,
            (h and len(h.entries), p1, p2))
    _goto(d, 0)
    yield 0.4
    d.check("going back to the start puts the image back", _pixels(img) == p0, (_pixels(img), p0))
    _goto(d, 1)
    yield 0.4
    d.check("going forward one step brings the first stroke back", _pixels(img) == p1, (_pixels(img), p1))
    _goto(d, 2)
    yield 0.4

    sp.image = loose
    yield 0.4
    q0 = _pixels(loose)
    yield from stroke(0)
    d.check("an image of no object isn't recorded", len(h.entries) == 2 and _pixels(loose) != q0,
            (len(h.entries), q0, _pixels(loose)))
