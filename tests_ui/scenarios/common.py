"""Helpers shared by the real-UI scenarios (run inside Blender)."""
import importlib
import math

import bpy

PKG = "bl_ext.user_default.hindsight"


def mod(name):
    return importlib.import_module(f"{PKG}.bl.{name}")


def enabled():
    return PKG in bpy.context.preferences.addons


def install_like_user(d, zip_path=None):
    zip_path = zip_path or d.spec["zip"]
    with d.override():
        return bpy.ops.extensions.package_install_files(filepath=zip_path, repo="user_default",
                                                        enable_on_install=True)


def dismiss_splash(d):
    d.key("ESC")


def fresh_sphere(d, name="Blob", segments=96, rings=48, radius=1.2):
    """Replace the default scene's objects with one smooth sphere, active and selected."""
    with d.override():
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for ob in list(bpy.data.objects):
            if ob.type == "MESH":
                bpy.data.objects.remove(ob)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, radius=radius)
        ob = bpy.context.active_object
        ob.name = name
        bpy.ops.object.shade_smooth()
    return ob


def frame_view(d):
    with d.override():
        bpy.ops.view3d.view_selected()


def set_mode(d, mode):
    with d.override():
        bpy.ops.object.mode_set(mode=mode)


def stroke_pts(reg, k=0, n=26, span=0.22):
    """A gently curved stroke across the middle of the viewport (region coords)."""
    cx, cy = reg.width * 0.5, reg.height * 0.5
    w = reg.width * span
    off = (k % 5 - 2) * reg.height * 0.05
    return [(cx - w + 2 * w * i / (n - 1), cy + off + math.sin(i / (n - 1) * math.pi) * 18)
            for i in range(n)]


def real_stroke(d, k=0):
    """Generator: one stroke with simulated mouse input, then wait for the recorder to commit."""
    win, area, reg, ui, space = d.view3d()
    yield from d.drag(reg, stroke_pts(reg, k), step_wait=0.015)
    yield 0.45


def info(d):
    with d.override():
        return mod("runtime").scrub_info(bpy.context)


def slider_layout(d):
    win, area, reg, ui, space = d.view3d()
    sc = mod("scrubber")
    with d.override():
        return sc._layout_for(bpy.context, reg)


def drag_slider_to(d, pos):
    """Generator: drag the on-screen slider handle to history position `pos`."""
    win, area, reg, ui, space = d.view3d()
    sc = mod("scrubber")
    i = info(d)
    lay = slider_layout(d)
    x0 = sc.x_from_pos(i["pos"], lay, i["len"])
    x1 = sc.x_from_pos(pos, lay, i["len"])
    y = (lay["bar"][1] + lay["bar"][3]) / 2
    pts = [(x0 + (x1 - x0) * t / 10, y) for t in range(11)]
    yield from d.drag(reg, pts, step_wait=0.03)
    yield 0.3


def show_sidebar_tab(d, name="Undo History"):
    """Generator: open the sidebar and click down its tab strip until the tab is active."""
    win, area, reg, ui, space = d.view3d()
    space.show_region_ui = True
    yield 0.4
    win, area, reg, ui, space = d.view3d()
    if ui.active_panel_category == name:
        return
    x = ui.x + ui.width - 10
    top = ui.y + ui.height - 8
    for k in range(0, 60):
        y = top - k * 10
        if y < ui.y + 8:
            break
        d.ev("MOUSEMOVE", x=x, y=y)
        d.ev("LEFTMOUSE", "PRESS", x=x, y=y)
        d.ev("LEFTMOUSE", "RELEASE", x=x, y=y)
        yield 0.06
        if ui.active_panel_category == name:
            d.ev("MOUSEMOVE", x=ui.x + 20, y=ui.y + 20)
            return
    d.log("could not switch the sidebar to", name, "now", ui.active_panel_category)


def type_text(d, text):
    for ch in text:
        key = {" ": "SPACE", "-": "MINUS", ".": "PERIOD"}.get(ch, ch.upper())
        d.ev(key, "PRESS", unicode=ch)
        d.ev(key, "RELEASE")


def keymap_hits(idname):
    kc = bpy.context.window_manager.keyconfigs.addon
    return [(km.name, kmi.type, kmi.ctrl, kmi.shift) for km in kc.keymaps for kmi in km.keymap_items
            if kmi.idname == idname]
