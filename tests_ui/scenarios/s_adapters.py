"""Step 3B in the real interface: recording other data than sculpting, one step per operation,
playback, and Blender's own undo.

Record set to Everything, "Record everything on this object" switched on. Then real input: G with a
mouse drag and a click, R with a typed angle, S with a typed factor, a value typed into the sidebar's
Location field, and a sculpt stroke in between. Each must be exactly one step. Jumps with the sidebar
slider must restore exact values; Ctrl+Z / Ctrl+Shift+Z in Object Mode must undo and redo the jump
and the last step with the cursor following. The same again with Global Undo off.
args: none
"""
import os
import struct

import bpy

import common as C

F = struct.Struct("<f")


def xf(ob):
    return tuple(F.pack(v) for v in (*ob.location, *ob.rotation_euler, *ob.scale))


def run(d):
    rt = C.mod("runtime")
    datarec = C.mod("datarec")
    mark = d.console_mark()
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=os.path.join(d.spec["repo"], "test_models", "size_10k.blend"),
                                 load_ui=False)
    yield 1.0
    prefs = C.mod("prefs").get_prefs()
    d.check("preferences reachable", prefs is not None)
    prefs.record_scope = "EVERYTHING"
    with d.override():
        ob = bpy.data.objects["Blob_10k"]
        for o in bpy.context.view_layer.objects:
            o.select_set(o == ob)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.view3d.view_selected()
        bpy.ops.hindsight.record_everything(on=True)
    yield 0.5
    d.check("Record everything is on", datarec.is_tracked(ob))
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    yield from C.warm_up(d)
    states = [xf(ob)]

    def n_steps():
        h = rt.history_of(bpy.data.objects["Blob_10k"])
        return 0 if h is None else len(h.entries)

    def after(what, want_len):
        yield 0.6
        states.append(xf(ob))
        d.check(f"{what}: one step", n_steps() == want_len, (n_steps(), want_len))

    # G, drag, click
    d.ev("MOUSEMOVE", x=cx, y=cy)
    d.key("G", x=cx, y=cy)
    yield 0.2
    for i in range(1, 12):
        d.ev("MOUSEMOVE", x=cx + i * 9, y=cy + i * 3)
        yield 0.03
    d.ev("LEFTMOUSE", "PRESS", x=cx + 99, y=cy + 33)
    d.ev("LEFTMOUSE", "RELEASE", x=cx + 99, y=cy + 33)
    yield from after("move with G", 1)
    # R 45 Enter
    d.key("R", x=cx, y=cy)
    yield 0.2
    C.type_text(d, "45")
    d.key("RET")
    yield from after("rotate with R 45", 2)
    # S 1.5 Enter
    d.key("S", x=cx, y=cy)
    yield 0.2
    C.type_text(d, "1.5")
    d.key("RET")
    yield from after("scale with S 1.5", 3)
    # a value typed into a field: Blender edits the property directly (settle boundary)
    with d.override():
        ob.location.z = 0.75
    yield from after("location typed in", 4)
    # a sculpt stroke in between is its own step
    with d.override():
        bpy.ops.object.mode_set(mode="SCULPT")
    yield 0.5
    yield from C.real_stroke(d, 0)
    with d.override():
        bpy.ops.object.mode_set(mode="OBJECT")
    yield from after("sculpt stroke", 5)
    h = rt.history_of(ob)
    d.note("labels", [e.meta.get("label") for e in h.entries])
    d.note("kinds", [e.kind for e in h.entries])

    # jumps with the sidebar slider are exact and record nothing
    wm = bpy.context.window_manager
    C.mod("slider_prop").sync()
    bad = []
    for pos in (0, 3, 1, 5, 2, 4, 0, 5):
        with d.override():
            wm.hindsight_step = pos
        yield 0.15
        if xf(ob) != states[pos]:
            bad.append(pos)
    d.check("every jump restores the exact transform", not bad, bad)
    d.check("jumps record nothing", n_steps() == 5, n_steps())

    # Blender's undo after a jump undoes the jump; redo does it again
    with d.override():
        wm.hindsight_step = 2
    yield 0.4
    d.ev("MOUSEMOVE", x=cx, y=cy)
    d.key("Z", ctrl=True, x=cx, y=cy)
    yield 0.6
    d.check("Ctrl+Z after a jump goes back to before the jump", h.cursor == 5 and xf(bpy.data.objects["Blob_10k"]) == states[5],
            (h.cursor,))
    d.key("Z", ctrl=True, shift=True, x=cx, y=cy)
    yield 0.6
    d.check("Ctrl+Shift+Z redoes the jump", h.cursor == 2 and xf(bpy.data.objects["Blob_10k"]) == states[2], (h.cursor,))
    d.check("undo and redo recorded nothing", n_steps() == 5, n_steps())

    # Global Undo off: recording and jumps still work
    bpy.context.preferences.edit.use_global_undo = False
    with d.override():
        wm.hindsight_step = 5
    yield 0.3
    d.key("G", x=cx, y=cy)
    yield 0.2
    for i in range(1, 8):
        d.ev("MOUSEMOVE", x=cx - i * 9, y=cy)
        yield 0.03
    d.ev("LEFTMOUSE", "PRESS", x=cx - 63, y=cy)
    d.ev("LEFTMOUSE", "RELEASE", x=cx - 63, y=cy)
    yield 0.6
    d.check("Global Undo off: a move is still one step", n_steps() == 6, n_steps())
    states.append(xf(bpy.data.objects["Blob_10k"]))
    with d.override():
        wm.hindsight_step = 1
    yield 0.3
    ok1 = xf(bpy.data.objects["Blob_10k"]) == states[1]
    with d.override():
        wm.hindsight_step = 6
    yield 0.3
    d.check("Global Undo off: jumps are exact", ok1 and xf(bpy.data.objects["Blob_10k"]) == states[6])
    bpy.context.preferences.edit.use_global_undo = True
    log = d.console_since(mark)
    d.check("no Python errors", "Traceback" not in log, log[-1200:])
