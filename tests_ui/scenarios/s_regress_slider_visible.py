"""Regression (beta 1): the on-screen slider was drawn under the Sculpt Mode asset shelf.

Takes a real screenshot and samples the pixels where the slider must be: the filled part must
be the slider colour, not the shelf. Also checks the empty track before the first stroke.
"""
import bpy

import common as C


def _pixel(path, x, y):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        x, y = int(min(max(x, 0), w - 1)), int(min(max(y, 0), h - 1))
        i = (y * w + x) * 4
        px = img.pixels[i:i + 4]
        return tuple(round(v, 3) for v in px)
    finally:
        bpy.data.images.remove(img)


def _orange(px):
    r, g, b = px[:3]
    return r > 0.8 and 0.4 < g < 0.8 and b < 0.45


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    C.fresh_sphere(d)
    C.frame_view(d)
    C.set_mode(d, "SCULPT")
    yield 0.6
    win, area, reg, ui, space = d.view3d()
    shelf = [r for r in area.regions if r.type in ("ASSET_SHELF", "ASSET_SHELF_HEADER") and r.height > 1]
    d.note("shelf", [(r.type, r.y, r.height) for r in shelf])
    path = d.shot("empty_track")
    lay = C.slider_layout(d)
    x0, y0, x1, y1 = lay["bar"]
    cy = (y0 + y1) / 2
    # the menu dot ring is the slider colour even with no history
    cx, dcy, r = lay["dot"]
    px = _pixel(path, reg.x + cx + r - 1, reg.y + dcy)
    d.check("empty track: menu dot visible above the shelf", _orange(px), px)

    for k in range(2):
        yield from C.real_stroke(d, k)
    path = d.shot("after_two_strokes")
    i = C.info(d)
    lay = C.slider_layout(d)
    x0, y0, x1, y1 = lay["bar"]
    cy = (y0 + y1) / 2
    px = _pixel(path, reg.x + (x0 + x1) / 2, reg.y + cy)
    d.check("filled slider is visible on screen (not under the shelf)", i["len"] == 2 and _orange(px), (i, px))
    if shelf:
        d.check("slider above the shelf", y0 >= max(r.y + r.height for r in shelf) - reg.y,
                (y0, [(r.y, r.height) for r in shelf]))
