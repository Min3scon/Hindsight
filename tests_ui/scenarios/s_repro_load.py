"""Does recording survive File > Open? (Blender drops non-persistent handlers on every file load.)"""
import importlib
import os

import bpy

PKG = "bl_ext.user_default.hindsight"


def _handler_names():
    out = {}
    for name in ("depsgraph_update_post", "undo_post", "redo_post", "load_post", "save_pre"):
        out[name] = [getattr(f, "__module__", "?") + "." + getattr(f, "__name__", "?")
                     for f in getattr(bpy.app.handlers, name)]
    return out


def _stroke_and_count(d):
    win, area, reg, ui, space = d.view3d()
    with d.override():
        bpy.ops.object.mode_set(mode="SCULPT")
    yield 0.5
    cx, cy = reg.width // 2, reg.height // 2
    pts = [(cx - 100 + i * 7, cy + (i % 4)) for i in range(30)]
    yield from d.drag(reg, pts, step_wait=0.02)
    yield 1.0
    rt = importlib.import_module(PKG + ".bl.runtime")
    with d.override():
        info = rt.scrub_info(bpy.context)
    d.note("last_info", info)
    with d.override():
        bpy.ops.object.mode_set(mode="OBJECT")
    yield 0.3


def run(d):
    yield 1.0
    d.key("ESC")
    yield 0.5
    with d.override():
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.5)
    yield 0.5
    d.note("handlers_at_start", _handler_names())
    yield from _stroke_and_count(d)
    d.check("records before any file load", (d.notes["last_info"] or {}).get("len", 0) >= 1, d.notes["last_info"])

    path = os.path.join(d.out, "repro_load.blend")
    with d.override():
        bpy.ops.wm.save_as_mainfile(filepath=path)
    yield 0.5
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=path)
    yield 1.5
    d.note("handlers_after_open", _handler_names())
    rec = [h for h in d.notes["handlers_after_open"]["depsgraph_update_post"] if "runtime.on_depsgraph" in h]
    d.check("recorder handler still installed after File > Open", bool(rec), d.notes["handlers_after_open"])
    with d.override():
        bpy.context.view_layer.objects.active = bpy.data.objects[0]
    before = 0
    rt = importlib.import_module(PKG + ".bl.runtime")
    with d.override():
        i0 = rt.scrub_info(bpy.context)
    before = (i0 or {}).get("len", 0)
    yield from _stroke_and_count(d)
    after = (d.notes["last_info"] or {}).get("len", 0)
    d.check("a stroke after File > Open adds a step", after > before, f"before={before} after={after}")
    d.shot("after_open_and_stroke")
