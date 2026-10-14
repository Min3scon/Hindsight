"""Round D: awkward models and abuse. Nothing may crash; unsupported data must warn.

Part 1 opens test_models/awkward.blend and sculpts every object. Part 2 abuses a 10k blob:
undo during a slider drag, mode and object switches during a stroke, deleting a recorded object,
saving during a stroke, missing / damaged / newer-format history files, memory-limit eviction,
and the 2M-vertex warning.
"""
import json
import os
import shutil

import bpy

import common as C

AWKWARD = {
    # object: (expected warning substring or None, should record)
    "Multires": ("Multires", False),
    "Mod_Subsurf": ("Modifiers", True),
    "Mod_Mirror": ("Modifiers", True),
    "Mod_Displace": ("Deform modifiers", True),
    "Mod_Armature": ("Deform modifiers", True),
    "ShapeKeys": ("Shape keys", True),
    "VertexGroups": ("Vertex groups", True),
    "Materials": ("Materials", True),
    "Animated": ("animated", True),
    "Parented": (None, True),
    "RotatedScaled": (None, True),          # no warning: history is stored in object space
    "NonManifold": (None, True),
    "OneVertex": (None, None),
    "EmptyMesh": (None, None),
}


def _open(d, path):
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)


def _activate(d, name, frame=True):
    with d.override():
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        ob = bpy.data.objects[name]
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        if frame:
            bpy.ops.view3d.view_selected()
    return ob


def _warnings(d, ob):
    with d.override():
        return C.mod("runtime").warnings_for(bpy.context, ob)


def run(d):
    repo = d.spec["repo"]
    models = os.path.join(repo, "test_models")
    work = os.path.join(d.out, "work")
    os.makedirs(work, exist_ok=True)
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3

    # ============================================================ part 1: awkward objects
    _open(d, os.path.join(models, "awkward.blend"))
    yield 1.0
    yield from C.show_sidebar_tab(d)
    mark = d.console_mark()
    for name, (warn, records) in AWKWARD.items():
        ob = _activate(d, name)
        try:
            C.set_mode(d, "SCULPT")
        except Exception as e:
            d.check(f"{name}: enters Sculpt Mode or refuses cleanly", "mode" in str(e).lower(), e)
            continue
        yield 0.4
        before = (C.info(d) or {}).get("len", 0)
        yield from C.real_stroke(d, 0)
        after = (C.info(d) or {}).get("len", 0)
        ws = _warnings(d, ob)
        if warn:
            d.check(f"{name}: warns", any(warn.lower() in t.lower() for _, t in ws), ws)
        if records is True:
            d.check(f"{name}: stroke recorded", after == before + 1, (before, after))
        elif records is False:
            d.check(f"{name}: recording paused, nothing recorded", after == before, (before, after))
        if name in ("Multires", "Mod_Armature", "OneVertex"):
            d.shot(f"awkward_{name}")
    C.set_mode(d, "OBJECT")
    log = d.console_since(mark)
    d.check("awkward objects: no Python errors", "Traceback" not in log, log[-1500:])

    # Dyntopo: toggle on a blob, stroke, toggle off
    ob = _activate(d, "Mod_Subsurf")
    with d.override():
        ob.modifiers.clear()
    C.set_mode(d, "SCULPT")
    yield 0.3
    mark = d.console_mark()
    try:
        with d.override():
            bpy.ops.sculpt.dynamic_topology_toggle()
        yield 0.4
        before = C.info(d)["len"]
        yield from C.real_stroke(d, 1)
        d.check("Dyntopo: warns and pauses", any("Dyntopo" in t for _, t in _warnings(d, ob))
                and C.info(d)["len"] == before, (_warnings(d, ob), C.info(d)))
        d.shot("dyntopo_warning")
        with d.override():
            bpy.ops.sculpt.dynamic_topology_toggle()
        yield 0.4
        yield from C.real_stroke(d, 2)
        d.check("Dyntopo off again: records", C.info(d)["len"] >= before + 1, C.info(d))
    except Exception as e:
        d.check("Dyntopo toggle available", False, e)
    log = d.console_since(mark)
    d.check("Dyntopo: no Python errors", "Traceback" not in log, log[-1200:])
    C.set_mode(d, "OBJECT")

    # ============================================================ part 2: abuse on a 10k blob
    src = os.path.join(models, "size_10k.blend")
    blend = os.path.join(work, "abuse.blend")
    shutil.copyfile(src, blend)
    _open(d, blend)
    yield 1.0
    ob = _activate(d, "Blob_10k")
    C.set_mode(d, "SCULPT")
    yield 0.5
    for k in range(4):
        yield from C.real_stroke(d, k)
    d.check("abuse setup: 4 steps", C.info(d)["len"] == 4, C.info(d))
    win, area, reg, ui, space = d.view3d()
    sc = C.mod("scrubber")
    rt = C.mod("runtime")
    mark = d.console_mark()

    # undo during a slider drag
    lay = C.slider_layout(d)
    y = (lay["bar"][1] + lay["bar"][3]) / 2
    x_end = sc.x_from_pos(4, lay, 4)
    x_mid = sc.x_from_pos(2, lay, 4)
    d.ev("MOUSEMOVE", x=reg.x + x_end, y=reg.y + y)
    d.ev("LEFTMOUSE", "PRESS", x=reg.x + x_end, y=reg.y + y)
    yield 0.05
    d.ev("MOUSEMOVE", x=reg.x + x_mid, y=reg.y + y)
    yield 0.1
    d.key("Z", ctrl=True)
    yield 0.1
    d.ev("LEFTMOUSE", "RELEASE", x=reg.x + x_mid, y=reg.y + y)
    yield 0.4
    i = C.info(d)
    d.check("undo during a slider drag: no crash, history intact", i["len"] == 4 and 0 <= i["pos"] <= 4, i)
    with d.override():
        bpy.context.window_manager.hindsight_step = 4

    # Keys a user can press while holding a stroke: Tab (mode), Ctrl+Z, Ctrl+S. (A script calling
    # mode_set mid-stroke crashes Blender 5.1 even without Hindsight, see DECISIONS.md, so the test
    # only uses what a user can do.)
    pts = C.stroke_pts(reg, 1)
    x0, y0 = d.region_to_win(reg, *pts[0])

    def held_stroke(key, **mods):
        d.ev("MOUSEMOVE", x=x0, y=y0)
        d.ev("LEFTMOUSE", "PRESS", x=x0, y=y0)
        for p in pts[1:8]:
            d.ev("MOUSEMOVE", x=reg.x + p[0], y=reg.y + p[1])
            yield 0.015
        d.key(key, **mods)
        yield 0.05
        for p in pts[8:]:
            d.ev("MOUSEMOVE", x=reg.x + p[0], y=reg.y + p[1])
            yield 0.01
        d.ev("LEFTMOUSE", "RELEASE", x=reg.x + pts[-1][0], y=reg.y + pts[-1][1])
        yield 0.6

    for key, mods, label in (("TAB", {}, "Tab"), ("Z", {"ctrl": True}, "Ctrl+Z"), ("S", {"ctrl": True}, "Ctrl+S")):
        ob = _activate(d, "Blob_10k", frame=False)
        C.set_mode(d, "SCULPT")
        yield 0.4
        n0 = len(rt.history_of(ob).entries)
        yield from held_stroke(key, **mods)
        n1 = len(rt.history_of(ob).entries)
        d.check(f"{label} during a stroke: no crash, history consistent", n0 - 1 <= n1 <= n0 + 1, (n0, n1))
    C.set_mode(d, "OBJECT")
    with d.override():
        bpy.ops.wm.save_mainfile()
    yield 0.3

    # Click another object right after releasing, before the recorder's debounce commits.
    with d.override():
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(3, 0, 0))
        other = bpy.context.active_object
        other.name = "Other"
    ob = _activate(d, "Blob_10k", frame=False)
    C.set_mode(d, "SCULPT")
    yield 0.4
    n0 = len(rt.history_of(ob).entries)
    yield from d.drag(reg, pts, step_wait=0.012)
    with d.override():
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = other
    yield 0.6
    d.check("switching objects right after a stroke still records it on the right object",
            len(rt.history_of(ob).entries) == n0 + 1 and rt.history_of(other) is None,
            (n0, len(rt.history_of(ob).entries)))

    # Save during a stroke (Ctrl+S above) and reopen: the history matches what was saved.
    ob = _activate(d, "Blob_10k")
    n_saved = len(rt.history_of(ob).entries)
    with d.override():
        bpy.ops.wm.save_mainfile()
    yield 0.3
    _open(d, blend)
    yield 1.0
    ob = _activate(d, "Blob_10k")
    d.check("file reopens with its history", len(rt.history_of(ob).entries) == n_saved,
            (n_saved, len(rt.history_of(ob).entries) if rt.history_of(ob) else None))

    # delete a recorded object, then Blender undo
    d.ev("MOUSEMOVE", x=reg.x + reg.width // 2, y=reg.y + reg.height // 2)
    yield 0.1
    d.key("DEL")                                # Delete key in Object Mode, pushes an undo step
    yield 0.5
    d.check("object deleted by the Delete key", bpy.data.objects.get("Blob_10k") is None)
    d.redraw()
    d.check("deleting a recorded object: no errors", "Traceback" not in d.console_since(mark))
    d.ev("MOUSEMOVE", x=reg.x + reg.width // 2, y=reg.y + reg.height // 2)
    d.key("Z", ctrl=True)                       # Blender's own undo, as a user presses it
    yield 0.8
    ob = bpy.data.objects.get("Blob_10k")
    d.check("undo brings the object back with its history", ob is not None and rt.history_of(ob) is not None
            and len(rt.history_of(ob).entries) == n_saved, None if ob is None else rt.history_of(ob))

    # ------------------------------------------------------------ history files
    folder = blend[:-len(".blend")] + "_hindsight"
    hsr = os.path.join(folder, "history.hsr")
    good = open(hsr, "rb").read()

    # missing folder
    shutil.move(folder, folder + "_moved")
    _open(d, blend)
    yield 1.0
    _activate(d, "Blob_10k")
    yield from C.show_sidebar_tab(d)
    ws = _warnings(d, bpy.data.objects["Blob_10k"])
    d.check("missing history folder: clear message", any("History folder missing" in t for _, t in ws), ws)
    d.shot("missing_history_folder")
    shutil.move(folder + "_moved", folder)

    # damaged step inside the file
    bad = bytearray(good)
    bad[-200] ^= 0xFF
    open(hsr, "wb").write(bytes(bad))
    _open(d, blend)
    yield 1.0
    ob = _activate(d, "Blob_10k")
    ws = _warnings(d, ob)
    h = rt.history_of(ob)
    d.check("damaged step: earlier steps load, clear message", h is not None and 0 < len(h.entries) < n_saved
            and any("damaged step" in t for _, t in ws), (None if h is None else len(h.entries), ws))
    d.shot("damaged_history")

    # unreadable file
    open(hsr, "wb").write(b"\x00garbage" * 50)
    _open(d, blend)
    yield 1.0
    ob = _activate(d, "Blob_10k")
    ws = _warnings(d, ob)
    kept = [f for f in os.listdir(folder) if ".unreadable-" in f]
    d.check("unreadable file: message, file set aside, no crash", kept and any("could not be read" in t for _, t in ws),
            (kept, ws))

    # newer format: refused and never overwritten
    js_len = int.from_bytes(good[4:8], "little")
    doc = json.loads(good[8:8 + js_len])
    doc["format"] = doc["version"] = 99
    js = json.dumps(doc).encode()
    newer = good[:4] + len(js).to_bytes(4, "little") + js + good[8 + js_len:]
    open(hsr, "wb").write(newer)
    _open(d, blend)
    yield 1.0
    ob = _activate(d, "Blob_10k")
    ws = _warnings(d, ob)
    d.check("newer format: refused with a clear message", any("newer Hindsight" in t for _, t in ws), ws)
    C.set_mode(d, "SCULPT")
    yield 0.4
    yield from C.real_stroke(d, 3)
    C.set_mode(d, "OBJECT")
    with d.override():
        bpy.ops.wm.save_mainfile()
    yield 0.4
    d.check("newer format: saving leaves that history untouched", open(hsr, "rb").read() == newer)
    d.shot("newer_format")
    open(hsr, "wb").write(good)

    log = d.console_since(mark)
    tracebacks = log.count("Traceback")
    d.note("abuse_tracebacks", tracebacks)
    d.note("abuse_log_tail", log[-3000:])

    # ------------------------------------------------------------ memory limit and the 2M mesh
    prefs = bpy.context.preferences.addons[C.PKG].preferences
    prefs.memory_limit_mb = 16
    _open(d, os.path.join(models, "size_500k.blend"))
    yield 1.0
    ob = _activate(d, "Blob_500k")
    C.set_mode(d, "SCULPT")
    yield 0.5
    for k in range(6):
        with d.override():
            bpy.ops.sculpt.mesh_filter(type='INFLATE', strength=0.01)
            rt.commit(ob)
        yield 0.1
    ws = _warnings(d, ob)
    d.check("memory limit: oldest steps dropped with a warning", rt.MEMORY["evicted"] > 0 and
            any("Memory limit reached" in t for _, t in ws), (rt.MEMORY, ws))
    d.shot("memory_limit_warning")
    prefs.memory_limit_mb = 4096
    C.set_mode(d, "OBJECT")
    _open(d, os.path.join(models, "size_2m.blend"))
    yield 1.5
    ob = _activate(d, "Blob_2m")
    ws = _warnings(d, ob)
    d.check("2M vertices: large-mesh warning", any("Large mesh" in t for _, t in ws), ws)
    C.set_mode(d, "SCULPT")
    yield 0.8
    yield from C.real_stroke(d, 0)
    yield 0.5
    d.check("2M vertices: a stroke is recorded", (C.info(d) or {}).get("len") == 1, C.info(d))
    d.shot("large_mesh_2m")
