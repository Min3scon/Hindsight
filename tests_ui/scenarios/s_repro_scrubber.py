"""Where is the on-screen slider after a stroke? Checks it against the asset shelf and other modes."""
import importlib

import bpy

PKG = "bl_ext.user_default.hindsight"


def run(d):
    yield 1.0
    d.key("ESC")
    yield 0.5
    win, area, reg, ui, space = d.view3d()
    with d.override():
        bpy.ops.object.mode_set(mode="SCULPT")
    yield 0.5
    cx, cy = reg.width // 2, reg.height // 2
    pts = [(cx - 120 + i * 8, cy + 10 + (i % 5)) for i in range(31)]
    yield from d.drag(reg, pts, step_wait=0.02)
    yield 1.0
    rt = importlib.import_module(PKG + ".bl.runtime")
    with d.override():
        d.note("scrub_info", rt.scrub_info(bpy.context))
    d.note("regions", [(r.type, r.x, r.y, r.width, r.height) for r in area.regions])
    d.note("asset_shelf_shown", getattr(space, "show_region_asset_shelf", None))
    d.shot("sculpt_with_shelf", area=area)
    if hasattr(space, "show_region_asset_shelf"):
        space.show_region_asset_shelf = False
        yield 0.5
        d.shot("sculpt_shelf_hidden", area=area)
        space.show_region_asset_shelf = True
    with d.override():
        bpy.ops.object.mode_set(mode="OBJECT")
    yield 0.5
    d.shot("object_mode", area=area)
    sc = importlib.import_module(PKG + ".bl.scrubber")
    d.note("draw_handle", repr(getattr(sc, "_draw_handle", None)))
    d.note("reported", sorted(map(str, getattr(sc, "_reported", []))))
