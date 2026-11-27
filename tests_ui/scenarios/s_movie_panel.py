"""The Movie Mode panel's rows in a real window (HANDOFF V2 checklist).

The panel is closed by default and Blender has no call that opens a panel, so the scenario registers it
again without DEFAULT_CLOSED (a fresh sidebar then shows it open), opens the Undo History tab with Movie
Mode on, and takes a screenshot of the sidebar to look at. Checks: the panel draws every row (Step, Frames
per step, Steps per frame, the movie length, Quick auto key, Camera and Lighting, Frame the model, Use this
view) with no error, and its draw ran.
"""
import bpy

import common as C


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    C.fresh_sphere(d, segments=32, rings=16)
    C.frame_view(d)
    C.set_mode(d, "SCULPT")
    yield 0.6
    for k in range(3):
        yield from C.real_stroke(d, k)
    C.set_mode(d, "OBJECT")
    yield 0.4
    sc = bpy.context.scene
    sc.hindsight.use_global = True
    sc.hindsight.movie_mode = True
    yield 1.0
    ui_panel = C.mod("ui_panel")
    cls = ui_panel.VIEW3D_PT_hindsight_movie
    drawn = []
    real_draw = cls.draw

    def draw(self, context):
        real_draw(self, context)
        drawn.append(True)
    bpy.utils.unregister_class(cls)
    cls.bl_options = set(cls.bl_options) - {"DEFAULT_CLOSED"}
    cls.draw = draw
    bpy.utils.register_class(cls)
    mark = d.console_mark()
    try:
        yield from C.show_sidebar_tab(d)
        yield 0.8
        win, area, reg, ui, space = d.view3d()
        d.redraw()
        yield 0.4
        d.shot("movie_panel_sidebar", area=area)
    finally:
        cls.draw = real_draw
    log = d.console_since(mark)
    d.check("Movie Mode panel drawn in the sidebar, no error", bool(drawn) and "Traceback" not in log,
            (len(drawn), log[-600:]))
