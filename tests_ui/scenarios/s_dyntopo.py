"""Dyntopo in a real window (D145, Record Dyntopo, off by default until this passes).

Real strokes with Dyntopo on add and remove triangles: each stroke is one step. Dragging the slider
back puts each earlier mesh back exactly, with Dyntopo still on (and off again before the moment it
was turned on, as Blender's own undo does); dragging forward brings the strokes back.
"""

import bpy

import common as C


def _dyn(d):
    with d.override():
        ob = bpy.context.active_object
        return bool(ob.use_dynamic_topology_sculpting)


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    C.fresh_sphere(d, segments=32, rings=16)
    C.frame_view(d)
    C.mod("prefs").get_prefs().record_dyntopo = True
    C.set_mode(d, "SCULPT")
    yield 0.6
    states = [C.dyntopo_state(d)]
    with d.override():
        bpy.ops.sculpt.dynamic_topology_toggle()
        ts = bpy.context.scene.tool_settings.sculpt
        ts.detail_type_method = "RELATIVE"
        ts.detail_size = 4.0                   # fine detail: a stroke clearly adds triangles
    yield 0.8
    n0 = C.info(d)["len"] if C.info(d) else 0
    states.append(C.dyntopo_state(d))
    d.check("turning Dyntopo on is a step (it triangulates the mesh)", n0 == 1 and states[1][1] > states[0][1]
            and _dyn(d), (n0, states))
    for k in range(2):
        yield from C.real_stroke(d, k)
        yield 0.4
        states.append(C.dyntopo_state(d))
    i = C.info(d)
    d.check("two Dyntopo strokes, two steps, each changing the triangles",
            i["len"] == 3 and len({s[:2] for s in states[1:]}) == 3, (i, states))
    d.shot("dyntopo_after_strokes")
    for pos in (2, 1, 0, 1, 3):
        yield from C.drag_slider_to(d, pos)
        yield 0.6
        got = C.dyntopo_state(d)
        d.check(f"slider to {pos}: the mesh of that step, Dyntopo {'on' if pos else 'off'}",
                C.info(d)["pos"] == pos and got == states[pos] and _dyn(d) == (pos > 0),
                (pos, C.info(d)["pos"], got, states[pos], _dyn(d)))
    yield from C.real_stroke(d, 3)
    yield 0.4
    states.append(C.dyntopo_state(d))
    d.check("a stroke at the end after the jumps is a fourth step", C.info(d)["len"] == 4, C.info(d))

    # Blender's own undo and redo of a Dyntopo stroke: the history follows (one position back and forward),
    # never a new step, and the triangles are the step's (Blender's undo may order vertices differently)
    i = C.info(d)
    n, pos = i["len"], i["pos"]
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    d.ev("MOUSEMOVE", x=cx, y=cy)
    yield 0.2
    d.key("Z", ctrl=True, x=cx, y=cy)
    yield 1.2
    j, got = C.info(d), C.dyntopo_state(d)
    d.check("Ctrl+Z after a Dyntopo stroke: one position back, no new step, the triangles before the stroke",
            j["len"] == n and j["pos"] == pos - 1 and got[:2] == states[3][:2] and _dyn(d),
            (n, pos, j, got, states[3:]))
    d.key("Z", ctrl=True, shift=True, x=cx, y=cy)
    yield 1.2
    k, got = C.info(d), C.dyntopo_state(d)
    d.check("Ctrl+Shift+Z: forward again, no new step, the stroke's triangles",
            k["len"] == n and k["pos"] == pos and got[:2] == states[4][:2], (n, pos, k, got, states[4]))
    yield from C.drag_slider_to(d, 2)
    yield 0.6
    d.check("after Blender's undo and redo the slider still puts step 2 back exactly",
            C.info(d)["pos"] == 2 and C.dyntopo_state(d) == states[2], (C.info(d), C.dyntopo_state(d), states[2]))
