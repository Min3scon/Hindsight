"""A third-party add-on's data in a real window (D114; owner: "recording should be for everything in
Blender including 3rd party addons").

The stand-in add-on tests/addons/hs_fake_addon.py keeps settings on objects and scenes (a float, an enum, a
list of items). With "Everything in this file" on, each run of its operator is one step on the global
timeline, a setting it changes on the scene is a step too, and dragging the global slider back and forward
puts every value back exactly.
"""
import importlib.util
import os

import bpy

import common as C

HERE = os.path.dirname(os.path.abspath(__file__))


class HSFAKE_PT_panel(bpy.types.Panel):
    """The add-on's own panel in the sidebar, as add-ons have."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HSFake"
    bl_label = "Fake"

    def draw(self, context):
        self.layout.prop(context.scene.hsfake, "strength")


def _fake():
    path = os.path.join(HERE, "..", "..", "tests", "addons", "hs_fake_addon.py")
    spec = importlib.util.spec_from_file_location("hs_fake_addon", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(ob, sc):
    s = ob.hsfake
    return (round(s.strength, 4), tuple(it.level for it in s.items), sc.hsfake.mode)


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    fake = _fake()
    fake.register()
    ob = C.fresh_sphere(d, segments=24, rings=12)
    sc = bpy.context.scene
    C.mod("prefs").get_prefs().record_scope = "EVERYTHING"
    with d.override():
        bpy.ops.hindsight.record_file(on=True)
    sc.hindsight.use_global = True
    yield 0.8
    # as a user runs them: from the window (a key here; a button or menu goes the same way), so Blender
    # registers them as operations. A script's bpy.ops call isn't one.
    km = bpy.context.window_manager.keyconfigs.addon.keymaps.new(name="3D View", space_type="VIEW_3D")
    km.keymap_items.new("hsfake.bump", "B", "PRESS", ctrl=True, alt=True)
    kmi = km.keymap_items.new("wm.context_set_enum", "M", "PRESS", ctrl=True, alt=True)
    kmi.properties.data_path = "scene.hsfake.mode"
    kmi.properties.value = "HARD"
    yield 0.3
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    d.ev("MOUSEMOVE", x=cx, y=cy)
    yield 0.2
    i0 = C.info(d)
    n0 = i0["len"] if i0 else 0
    states = [_read(ob, sc)]
    for k in range(3):
        d.key("B", ctrl=True, alt=True, x=cx, y=cy)
        yield 0.8
        states.append(_read(ob, sc))
    d.key("M", ctrl=True, alt=True, x=cx, y=cy)       # the add-on's setting on the scene, as a menu sets it
    yield 1.0
    states.append(_read(ob, sc))
    i = C.info(d)
    d.check("three runs of the add-on's operator and one setting: four steps on the global timeline",
            i is not None and i["len"] == n0 + 4 and len(set(states)) == 5, (n0, i, states))
    d.shot("addon_steps")
    for pos, want in ((n0 + 3, 3), (n0 + 1, 1), (n0, 0), (n0 + 2, 2), (n0 + 4, 4)):
        yield from C.drag_slider_to(d, pos)
        yield 0.5
        got = _read(ob, sc)
        d.check(f"slider to {pos}: the add-on's values of that step", C.info(d)["pos"] == pos and got == states[want],
                (pos, C.info(d)["pos"], got, states[want]))

    # ---------------------------------------------------------------- a drag on the add-on's panel slider
    # D147: no modal operator runs during a UI drag, and slow redraws (software GL) leave gaps longer than
    # the recorder's settle time between updates: it must still be one step
    bpy.utils.register_class(HSFAKE_PT_panel)
    try:
        yield from C.show_sidebar_tab(d, "HSFake")
        yield 0.5
        win, area, reg, ui, space = d.view3d()
        n1 = C.info(d)["len"]
        v0 = round(sc.hsfake.strength, 4)
        x0, y0 = ui.x + ui.width // 2, ui.y + ui.height - 40        # the first row under the panel header
        d.ev("MOUSEMOVE", x=x0, y=y0)
        yield 0.1
        d.ev("LEFTMOUSE", "PRESS", x=x0, y=y0)
        yield 0.05
        for k in range(8):
            d.ev("MOUSEMOVE", x=x0 + 6 * (k + 1), y=y0)
            yield 0.03
        d.ev("LEFTMOUSE", "RELEASE", x=x0 + 48, y=y0)
        yield 2.0
        v1 = round(sc.hsfake.strength, 4)
        i = C.info(d)
        d.check("a drag on the add-on's panel slider is one step", v1 != v0 and i["len"] == n1 + 1, (n1, i, v0, v1))
        with d.override():
            C.mod("runtime").goto(bpy.context, n1)
        yield 0.3
        d.check("going back undoes the whole drag", round(sc.hsfake.strength, 4) == v0, (sc.hsfake.strength, v0))
        with d.override():
            C.mod("runtime").goto(bpy.context, n1 + 1)
        yield 0.3
        d.check("going forward gives the end of the drag", round(sc.hsfake.strength, 4) == v1, (sc.hsfake.strength, v1))
    finally:
        bpy.utils.unregister_class(HSFAKE_PT_panel)
