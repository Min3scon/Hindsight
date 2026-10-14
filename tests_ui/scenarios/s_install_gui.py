"""Install the zip the way a user does (Install from Disk) in a live window, then look for the UI.

Reproduces what a first-time user sees: before any stroke, with the sidebar closed (Blender's
default), and after one real stroke made with simulated mouse input.
"""
import bpy

PKG = "bl_ext.user_default.hindsight"


def _enabled():
    return PKG in bpy.context.preferences.addons


def run(d):
    yield 1.0
    d.key("ESC")  # dismiss the splash screen of a fresh profile
    yield 0.5
    d.shot("before_install")
    d.check("not installed before", not _enabled())

    mark = d.console_mark()
    zip_path = d.args.get("zip") or d.spec["zip"]
    with d.override():
        r = bpy.ops.extensions.package_install_files(filepath=zip_path, repo="user_default",
                                                     enable_on_install=True)
    d.log("install result", r)
    yield 2.0
    log = d.console_since(mark)
    d.check("enabled after install", _enabled(), sorted(bpy.context.preferences.addons.keys()))
    d.check("no traceback during install", "Traceback" not in log, log[-1500:])
    d.check("panel class registered", hasattr(bpy.types, "VIEW3D_PT_hindsight"))
    d.shot("after_install_default_layout")

    # What a new user sees in Sculpt Mode before touching anything (sidebar closed).
    with d.override():
        bpy.ops.object.mode_set(mode="SCULPT")
    yield 1.0
    d.shot("sculpt_mode_before_stroke")

    # One real stroke with simulated mouse input across the cube.
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.width // 2, reg.height // 2
    pts = [(cx - 120 + i * 8, cy + 10 + (i % 5)) for i in range(31)]
    mark = d.console_mark()
    yield from d.drag(reg, pts, step_wait=0.02)
    yield 1.0
    d.shot("sculpt_mode_after_stroke")
    log = d.console_since(mark)
    d.check("no traceback after stroke", "Traceback" not in log, log[-1500:])
    try:
        import importlib
        rt = importlib.import_module(PKG + ".bl.runtime")
        with d.override():
            info = rt.scrub_info(bpy.context)
        d.note("scrub_info_after_stroke", info)
        d.check("history recorded after real stroke", info is not None and info.get("len", 0) >= 1, info)
    except Exception as e:
        d.check("runtime importable", False, e)

    space.show_region_ui = True
    yield 0.5
    d.shot("sidebar_open")
    d.note("console_tail", d.console()[-3000:])
