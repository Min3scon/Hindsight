"""Regression (Round E, beta 2): on a slow modifier stack one stroke was split into two steps,
because updates paused longer than the 0.15 s debounce while the mouse was still down.

A test-only depsgraph handler sleeps 0.25 s on every update to make Blender that slow.
One stroke must still be exactly one step.
"""
import time

import bpy

import common as C


def _slow(scene, depsgraph=None):
    time.sleep(0.25)


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    C.fresh_sphere(d)
    C.frame_view(d)
    C.set_mode(d, "SCULPT")
    yield 0.5
    yield from C.real_stroke(d, 0)
    d.check("normal stroke: one step", C.info(d)["len"] == 1, C.info(d))
    rt = C.mod("runtime")
    real = rt.stroke_running
    if d.args.get("without_fix") == "1":       # proves the test catches the old behaviour
        rt.stroke_running = lambda: False
    bpy.app.handlers.depsgraph_update_post.append(_slow)
    try:
        win, area, reg, ui, space = d.view3d()
        yield from d.drag(reg, C.stroke_pts(reg, 2, n=14), step_wait=0.02)
        yield 1.5
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(_slow)
        rt.stroke_running = real
    yield 0.6
    d.check("slow updates: the stroke is still one step", C.info(d)["len"] == 2, C.info(d))
