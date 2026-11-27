"""Selection operators in a real window read little (R048), and what comes after them is still recorded.

With Everything in this file on and 31 recorded objects: a click that selects another object reads only
the selected, active and updated objects (not all 31) and records nothing; G then moves it as one step.
In Edit Mode, A (Select All) and a click don't read the mesh (no Edit Mode step is made for them);
G then moves the vertices as one step. The operator names Blender lists are the ones Hindsight knows.
"""
import bpy
from bpy_extras import view3d_utils

import common as C


class _Count:
    """Count calls to module functions while it is active."""

    def __init__(self, *targets):
        self.targets, self.n, self.saved = targets, {}, []

    def __enter__(self):
        for mod, name in self.targets:
            orig = getattr(mod, name)
            self.saved.append((mod, name, orig))
            self.n[name] = 0

            def wrap(*a, _o=orig, _n=name, **k):
                self.n[_n] += 1
                return _o(*a, **k)
            setattr(mod, name, wrap)
        return self.n

    def __exit__(self, *exc):
        for mod, name, orig in self.saved:
            setattr(mod, name, orig)
        return False


def _steps():
    rt = C.mod("runtime")
    return sum(len(h.entries) for h in rt.reg().histories.values())


def _last_op():
    ops = bpy.context.window_manager.operators
    return ops[-1].bl_idname if len(ops) else None


def _screen_xy(d, co):
    win, area, reg, ui, space = d.view3d()
    p = view3d_utils.location_3d_to_region_2d(reg, space.region_3d, co)
    return (reg.x + int(p.x), reg.y + int(p.y))


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    ob = C.fresh_sphere(d, segments=48, rings=24, radius=0.6)
    with d.override():
        for i in range(30):
            bpy.ops.mesh.primitive_cube_add(size=0.25, location=(2.0 + (i % 6) * 0.5, (i // 6) * 0.5 - 1.0, 0))
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        bpy.ops.view3d.view_all()
    C.mod("prefs").get_prefs().record_scope = "EVERYTHING"
    with d.override():
        bpy.ops.hindsight.record_file(on=True)
    yield 1.0
    dr, rt = C.mod("datarec"), C.mod("runtime")
    # #62: datablocks that still exist (the default Cube, switched on when the file opened, was removed)
    tracked = sum(C.mod("idmap").find(u) is not None for u in dr.tracked_uids())
    d.check("31 objects and the scene recorded", tracked >= 31, tracked)
    win, area, reg, ui, space = d.view3d()
    cube = next(o for o in bpy.data.objects if o.name.startswith("Cube"))
    x, y = _screen_xy(d, cube.matrix_world.translation)
    d.ev("MOUSEMOVE", x=x, y=y)
    yield 0.3
    n0 = _steps()
    with _Count((dr, "capture")) as n:
        d.ev("LEFTMOUSE", "PRESS", x=x, y=y)
        d.ev("LEFTMOUSE", "RELEASE", x=x, y=y)
        yield 1.0
    d.note("click_op", _last_op())
    d.note("click_captures", n["capture"])
    picked = bpy.context.view_layer.objects.active
    d.check("a click selects a cube: VIEW3D_OT_select, the selection path, nothing recorded",
            picked is not None and picked.name.startswith("Cube") and _last_op() == "VIEW3D_OT_select"
            and 0 < n["capture"] <= 4 and _steps() == n0, (_last_op(), dict(n), n0, _steps(), picked))
    cube = picked if picked is not None and picked.name.startswith("Cube") else cube
    before = tuple(cube.location)
    with _Count((dr, "capture")) as n:
        d.key("G", x=x, y=y)
        yield 0.3
        for i in range(1, 9):
            d.ev("MOUSEMOVE", x=x + 6 * i, y=y)
            yield 0.05
        d.ev("LEFTMOUSE", "PRESS", x=x + 48, y=y)
        d.ev("LEFTMOUSE", "RELEASE", x=x + 48, y=y)
        yield 1.0
    d.check("G after the click: the cube moves, one step, every recorded object read",
            tuple(cube.location) != before and _steps() == n0 + 1 and n["capture"] >= tracked,
            (before, tuple(cube.location), n0, _steps(), dict(n), tracked))

    # Edit Mode on the sphere
    with d.override():
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
    C.set_mode(d, "EDIT")
    yield 0.8
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    d.ev("MOUSEMOVE", x=cx, y=cy)
    yield 0.2
    n1 = _steps()
    with _Count((rt, "commit_edit")) as n:
        d.key("A", x=cx, y=cy)
        yield 1.0
        op_a = _last_op()
        sx, sy = _screen_xy(d, ob.matrix_world @ ob.data.vertices[5].co)
        d.ev("MOUSEMOVE", x=sx, y=sy)
        yield 0.2
        d.ev("LEFTMOUSE", "PRESS", x=sx, y=sy)
        d.ev("LEFTMOUSE", "RELEASE", x=sx, y=sy)
        yield 1.0
    d.note("edit_ops", (op_a, _last_op()))
    d.check("A and a click in Edit Mode: Select All and VIEW3D_OT_select, no mesh read, nothing recorded",
            op_a == "MESH_OT_select_all" and _last_op() == "VIEW3D_OT_select" and n["commit_edit"] == 0
            and _steps() == n1, (op_a, _last_op(), dict(n), n1, _steps()))
    with _Count((rt, "commit_edit")) as n:
        d.key("G", x=sx, y=sy)
        yield 0.3
        for i in range(1, 9):
            d.ev("MOUSEMOVE", x=sx + 5 * i, y=sy + 3 * i)
            yield 0.05
        d.ev("LEFTMOUSE", "PRESS", x=sx + 40, y=sy + 24)
        d.ev("LEFTMOUSE", "RELEASE", x=sx + 40, y=sy + 24)
        yield 1.0
    d.check("G in Edit Mode after them: the vertex moves as one Edit Mode step",
            n["commit_edit"] == 1 and _steps() == n1 + 1, (dict(n), n1, _steps()))
    C.set_mode(d, "OBJECT")
    yield 0.5
