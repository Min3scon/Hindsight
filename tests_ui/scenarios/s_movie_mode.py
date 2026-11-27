"""Movie Mode in a real window (HANDOFF, V2 checklist).

The camera and three lamps Movie Mode adds; "Use this view" turning the camera; the camera and a lamp
put back after being deleted, but never in the middle of a stroke, a drag (G), playback or a render;
and a timelapse (Quick auto key) rendered with Workbench and with EEVEE, its frames showing different
steps. Strokes and the drag are simulated mouse input in the viewport.
"""
import os
import tempfile

import bpy

import common as C

RIG = ("Hindsight Camera", "Hindsight Key", "Hindsight Fill", "Hindsight Rim")


def _rig():
    return {n for n in RIG if n in bpy.data.objects and bpy.data.objects[n].users_scene}


def _drop(name):
    ob = bpy.data.objects.get(name)
    if ob is not None:
        bpy.data.objects.remove(ob, do_unlink=True)


def _back_after(d, name, label, wait=1.6):
    """Generator: once nothing is running any more, the deleted piece is put back."""
    yield wait
    d.check(f"{label}: put back afterwards", name in _rig(), sorted(_rig()))


def _png_sum(path):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        buf = [0.0] * (img.size[0] * img.size[1] * 4)
        img.pixels.foreach_get(buf)
        return round(sum(buf), 3)
    finally:
        bpy.data.images.remove(img)


def _engines():
    items = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    eevee = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in items else "BLENDER_EEVEE"
    return [("Workbench", "BLENDER_WORKBENCH"), ("EEVEE", eevee)]


def run(d):
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    with d.override():
        for ob in list(bpy.data.objects):
            if ob.type in ("CAMERA", "LIGHT"):
                bpy.data.objects.remove(ob)
    C.fresh_sphere(d, segments=48, rings=24)
    C.frame_view(d)
    C.set_mode(d, "SCULPT")
    yield 0.6
    for k in range(4):
        yield from C.real_stroke(d, k)
    d.check("four strokes, four steps", C.info(d)["len"] == 4, C.info(d))
    C.set_mode(d, "OBJECT")
    yield 0.3
    sc = bpy.context.scene
    sh = sc.hindsight
    sh.use_global = True
    sh.movie_camera = True
    sh.movie_lighting = True
    sh.movie_mode = True
    sh.movie_step = 4                     # the end: strokes made while Movie Mode is on add to it
    yield 1.4
    cam = sc.camera
    d.check("Movie Mode adds its camera and three lamps", _rig() == set(RIG) and cam is not None
            and cam.name == "Hindsight Camera", (sorted(_rig()), cam and cam.name))
    d.shot("movie_mode_rig")

    # ------------------------------------------------------------------ Use this view
    win, area, reg, ui, space = d.view3d()
    before = (round(sh.movie_view_angle, 4), round(sh.movie_view_height, 4),
              tuple(round(x, 3) for x in sc.camera.matrix_world.translation))
    r3d = space.region_3d
    r3d.view_rotation = (0.6533, 0.2706, 0.2706, 0.6533)      # looking from the side, a little from above
    yield 0.3
    with d.override(space_data=space):
        bpy.ops.hindsight.movie_view_from_viewport()
    yield 0.8
    after = (round(sh.movie_view_angle, 4), round(sh.movie_view_height, 4),
             tuple(round(x, 3) for x in sc.camera.matrix_world.translation))
    d.check("Use this view turns the camera", after[:2] != before[:2] and after[2] != before[2], (before, after))

    # ------------------------------------------------------------------ deleted, then put back
    old_cam = bpy.data.objects["Hindsight Camera"].session_uid
    with d.override():
        for ob in bpy.context.view_layer.objects:
            ob.select_set(ob.name == "Hindsight Camera")
        bpy.context.view_layer.objects.active = bpy.data.objects["Hindsight Camera"]
        res = bpy.ops.object.delete()
    yield 1.6
    # the check can't look for a gap: the timer may put it back within milliseconds; a new object proves both
    new_cam = bpy.data.objects.get("Hindsight Camera")
    d.check("deleted camera put back (a new object) and made the scene's camera",
            res == {"FINISHED"} and new_cam is not None and new_cam.session_uid != old_cam
            and "Hindsight Camera" in _rig() and sc.camera == new_cam, (res, sorted(_rig()), sc.camera))
    with d.override():
        blob = bpy.data.objects["Blob"]
        for ob in bpy.context.view_layer.objects:
            ob.select_set(ob == blob)
        bpy.context.view_layer.objects.active = blob

    # ------------------------------------------------------------------ not during a stroke
    C.set_mode(d, "SCULPT")
    yield 0.6
    win, area, reg, ui, space = d.view3d()
    pts = C.stroke_pts(reg, 1, n=70)
    rt = C.mod("runtime")
    x, y = d.region_to_win(reg, *pts[0])
    d.ev("MOUSEMOVE", x=x, y=y)
    yield 0.05
    d.ev("LEFTMOUSE", "PRESS", x=x, y=y)
    running = False
    for i, p in enumerate(pts[1:]):
        x, y = d.region_to_win(reg, *p)
        d.ev("MOUSEMOVE", x=x, y=y)
        yield 0.03
        if i == 6:
            running = rt.stroke_running()
            _drop("Hindsight Key")
    mid = "Hindsight Key" in _rig()
    still = rt.stroke_running()
    d.ev("LEFTMOUSE", "RELEASE", x=x, y=y)
    d.check("not put back during a stroke", running and still and not mid, (running, still, mid))
    yield from _back_after(d, "Hindsight Key", "lamp deleted during a stroke")
    C.set_mode(d, "OBJECT")
    yield 0.4

    # ------------------------------------------------------------------ not during a drag (G)
    dr = C.mod("datarec")
    win, area, reg, ui, space = d.view3d()
    cx, cy = reg.x + reg.width // 2, reg.y + reg.height // 2
    d.ev("MOUSEMOVE", x=cx, y=cy)
    yield 0.1
    d.key("G", x=cx, y=cy)
    yield 0.2
    dragging = False
    for i in range(50):
        d.ev("MOUSEMOVE", x=cx + i * 2, y=cy)
        yield 0.03
        if i == 3:
            dragging = dr._operation_running()
            _drop("Hindsight Fill")
    mid = "Hindsight Fill" in _rig()
    still = dr._operation_running()
    d.key("ESC")
    d.check("not put back during a drag", dragging and still and not mid, (dragging, still, mid))
    yield from _back_after(d, "Hindsight Fill", "lamp deleted during a drag")

    # ------------------------------------------------------------------ not during playback
    with d.override():
        bpy.ops.screen.animation_play()
    yield 0.3
    _drop("Hindsight Rim")
    yield 1.6
    mid = "Hindsight Rim" in _rig()
    playing = bool(bpy.context.window_manager.windows[0].screen.is_animation_playing)
    with d.override():
        bpy.ops.screen.animation_cancel(restore_frame=True)
    d.check("not put back during playback", playing and not mid, (playing, mid))
    yield from _back_after(d, "Hindsight Rim", "lamp deleted during playback")

    # ------------------------------------------------------------------ not during a render
    prefs_view = bpy.context.preferences.view
    display = prefs_view.render_display_type
    prefs_view.render_display_type = "NONE"
    r = sc.render
    r.engine = "CYCLES"
    r.resolution_x, r.resolution_y, r.resolution_percentage = 160, 120, 100
    sc.cycles.samples = 4096
    sc.cycles.use_adaptive_sampling = False
    sc.cycles.time_limit = 4.0
    with d.override():
        bpy.ops.render.render("INVOKE_DEFAULT")
    started = False
    for _ in range(40):
        yield 0.1
        if bpy.app.is_job_running("RENDER"):
            started = True
            break
    _drop("Hindsight Key")
    yield 1.6
    mid = "Hindsight Key" in _rig()
    still = bpy.app.is_job_running("RENDER")
    d.check("not put back during a render", started and still and not mid, (started, still, mid))
    for _ in range(300):
        if not bpy.app.is_job_running("RENDER"):
            break
        yield 0.2
    # the render operator ends at the next event after its job (here no one moves the mouse): move it
    win, area, reg, ui, space = d.view3d()
    for i in range(3):
        d.ev("MOUSEMOVE", x=reg.x + reg.width // 2 + 10 * i, y=reg.y + reg.height // 2)
        yield 0.1
    yield from _back_after(d, "Hindsight Key", "lamp deleted during a render")
    prefs_view.render_display_type = display

    # ------------------------------------------------------------------ a timelapse, rendered
    sh.frames_per_step = 1
    with d.override():
        bpy.ops.hindsight.quick_auto_key()
    yield 0.5
    fc = C.mod("movie")._step_fcurve(sc)
    sc.frame_end = int(max(k.co[0] for k in fc.keyframe_points))      # just the keyed frames
    d.note("frames", (sc.frame_start, sc.frame_end, sh.movie_step))
    out = tempfile.mkdtemp(prefix="hs_movie_")
    r.image_settings.file_format = "PNG"
    for label, engine in _engines():
        r.engine = engine
        if engine != "BLENDER_WORKBENCH":
            sc.eevee.taa_render_samples = 1
        r.filepath = os.path.join(out, label + "_####")
        with d.override():
            bpy.ops.render.render(animation=True)
        yield 0.5
        files = sorted(f for f in os.listdir(out) if f.startswith(label + "_") and f.endswith(".png"))
        sums = [_png_sum(os.path.join(out, f)) for f in files] if files else []
        want = sc.frame_end - sc.frame_start + 1
        d.check(f"timelapse rendered with {label}: every frame written, first and last frames differ",
                len(files) == want and len(set(sums)) > 1 and sums[0] != sums[-1], (want, files, sums))
        d.check(f"{label}: rendered from the Movie Mode camera with its lamps",
                sc.camera is not None and sc.camera.name == "Hindsight Camera" and _rig() == set(RIG),
                (sc.camera and sc.camera.name, sorted(_rig())))
    d.note("render_dir", out)
