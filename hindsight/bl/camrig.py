"""Movie Mode camera and lighting.

While Movie Mode is on, Hindsight keeps its own camera with the whole model in frame and, when the
scene has no light of its own, three sun lamps. They are ordinary objects marked with rigmark, made
with bpy.data only (works from a timer, a property update and in any mode). Objects without the mark
are never moved, hidden or deleted. Nothing here records or moves history.
"""
import math
import time

import bpy
import numpy as np
from mathutils import Matrix, Vector

from ..core import framing
from . import guard, log, rigmark, runtime

NAMES = {"camera": "Hindsight Camera", "key": "Hindsight Key", "fill": "Hindsight Fill", "rim": "Hindsight Rim"}
LIGHT_ROLES = ("key", "fill", "rim")
FEATURE = "movie_camera"
INTERVAL = 0.5
MARGIN = 0.10          # share of the frame left free around the model
_LAST = [0.0]          # time.monotonic() of the last real check in tick()
_ANGLE_RANGE = (-math.pi, math.pi)
_HEIGHT_RANGE = (-1.5533430342749532, 1.5533430342749532)


def _props(scene):
    return getattr(scene, "hindsight", None) if scene is not None else None


# ----------------------------------------------------------------------------- what exists
def rig(scene) -> dict:
    """role -> object for Hindsight's marked objects in this scene. A second object with the same
    role (a duplicate the user made) loses its mark and becomes the user's own object."""
    found = {}
    for ob in scene.objects:
        role = rigmark.role_of(ob)
        if role is not None:
            found.setdefault(role, []).append(ob)
    out = {}
    for role, obs in found.items():
        keep = obs[0]
        if len(obs) > 1:
            keep = next((o for o in obs if o.name == NAMES[role]), obs[0])
            with runtime.suspended():
                for o in obs:
                    if o is not keep:
                        rigmark.unmark(o)
        out[role] = keep
    return out


def scene_has_own_light(scene) -> bool:
    for ob in scene.objects:
        if ob.type == "LIGHT" and not ob.hide_render and not rigmark.is_rig(ob):
            return True
    return False


def wanted(scene) -> set:
    sh = _props(scene)
    if sh is None or not sh.movie_mode:
        return set()
    out = set()
    if sh.movie_camera:
        out.add("camera")
    if sh.movie_lighting and not scene_has_own_light(scene):
        out.update(LIGHT_ROLES)
    return out


def needs_attention(scene) -> bool:
    want = wanted(scene)
    have = rig(scene)
    if want != set(have):
        return True
    return "camera" in want and scene.camera is not have.get("camera")


def model_objects(scene) -> list:
    """Mesh objects with a Hindsight history; else meshes that render; else anything else that renders
    (never cameras, lights or Hindsight's own objects)."""
    members = set()
    for h in runtime.reg().all():
        members.update(h.members)
    obs = [ob for ob in scene.objects if not rigmark.is_rig(ob)]
    out = []
    if members:
        out = [ob for ob in obs if ob.type == "MESH" and runtime.obj_uid(ob) in members]
    if not out:
        out = [ob for ob in obs if ob.type == "MESH" and not ob.hide_render]
    if not out:
        out = [ob for ob in obs if ob.type not in ("CAMERA", "LIGHT") and not ob.hide_render]
    return out


# ----------------------------------------------------------------------------- create / remove
def _create(scene, role):
    name = NAMES[role]
    coll = bpy.data.cameras if role == "camera" else bpy.data.lights
    data = coll.get(name)   # left behind when the object was deleted: reuse it instead of piling up
    if data is None or data.users != 0 or data.library is not None or \
            (role != "camera" and data.type != "SUN"):
        data = bpy.data.cameras.new(name) if role == "camera" else bpy.data.lights.new(name, type="SUN")
    ob = bpy.data.objects.new(name, data)
    rigmark.mark(ob, role)
    scene.collection.objects.link(ob)
    return ob


def _delete(ob) -> None:
    data = ob.data
    bpy.data.objects.remove(ob, do_unlink=True)
    if data is None:
        return
    try:
        if data.users == 0:
            if isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
    except ReferenceError:
        pass


def _take_camera(scene, cam) -> None:
    sh = scene.hindsight
    cur = scene.camera
    if cur is cam:
        return
    if cur is not None and not rigmark.is_rig(cur):
        sh.movie_prev_camera = cur.name
    elif cur is not None:
        sh.movie_prev_camera = ""
    # cur is None: our camera was deleted and is coming back, or there never was one; keep the name
    scene.camera = cam


def _give_camera_back(scene) -> None:
    sh = _props(scene)
    if sh is None:
        return
    name = sh.movie_prev_camera
    ob = bpy.data.objects.get(name) if name else None
    if ob is not None and ob.type == "CAMERA" and scene.objects.get(ob.name) is ob:
        scene.camera = ob
    sh.movie_prev_camera = ""


def ensure(scene, reframe: bool = True) -> dict:
    """Create the wanted pieces, remove the others, hand the scene camera over."""
    res = {"created": [], "removed": [], "framed": False}
    if _props(scene) is None:
        return res
    want = wanted(scene)
    have = rig(scene)
    if not want:
        _PAST.clear()
    with runtime.suspended():
        for role in rigmark.ROLES:
            if role in have and role not in want:
                ob = have.pop(role)
                was_scene_cam = role == "camera" and (scene.camera is ob or scene.camera is None)
                _delete(ob)
                res["removed"].append(role)
                if was_scene_cam:
                    _give_camera_back(scene)
        for role in rigmark.ROLES:
            if role in want and role not in have:
                have[role] = _create(scene, role)
                res["created"].append(role)
        if "camera" in have:
            _take_camera(scene, have["camera"])
    if have and (reframe or res["created"]):
        res["framed"] = frame(scene)
    return res


def remove(scene) -> int:
    """Take all of Hindsight's objects out of the scene and give the scene its camera back."""
    have = rig(scene)
    n = 0
    with runtime.suspended():
        cam = have.get("camera")
        was_scene_cam = cam is not None and (scene.camera is cam or scene.camera is None)
        for ob in have.values():
            _delete(ob)
            n += 1
        if was_scene_cam:
            _give_camera_back(scene)
    return n


# ----------------------------------------------------------------------------- framing
def _np_matrix(m) -> np.ndarray:
    return np.array([list(row) for row in m], dtype=np.float64)


_PAST = {}             # (history uid, object uid) -> (signature, extents of the mesh's other history positions)


def _history_extents(ob, uid, local_basis, tx, ty):
    """Extents of every earlier and later shape of this mesh (framing.history_extents), in the
    mesh's own space. One walk over the history, kept until the history, the object's transform or
    the view changes."""
    h = runtime.history_of(ob) if uid is not None else None
    if h is None or not h.entries or ob.mode == "EDIT":
        return None
    sig = (len(h.entries), h.entries[0].seq, h.entries[-1].seq, local_basis.tobytes(), tx, ty)
    hit = _PAST.get((h.uid, uid))
    if hit is None or hit[0] != sig:
        st = runtime._STATES.get(uid) or runtime.ensure_state(ob)
        ext = framing.history_extents(h, st, uid, local_basis, tx, ty) if st is not None and st.counts else None
        hit = _PAST[(h.uid, uid)] = (sig, ext)
    return hit[1]


def _measure(ob, basis, tx, ty):
    """(extents in camera axes, world bounds) of one model object: its shape now and, for a mesh
    with a history, every earlier and later shape. The mesh is measured in its own space, so no
    world-space copy of it is made."""
    mw = _np_matrix(ob.matrix_world)
    local_basis = mw[:3, :3].T @ basis                 # local points @ local_basis == world @ basis
    if ob.type == "MESH" and ob.mode != "EDIT" and ob.data is not None:
        attr = ob.data.attributes.get("position")
        pts = np.empty((len(attr.data) if attr is not None else 0) * 3, np.float32)
        if pts.size:
            attr.data.foreach_get("vector", pts)
    else:
        pts = np.array([c[:] for c in ob.bound_box], np.float32)
    ext = framing.extents(pts, local_basis, tx, ty)
    if ob.type == "MESH":
        ext = framing.merge(ext, _history_extents(ob, runtime.obj_uid(ob), local_basis, tx, ty))
    bnd = None
    box = framing.bounds(pts)
    if box is not None:                                # the 8 corners of its box, in world space
        lo, hi = box
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        bnd = framing.bounds(corners @ mw[:3, :3].T + mw[:3, 3])
    return framing.shifted(ext, mw[:3, 3] @ basis, tx, ty), bnd


def _tangents(scene, cam_ob):
    cd = cam_ob.data if cam_ob is not None else None
    r = scene.render
    lens = cd.lens if cd is not None else 50.0
    sw = cd.sensor_width if cd is not None else 36.0
    sht = cd.sensor_height if cd is not None else 24.0
    fit_mode = cd.sensor_fit if cd is not None else "AUTO"
    tx, ty = framing.lens_tangents(lens, sw, sht, fit_mode, r.resolution_x, r.resolution_y,
                                   r.pixel_aspect_x, r.pixel_aspect_y)
    return framing.shrink(tx, ty, MARGIN)


def _place_lights(have, basis, center, radius) -> None:
    for spec in framing.light_rig(basis, np.asarray(center, np.float64), float(radius)):
        ob = have.get(spec["role"])
        if ob is None or ob.data is None:
            continue
        d = Vector(tuple(float(x) for x in spec["direction"]))
        if d.length < 1e-12:
            continue
        m = d.normalized().to_track_quat('-Z', 'Y').to_matrix().to_4x4()
        m.translation = Vector(tuple(float(x) for x in spec["location"]))
        ob.matrix_world = m
        ld = ob.data
        ld.energy = float(spec["strength"])
        if hasattr(ld, "angle"):
            ld.angle = float(spec["angle"])
        ld.color = tuple(float(c) for c in spec["color"])[:3]


def _basis_matrix(basis, location) -> Matrix:
    b = np.asarray(basis, np.float64)
    m = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            m[i][j] = float(b[i, j])
        m[i][3] = float(location[i])
    return m


def frame(scene) -> bool:
    """Place the camera and lighting around the model. False when there is nothing to frame."""
    sh = _props(scene)
    if sh is None:
        return False
    have = rig(scene)
    cam = have.get("camera")
    basis = np.asarray(framing.view_basis(sh.movie_view_angle, sh.movie_view_height), np.float64)
    tx, ty = _tangents(scene, cam)
    ext = None
    bnd = None
    for ob in model_objects(scene):
        e, b = _measure(ob, basis, tx, ty)
        ext = framing.merge(ext, e)
        bnd = framing.merge_bounds(bnd, b)
    fitted = framing.fit(ext, basis, tx, ty) if ext is not None else None
    with runtime.suspended():
        if fitted is None or bnd is None:
            back = basis[:, 2]
            if cam is not None:
                cam.matrix_world = _basis_matrix(basis, back * 10.0)
                _camera_data(cam.data, 1.0, 11.0)
            _place_lights(have, basis, (0.0, 0.0, 0.0), 1.0)
            return False
        if cam is not None:
            cam.matrix_world = _basis_matrix(basis, fitted["location"])
            _camera_data(cam.data, float(fitted["near"]), float(fitted["far"]))
        lo, hi = np.asarray(bnd[0], np.float64), np.asarray(bnd[1], np.float64)
        center = (lo + hi) * 0.5
        radius = max(float(np.linalg.norm(hi - lo)) * 0.5, 1e-3)
        _place_lights(have, basis, center, radius)
    return True


def _camera_data(cd, near, far) -> None:
    cd.type = 'PERSP'
    clip_start = max(near * 0.1, 1e-4)      # well in front of the model, whatever its size
    cd.clip_start = clip_start
    cd.clip_end = max(far * 2.0, clip_start * 10.0)
    try:
        cd.dof.use_dof = False
    except AttributeError:
        pass


# ----------------------------------------------------------------------------- entry points
_HOLD = [False]         # True while several settings are set at once (one framing at the end)


@guard.guarded(FEATURE)
def on_settings_changed(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None or _props(scene) is None or _HOLD[0]:
        return
    ensure(scene, reframe=True)
    runtime.tag_redraw_all()


def _render_running() -> bool:
    try:
        return bool(bpy.app.is_job_running('RENDER'))
    except Exception:
        return False


def _playing() -> bool:
    try:
        scr = bpy.context.screen
        return bool(scr is not None and scr.is_animation_playing)
    except Exception:
        return False


@guard.guarded(FEATURE)
def tick() -> None:
    """Put back missing pieces (deleted by the user or by Blender's undo). Rate limited."""
    now = time.monotonic()
    if now - _LAST[0] < INTERVAL:
        return
    _LAST[0] = now
    scene = getattr(bpy.context, "scene", None)
    sh = _props(scene)
    if sh is None or not sh.movie_mode:     # nothing to keep in place: costs nothing while off
        return
    if runtime.IN_HANDLER[0] or _render_running() or _playing():
        return
    if needs_attention(scene):
        res = ensure(scene, reframe=False)
        if res["created"] or res["removed"]:
            log.info(f"Movie Mode camera and lighting: added {res['created']}, removed {res['removed']}")
            runtime.tag_redraw_all()


def view_from_region(scene, view_rotation) -> None:
    """Set the view angle and height from a viewport rotation (RegionView3D.view_rotation)."""
    back = view_rotation @ Vector((0.0, 0.0, 1.0))
    angle = math.atan2(back.x, -back.y)
    height = math.asin(max(-1.0, min(1.0, back.z)))
    sh = scene.hindsight
    _HOLD[0] = True
    try:
        sh.movie_view_angle = max(_ANGLE_RANGE[0], min(_ANGLE_RANGE[1], angle))
    finally:
        _HOLD[0] = False
    sh.movie_view_height = max(_HEIGHT_RANGE[0], min(_HEIGHT_RANGE[1], height))   # frames once


# ----------------------------------------------------------------------------- operators
class HINDSIGHT_OT_movie_frame(bpy.types.Operator):
    """Move the Movie Mode camera so the whole model is in frame"""
    bl_idname = "hindsight.movie_frame"
    bl_label = "Frame the model"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        sh = _props(getattr(context, "scene", None))
        if sh is None or not sh.movie_mode:
            cls.poll_message_set("Turn on Movie Mode first")
            return False
        return True

    def execute(self, context):
        res = ensure(context.scene, reframe=True)
        runtime.tag_redraw_all()
        if not res["framed"]:
            self.report({'WARNING'}, "Nothing to frame yet")
        return {'FINISHED'}


class HINDSIGHT_OT_movie_view_from_viewport(bpy.types.Operator):
    """Point the Movie Mode camera from the direction this viewport looks"""
    bl_idname = "hindsight.movie_view_from_viewport"
    bl_label = "Use this view"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        sd = getattr(context, "space_data", None)
        if sd is None or sd.type != 'VIEW_3D' or getattr(sd, "region_3d", None) is None:
            cls.poll_message_set("Needs a 3D viewport")
            return False
        return _props(getattr(context, "scene", None)) is not None

    def execute(self, context):
        view_from_region(context.scene, context.space_data.region_3d.view_rotation)
        return {'FINISHED'}


classes = (HINDSIGHT_OT_movie_frame, HINDSIGHT_OT_movie_view_from_viewport)


def register():
    _LAST[0] = 0.0
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
