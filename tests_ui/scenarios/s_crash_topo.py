"""Crash recovery in a real window after a big topology change (R079): its journal record is written by the helper.

As s_crash, with a real Voxel Remesh (to about 200k vertices: a step of far more than 4 MB, so its record is packed
and written on the helper thread) among the unsaved steps before Blender is killed.

phase=make: three real strokes, File > Save, four more strokes (in the crash journal only), then Blender
is killed (SIGKILL: no handler, no save, no quit). The mesh after each stroke goes to <dir>/expected.json.
phase=check: Crash Recovery set to Interrupted, the file opened again: the history has the seven steps,
it stands at the saved one with the file's mesh, and the slider puts back the mesh after every stroke,
the four recovered ones included. A second open with Last saved (the default) has the three saved steps
and an empty journal (R067).

args: phase=make|check  dir=<folder for the file and the expected states>
"""
import hashlib
import importlib
import json
import os

import bpy

import common as C


def _mesh_hash(ob):
    me = ob.data
    if ob.mode == "SCULPT":
        bpy.ops.ed.flush_edits()
    co = [0.0] * (3 * len(me.vertices))
    me.vertices.foreach_get("co", co)
    return hashlib.sha1(repr([round(x, 5) for x in co]).encode()).hexdigest()[:16]


def _state(d):
    with d.override():
        return _mesh_hash(bpy.context.active_object)


def _core(name):
    return importlib.import_module(f"{C.PKG}.core.{name}")


def _set_recovery(value):
    C.mod("prefs").get_prefs().crash_recovery = value      # its update sets SETTINGS too
    _core("types").SETTINGS.crash_recovery = value


def make(d, folder):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "crash.blend")
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    C.fresh_sphere(d, segments=48, rings=24)
    C.frame_view(d)
    _set_recovery("INTERRUPTED")
    C.set_mode(d, "SCULPT")
    yield 0.6
    states = [_state(d)]
    for k in range(3):
        yield from C.real_stroke(d, k)
        states.append(_state(d))
    with d.override():
        bpy.ops.wm.save_as_mainfile(filepath=path)
    yield 0.5
    yield from C.real_stroke(d, 3)
    states.append(_state(d))
    with d.override():
        ob = bpy.context.active_object
        ob.data.remesh_voxel_size = 0.012
        bpy.ops.object.voxel_remesh()
    yield 1.5
    states.append(_state(d))
    for k in (4, 5):
        yield from C.real_stroke(d, k)
        states.append(_state(d))
    i = C.info(d)
    with d.override():
        nv = len(bpy.context.active_object.data.vertices)
    ok = d.check("three saved strokes, a stroke, a Voxel Remesh (big), two strokes: seven steps",
                 i is not None and i["len"] == 7 and len(set(states)) == 8 and nv > 100_000, (i, nv, states))
    with open(os.path.join(folder, "expected.json"), "w", encoding="utf-8") as fh:
        json.dump({"states": states, "saved_at": 3, "file": path, "made_ok": ok}, fh)
    yield 0.3
    d.crash_now()


def check(d, folder):
    exp = json.load(open(os.path.join(folder, "expected.json"), encoding="utf-8"))
    states, saved = exp["states"], exp["saved_at"]
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    _set_recovery("INTERRUPTED")
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=exp["file"])
    yield 1.5
    with d.override():
        ob = bpy.data.objects.get("Blob") or bpy.context.active_object
        bpy.context.view_layer.objects.active = ob
    C.frame_view(d)
    C.set_mode(d, "SCULPT")
    yield 0.6
    i = C.info(d)
    d.check("after the crash: seven steps, at the saved one, with the file's mesh",
            i is not None and i["len"] == 7 and i["pos"] == saved and _state(d) == states[saved], (i, _state(d), states[saved]))
    for pos in (7, 0, 5, saved, 1, 7):
        yield from C.drag_slider_to(d, pos)
        yield 0.5
        got = _state(d)
        d.check(f"slider to {pos}: the mesh after {pos} strokes", C.info(d)["pos"] == pos and got == states[pos],
                (pos, C.info(d), got, states[pos]))
    # Last saved (the default): the saved steps only, and the journal starts again (R067)
    C.set_mode(d, "OBJECT")
    yield 0.3
    _set_recovery("LAST_SAVED")
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=exp["file"])
    yield 1.5
    with d.override():
        ob = bpy.data.objects.get("Blob") or bpy.context.active_object
        bpy.context.view_layer.objects.active = ob
    C.set_mode(d, "SCULPT")
    yield 0.6
    i = C.info(d)
    pers = C.mod("persistence")
    jr = _core("journal").Journal(pers.journal_path(exp["file"]))
    recs = jr.read()
    jr.close()
    d.check("Last saved: the three saved steps, the file's mesh, the journal started again",
            i is not None and i["len"] == saved and i["pos"] == saved and _state(d) == states[saved]
            and recs is not None and not [r for r in recs if r[0] == "STEP"], (i, None if recs is None else [r[0] for r in recs]))


def run(d):
    folder = os.path.abspath(d.args.get("dir") or os.path.join(d.out, "files"))
    if d.args.get("phase") == "check":
        yield from check(d, folder)
    else:
        yield from make(d, folder)
