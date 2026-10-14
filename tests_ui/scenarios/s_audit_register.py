"""Evidence for the registration checklist: modules, keymaps, preferences, handlers, timers."""
import importlib
import sys

import bpy

PKG = "bl_ext.user_default.hindsight"


def run(d):
    yield 1.0
    d.key("ESC")
    yield 0.3
    top = sys.modules.get(PKG)
    d.check("package imported", top is not None)
    loaded = [m.__name__.rsplit(".", 1)[-1] for m in getattr(top, "_loaded", [])]
    d.note("loaded_modules", loaded)
    wanted = [n for n, _ in getattr(top, "_MODULES", ())]
    d.check("every module registered", loaded == wanted, f"loaded={loaded} wanted={wanted}")
    addon = bpy.context.preferences.addons.get(PKG)
    d.check("preferences object present", addon is not None and addon.preferences is not None)
    kc = bpy.context.window_manager.keyconfigs.addon
    items = []
    for km in kc.keymaps:
        for kmi in km.keymap_items:
            if kmi.idname.startswith("hindsight."):
                items.append(f"{km.name}: {kmi.idname} {kmi.type} ctrl={kmi.ctrl} shift={kmi.shift}")
    d.note("keymap_items", items)
    d.check("keymaps registered", len(items) > 0, items)
    sc = importlib.import_module(PKG + ".bl.scrubber")
    d.check("draw handler registered", sc._draw_handle is not None)
    rt = importlib.import_module(PKG + ".bl.runtime")
    d.check("tick timer registered", bpy.app.timers.is_registered(rt.tick))
    d.note("gizmo_group", hasattr(bpy.types, "HINDSIGHT_GGT_scrubber"))
    d.note("panels", [n for n in dir(bpy.types) if n.startswith("VIEW3D_PT_hindsight")])
    d.note("operators", sorted(n for n in dir(bpy.types) if n.startswith("HINDSIGHT_OT_")))
    d.note("menus", sorted(n for n in dir(bpy.types) if n.startswith("HINDSIGHT_MT_")))
    d.note("tools", [t for t in dir(bpy.types) if "TOOL" in t.upper() and "HINDSIGHT" in t.upper()])
