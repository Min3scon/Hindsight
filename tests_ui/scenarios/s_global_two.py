"""The global slider over strokes made on two objects in turn (R056: one mesh write per object and jump).

Two spheres, real strokes alternating between them (four each), global history on. Dragging the global
slider to the start, the middle and back to the end puts both meshes exactly where they were after
that many strokes; the drag is quick enough to follow the mouse.
"""
import hashlib
import time

import bpy
from bpy_extras import view3d_utils

import common as C


def _mesh_hash(ob):
    me = ob.data
    co = [0.0] * (3 * len(me.vertices))
    me.vertices.foreach_get("co", co)
    return hashlib.sha1(repr([round(x, 5) for x in co]).encode()).hexdigest()[:12]


def _both(a, b):
    return (_mesh_hash(a), _mesh_hash(b))


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    with d.override():
        for ob in list(bpy.data.objects):
            if ob.type == "MESH":
                bpy.data.objects.remove(ob)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.0, location=(-1.3, 0, 0))
        a = bpy.context.active_object
        a.name = "Left"
        bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1.0, location=(1.3, 0, 0))
        b = bpy.context.active_object
        b.name = "Right"
        bpy.ops.view3d.view_axis(type="FRONT")
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.view3d.view_selected()
        bpy.ops.object.select_all(action="DESELECT")
    yield 0.6
    states = [_both(a, b)]
    win, area, reg, ui, space = d.view3d()

    def to_screen(ob):
        p = view3d_utils.location_3d_to_region_2d(reg, space.region_3d, ob.matrix_world.translation)
        return p.x, p.y

    def stroke_on(ob, k):
        cx, cy = to_screen(ob)
        pts = [(cx - 60 + 120 * i / 19, cy + (k - 2) * 12 + 10 * ((i % 5) - 2) / 2) for i in range(20)]
        yield from d.drag(reg, pts, step_wait=0.015)
        yield 0.45

    for k in range(4):
        for ob in (a, b):
            with d.override():
                if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")
                for o in bpy.context.view_layer.objects:
                    o.select_set(False)
                bpy.context.view_layer.objects.active = ob
                ob.select_set(True)
                bpy.ops.object.mode_set(mode="SCULPT")
            yield 0.4
            yield from stroke_on(ob, k)
            states.append(_both(a, b))
    C.set_mode(d, "OBJECT")
    yield 0.4
    bpy.context.scene.hindsight.use_global = True
    yield 0.4
    i = C.info(d)
    hs = C.mod("runtime").history_of(bpy.context.scene)
    base = len(hs.entries) if hs is not None else 0     # Record on Everything: the setup is the scene's (#104)
    d.check("eight strokes on two objects in turn: eight positions on the global timeline, each changing a mesh",
            i is not None and i["len"] == base + 8 and len(set(states)) == 9, (i, base, states))
    for k in (0, 4, 8, 3, 0, 8):
        pos = base + k
        t = time.perf_counter()
        yield from C.drag_slider_to(d, pos)
        took = time.perf_counter() - t
        yield 0.4
        got = _both(a, b)
        d.check(f"global slider to {pos}: both meshes as after {k} strokes",
                C.info(d)["pos"] == pos and got == states[k], (pos, C.info(d)["pos"], got, states[k], round(took, 2)))
