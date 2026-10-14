"""Regressions (beta 2): one stroke must be exactly one step.

1. Round E: on a slow modifier stack one stroke was split into two steps, because updates paused
   longer than the 0.15 s debounce while the mouse was still down.
2. Round C: quick strokes (less than 0.15 s apart) were merged into one step.

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

    # quick strokes, about 0.1 s apart, each must be its own step
    before = C.info(d)["len"]
    win, area, reg, ui, space = d.view3d()
    for k in range(12):
        x0, y0 = reg.width * (0.42 + 0.015 * k), reg.height * 0.5
        yield from d.drag(reg, [(x0 + j * 9, y0) for j in range(7)], step_wait=0.01)
        yield 0.05
    yield 0.6
    d.check("12 quick strokes, 12 steps", C.info(d)["len"] == before + 12, (before, C.info(d)))
