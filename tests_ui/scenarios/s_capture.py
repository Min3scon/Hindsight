"""Capture the website's media in real Blender, from generated models only (never the owner's files).

Builds a sculpt with real simulated strokes on a subdivided Suzanne, then writes PNG frames and stills
to args["out"]:
  hero/           the slider scrubbing the whole history back and forward (3D Viewport area)
  scrub/          two objects, each scrubbed on its own (feature 1)
  morph/          the Morph History brush painting an earlier shape back (feature 2)
  movie/          Movie Mode playing the history on the timeline (feature 3, full window)
  stills/*.png    tab, slider, menu, morph tool, movie panel, filtering, preferences, where-to-find-it
tools/encode_media.py turns these into WebM/MP4/WebP for the site.
"""
import math
import os

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

import common as C

ESS = "brushes/essentials_brushes-mesh_sculpt.blend/Brush/"


def out_dir(d, *parts):
    p = os.path.join(d.args["out"], *parts)
    os.makedirs(p, exist_ok=True)
    return p


def area_shot(d, area, path):
    region = next(r for r in area.regions if r.type == "WINDOW")
    win = d.window
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot_area(filepath=path, check_existing=False)


def win_shot(d, path):
    win = d.window
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=path, check_existing=False)


def brush(d, name, size=None, strength=None):
    try:
        with d.override():
            bpy.ops.brush.asset_activate(asset_library_type='ESSENTIALS', relative_asset_identifier=ESS + name)
    except Exception as e:
        d.log("brush", name, "not available:", e)
    ups = bpy.context.tool_settings.unified_paint_settings
    b = bpy.context.tool_settings.sculpt.brush
    if size is not None:
        if ups.use_unified_size:
            ups.size = size
        elif b is not None:
            b.size = size
    if strength is not None and b is not None:
        b.strength = strength


def to_screen(d, ob, local):
    win, area, reg, ui, space = d.view3d()
    p = view3d_utils.location_3d_to_region_2d(reg, space.region_3d, ob.matrix_world @ Vector(local))
    return (p.x, p.y) if p is not None else (reg.width / 2, reg.height / 2)


def path_between(a, b, n=14, bend=0.0):
    out = []
    for i in range(n):
        t = i / (n - 1)
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t + math.sin(t * math.pi) * bend
        out.append((x, y))
    return out


def stroke(d, pts, wait=0.012):
    win, area, reg, ui, space = d.view3d()
    yield from d.drag(reg, pts, step_wait=wait)
    yield 0.25


def setup_look(d, sidebar=False, toolbar=False, shelf=False):
    win, area, reg, ui, space = d.view3d()
    sh = space.shading
    sh.type = 'SOLID'
    sh.light = 'MATCAP'
    try:
        sh.studio_light = 'clay_studio.exr'
    except Exception:
        pass
    sh.show_cavity = True
    sh.cavity_type = 'WORLD'
    sh.background_type = 'VIEWPORT'
    sh.background_color = (0.043, 0.043, 0.05)
    ov = space.overlay
    for attr in ("show_floor", "show_axis_x", "show_axis_y", "show_axis_z", "show_cursor", "show_text",
                 "show_stats", "show_object_origins", "show_outline_selected", "show_relationship_lines"):
        if hasattr(ov, attr):
            setattr(ov, attr, False)
    space.show_gizmo_navigate = False
    space.show_region_toolbar = toolbar
    space.show_region_ui = sidebar
    if hasattr(space, "show_region_asset_shelf"):
        space.show_region_asset_shelf = shelf
    if hasattr(space, "show_region_tool_header"):
        space.show_region_tool_header = False


def make_suzanne(d, name, x):
    with d.override():
        bpy.ops.mesh.primitive_monkey_add(location=(x, 0, 0))
        ob = bpy.context.active_object
        ob.name = name
        m = ob.modifiers.new("Subdivision", 'SUBSURF')
        m.levels = 3
        bpy.ops.object.modifier_apply(modifier=m.name)
        bpy.ops.object.shade_smooth()
    return ob


def frame_objects(d, obs, orbit=(0.42, 0.18), zoom=0.0):
    with d.override():
        for o in bpy.context.view_layer.objects:
            o.select_set(o in obs)
        bpy.context.view_layer.objects.active = obs[0]
        bpy.ops.view3d.view_axis(type='FRONT')
        bpy.ops.view3d.view_orbit(angle=orbit[0], type='ORBITRIGHT')
        bpy.ops.view3d.view_orbit(angle=orbit[1], type='ORBITUP')
        bpy.ops.view3d.view_selected()
    win, area, reg, ui, space = d.view3d()
    space.region_3d.view_distance *= (1.0 + zoom)
    space.lens = 70


def sculpt_monkey(d, ob, horns=True, k=1.0):
    """About 30 real strokes: horns, a brow, cheeks, a bigger nose, a smoothed chin."""
    P = lambda loc: to_screen(d, ob, loc)
    if horns:
        brush(d, "Snake Hook", size=45, strength=1.0)
        for side in (-1, 1):
            for j in range(4):
                a = P((side * 0.32, -0.15, 0.72 - 0.02 * j))
                b = P((side * (0.55 + 0.05 * j), -0.1, 1.05 + 0.08 * j))
                yield from stroke(d, path_between(a, b, 12, -10 * side))
    brush(d, "Clay Strips", size=38, strength=0.6)
    for j in range(5):
        a, b = P((-0.55, -0.72, 0.38 + 0.015 * j)), P((0.55, -0.72, 0.38 + 0.015 * j))
        yield from stroke(d, path_between(a, b, 18, -8))
    brush(d, "Inflate/Deflate", size=55, strength=0.5)
    for side in (-1, 1):
        for j in range(3):
            a, b = P((side * 0.48, -0.72, -0.12)), P((side * 0.62, -0.62, -0.25))
            yield from stroke(d, path_between(a, b, 10, 4))
    brush(d, "Draw", size=34, strength=0.7)
    for j in range(4):
        a, b = P((0.0, -0.88, 0.15)), P((0.0, -0.95, -0.12))
        yield from stroke(d, path_between(a, b, 10, 0))
    brush(d, "Smooth", size=60, strength=0.5)
    a, b = P((-0.4, -0.75, -0.62)), P((0.4, -0.75, -0.62))
    yield from stroke(d, path_between(a, b, 16, 6))


def run(d):
    rt_steps = lambda: (C.info(d) or {}).get("len", 0)
    yield 1.0
    C.dismiss_splash(d)
    yield 0.4
    with d.override():
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)
    left = make_suzanne(d, "Suzanne", 0.0)
    setup_look(d)
    frame_objects(d, [left], zoom=0.15)
    C.set_mode(d, "SCULPT")
    yield 0.6
    yield from C.warm_up(d)
    yield from sculpt_monkey(d, left)
    n = rt_steps()
    d.check("capture: the sculpt recorded at least 25 steps", n >= 25, n)
    d.note("steps", n)
    win, area, reg, ui, space = d.view3d()

    # ------------------------------------------------------------ hero: scrub all the way back and forward
    hero = out_dir(d, "hero")
    rt = C.mod("runtime")
    fps, half = 24, 2.6
    frames = []
    ease = lambda t: 0.5 - 0.5 * math.cos(math.pi * t)
    for i in range(int(fps * 0.6)):
        frames.append(n)
    for i in range(int(fps * half)):
        frames.append(round(n * (1 - ease(i / (fps * half - 1)))))
    for i in range(int(fps * 0.6)):
        frames.append(0)
    for i in range(int(fps * half)):
        frames.append(round(n * ease(i / (fps * half - 1))))
    for k, pos in enumerate(frames):
        with d.override():
            rt.goto(bpy.context, pos, final=False)
        area_shot(d, area, os.path.join(hero, f"f{k:04d}.png"))
        if k % 12 == 0:
            yield 0.01
    with d.override():
        rt.goto(bpy.context, n)
    yield 0.3

    # ------------------------------------------------------------ stills: slider, tab, menu
    stills = out_dir(d, "stills")
    area_shot(d, area, os.path.join(stills, "viewport_slider.png"))
    with d.override():
        rt.goto(bpy.context, int(n * 0.6))
    space.show_region_ui = True
    yield from C.show_sidebar_tab(d)
    yield 0.4
    area_shot(d, area, os.path.join(stills, "tab_and_slider.png"))
    lay = C.slider_layout(d)
    cx, cy, rr = lay["dot"]
    yield from d.click(reg, cx, cy)
    yield 0.6
    win_shot(d, os.path.join(stills, "history_menu_window.png"))
    d.note("dot_in_window", [reg.x + cx, reg.y + cy])
    d.key("ESC")
    yield 0.3
    with d.override():
        rt.goto(bpy.context, n)
    space.show_region_ui = False
    yield 0.3

    # ------------------------------------------------------------ morph: paint the horn back
    morph = out_dir(d, "morph")
    pre_horns = 0
    with d.override():
        rt.goto(bpy.context, pre_horns)
        bpy.ops.hindsight.set_morph_target()
        rt.goto(bpy.context, n)
    yield 0.3
    try:
        with d.override():
            bpy.ops.wm.tool_set_by_id(name="hindsight.morph_history", space_type='VIEW_3D')
    except Exception as e:
        d.log("morph tool", e)
    ups = bpy.context.tool_settings.unified_paint_settings
    try:
        ups.size = 70
    except Exception:
        pass
    space.show_region_toolbar = True
    yield 0.4
    area_shot(d, area, os.path.join(stills, "morph_tool.png"))
    P = lambda loc: to_screen(d, left, loc)
    horn_path = path_between(P((-0.5, -0.1, 0.8)), P((-0.85, -0.1, 1.35)), 26, 0)
    k = 0
    for rep in range(3):
        x, y = d.region_to_win(reg, *horn_path[0])
        d.ev("MOUSEMOVE", x=x, y=y)
        d.ev("LEFTMOUSE", "PRESS", x=x, y=y)
        for p in horn_path[1:] + list(reversed(horn_path[:-1])):
            x, y = d.region_to_win(reg, *p)
            d.ev("MOUSEMOVE", x=x, y=y)
            yield 0.02
            area_shot(d, area, os.path.join(morph, f"f{k:04d}.png"))
            k += 1
        d.ev("LEFTMOUSE", "RELEASE", x=x, y=y)
        yield 0.3
    for j in range(12):                       # hold on the result
        area_shot(d, area, os.path.join(morph, f"f{k:04d}.png"))
        k += 1
    d.check("capture: the morph strokes recorded steps", rt_steps() > n, rt_steps())
    space.show_region_toolbar = False
    with d.override():
        bpy.ops.wm.tool_set_by_id(name="builtin.brush", space_type='VIEW_3D')

    # ------------------------------------------------------------ scrub: two objects, one history each
    C.set_mode(d, "OBJECT")
    right = make_suzanne(d, "Suzanne.001", 2.9)
    frame_objects(d, [left, right], orbit=(0.25, 0.15), zoom=0.05)
    with d.override():
        bpy.context.view_layer.objects.active = right
        for o in bpy.context.view_layer.objects:
            o.select_set(o == right)
    C.set_mode(d, "SCULPT")
    yield 0.5
    yield from sculpt_monkey(d, right, horns=False)
    scrub = out_dir(d, "scrub")
    k = 0
    for ob in (right, left):
        C.set_mode(d, "OBJECT")
        with d.override():
            bpy.context.view_layer.objects.active = ob
            for o in bpy.context.view_layer.objects:
                o.select_set(o == ob)
        C.set_mode(d, "SCULPT")
        yield 0.3
        m = rt_steps()
        seq = [m] * 8 + [round(m * (1 - (0.5 - 0.5 * math.cos(math.pi * i / 47)))) for i in range(48)] + [0] * 8 + \
              [round(m * (0.5 - 0.5 * math.cos(math.pi * i / 47))) for i in range(48)]
        for pos in seq:
            with d.override():
                rt.goto(bpy.context, pos, final=False)
            area_shot(d, area, os.path.join(scrub, f"f{k:04d}.png"))
            k += 1
            if k % 12 == 0:
                yield 0.01
        with d.override():
            rt.goto(bpy.context, m)
    yield 0.2

    # ------------------------------------------------------------ stills: movie panel, filtering, prefs
    C.set_mode(d, "OBJECT")
    sh = bpy.context.scene.hindsight
    sh.use_global = True
    space.show_region_ui = True
    yield from C.show_sidebar_tab(d)
    # The sub-panels start collapsed. For this still, swap two of them for copies that start open.
    up = C.mod("ui_panel")
    swapped = []
    for name in ("VIEW3D_PT_hindsight_movie", "VIEW3D_PT_hindsight_filter"):
        cls = getattr(up, name)
        copy = type(name + "_open", (cls,), {"bl_idname": name + "_open", "bl_options": set()})
        bpy.utils.unregister_class(cls)
        bpy.utils.register_class(copy)
        swapped.append((cls, copy))
    sh.movie_mode = True
    with d.override():
        try:
            bpy.ops.hindsight.quick_auto_key()
        except Exception as e:
            d.log("quick auto key", e)
    coll = bpy.data.collections.new("Studies")
    bpy.context.scene.collection.children.link(coll)
    sh.filter_mode = 'BLACKLIST'
    sh.filter_collections.add().collection = coll
    yield 0.4
    win_shot(d, os.path.join(stills, "panels_window.png"))
    d.note("ui_region", [ui.x, ui.y, ui.width, ui.height])
    for cls, copy in swapped:
        bpy.utils.unregister_class(copy)
        bpy.utils.register_class(cls)

    # ------------------------------------------------------------ movie: play the global history on the timeline
    movie = out_dir(d, "movie")
    scene = bpy.context.scene
    d.note("frame_range", [scene.frame_start, scene.frame_end])
    space.show_region_ui = False
    yield 0.2
    end = scene.frame_end
    k = 0
    step = max(1, (end - scene.frame_start) // 140)
    for f in range(scene.frame_start, end + 1, step):
        scene.frame_set(f)
        win_shot(d, os.path.join(movie, f"f{k:04d}.png"))
        k += 1
        if k % 10 == 0:
            yield 0.01
    d.note("movie_frames", k)
    sh.movie_mode = False
    sh.use_global = False

    # ------------------------------------------------------------ where to find it (sidebar open, slider visible)
    C.set_mode(d, "SCULPT")
    space.show_region_ui = True
    space.show_region_toolbar = True
    yield from C.show_sidebar_tab(d)
    yield 0.4
    win_shot(d, os.path.join(stills, "where_window.png"))
    d.note("view3d_area", [area.x, area.y, area.width, area.height])
    d.note("window_size", [d.window.width, d.window.height])
    lay = C.slider_layout(d)
    d.note("slider_bar_window", [reg.x + lay["bar"][0], reg.y + lay["bar"][1], reg.x + lay["bar"][2], reg.y + lay["bar"][3]])
    d.note("ui_region_window", [ui.x, ui.y, ui.width, ui.height])
    d.note("header_region", [[r.type, r.x, r.y, r.width, r.height] for r in area.regions if r.type == "HEADER"])

    # ------------------------------------------------------------ preferences page
    old = area.type
    area.type = 'PREFERENCES'
    bpy.context.preferences.active_section = 'ADDONS'
    bpy.context.window_manager.addon_search = "Hindsight"
    yield 0.4
    ax, ay = area.x + 183, area.y + area.height - 79
    d.ev("MOUSEMOVE", x=ax, y=ay)
    d.ev("LEFTMOUSE", "PRESS", x=ax, y=ay)
    d.ev("LEFTMOUSE", "RELEASE", x=ax, y=ay)
    yield 0.8
    area_shot(d, area, os.path.join(stills, "preferences.png"))
    area.type = old
