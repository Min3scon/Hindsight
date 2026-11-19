"""A material's node input, the scene's render settings and a light's power changed from the window
(D112, D116, D117; recorded headless before).

Each change runs as an operator from a key (wm.context_set_*, what menus and many buttons run), so Blender
registers it like a user's: one step each on the global timeline, and the global slider puts every value
back, both ways.
"""
import bpy

import common as C

ROUGH = 'object.active_material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value'


def _read(ob, light):
    sc = bpy.context.scene
    mat = ob.active_material
    r = mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value
    return (round(r, 4), sc.render.resolution_x, round(light.data.energy, 3))


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    ob = C.fresh_sphere(d, segments=16, rings=8)
    C.frame_view(d)
    mat = bpy.data.materials.new("Clay")
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    ob.data.materials.append(mat)
    light = next(o for o in bpy.data.objects if o.type == "LIGHT")
    C.mod("prefs").get_prefs().record_scope = "EVERYTHING"
    with d.override():
        bpy.ops.hindsight.record_file(on=True)
    bpy.context.scene.hindsight.use_global = True
    km = bpy.context.window_manager.keyconfigs.addon.keymaps.new(name="3D View", space_type="VIEW_3D")
    for key, op, path, value in (("R", "wm.context_set_float", ROUGH, 0.15),
                                 ("X", "wm.context_set_int", "scene.render.resolution_x", 1280),
                                 ("P", "wm.context_set_float", 'scene.objects["Light"].data.energy', 2500.0)):
        kmi = km.keymap_items.new(op, key, "PRESS", ctrl=True, alt=True)
        kmi.properties.data_path = path
        kmi.properties.value = value
    yield 0.6
    n0 = (C.info(d) or {}).get("len", 0)
    states = [_read(ob, light)]
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    d.ev("MOUSEMOVE", x=cx, y=cy)
    yield 0.2
    for key in ("R", "X", "P"):
        d.key(key, ctrl=True, alt=True, x=cx, y=cy)
        yield 1.0
        states.append(_read(ob, light))
    i = C.info(d)
    d.check("a material input, the render resolution and a light's power: three steps",
            i is not None and i["len"] == n0 + 3 and len(set(states)) == 4, (n0, i, states))
    for k in (2, 0, 1, 3):
        yield from C.drag_slider_to(d, n0 + k)
        yield 0.6
        d.check(f"slider to {n0 + k}: the values of that step", C.info(d)["pos"] == n0 + k
                and _read(ob, light) == states[k], (k, C.info(d), _read(ob, light), states[k]))
