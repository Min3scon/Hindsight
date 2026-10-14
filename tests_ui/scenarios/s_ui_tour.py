"""Step 3 gate: the UI a user sees, in every mode, from a fresh install. Screenshots plus checks.

Run with --fresh --no-install: the zip is installed through Blender's own Install from Disk.
"""
import bpy

import common as C


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.4

    # ---------------------------------------------------------------- install like a user
    mark = d.console_mark()
    r = C.install_like_user(d)
    yield 1.5
    log = d.console_since(mark)
    d.check("installed and enabled", C.enabled() and r == {'FINISHED'}, r)
    d.check("no traceback on install", "Traceback" not in log, log[-1500:])
    d.check("first-run notice logged", "Hindsight is on." in d.console_since(mark), "")
    d.shot("A_first_run_status_bar")

    # ---------------------------------------------------------------- object mode, no history
    ob = C.fresh_sphere(d)
    C.frame_view(d)
    yield from C.show_sidebar_tab(d)
    win, area, reg, ui, space = d.view3d()
    d.check("sidebar shows the Undo History tab", ui.active_panel_category == "Undo History",
            ui.active_panel_category)
    yield 0.4
    d.shot("B_object_mode_no_history")

    # ---------------------------------------------------------------- sculpt mode, before a stroke
    C.set_mode(d, "SCULPT")
    yield 0.6
    d.shot("C_sculpt_before_stroke")
    with d.override():
        si = C.mod("scrubber").slider_info(bpy.context)
    d.check("on-screen slider shows an empty track before the first stroke",
            si is not None and si.get("empty"), si)
    lay = C.slider_layout(d)
    shelf = [r for r in area.regions if r.type in ("ASSET_SHELF", "ASSET_SHELF_HEADER") and r.height > 1]
    if shelf:
        top = max(r.y + r.height for r in shelf) - reg.y
        d.check("slider sits above the asset shelf", lay["bar"][1] >= top, f"bar y0={lay['bar'][1]} shelf top={top}")
    tools = [r for r in area.regions if r.type == "TOOLS" and r.width > 1]
    if tools:
        d.check("slider clears the toolbar", lay["dot"][0] - lay["dot"][2] > tools[0].width,
                f"dot={lay['dot']} toolbar w={tools[0].width}")

    # ---------------------------------------------------------------- three real strokes
    for k in range(3):
        yield from C.real_stroke(d, k)
    i = C.info(d)
    d.check("three strokes, three steps", i is not None and i["len"] == 3 and i["pos"] == 3, i)
    d.shot("D_sculpt_three_steps")

    # ---------------------------------------------------------------- drag the on-screen slider
    yield from C.drag_slider_to(d, 1)
    i = C.info(d)
    d.check("dragging the on-screen slider moves to step 1 without a new stroke",
            i is not None and i["pos"] == 1 and i["len"] == 3, i)
    d.shot("E_sculpt_slider_dragged_to_1")

    # ---------------------------------------------------------------- the sidebar slider property
    wm = bpy.context.window_manager
    yield 0.4
    d.check("sidebar slider range follows the history", wm.bl_rna.properties["hindsight_step"].hard_max == 3,
            wm.bl_rna.properties["hindsight_step"].hard_max)
    with d.override():
        wm.hindsight_step = 2
    yield 0.3
    i = C.info(d)
    d.check("setting the sidebar slider jumps to step 2", i["pos"] == 2, i)
    with d.override():
        bpy.ops.hindsight.step_forward(native_fallback=False)
    d.check("forward button reaches step 3", C.info(d)["pos"] == 3, C.info(d))
    with d.override():
        r = bpy.ops.hindsight.step_forward(native_fallback=False)
    d.check("forward at the end does nothing", r == {'CANCELLED'} and C.info(d)["pos"] == 3, r)

    # ---------------------------------------------------------------- Ctrl+Z / Ctrl+Shift+Z in Sculpt Mode
    d.ev("MOUSEMOVE", x=reg.x + reg.width // 2, y=reg.y + reg.height // 2)
    d.key("Z", ctrl=True)
    yield 0.4
    d.check("Ctrl+Z steps the history back", C.info(d)["pos"] == 2 and C.info(d)["len"] == 3, C.info(d))
    d.key("Z", ctrl=True, shift=True)
    yield 0.4
    d.check("Ctrl+Shift+Z steps forward", C.info(d)["pos"] == 3, C.info(d))

    # ---------------------------------------------------------------- header button and popover
    funcs = [f.__name__ for f in bpy.types.VIEW3D_MT_editor_menus._dyn_ui_initialize()]
    d.check("header button installed", "draw_header_button" in funcs, funcs[-5:])
    d.shot("F_header_area")
    d.shot("F2_header_closeup", area=area)
    x, y = reg.x + reg.width // 2, reg.y + reg.height - 60
    d.ev("MOUSEMOVE", x=x, y=y)
    yield 0.1
    with d.override():
        bpy.ops.wm.call_panel(name="VIEW3D_PT_hindsight_popover", keep_open=True)
    yield 0.6
    d.shot("G_popover")
    d.key("ESC")
    yield 0.3

    # ---------------------------------------------------------------- History Menu from the dot
    lay = C.slider_layout(d)
    cx, cy, rr = lay["dot"]
    yield from d.click(reg, cx, cy)
    yield 0.5
    d.shot("H_history_menu_from_dot")
    d.key("ESC")
    yield 0.3
    d.check("clicking the dot did not add a step", C.info(d)["len"] == 3, C.info(d))

    # ---------------------------------------------------------------- F3 search
    d.ev("MOUSEMOVE", x=reg.x + reg.width // 2, y=reg.y + reg.height // 2)
    d.key("F3")
    yield 0.5
    C.type_text(d, "hindsight")
    yield 0.6
    d.shot("I_f3_search_hindsight")
    d.key("ESC")
    yield 0.3

    # ---------------------------------------------------------------- vertex paint and edit mode
    C.set_mode(d, "VERTEX_PAINT")
    yield 0.6
    d.shot("J_vertex_paint")
    C.set_mode(d, "EDIT")
    yield 0.6
    d.shot("K_edit_mode")
    C.set_mode(d, "OBJECT")
    yield 0.5
    d.shot("L_object_mode_with_history")

    # ---------------------------------------------------------------- self-check and diagnostics
    with d.override():
        bpy.ops.hindsight.self_check('INVOKE_DEFAULT')
    yield 0.6
    d.shot("M_self_check_popup")
    res = C.mod("selfcheck").LAST["results"]
    d.note("self_check", res)
    d.check("self-check passes in the live session", all(s is not False for s, _, _ in res), res)
    d.key("ESC")
    yield 0.2
    with d.override():
        bpy.ops.hindsight.copy_diagnostics()
    clip = wm.clipboard
    d.note("diagnostics", clip)
    import os
    home = os.path.expanduser("~")
    d.check("diagnostics has versions", "Hindsight " in clip and "Blender " in clip and "GPU" in clip, clip[:300])
    d.check("diagnostics has no personal paths", home not in clip and home.replace("\\", "/") not in clip
            and os.environ.get("USERNAME", "@@") not in clip, "")

    # ---------------------------------------------------------------- preferences page
    old_type = area.type
    area.type = 'PREFERENCES'
    bpy.context.preferences.active_section = 'ADDONS'
    try:
        wm.addon_search = "Hindsight"
    except Exception:
        pass
    yield 0.4
    yield 0.3
    # click the disclosure arrow of the (only) listed add-on, like a user
    ax, ay = area.x + 183, area.y + area.height - 79
    d.ev("MOUSEMOVE", x=ax, y=ay)
    d.ev("LEFTMOUSE", "PRESS", x=ax, y=ay)
    d.ev("LEFTMOUSE", "RELEASE", x=ax, y=ay)
    yield 0.8
    d.shot("N_preferences", area=area)
    area.type = old_type
    yield 0.3
