"""The on-screen slider with the toolbar, the sidebar, both and neither open (HANDOFF, beta 3 checklist).

In each layout the slider's bar must stay clear of the toolbar and the sidebar (Region Overlap draws
them over the viewport), be visible on screen (a screenshot pixel in the middle of the filled bar is
the slider colour), and a drag on it must move the history to the position under the mouse. Where it
sits follows D62: centred on the viewport; the narrow toolbar doesn't move it, the sidebar moves the
centre to the middle of the space it leaves.
"""
import bpy

import common as C


def _pixel(path, x, y):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        x, y = int(min(max(x, 0), w - 1)), int(min(max(y, 0), h - 1))
        i = (y * w + x) * 4
        return tuple(round(v, 3) for v in img.pixels[i:i + 4])
    finally:
        bpy.data.images.remove(img)


def _orange(px):
    r, g, b = px[:3]
    return r > 0.8 and 0.4 < g < 0.8 and b < 0.45


def _side(area, region, align):
    """Width of the toolbar (LEFT) or sidebar (RIGHT) covering the viewport region, 0 when hidden."""
    for r in area.regions:
        if r.type in ("TOOLS", "UI") and r.width > 1 and getattr(r, "alignment", "") == align:
            return (r.x + r.width - region.x) if align == "LEFT" else (region.x + region.width - r.x)
    return 0


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    C.fresh_sphere(d)
    C.frame_view(d)
    C.set_mode(d, "SCULPT")
    yield 0.6
    for k in range(4):
        yield from C.real_stroke(d, k)
    d.check("four strokes", C.info(d)["len"] == 4, C.info(d))

    centres = {}
    for label, tools, ui in (("neither", False, False), ("toolbar", True, False), ("sidebar", False, True),
                             ("both", True, True)):
        win, area, reg, uireg, space = d.view3d()
        space.show_region_toolbar = tools
        space.show_region_ui = ui
        yield 0.6
        win, area, reg, uireg, space = d.view3d()
        left, right = _side(area, reg, "LEFT"), _side(area, reg, "RIGHT")
        lay = C.slider_layout(d)
        x0, y0, x1, y1 = lay["bar"]
        d.check(f"{label}: bar clear of the toolbar and the sidebar", x0 >= left and x1 <= reg.width - right,
                (x0, x1, left, reg.width - right))
        centres[label] = ((x0 + x1) / 2, right)
        path = d.shot(f"slider_{label}")
        i = C.info(d)
        sc = C.mod("scrubber")
        fx = sc.x_from_pos(max(1, i["pos"]) / 2, lay, i["len"]) if i["pos"] else (x0 + x1) / 2
        px = _pixel(path, reg.x + fx, reg.y + (y0 + y1) / 2) if path else None
        d.check(f"{label}: filled bar visible on screen", px is not None and _orange(px), (fx, px))
        target = 1 if i["pos"] != 1 else 3
        yield from C.drag_slider_to(d, target)
        yield 0.4
        d.check(f"{label}: dragging the bar moves to the position under the mouse",
                C.info(d)["pos"] == target, (target, C.info(d)))
        yield from C.drag_slider_to(d, 4)
        yield 0.4
    c = {k: v[0] for k, v in centres.items()}
    side = centres["sidebar"][1]
    d.check("D62: the toolbar doesn't move the slider", abs(c["toolbar"] - c["neither"]) <= 1
            and abs(c["both"] - c["sidebar"]) <= 1, c)
    d.check("D62: the sidebar moves the centre to the middle of the space it leaves",
            abs((c["neither"] - c["sidebar"]) - side / 2) <= 2, (c, side))
