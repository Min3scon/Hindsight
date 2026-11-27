import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import math

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Euler, Matrix, Vector

import tests.harness as H
from hindsight.bl import camrig, rigmark, runtime

C = bpy.context
ROLES = {"camera", "key", "fill", "rim"}


def hist_state():
    return sorted((h.uid, len(h.entries), h.cursor) for h in runtime.reg().all())


def world_points(ob):
    me = ob.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)   # readable in Edit Mode too (the mesh as it was on entering)
    mw = np.array([list(r) for r in ob.matrix_world], np.float64)
    return co.reshape(-1, 3).astype(np.float64) @ mw[:3, :3].T + mw[:3, 3]


def scale_mesh(ob, f):
    me = ob.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.attributes["position"].data.foreach_get("vector", co)
    me.attributes["position"].data.foreach_set("vector", co * np.float32(f))
    me.update()


def build(name="Sphere", location=(0, 0, 0), n=1500):
    """Empty factory scene, one sculpted sphere, global history on."""
    H.fresh()
    ob = H.make_mesh(n, name, location)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    assert H.commit(ob) is not None
    H.fake_stroke(ob, 2)
    assert H.commit(ob) is not None
    runtime.set_global(C, True)
    return ob


def check_in_frame(obs, tight=True, points=None, msg=""):
    sc = C.scene
    cam = camrig.rig(sc)["camera"]
    assert sc.camera is cam, msg
    margin = camrig.MARGIN
    xs, ys = [], []
    sets = points if points is not None else [world_points(o) for o in obs]
    for pts in sets:
        for p in pts:
            v = world_to_camera_view(sc, cam, Vector(p))
            xs.append(v.x)
            ys.append(v.y)
            assert v.z > 0, f"{msg} behind camera {tuple(v)}"
    xs, ys = np.array(xs), np.array(ys)
    eps = 1e-4
    assert xs.min() >= -eps and xs.max() <= 1 + eps, f"{msg} x out {xs.min()} {xs.max()}"
    assert ys.min() >= -eps and ys.max() <= 1 + eps, f"{msg} y out {ys.min()} {ys.max()}"
    sx, sy = xs.max() - xs.min(), ys.max() - ys.min()
    assert sx <= 1 - margin + 1e-3 and sy <= 1 - margin + 1e-3, f"{msg} margin not free {sx} {sy}"
    if tight:
        assert max(sx, sy) >= 0.5, f"{msg} not tight {sx} {sy}"
    return sx, sy


def mat(ob):
    return [tuple(r) for r in ob.matrix_world]


def same_mat(a, b):
    return np.allclose(np.array(a), np.array(b), atol=1e-5)


def test_empty_scene_creates_rig_and_fits():
    ob = build()
    sc = C.scene
    assert sc.camera is None and not any(o.type == "LIGHT" for o in sc.objects)
    before = hist_state()
    sc.hindsight.movie_mode = True
    r = camrig.rig(sc)
    assert set(r) == ROLES, set(r)
    for role, o in r.items():
        assert o.name == camrig.NAMES[role], o.name
        assert o.data.name == camrig.NAMES[role], o.data.name
        assert o.type == ("CAMERA" if role == "camera" else "LIGHT")
        assert role == "camera" or o.data.type == "SUN"
    assert sc.camera is r["camera"]
    assert not camrig.needs_attention(sc)
    assert camrig.model_objects(sc) == [ob]
    cam = r["camera"]
    for rx, ry in ((1920, 1080), (1080, 1920)):
        for fit_mode in ("AUTO", "HORIZONTAL", "VERTICAL"):
            sc.render.resolution_x, sc.render.resolution_y = rx, ry
            cam.data.sensor_fit = fit_mode
            assert camrig.frame(sc) is True
            check_in_frame([ob], msg=f"{rx}x{ry} {fit_mode}")
    # the view direction follows the settings
    az, el = sc.hindsight.movie_view_angle, sc.hindsight.movie_view_height
    back = Vector(cam.matrix_world.col[2][:3])
    want = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    assert (back - want).length < 1e-4, (back, want)
    assert cam.data.type == "PERSP" and not cam.data.dof.use_dof
    assert cam.data.clip_start > 0 and cam.data.clip_end > cam.data.clip_start
    assert ob.mode == "SCULPT"
    assert hist_state() == before, "the camera never changes history"


def test_far_transformed_model_fits():
    ob = build(location=(40, -25, 12))
    ob.rotation_euler = (0.4, 1.1, -0.7)
    ob.scale = (3.0, 0.4, 1.7)
    C.view_layer.update()
    sc = C.scene
    sc.hindsight.movie_mode = True
    check_in_frame([ob], msg="far")
    sc.hindsight.movie_view_angle = math.radians(-120)
    sc.hindsight.movie_view_height = math.radians(-40)
    check_in_frame([ob], msg="far other view")


def test_model_change_and_operator_reframes():
    ob = build()
    sc = C.scene
    sc.hindsight.movie_mode = True
    cam = camrig.rig(sc)["camera"]
    m0 = mat(cam)
    ob.scale = (4.0, 4.0, 4.0)
    C.view_layer.update()
    assert same_mat(mat(cam), m0), "nothing moves the camera on its own"
    assert bpy.ops.hindsight.movie_frame() == {'FINISHED'}
    assert not same_mat(mat(cam), m0)
    check_in_frame([ob], msg="after scale")
    # poll needs Movie Mode
    sc.hindsight.movie_mode = False
    assert not bpy.ops.hindsight.movie_frame.poll()


def test_quick_auto_key_frames_the_movie():
    ob = build()
    sc = C.scene
    sc.hindsight.movie_mode = True
    cam = camrig.rig(sc)["camera"]
    cam.location = (500.0, 500.0, 500.0)             # moved away by hand
    C.view_layer.update()
    steps = [len(h.entries) for h in runtime.reg().all()]
    assert bpy.ops.hindsight.quick_auto_key() == {'FINISHED'}     # also shows the step keyed for this frame
    check_in_frame([ob], tight=False, msg="after Quick auto key")
    assert [len(h.entries) for h in runtime.reg().all()] == steps, "no step recorded"


def test_earlier_bigger_state_stays_in_frame():
    H.fresh()
    ob = H.make_mesh(1500, "Sphere")
    H.enter(ob, "SCULPT")
    small = world_points(ob)
    scale_mesh(ob, 3.0)
    assert H.commit(ob) is not None
    big = world_points(ob)
    scale_mesh(ob, 1.0 / 3.0)
    assert H.commit(ob) is not None
    runtime.set_global(C, True)
    sc = C.scene
    info = runtime.scrub_info(C)
    assert info["len"] == 2 and info["pos"] == 2, info
    before = hist_state()
    sc.hindsight.movie_mode = True
    assert hist_state() == before
    cam = camrig.rig(sc)["camera"]
    m0 = mat(cam)
    sx, _sy = check_in_frame([ob], tight=False, msg="small now")
    check_in_frame(None, points=[big], msg="big state, before scrubbing")
    # scrub to the big state the way Movie Mode playback does
    prev = getattr(runtime.goto, "_movie", False)
    runtime.goto._movie = True
    try:
        with runtime.suspended():
            runtime.goto(C, 1)
    finally:
        runtime.goto._movie = prev
    assert runtime.scrub_info(C)["pos"] == 1
    assert np.abs(world_points(ob) - big).max() < 1e-4
    assert same_mat(mat(cam), m0)
    check_in_frame([ob], msg="big state")
    del small


def test_own_camera_and_light_untouched():
    build()
    sc = C.scene
    ucd = bpy.data.cameras.new("MyCam")
    ucam = bpy.data.objects.new("MyCam", ucd)
    sc.collection.objects.link(ucam)
    ucam.matrix_world = Matrix.Translation((3, -9, 4)) @ Euler((1.2, 0, 0.3)).to_matrix().to_4x4()
    uld = bpy.data.lights.new("MyLamp", type="POINT")
    ulamp = bpy.data.objects.new("MyLamp", uld)
    sc.collection.objects.link(ulamp)
    ulamp.location = (1, 2, 5)
    C.view_layer.update()
    sc.camera = ucam
    mc, ml = mat(ucam), mat(ulamp)
    assert camrig.scene_has_own_light(sc)
    sh = sc.hindsight
    sh.movie_mode = True
    r = camrig.rig(sc)
    assert set(r) == {"camera"}, "no lighting is added next to the scene's own light"
    assert sc.camera is r["camera"] and sh.movie_user_camera == ucam and sh.movie_prev_camera == ""
    # a light that doesn't render doesn't count as the scene's own
    ulamp.hide_render = True
    camrig.ensure(sc)
    assert set(camrig.rig(sc)) == ROLES
    assert not camrig.scene_has_own_light(sc), "Hindsight's own lighting doesn't count as the scene's"
    sh.movie_lighting = False
    assert set(camrig.rig(sc)) == {"camera"}
    sh.movie_lighting = True
    assert set(camrig.rig(sc)) == ROLES
    ulamp.hide_render = False
    camrig.ensure(sc)
    assert set(camrig.rig(sc)) == {"camera"}
    # our camera deleted and put back: the user's camera is still remembered
    bpy.data.objects.remove(camrig.rig(sc)["camera"])
    camrig._LAST[0] = -1e9
    camrig.tick()
    assert sc.camera is camrig.rig(sc)["camera"] and sh.movie_user_camera == ucam
    bpy.ops.hindsight.movie_frame()
    sh.movie_mode = False
    assert camrig.rig(sc) == {}
    assert sc.camera is ucam and sh.movie_user_camera is None and sh.movie_prev_camera == ""
    assert bpy.data.objects.get("MyCam") is ucam and bpy.data.objects.get("MyLamp") is ulamp
    assert same_mat(mat(ucam), mc) and same_mat(mat(ulamp), ml)
    assert not ucam.hide_render and not ucam.hide_get() and not ulamp.hide_render


def test_tick_puts_missing_pieces_back():
    ob = build()
    sc = C.scene
    before = hist_state()
    sc.hindsight.movie_mode = True
    for role in ("camera", "key"):
        victim = camrig.rig(sc)[role]
        bpy.data.objects.remove(victim)
        assert role not in camrig.rig(sc)
        assert camrig.needs_attention(sc)
        camrig._LAST[0] = -1e9
        camrig.tick()
        assert set(camrig.rig(sc)) == ROLES, role
        assert sc.camera is camrig.rig(sc)["camera"]
    check_in_frame([ob], msg="after put back")
    # rate limited: a second call right away does nothing
    bpy.data.objects.remove(camrig.rig(sc)["fill"])
    camrig.tick()
    assert "fill" not in camrig.rig(sc)
    # not during Movie Mode playback in a handler
    camrig._LAST[0] = -1e9
    runtime.IN_HANDLER[0] = True
    try:
        camrig.tick()
    finally:
        runtime.IN_HANDLER[0] = False
    assert "fill" not in camrig.rig(sc)
    # not in the middle of a brush stroke or a drag (a modal operator is running)
    from hindsight.bl import datarec
    real = datarec._operation_running
    datarec._operation_running = lambda: True
    try:
        camrig._LAST[0] = -1e9
        camrig.tick()
    finally:
        datarec._operation_running = real
    assert "fill" not in camrig.rig(sc)
    camrig._LAST[0] = -1e9
    camrig.tick()
    assert set(camrig.rig(sc)) == ROLES
    # no datablocks pile up
    assert len([c for c in bpy.data.cameras if c.name.startswith("Hindsight")]) == 1
    assert len([l for l in bpy.data.lights if l.name.startswith("Hindsight")]) == 3
    assert hist_state() == before


def test_movie_off_removes_everything():
    ob = build()
    sc = C.scene
    before = hist_state()
    sh = sc.hindsight
    sh.movie_mode = True
    assert set(camrig.rig(sc)) == ROLES
    sh.movie_mode = False
    assert camrig.rig(sc) == {}
    assert sc.camera is None
    assert not [o for o in bpy.data.objects if rigmark.is_rig(o)]
    assert not [c for c in bpy.data.cameras if c.name.startswith("Hindsight")]
    assert not [l for l in bpy.data.lights if l.name.startswith("Hindsight")]
    camrig._LAST[0] = -1e9
    camrig.tick()
    assert camrig.rig(sc) == {}, "tick adds nothing while Movie Mode is off"
    # both settings off: nothing of Hindsight's stays, Movie Mode itself stays on
    sh.movie_mode = True
    sh.movie_camera = False
    sh.movie_lighting = False
    assert camrig.rig(sc) == {} and sc.camera is None
    assert sh.movie_mode
    sh.movie_camera = True
    assert set(camrig.rig(sc)) == {"camera"}
    assert camrig.remove(sc) == 1 and sc.camera is None
    assert ob.name in sc.objects
    assert hist_state() == before


def test_duplicate_loses_mark():
    build()
    sc = C.scene
    sc.hindsight.movie_mode = True
    cam = camrig.rig(sc)["camera"]
    dup = cam.copy()
    dup.data = cam.data.copy()
    sc.collection.objects.link(dup)
    assert rigmark.is_rig(dup)
    r = camrig.rig(sc)
    assert r["camera"] is cam
    assert not rigmark.is_rig(dup)
    assert not camrig.needs_attention(sc)
    sc.hindsight.movie_mode = False
    assert bpy.data.objects.get(dup.name) is dup, "the duplicate is the user's now"


def test_view_from_region():
    build()
    sc = C.scene
    sh = sc.hindsight
    sh.movie_mode = True
    cases = [
        (Euler((math.pi / 2, 0, 0)), 0.0, 0.0),                       # front view
        (Euler((math.pi / 2, 0, math.pi / 2)), math.pi / 2, 0.0),     # right view
        (Euler((math.pi / 3, 0, math.pi / 4)), math.pi / 4, math.pi / 6),
        (Euler((math.pi / 2, 0, -3 * math.pi / 4)), -3 * math.pi / 4, 0.0),
        (Euler((0.0, 0, 0)), None, 1.5533430342749532),               # top view: height clamped
    ]
    for eul, az, el in cases:
        camrig.view_from_region(sc, eul.to_quaternion())
        if az is not None:
            assert abs(sh.movie_view_angle - az) < 1e-5, (tuple(eul), sh.movie_view_angle, az)
        assert abs(sh.movie_view_height - el) < 1e-5, (tuple(eul), sh.movie_view_height, el)
    camrig.view_from_region(sc, Euler((math.pi / 3, 0, math.pi / 4)).to_quaternion())
    back = Vector(camrig.rig(sc)["camera"].matrix_world.col[2][:3])
    want = Euler((math.pi / 3, 0, math.pi / 4)).to_quaternion() @ Vector((0, 0, 1))
    assert (back - want).length < 1e-4, "the camera looks the way the viewport did"


def test_lights_point_at_model():
    ob = build(location=(5, 3, -2))
    sc = C.scene
    sc.hindsight.movie_mode = True
    r = camrig.rig(sc)
    pts = world_points(ob)
    center = Vector(((pts.min(0) + pts.max(0)) / 2).tolist())
    cam_fwd = -Vector(r["camera"].matrix_world.col[2][:3])
    for role in ("key", "fill", "rim"):
        lo = r[role]
        travel = -Vector(lo.matrix_world.col[2][:3]).normalized()
        to_center = center - lo.matrix_world.translation
        assert travel.dot(to_center) > 0, role
        assert lo.data.energy > 0, role
    key_travel = -Vector(r["key"].matrix_world.col[2][:3])
    assert key_travel.dot(cam_fwd) > 0, "the key light lights the side the camera sees"


def test_edit_mode_and_object_mode():
    ob = build()
    sc = C.scene
    before = hist_state()
    H.enter(ob, "EDIT")
    sc.hindsight.movie_mode = True
    assert set(camrig.rig(sc)) == ROLES
    assert ob.mode == "EDIT"
    check_in_frame([ob], msg="edit mode")
    assert bpy.ops.hindsight.movie_frame() == {'FINISHED'}
    check_in_frame([ob], msg="edit mode reframe")
    bpy.ops.object.mode_set(mode="OBJECT")
    assert bpy.ops.hindsight.movie_frame() == {'FINISHED'}
    check_in_frame([ob], msg="object mode")
    assert hist_state() == before


def test_nothing_to_frame():
    H.fresh()
    sc = C.scene
    runtime.set_global(C, True)
    sc.hindsight.movie_mode = True
    r = camrig.rig(sc)
    assert set(r) == ROLES
    assert camrig.frame(sc) is False
    loc = r["camera"].matrix_world.translation
    assert abs(loc.length - 10.0) < 1e-4, loc
    assert bpy.ops.hindsight.movie_frame() == {'FINISHED'}


def test_model_choice_without_history():
    H.fresh()
    sc = C.scene
    a = H.make_mesh(300, "A", (0, 0, 0))
    b = H.make_mesh(300, "B", (6, 0, 0))
    b.hide_render = True
    assert camrig.model_objects(sc) == [a]
    a.hide_render = True
    assert camrig.model_objects(sc) == []
    e = bpy.data.objects.new("E", None)
    sc.collection.objects.link(e)
    assert camrig.model_objects(sc) == [e]


def test_unregister_twice():
    H.fresh()
    camrig.unregister()
    camrig.unregister()
    assert not hasattr(bpy.types, "HINDSIGHT_OT_movie_frame")
    camrig.register()
    assert hasattr(bpy.types, "HINDSIGHT_OT_movie_frame")


for f in [test_empty_scene_creates_rig_and_fits, test_far_transformed_model_fits,
          test_model_change_and_operator_reframes, test_quick_auto_key_frames_the_movie,
          test_earlier_bigger_state_stays_in_frame,
          test_own_camera_and_light_untouched, test_tick_puts_missing_pieces_back,
          test_movie_off_removes_everything, test_duplicate_loses_mark, test_view_from_region,
          test_lights_point_at_model, test_edit_mode_and_object_mode, test_nothing_to_frame,
          test_model_choice_without_history, test_unregister_twice]:
    H.run(f)
H.finish()
