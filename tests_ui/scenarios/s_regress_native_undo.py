"""Regression (Round C, beta 2): after a Hindsight jump in Sculpt Mode, Blender's own undo
re-applied its stored stroke data on top of the jumped-to mesh. The result matched no state in
either history (a corrupted shape).

Reproduced two ways: Ctrl+Z at the start of the history (Hindsight used to fall back to Blender's
undo there), and Blender's undo directly (Hindsight's Ctrl+Z switched off, as if the user used
Edit > Undo). After the fix the mesh must always be one of the recorded states.
"""
import hashlib

import bpy

import common as C


def mesh_hash(ob):
    snap = C.mod("meshio").read_snapshot(ob.data, sculpt_only=False)
    h = hashlib.blake2b(digest_size=16)
    for k in sorted(snap.arrays):
        h.update(k.encode())
        h.update(snap.arrays[k].tobytes())
    return h.hexdigest()


def run(d):
    import os
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    # Start from a saved file (clean undo stack) and push undo steps like the UI does: operators
    # run from a script don't push them, and Blender's undo then crashes on its own.
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=os.path.join(d.spec["repo"], "test_models", "size_10k.blend"),
                                 load_ui=False)
    yield 1.0
    with d.override():
        ob = bpy.data.objects["Blob_10k"]
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        bpy.ops.view3d.view_selected()
        bpy.ops.object.mode_set('EXEC_DEFAULT', True, mode="SCULPT")
    yield 0.5
    states = [mesh_hash(ob)]
    for k in range(3):
        yield from C.real_stroke(d, k)
        states.append(mesh_hash(ob))
    d.check("3 strokes, 3 steps", C.info(d)["len"] == 3, C.info(d))
    win, area, reg, ui, space = d.view3d()
    d.ev("MOUSEMOVE", x=reg.x + reg.width // 2, y=reg.y + reg.height // 2)

    # 1. scrub to the start, then Ctrl+Z once more
    yield from C.drag_slider_to(d, 0)
    d.key("Z", ctrl=True)
    yield 0.6
    d.check("Ctrl+Z at the start after a jump leaves a recorded state", mesh_hash(ob) in states,
            C.info(d))
    d.check("...and keeps every step", C.info(d)["len"] == 3, C.info(d))

    # 2. jump to the middle, then Blender's own undo (Hindsight's Ctrl+Z switched off)
    yield from C.drag_slider_to(d, 2)
    kc = bpy.context.window_manager.keyconfigs.addon
    ours = [k for km in kc.keymaps for k in km.keymap_items if k.idname.startswith("hindsight.step")]
    for k in ours:
        k.active = False
    try:
        for _ in range(2):
            d.key("Z", ctrl=True)
            yield 0.6
            d.check("Blender undo after a jump leaves a recorded state", mesh_hash(ob) in states, C.info(d))
            d.check("...and keeps every step", C.info(d)["len"] == 3, C.info(d))
    finally:
        for k in ours:
            k.active = True
    d.shot("after_native_undo")
