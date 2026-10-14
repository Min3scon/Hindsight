"""Tier 1 in the real interface: object operations by keyboard and menus, each one step, exact
playback, Edit Mode step by step with Blender's undo, and the global slider across adding and
deleting objects.

args: none (uses test_models/size_10k.blend)
"""
import hashlib
import os
import struct

import bpy

import common as C

F = struct.Struct("<f")


def fb(v):
    return tuple(F.pack(x) for x in v)


def obj_state(ob):
    return (ob.name, ob.parent.name if ob.parent else None, tuple(fb(r) for r in ob.matrix_basis),
            tuple(sorted(c.name for c in ob.users_collection)), ob.hide_get(),
            tuple((c.type, c.name, round(c.influence, 6)) for c in ob.constraints))


def mesh_hash(ob):
    snap = C.mod("runtime").read_mesh_snapshot(ob)
    h = hashlib.blake2b(digest_size=12)
    for k in sorted(snap.arrays):
        h.update(k.encode())
        h.update(snap.arrays[k].tobytes())
    return h.hexdigest()


def scene_names():
    return tuple(sorted(o.name for o in bpy.context.scene.objects))


def run(d):
    rt = C.mod("runtime")
    datarec = C.mod("datarec")
    idmap = C.mod("idmap")
    mark = d.console_mark()
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    with d.override():
        bpy.ops.wm.open_mainfile(filepath=os.path.join(d.spec["repo"], "test_models", "size_10k.blend"),
                                 load_ui=False)
    yield 1.0
    prefs = C.mod("prefs").get_prefs()
    prefs.record_scope = "EVERYTHING"
    with d.override():
        ob = bpy.data.objects["Blob_10k"]
        for o in bpy.context.view_layer.objects:
            o.select_set(o == ob)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.view3d.view_selected()
        bpy.ops.hindsight.record_everything(on=True)
        bpy.ops.hindsight.record_scene(on=True)
        bpy.ops.object.empty_add(location=(3, 0, 0))
        empty = bpy.context.active_object
        empty.name = "Pivot"
    yield 0.6
    uid = rt.obj_uid(ob)
    su = idmap.uid_of(bpy.context.scene)
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    yield from C.warm_up(d)

    def blob():
        return idmap.find(uid)

    def hist():
        return rt.history_of(blob())

    obj_states = [obj_state(ob)]

    def object_step(what):
        yield 0.7
        h = hist()
        n = 0 if h is None else len(h.entries)
        obj_states.append(obj_state(blob()))
        d.check(f"{what}: one step", n == len(obj_states) - 1, (n, len(obj_states) - 1,
                                                               None if h is None else [e.meta.get("label") for e in h.entries]))

    def select_only(o, active=None):
        with d.override():
            for x in bpy.context.view_layer.objects:
                x.select_set(x == o or x == active)
            bpy.context.view_layer.objects.active = active or o

    # Ctrl+P: parent Blob to Pivot (menu, first item)
    select_only(blob(), empty)
    with d.override():
        blob().select_set(True)
        empty.select_set(True)
        bpy.context.view_layer.objects.active = empty
    d.ev("MOUSEMOVE", x=cx, y=cy)
    d.key("P", ctrl=True, x=cx, y=cy)
    yield 0.4
    d.key("RET")
    yield from object_step("Ctrl+P, Object")
    select_only(blob())
    with d.override():
        bpy.ops.object.constraint_add(type="COPY_ROTATION")
    yield from object_step("add a constraint")
    with d.override():
        props = bpy.data.collections.new("Props")
        bpy.context.scene.collection.children.link(props)
        b = blob()
        old_colls = list(b.users_collection)
        props.objects.link(b)
        for c in old_colls:
            c.objects.unlink(b)
    yield from object_step("move to a new collection")
    d.ev("MOUSEMOVE", x=cx, y=cy)
    d.key("H", x=cx, y=cy)
    yield from object_step("H hides it")
    d.key("H", alt=True, x=cx, y=cy)
    yield from object_step("Alt+H shows it")
    select_only(blob())
    d.key("G", x=cx, y=cy)
    yield 0.2
    for i in range(1, 8):
        d.ev("MOUSEMOVE", x=cx + i * 7, y=cy)
        yield 0.03
    d.ev("LEFTMOUSE", "PRESS", x=cx + 49, y=cy)
    d.ev("LEFTMOUSE", "RELEASE", x=cx + 49, y=cy)
    yield from object_step("G move")

    wm = bpy.context.window_manager
    C.mod("slider_prop").sync()
    bad = []
    n = len(obj_states) - 1
    for pos in (0, n, 2, 4, 1, 3, 0, n):
        select_only(blob())
        with d.override():
            wm.hindsight_step = pos
        yield 0.2
        if obj_state(blob()) != obj_states[pos]:
            bad.append((pos, obj_state(blob()), obj_states[pos]))
    d.check("object jumps are exact (parent, collections, hide, constraints, transform)", not bad, bad[:2])
    select_only(blob())
    with d.override():
        wm.hindsight_step = n
    yield 0.3

    # scene: Shift+D (right-click cancels the move), X delete, Shift+A cube
    scene_states = [scene_names()]
    sh = lambda: rt.reg().history_for(su)
    base = len(sh().entries) if sh() is not None else 0       # adding the empty above

    def scene_step(what):
        yield 0.7
        scene_states.append(scene_names())
        h = sh()
        d.check(f"{what}: one scene step", h is not None and len(h.entries) == base + len(scene_states) - 1,
                None if h is None else [e.meta.get("label") for e in h.entries])

    select_only(empty)
    d.key("D", shift=True, x=cx, y=cy)
    yield 0.3
    d.ev("RIGHTMOUSE", "PRESS", x=cx, y=cy)
    d.ev("RIGHTMOUSE", "RELEASE", x=cx, y=cy)
    yield from scene_step("Shift+D")
    d.key("X", x=cx, y=cy)
    yield 0.3
    d.key("RET")
    yield from scene_step("X, Delete")
    with d.override():
        bpy.ops.mesh.primitive_cube_add(location=(0, -3, 0))
    yield from scene_step("Shift+A cube")

    # global history across adding and deleting
    sp = bpy.context.scene.hindsight
    sp.use_global = True
    yield 0.3
    info = C.info(d)
    total = info["len"]
    seen = []
    for g in (0, total, total // 2, 0, total):
        with d.override():
            rt.global_goto(bpy.context, g)
        yield 0.2
        seen.append(scene_names())
    d.check("global history: objects come and go with the slider", seen[0] != seen[1] and seen[3] == seen[0]
            and seen[4] == seen[1] == scene_states[-1], seen)
    sp.use_global = False
    yield 0.3

    # Edit Mode step by step, Blender's undo in Edit Mode
    select_only(blob())
    with d.override():
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.mesh.select_random(ratio=0.05, seed=2)
    yield 0.5
    before = len(hist().entries)
    meshes = [mesh_hash(blob())]
    d.key("G", x=cx, y=cy)
    yield 0.2
    for i in range(1, 6):
        d.ev("MOUSEMOVE", x=cx, y=cy - i * 6)
        yield 0.03
    d.ev("LEFTMOUSE", "PRESS", x=cx, y=cy - 30)
    d.ev("LEFTMOUSE", "RELEASE", x=cx, y=cy - 30)
    yield 0.7
    meshes.append(mesh_hash(blob()))
    d.key("E", x=cx, y=cy)
    yield 0.2
    for i in range(1, 6):
        d.ev("MOUSEMOVE", x=cx + i * 5, y=cy)
        yield 0.03
    d.ev("LEFTMOUSE", "PRESS", x=cx + 25, y=cy)
    d.ev("LEFTMOUSE", "RELEASE", x=cx + 25, y=cy)
    yield 0.7
    meshes.append(mesh_hash(blob()))
    h = hist()
    d.check("Edit Mode: G and E are two steps", len(h.entries) == before + 2,
            [e.meta.get("label") for e in h.entries[before:]])
    d.key("Z", ctrl=True, x=cx, y=cy)
    yield 0.7
    d.check("Ctrl+Z in Edit Mode: one step back, cursor follows", h.cursor == before + 1
            and mesh_hash(blob()) == meshes[1], (h.cursor, before))
    d.key("Z", ctrl=True, shift=True, x=cx, y=cy)
    yield 0.7
    d.check("Ctrl+Shift+Z in Edit Mode: forward again", h.cursor == before + 2 and mesh_hash(blob()) == meshes[2],
            (h.cursor, before))
    with d.override():
        wm.hindsight_step = before
    yield 0.5
    d.check("slider jump in Edit Mode is exact", blob().mode == "EDIT" and mesh_hash(blob()) == meshes[0])
    with d.override():
        wm.hindsight_step = before + 2
        bpy.ops.object.mode_set(mode="OBJECT")
    yield 0.5
    d.check("leaving Edit Mode adds no step", len(hist().entries) == before + 2, len(hist().entries))
    log = d.console_since(mark)
    d.check("no Python errors", "Traceback" not in log, log[-1500:])
