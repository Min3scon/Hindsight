"""Round A: enable, disable, re-enable, uninstall and reinstall through Blender's own operators.

After disable and uninstall nothing of Hindsight may be left: classes, handlers, timers, draw
handlers, keymaps, menu entries, sys.modules entries, files. The console must stay clean.
"""
import os
import sys

import bpy

import common as C

PKG = C.PKG


def _ours(fn):
    return (getattr(fn, "__module__", "") or "").startswith(PKG)


def leftovers():
    out = []
    for name in dir(bpy.app.handlers):
        hl = getattr(bpy.app.handlers, name)
        if isinstance(hl, list):
            out += [f"handler {name}: {fn.__name__}" for fn in hl if _ours(fn)]
    out += [f"type {n}" for n in dir(bpy.types)
            if n.startswith(("HINDSIGHT_", "VIEW3D_PT_hindsight", "VIEW3D_MT_hindsight"))]
    kc = bpy.context.window_manager.keyconfigs.addon
    out += [f"keymap {km.name}: {k.idname}" for km in kc.keymaps for k in km.keymap_items
            if k.idname.startswith("hindsight.")]
    for menu in ("VIEW3D_MT_editor_menus", "VIEW3D_MT_object", "VIEW3D_MT_sculpt"):
        cls = getattr(bpy.types, menu)
        out += [f"menu {menu}: {f.__name__}" for f in cls._dyn_ui_initialize() if _ours(f)]
    if hasattr(bpy.types.WindowManager, "hindsight_step"):
        out.append("WindowManager.hindsight_step")
    if "hindsight" in bpy.types.Scene.bl_rna.properties:
        out.append("Scene.hindsight")
    return out


def timers_alive():
    alive = []
    for modname in ("bl.runtime", "bl.slider_prop", "bl.selfcheck"):
        mod = sys.modules.get(f"{PKG}.{modname}")
        if mod is None:
            continue
        for fname in ("tick", "_timer", "_welcome", "_clear_status"):
            fn = getattr(mod, fname, None)
            if fn is not None and bpy.app.timers.is_registered(fn):
                alive.append(f"{modname}.{fname}")
    return alive


def draw_handles():
    out = []
    for modname, attr in (("bl.scrubber", "_draw_handle"), ("bl.warnings", "_handle")):
        mod = sys.modules.get(f"{PKG}.{modname}")
        if mod is not None and getattr(mod, attr, None) is not None:
            out.append(modname)
    return out


def noisy(log):
    bad = []
    for line in log.splitlines():
        low = line.lower()
        if ("traceback" in low or "error" in low or "warning" in low) and "[driver" not in line \
                and "draw window and swap" not in low:
            bad.append(line)
    return bad


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    d.check("enabled at start", C.enabled())
    C.fresh_sphere(d)
    C.frame_view(d)
    C.set_mode(d, "SCULPT")
    yield 0.5
    yield from C.real_stroke(d, 0)
    d.check("records before the lifecycle test", C.info(d)["len"] == 1, C.info(d))
    C.set_mode(d, "OBJECT")
    yield 0.3

    mark = d.console_mark()
    with d.override():
        bpy.ops.preferences.addon_disable(module=PKG)
    yield 1.0
    d.check("disabled", not C.enabled())
    d.check("nothing left after disable", not leftovers(), leftovers())
    d.check("no timers left after disable", not timers_alive(), timers_alive())
    d.check("no draw handlers left after disable", not draw_handles(), draw_handles())
    d.shot("disabled")

    with d.override():
        bpy.ops.preferences.addon_enable(module=PKG)
    yield 1.0
    d.check("re-enabled", C.enabled() and hasattr(bpy.types, "VIEW3D_PT_hindsight"))
    C.set_mode(d, "SCULPT")
    yield 0.5
    yield from C.real_stroke(d, 1)
    i = C.info(d)
    d.check("records after re-enable (history of this session kept in the object)", i is not None and i["len"] >= 1, i)
    C.set_mode(d, "OBJECT")
    yield 0.3

    ext_dir = os.path.join(os.environ["BLENDER_USER_EXTENSIONS"], "user_default", "hindsight")
    d.check("installed files present", os.path.isdir(ext_dir), ext_dir)
    with d.override():
        r = bpy.ops.extensions.package_uninstall(repo_index=_repo_index(), pkg_id="hindsight")
    yield 1.5
    d.check("uninstall finished", r == {'FINISHED'}, r)
    d.check("files removed on uninstall", not os.path.isdir(ext_dir), os.listdir(os.path.dirname(ext_dir)))
    d.check("nothing left after uninstall", not leftovers(), leftovers())
    d.check("no timers left after uninstall", not timers_alive(), timers_alive())
    left_mods = [m for m in sys.modules if m.startswith(PKG)]
    d.note("modules_after_uninstall", left_mods)
    user_data = os.path.join(os.environ["BLENDER_USER_EXTENSIONS"], ".user", "user_default", "hindsight")
    d.note("user_data_after_uninstall", os.listdir(user_data) if os.path.isdir(user_data) else None)

    with d.override():
        r = C.install_like_user(d)
    yield 1.5
    d.check("reinstalled and enabled", r == {'FINISHED'} and C.enabled(), r)
    C.set_mode(d, "SCULPT")
    yield 0.5
    before = (C.info(d) or {}).get("len", 0)
    yield from C.real_stroke(d, 2)
    after = (C.info(d) or {}).get("len", 0)
    d.check("records after reinstall", after == before + 1, f"{before} -> {after}")
    log = d.console_since(mark)
    d.check("console clean through the whole lifecycle", not noisy(log), noisy(log)[:12])
    d.shot("reinstalled")


def _repo_index():
    repos = bpy.context.preferences.extensions.repos
    for i, r in enumerate(repos):
        if r.module == "user_default":
            return i
    return 0
