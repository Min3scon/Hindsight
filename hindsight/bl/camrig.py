"""Movie Mode camera and lighting.

While Movie Mode is on, Hindsight keeps its own camera with the whole model in frame and, when the
scene has no light of its own, three sun lamps. They are ordinary objects marked with rigmark, made
with bpy.data only (works from a timer, a property update and in any mode). Objects without the mark
are never moved, hidden or deleted. Nothing here records or moves history.

What the camera holds (the model as a render shows it, at every step of the history): every
object with a history that renders (renders()), of any type; other types than meshes as they render
now (_shape: exact; a bound for volumes, 4.2's Grease Pencil, hair curves (drawn with flat ends) and
points of different radii). bounded() names what the last placement held by a bound. For meshes:
- the shape now: exact, from the evaluated object; when the viewport doesn't evaluate it the way a
  render does (render-only modifiers, other render levels, disabled in viewports) from a temporary
  copy set up for rendering, unless that copy would pass COPY_BUDGET vertices;
- the other steps (only the base mesh is recorded): exact for no modifier, Mirror and fixed-count
  Array (framing.envelope), for an Armature on vertex groups without B-Bones, and for shape keys
  (_measure_deformed); a bound from the stretch measured now for the other modifiers that only move
  vertices (Lattice, Hook, Curve, ...); a bound from the reach measured now, grown with the history's
  size, for every other modifier (Subdivision, Multires, Solidify, Displace, Geometry Nodes, ...).
Known limits: those last two are estimates, not proofs (a Geometry Nodes tree can do anything); a
render copy over COPY_BUDGET is not made; the history walk stops at a Join or a step it can't read,
as History.goto does; Simplify's render settings are not read.
"""
import contextlib
import math
import time

import bpy

from . import reg
import numpy as np
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector

from ..core import framing
from ..core.types import POSITION_KEY
from . import guard, log, meshio, rigmark, runtime

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
    prop = rigmark.RIG_PROP
    for ob in [o for o in scene.objects[:] if prop in o]:      # the C-level test first: called twice a second
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


def _render_layers(scene) -> list:
    """The view layers a render of the scene renders (all of them when none is switched on)."""
    vls = list(scene.view_layers)
    return [vl for vl in vls if vl.use] or vls


def _rendered_objects(scene) -> set:
    """as_pointer() of every object in a collection that renders in a view layer the render uses."""
    return {o.as_pointer() for vl in _render_layers(scene) for c in _rendering_collections(vl) for o in c.objects}


def _rendering_collections(view_layer) -> list:
    """The collections whose objects render in this view layer: reached from the scene's
    collection through collections that are neither excluded from the view layer nor disabled in
    renders."""
    out = []
    todo = [view_layer.layer_collection]
    while todo:
        lc = todo.pop()
        coll = lc.collection
        if lc.exclude or getattr(coll, "hide_render", False):
            continue
        out.append(coll)
        todo.extend(lc.children)
    return out


_WORLD_LIGHT_NODES = ("ShaderNodeTexEnvironment", "ShaderNodeTexSky")


def world_lights(scene) -> bool:
    """True when the scene's world is an image or a sky (an Environment or Sky Texture node feeds
    its active World Output, not through a Background or Emission of strength 0): that lights a
    render. A plain colour (the default grey world) doesn't count, whatever its strength."""
    w = getattr(scene, "world", None)
    if w is None or w.node_tree is None:
        return False
    if bpy.app.version < (5, 0, 0) and not w.use_nodes:     # Blender 5 always uses the nodes
        return False
    nodes = w.node_tree.nodes
    outs = [n for n in nodes if n.bl_idname == "ShaderNodeOutputWorld"]
    outs = [n for n in outs if n.is_active_output] or outs
    todo = [n for n in outs]
    seen = set()
    while todo:
        n = todo.pop()
        if n.as_pointer() in seen:
            continue
        seen.add(n.as_pointer())
        if n.bl_idname in _WORLD_LIGHT_NODES and not n.mute and \
                (n.bl_idname != "ShaderNodeTexEnvironment" or n.image is not None):
            return True
        if n.bl_idname in ("ShaderNodeBackground", "ShaderNodeEmission") and not n.mute:
            st = n.inputs.get("Strength")
            if st is not None and not st.is_linked and st.default_value <= 0.0:
                continue                    # strength 0: what feeds it lights nothing
        for sock in n.inputs:
            for link in sock.links:
                if link.is_valid and not getattr(link, "is_muted", False):
                    todo.append(link.from_node)
    return False


def _lamp_lights(ob) -> bool:
    ld = ob.data
    try:
        return ld is not None and ld.energy > 0.0 and max(ld.color) > 0.0
    except (AttributeError, TypeError):
        return ld is not None


def scene_has_own_light(scene) -> bool:
    """True when something of the user's lights a render of this scene: a light object that renders
    (not disabled in renders, energy above zero, in a collection that renders in a view layer the
    render uses), or a world with an image or a sky (`world_lights`). Lights that only exist inside
    an instanced collection, and meshes with an emission shader, don't count."""
    lamps = [ob for ob in scene.objects[:]
             if ob.type == "LIGHT" and not ob.hide_render and not rigmark.is_rig(ob) and _lamp_lights(ob)]
    if lamps:
        for vl in _render_layers(scene):
            for coll in _rendering_collections(vl):
                objs = coll.objects
                for ob in lamps:
                    hit = objs.get(ob.name)
                    if hit == ob or (hit is not None and any(o == ob for o in objs)):
                        return True
    return world_lights(scene)


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
    # the scene has no camera (ours was deleted and put back). A camera the user picked while Movie
    # Mode is on is their choice: it stays.
    return "camera" in want and scene.camera is None


_RENDERS = {"MESH", "CURVE", "SURFACE", "FONT", "META", "GPENCIL", "GREASEPENCIL", "CURVES", "POINTCLOUD", "VOLUME"}


def renders(ob) -> bool:
    """The object shows in a render: a type with geometry, or an empty instancing a collection."""
    if ob.hide_render or rigmark.is_rig(ob):
        return False
    if ob.type == "EMPTY":
        return ob.instance_type == "COLLECTION" and ob.instance_collection is not None
    return ob.type in _RENDERS


def model_objects(scene) -> list:
    """Objects with a Hindsight history that render, of any type; else meshes that render; else
    anything else that renders (never cameras, lights or Hindsight's own objects)."""
    members = set()
    for h in runtime.reg().all():
        members.update(h.members)
    obs = [ob for ob in scene.objects if not rigmark.is_rig(ob)]
    out = []
    if members:
        out = [ob for ob in obs if renders(ob) and runtime.any_uid(ob) in members]
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
    with contextlib.suppress(ReferenceError):
        if data.users == 0:
            if isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)


# The user's camera is remembered as the object itself (scene.hindsight.movie_user_camera), so a
# rename doesn't lose it. That pointer counts as a user of the object: it is cleared as soon as the
# camera is given back, and as soon as the object is in no scene any more (deleted), so it never
# keeps a deleted camera alive in a saved file. Files saved by earlier builds only have the name
# (movie_prev_camera): it is read when the pointer is empty.

def _deleted(ob) -> bool:
    try:
        return not ob.users_scene and not ob.use_fake_user
    except ReferenceError:
        return True


def user_camera(scene):
    """The user's camera that Hindsight's camera stands in for, or None."""
    sh = _props(scene)
    if sh is None:
        return None
    ob = getattr(sh, "movie_user_camera", None)
    if ob is None and sh.movie_prev_camera:
        ob = bpy.data.objects.get(sh.movie_prev_camera)     # a file from an earlier build: local by name
    if ob is None or rigmark.is_rig(ob) or _deleted(ob):
        return None
    return ob


def _remember(scene, ob) -> None:
    sh = scene.hindsight
    if getattr(sh, "movie_user_camera", None) is not ob:
        sh.movie_user_camera = ob
    if sh.movie_prev_camera:
        sh.movie_prev_camera = ""


def _forget(scene) -> None:
    sh = _props(scene)
    if sh is None:
        return
    if getattr(sh, "movie_user_camera", None) is not None:
        sh.movie_user_camera = None
    if sh.movie_prev_camera:
        sh.movie_prev_camera = ""


def keep_user_camera(scene) -> None:
    """Drop the memory of a camera that was deleted (so the pointer doesn't keep it in the file) and
    turn a name from an earlier build into the object."""
    sh = _props(scene)
    if sh is None:
        return
    ob = getattr(sh, "movie_user_camera", None)
    if ob is not None:
        if _deleted(ob):
            sh.movie_user_camera = None
        return
    if sh.movie_prev_camera:
        found = user_camera(scene)
        if found is not None:
            _remember(scene, found)


def _take_camera(scene, cam, from_user_cam: bool) -> None:
    """Make our camera the scene camera. The camera the scene had is remembered when Movie Mode (or
    its Camera switch) was just turned on (`from_user_cam`). Otherwise a camera of the user's that is
    the scene camera was picked by them while Movie Mode was on, and it stays."""
    cur = scene.camera
    if cur is cam:
        return
    if cur is not None and not rigmark.is_rig(cur):
        if not from_user_cam:
            return
        _remember(scene, cur)
    # cur is None: our camera was deleted and is coming back, or there never was one; keep the memory
    scene.camera = cam


def _give_camera_back(scene, restore: bool) -> None:
    """Forget the user's camera; first make it the scene camera again when `restore` (the scene
    camera was ours, or empty) and it still exists."""
    if restore:
        ob = user_camera(scene)
        if ob is not None:
            scene.camera = ob
    _forget(scene)


def ensure(scene, reframe: bool = True, take: bool = False) -> dict:
    """Create the wanted pieces, remove the others, hand the scene camera over. `take`: a setting
    was just changed by the user, so a camera created now takes over from the scene's camera."""
    res = {"created": [], "removed": [], "framed": False}
    if _props(scene) is None:
        return res
    want = wanted(scene)
    have = rig(scene)
    if not want:
        _PAST.clear()
        _REACH.clear()
    with runtime.suspended():
        cam = have.get("camera")
        ours = scene.camera is None or (cam is not None and scene.camera is cam)
        for role in rigmark.ROLES:
            if role in have and role not in want:
                _delete(have.pop(role))
                res["removed"].append(role)
        if "camera" not in want:
            sh = scene.hindsight
            if getattr(sh, "movie_user_camera", None) is not None or sh.movie_prev_camera:
                _give_camera_back(scene, ours)
        for role in rigmark.ROLES:
            if role in want and role not in have:
                have[role] = _create(scene, role)
                res["created"].append(role)
        if "camera" in have:
            _take_camera(scene, have["camera"], take and "camera" in res["created"])
    if have and (reframe or res["created"]):
        if runtime.IN_HANDLER[0] and not reframe and "camera" not in res["created"]:
            # #117 review 1: the movie's frame handler made the lamps. It can't walk the history, so the
            # camera keeps its shot and the lamps go where the last framing put them (a sun lights the
            # same from anywhere: only their place in the viewport would differ).
            sh = scene.hindsight
            basis = np.asarray(framing.view_basis(sh.movie_view_angle, sh.movie_view_height), np.float64)
            with runtime.suspended():
                _place_lights(have, basis, *_LIT.get(scene.name_full, ((0.0, 0.0, 0.0), 1.0)))
        else:
            res["framed"] = frame(scene)
    return res


def remove(scene) -> int:
    """Take all of Hindsight's objects out of the scene and give the scene its camera back."""
    have = rig(scene)
    n = 0
    with runtime.suspended():
        cam = have.get("camera")
        ours = scene.camera is None or (cam is not None and scene.camera is cam)
        for ob in have.values():
            _delete(ob)
            n += 1
        _give_camera_back(scene, ours)
    return n


# ----------------------------------------------------------------------------- framing
def _np_matrix(m) -> np.ndarray:
    return np.array([list(row) for row in m], dtype=np.float64)


_PAST = {}             # (history uid, object uid) -> (signature, extents of the mesh's other history positions)
_BOUND_HIT = [False]   # set by a measure that held the object being measured by a safe bound, not exactly
_BOUNDED = {}          # scene name -> names of the objects the last placement held by a safe bound
_COUNTED = {}          # scene name -> names of the objects without a history the last walk counted (D181)
_LIT = {}              # scene name -> (center, radius) the last placement put the lamps around


def bounded(scene) -> tuple:
    """#114: names of the objects the camera's last placement held by a safe bound (never cut off, not
    an exact measure, so the frame can be looser than tight); the panel says so."""
    return _BOUNDED.get(scene.name_full, ())


def _history_extents(ob, uid, bases, tans):
    """(k, 6) extents of every shape of this mesh's history, the one at the cursor included
    (framing.history_extents_multi), for k bases, in the mesh's own space. One walk over the
    history, kept until the history, the object's transform or the view changes. It holds every
    position, so moving the cursor (scrubbing) doesn't make it stale."""
    h = runtime.history_of(ob) if uid is not None else None
    if h is None or not h.entries:
        return None
    sig = (len(h.entries), h.entries[0].seq, h.entries[-1].seq,
           np.asarray(bases, np.float64).tobytes(), np.asarray(tans, np.float64).tobytes())
    hit = _PAST.get((h.uid, uid))
    if hit is None or hit[0] != sig:
        st = runtime._STATES.get(uid) or runtime.ensure_state(ob)
        ext = None
        if st is not None and st.counts:
            ext = framing.history_extents_multi(h, st, uid, bases, tans)
            pos = st.light.get(POSITION_KEY)
            here = framing.extents_multi(pos, bases, tans) if pos is not None else None
            if ext is None or here is None:
                ext = here if ext is None else ext
            else:
                lo = np.minimum(ext[:, 4], here[:, 4])
                ext = np.maximum(ext, here)
                ext[:, 4] = lo
        hit = _PAST[(h.uid, uid)] = (sig, ext)
    return hit[1]


def _positions(me) -> np.ndarray:
    """(n*3,) float32 vertex positions of a mesh (its own space)."""
    attr = me.attributes.get("position") if me is not None else None
    pts = np.empty((len(attr.data) if attr is not None else 0) * 3, np.float32)
    if pts.size:
        attr.data.foreach_get("vector", pts)
    return pts


def _live(m) -> bool:
    return bool(m.show_viewport or m.show_render)


def has_stack(ob) -> bool:
    """True when what renders isn't just the base mesh: a modifier that is on (in the viewport or
    for renders) or shape keys."""
    if ob.type != "MESH" or ob.data is None:
        return False
    return getattr(ob.data, "shape_keys", None) is not None or any(_live(m) for m in ob.modifiers)


def stack_of(ob) -> list:
    """The modifier stack as framing.envelope reads it: Mirror and fixed-count Array exactly, every
    other modifier as ("OTHER", type) (left to the measured reach)."""
    out = []
    inv = None
    for m in ob.modifiers:
        if not _live(m):
            continue
        if m.type == "MIRROR":
            fr = np.eye(4)
            mo = m.mirror_object
            if mo is not None:
                if inv is None:
                    inv = np.linalg.pinv(_np_matrix(ob.matrix_world))
                fr = inv @ _np_matrix(mo.matrix_world)
            out.append(("MIRROR", tuple(bool(a) for a in m.use_axis), fr))
        elif m.type == "ARRAY" and m.fit_type == "FIXED_COUNT" and not m.use_object_offset \
                and m.start_cap is None and m.end_cap is None:
            const = np.array(m.constant_offset_displace[:], np.float64) if m.use_constant_offset else np.zeros(3)
            rel = np.array(m.relative_offset_displace[:], np.float64) if m.use_relative_offset else np.zeros(3)
            out.append(("ARRAY", int(m.count), const, rel))
        else:
            out.append(("OTHER", m.type))
    return out


def _depsgraph(scene, refresh: bool = False):
    """The evaluated depsgraph of the scene's view layer, or None. `refresh`: evaluate what
    changed since (another scene's depsgraph is only evaluated on request)."""
    try:
        if getattr(bpy.context, "scene", None) == scene:
            return bpy.context.evaluated_depsgraph_get()
        vl = scene.view_layers[0] if len(scene.view_layers) else None
        dg = vl.depsgraph if vl is not None else None
        if dg is not None and refresh:
            dg.update()
        return dg
    except Exception:
        return None


_LEVELS = ("SUBSURF", "MULTIRES")


def renders_differently(ob) -> bool:
    """True when the viewport shows a different modifier stack than a render: a modifier on for
    renders but not in the viewport (or the other way round), or a Subdivision or Multires with
    other levels for renders. The viewport's evaluated mesh then isn't what renders."""
    for m in ob.modifiers:
        if bool(m.show_viewport) != bool(m.show_render):
            return True
        if m.show_render and m.type in _LEVELS:
            if m.levels != m.render_levels:
                return True
            if m.type == "MULTIRES" and ob.mode == "SCULPT" and m.sculpt_levels != m.render_levels:
                return True
    return False


def _render_copy_points(ob, scene, dg_getter):
    """What a render shows of `ob`, for a mesh the viewport doesn't evaluate the way a render does
    (`renders_differently`, or not evaluated at all: hidden in viewports, or in a collection
    switched off in viewports). A temporary copy of the object (sharing its mesh) with each
    modifier set as it is for renders is evaluated, measured and removed again; nothing of the
    user's is changed. None when it can't be done."""
    if scene is None:
        return None
    tmp = None
    with runtime.suspended():
        try:
            tmp = ob.copy()
            for key in (runtime.UID_PROP, rigmark.RIG_PROP):    # not a second object with our id
                if key in tmp:
                    del tmp[key]
            tmp.hide_viewport = False
            tmp.hide_render = False
            for m in tmp.modifiers:
                m.show_viewport = m.show_render
                if m.type in _LEVELS:
                    m.levels = m.render_levels
                    if m.type == "MULTIRES":
                        m.sculpt_levels = m.render_levels
            scene.collection.objects.link(tmp)
            dg = dg_getter(refresh=True)
            if dg is None:
                return None
            return _evaluated_points(tmp, dg, ob.matrix_world)
        except Exception as e:
            log.error_once(("camrig_render_copy", type(e).__name__), f"Movie Mode camera: {e}", e)
            return None
        finally:
            if tmp is not None:
                with contextlib.suppress(ReferenceError, RuntimeError):
                    bpy.data.objects.remove(tmp, do_unlink=True)
                dg_getter(refresh=True)           # evaluated now, while our own changes are ignored


COPY_BUDGET = 4_000_000     # vertices: a render copy that would be bigger isn't evaluated


def _copy_size(ob, ev) -> int:
    """About how many vertices the render copy evaluates: each level a Subdivision or Multires
    renders above what the viewport shows multiplies them by four."""
    n = max(len(ev.data.vertices) if ev.type == "MESH" and ev.data is not None else 0,
            len(ob.data.vertices) if ob.data is not None else 0)
    for m in ob.modifiers:
        if m.show_render and m.type in _LEVELS:
            shown = m.levels if m.show_viewport else 0
            n *= 4 ** max(0, m.render_levels - shown)
    return n


def _rendered_points(ob, scene, dg_getter):
    """(n, 3) float32 of what renders of this mesh object now, in its own space. A render copy
    over COPY_BUDGET vertices isn't made: the viewport's mesh is measured (see the module's
    known limits)."""
    dg = dg_getter() if dg_getter is not None else None
    if dg is not None:
        ev = ob.evaluated_get(dg)
        if ob.mode != "EDIT" and (not ev.is_evaluated or renders_differently(ob)) \
                and _copy_size(ob, ev) <= COPY_BUDGET:
            pts = _render_copy_points(ob, scene, dg_getter)
            if pts is not None:
                return pts
    return _evaluated_points(ob, dg)


def _evaluated_points(ob, dg, matrix_world=None) -> np.ndarray:
    """(n, 3) float32: what renders of this object now, in its own space: the evaluated mesh, and
    the corners of the boxes of the instances its Geometry Nodes make. Without a depsgraph, the
    corners of the object's box (Blender keeps the evaluated one on it)."""
    if dg is None:
        return np.array([c[:] for c in ob.bound_box], np.float32)
    ev = ob.evaluated_get(dg)
    if ob.mode == "EDIT" and ev.type == "MESH":
        try:                                # the evaluated mesh wraps the edited copy: read a real one
            pts = _positions(ev.to_mesh()).reshape(-1, 3)
        finally:
            ev.to_mesh_clear()
    else:
        pts = _positions(ev.data if ev.type == "MESH" else None).reshape(-1, 3)
    if any(m.type == "NODES" and _live(m) for m in ob.modifiers):
        inv = np.linalg.pinv(_np_matrix(ob.matrix_world if matrix_world is None else matrix_world))
        extra = []
        for inst in dg.object_instances:
            if not inst.is_instance or inst.parent is None or inst.parent.original != ob:
                continue
            box = np.array([c[:] for c in inst.object.bound_box], np.float64)
            m = inv @ _np_matrix(inst.matrix_world)
            extra.append(box @ m[:3, :3].T + m[:3, 3])
        if extra:
            pts = np.vstack([pts.astype(np.float64)] + extra).astype(np.float32)
    return pts


# Modifiers that only move vertices (same vertices, same order): the rendered shape of a past
# position is then measured vertex by vertex (_measure_deformed).
DEFORMERS = frozenset((
    "ARMATURE", "CAST", "CORRECTIVE_SMOOTH", "CURVE", "HOOK", "LAPLACIANDEFORM", "LAPLACIANSMOOTH",
    "LATTICE", "MESH_DEFORM", "SIMPLE_DEFORM", "SMOOTH", "SURFACE_DEFORM", "WARP", "WAVE",
    "NORMAL_EDIT", "WEIGHTED_NORMAL", "UV_PROJECT", "UV_WARP", "VERTEX_WEIGHT_EDIT",
    "VERTEX_WEIGHT_MIX", "VERTEX_WEIGHT_PROXIMITY"))


_NO_MOVE = frozenset(("NORMAL_EDIT", "WEIGHTED_NORMAL", "UV_PROJECT", "UV_WARP", "VERTEX_WEIGHT_EDIT",
                      "VERTEX_WEIGHT_MIX", "VERTEX_WEIGHT_PROXIMITY"))


def deforms_only(ob) -> bool:
    """True when every modifier that renders only moves vertices (shape keys too)."""
    return all(m.type in DEFORMERS for m in ob.modifiers if m.show_render)


def _bone_scale(m) -> float:
    """For an Armature modifier that moves each vertex by a fixed blend of its bones' transforms
    (vertex groups, not bone envelopes, whose weights change with the position; no B-Bones): the
    largest scale any deforming bone applies (1 for bones that only turn and move). None for any
    other modifier."""
    arm = getattr(m, "object", None) if m.type == "ARMATURE" else None
    if arm is None or arm.type != "ARMATURE" or arm.pose is None or not m.use_vertex_groups \
            or m.use_bone_envelopes:
        return None
    if any(pb.bone.use_deform and pb.bone.bbone_segments > 1 for pb in arm.pose.bones):
        return None                         # B-Bones bend: each segment moves on its own
    s = 1.0
    for pb in arm.pose.bones:
        if pb.bone.use_deform:
            d = _np_matrix(pb.matrix) @ np.linalg.pinv(_np_matrix(pb.bone.matrix_local))
            s = max(s, float(np.linalg.norm(d[:3, :3], 2)))
    return s * _similarity_error(arm, m.id_data)


def _similarity_error(arm, ob) -> float:
    """1 when the armature's and the mesh's transforms keep shapes (uniform scale); else how much
    the change between their spaces can stretch one direction against another."""
    m = np.linalg.pinv(_np_matrix(arm.matrix_world)) @ _np_matrix(ob.matrix_world)
    sv = np.linalg.svd(m[:3, :3], compute_uv=False)
    return float(sv[0] / sv[-1]) if sv[-1] > 1e-12 else 1e6


def _stretch(ob, ev, bs) -> float:
    """How far a vertex can render from where it renders now, per unit it moves in the base mesh:
    1 for shape keys, the bones' largest scale for an Armature on vertex groups (both exact), and
    for any other modifier that only moves vertices, an estimate: the largest ratio of an edge's
    rendered length to its length in the base mesh (edges shorter than a thousandth of the mesh's
    size left out)."""
    s = 1.0
    other = False
    for m in ob.modifiers:
        if not m.show_render or m.type not in DEFORMERS:
            continue
        b = _bone_scale(m)
        if b is not None:
            s *= b
        elif m.type not in _NO_MOVE:
            other = True
    if not other:
        return s
    me = ob.data
    ne = len(me.edges)
    if ne:
        # R076: the ".edge_verts" attribute (one copy) instead of me.edges (RNA edge by edge: 86 ms at 1M)
        ed = meshio._edge_verts(me, ne).reshape(-1, 2)
        with np.errstate(invalid="ignore", over="ignore", divide="ignore"):
            lb = np.linalg.norm(bs[ed[:, 0]].astype(np.float64) - bs[ed[:, 1]], axis=1)
            le = np.linalg.norm(ev[ed[:, 0]].astype(np.float64) - ev[ed[:, 1]], axis=1)
            box = framing.bounds(bs)
            size = float(np.linalg.norm(box[1] - box[0])) if box is not None else 0.0
            ok = np.isfinite(lb) & np.isfinite(le) & (lb > size * 1e-3)
            if ok.any():
                s = max(s, float(np.max(le[ok] / lb[ok])))
    return s


_REACH = {}            # (history uid, object uid) -> (signature, history_vertex_reach)


def _measure_deformed(ob, ev, bs, local_basis, tx, ty):
    """Extents (mesh space, camera axes) of every past and later rendered shape of a mesh whose
    modifiers only move its vertices, or None (no history, or one that changes the vertex count).

    A vertex that is `d` away from where it is now, somewhere in the history, renders at most
    `stretch * d` away from where it renders now (exact for an Armature: each vertex is moved by a
    fixed blend of its bones' transforms; for the others, the stretch measured now stands for
    theirs): the union of those balls holds every rendered position of the history."""
    uid = runtime.obj_uid(ob)
    h = runtime.history_of(ob) if uid is not None else None
    if h is None or not h.entries:
        return None
    st = runtime._STATES.get(uid) or runtime.ensure_state(ob)
    if st is None or not st.counts:
        return None
    sig = (len(h.entries), h.entries[0].seq, h.entries[-1].seq, h.cursor)
    hit = _REACH.get((h.uid, uid))
    if hit is None or hit[0] != sig:
        hit = _REACH[(h.uid, uid)] = (sig, framing.history_vertex_reach(h, st, uid))
    rho = hit[1]
    pos0 = st.light.get(POSITION_KEY)
    if rho is None or pos0 is None or len(rho) != len(bs) or pos0.size != bs.size:
        return None
    with np.errstate(invalid="ignore", over="ignore"):
        off = np.linalg.norm(np.asarray(pos0, np.float64).reshape(-1, 3) - bs, axis=1)
    off[~np.isfinite(off)] = 0.0
    r = (rho.astype(np.float64) + off) * _stretch(ob, ev, bs) * (1.0 + 1e-6)
    balls = framing.extents_radii(ev, r, local_basis, tx, ty)
    hull = _bone_hull(ob, uid, local_basis, tx, ty)
    if balls is None or hull is None:
        return balls
    out = np.minimum(balls, hull)            # both hold every rendered position: so does the tighter
    out[4] = max(balls[4], hull[4])
    return out


MAX_BONES = 16


def _bone_hull(ob, uid, local_basis, tx, ty):
    """Extents of every rendered position of the history for a mesh whose only modifier that moves
    vertices is one Armature on vertex groups (no shape keys, no Preserve Volume): each vertex then
    renders as a blend, with weights that add up to one, of its place moved by each of its bones
    (or stays put), so it lies in the hull of the history moved by each bone. None when that
    doesn't apply or there are more than MAX_BONES such bones."""
    if getattr(ob.data, "shape_keys", None) is not None:
        return None
    arms = [m for m in ob.modifiers if m.show_render and m.type in DEFORMERS and m.type not in _NO_MOVE]
    if len(arms) != 1 or arms[0].type != "ARMATURE":
        return None
    m = arms[0]
    arm = m.object
    if _bone_scale(m) is None or m.use_deform_preserve_volume or m.use_multi_modifier:
        return None
    names = set(ob.vertex_groups.keys())
    bones = [pb for pb in arm.pose.bones if pb.bone.use_deform and pb.name in names]
    if len(bones) > MAX_BONES:
        return None
    pre = np.linalg.pinv(_np_matrix(arm.matrix_world)) @ _np_matrix(ob.matrix_world)
    post = np.linalg.pinv(pre)
    mats = [np.eye(4)] + [post @ _np_matrix(pb.matrix) @ np.linalg.pinv(_np_matrix(pb.bone.matrix_local)) @ pre
                          for pb in bones]
    bases = np.stack([np.eye(3)] + [M[:3, :3].T @ local_basis for M in mats])
    tans = np.asarray([(0.0, 0.0)] + [(tx, ty)] * len(mats))
    past = _history_extents(ob, uid, bases, tans)
    if past is None:
        return None
    out = None
    for k, M in enumerate(mats):
        out = framing.merge(out, framing.plus(past[k + 1], framing.extents(M[:3, 3][None], local_basis, tx, ty)))
    return out


def _measure_stack(ob, base, local_basis, tx, ty, scene, dg_getter, now_only=False):
    """Extents (mesh space, camera axes) of a mesh with modifiers or shape keys, and its points now
    (`now_only`: of what renders now alone).

    Now: exact, from the evaluated object. Every other position of its history (only the base mesh
    is recorded): the envelope of the stack (framing.envelope: Mirror and Array exactly) around the
    base mesh there, grown by the reach the evaluated shape has past that envelope now, scaled up
    when the history's base mesh is bigger than now's."""
    ev = _rendered_points(ob, scene, dg_getter)
    if not len(ev):
        ev = base.reshape(-1, 3)
    ext = framing.extents(ev, local_basis, tx, ty)
    if now_only:
        return ext, ev
    bs = base.reshape(-1, 3)
    if len(ev) == len(bs) and ob.mode != "EDIT" and deforms_only(ob):
        moved = _measure_deformed(ob, ev, bs, local_basis, tx, ty)
        if moved is not None:
            _BOUND_HIT[0] = True                # balls around where each vertex renders now
            return framing.merge(ext, moved), ev
    box_now = framing.bounds(bs)
    if box_now is None:
        return ext, ev
    stack = stack_of(ob)
    groups = framing.envelope(stack, box_now[0], box_now[1], exact=True)
    if groups is None:                      # too many mirrored copies to follow: measured reach only
        stack = [s for s in stack if s[0] not in ("MIRROR", "ARRAY")]
        groups = framing.envelope(stack, box_now[0], box_now[1], exact=True)
    view = np.array([[1, 0, tx], [-1, 0, tx], [0, 1, ty], [0, -1, ty], [0, 0, 1], [0, 0, -1]], np.float64)
    r = framing.reach(ev, bs, groups, framing.directions(view @ local_basis.T))
    if len(ev) == len(bs) and len(groups) == 1 and len(groups[0][1]) == 1 and not groups[0][1].any():
        with np.errstate(invalid="ignore", over="ignore"):
            d = np.linalg.norm(ev.astype(np.float64) - bs, axis=1)   # same vertices, moved: shape keys, deform
        d = d[np.isfinite(d)]
        if len(d):
            r = max(r, float(d.max()))
    eye = np.eye(3)
    bases = [eye] + [np.asarray(L, np.float64).T @ local_basis for L, _Z in groups]
    tans = [(0.0, 0.0)] + [(tx, ty)] * len(groups)
    past = _history_extents(ob, runtime.obj_uid(ob), np.stack(bases), np.asarray(tans))
    if past is None:
        return ext, ev
    lo_h, hi_h = framing.box_from_extents(past[0])
    lo = np.minimum(lo_h, box_now[0])
    hi = np.maximum(hi_h, box_now[1])
    wide = framing.envelope(stack, lo, hi, exact=False)
    size_now = float(np.linalg.norm(box_now[1] - box_now[0]))
    size_all = float(np.linalg.norm(hi - lo))
    grow = 1.0 if size_all <= size_now else (size_all / size_now if size_now > 1e-12 else 1e6)
    if r > 0:                                   # the reach past the exact envelope: a bound
        _BOUND_HIT[0] = True
    ball = framing.ball_extents(r * grow, local_basis, tx, ty)
    for k, (_L, Z) in enumerate(wide):
        e = framing.plus(framing.plus(past[k + 1], framing.extents(Z, local_basis, tx, ty)), ball)
        ext = framing.merge(ext, e)
    return ext, ev


def _entry_positions(ob) -> np.ndarray:
    """In Edit Mode the mesh's own arrays are empty (Blender edits a copy): the positions it had
    on entering Edit Mode, which are the history's at its cursor."""
    uid = runtime.obj_uid(ob)
    st = runtime._STATES.get(uid) if uid is not None else None
    pos = st.light.get(POSITION_KEY) if st is not None else None
    return np.asarray(pos, np.float32).reshape(-1) if pos is not None else np.empty(0, np.float32)


DEFAULT_RADIUS = 0.01      # Blender's radius for curves, point clouds and Grease Pencil points that store none


def _shape(ev):
    """(points (n, 3) float32 in the object's own space, radius) of what renders of an evaluated
    object: the evaluated mesh of meshes, curves, text, surfaces and metaballs; the points of point
    clouds, hair curves and Grease Pencil (each visible layer's current drawing) with their largest
    radius; else (volumes, 4.2's Grease Pencil) the corners of the evaluated box, a safe bound. Each
    bound sets _BOUND_HIT."""
    t = ev.type
    if t == "CURVES":                  # #114: Cycles draws a hair's ends flat: the balls there are a bound
        _BOUND_HIT[0] = True
        return _hair_hull(ev.data), _radius(ev.data)
    if t == "POINTCLOUD":
        return _positions(ev.data).reshape(-1, 3), _radius(ev.data)
    if t == "GREASEPENCIL":
        pts, r = [np.empty((0, 3), np.float32)], 0.0
        for layer in ev.data.layers:
            fr = None if layer.hide else layer.current_frame()
            dr = getattr(fr, "drawing", None)
            if dr is None:
                continue
            p = _positions(dr).reshape(-1, 3)
            m = getattr(layer, "matrix_local", None)
            if m is not None and len(p):
                m = _np_matrix(m)
                p = (p @ m[:3, :3].T + m[:3, 3]).astype(np.float32)
                r = max(r, _radius(dr) * float(max(np.linalg.norm(m[:3, :3], axis=0))))
            elif len(p):
                r = max(r, _radius(dr))
            pts.append(p)
        return np.vstack(pts), r
    if t in ("MESH", "CURVE", "SURFACE", "FONT", "META"):
        try:
            me = ev.to_mesh()
            return _positions(me).reshape(-1, 3), 0.0
        except RuntimeError:
            return np.empty((0, 3), np.float32), 0.0
        finally:
            ev.to_mesh_clear()
    _BOUND_HIT[0] = True
    return np.array([c[:] for c in ev.bound_box], np.float32), 0.0


def _hair_hull(cv) -> np.ndarray:
    """D182: points whose hull holds the curve Cycles draws through each hair's points (Catmull-Rom,
    ends doubled): each point and its Bezier handles P +- (next - previous) / 6, plus the handles of
    Bezier curves."""
    p = _positions(cv).reshape(-1, 3)
    n = len(p)
    off = np.empty(len(cv.curves) + 1, np.int32)
    cv.curve_offset_data.foreach_get("value", off)
    if n == 0 or len(off) < 2:
        return p
    first = np.repeat(off[:-1], np.diff(off))
    last = np.repeat(off[1:] - 1, np.diff(off))
    i = np.arange(n)
    tan = (p[np.minimum(i + 1, last)] - p[np.maximum(i - 1, first)]) / 6.0
    out = [p, (p + tan)[i < last], (p - tan)[i > first]]
    for name in ("handle_left", "handle_right"):
        a = cv.attributes.get(name)
        if a is not None and len(a.data) == n:
            h = np.empty(n * 3, np.float32)
            a.data.foreach_get("vector", h)
            out.append(h.reshape(-1, 3))
    return np.vstack(out).astype(np.float32)


def _radius(data) -> float:
    rad = data.attributes.get("radius")
    if rad is None or not len(rad.data):
        return DEFAULT_RADIUS
    rr = np.empty(len(rad.data), np.float32)
    rad.data.foreach_get("value", rr)
    if rr.min() != rr.max():                   # the largest stands for all: a bound
        _BOUND_HIT[0] = True
    return float(rr.max())


def _measure_other(ob, basis, tx, ty, dg):
    """(extents in camera axes, world bounds) of an object that isn't a mesh, as a render shows it now
    (see _shape); an instancing empty: its instances. Points with a radius are grown by it exactly
    (the largest radius stands for all: a bound when they differ)."""
    if dg is None:
        _BOUND_HIT[0] = True
        parts = [((np.array([c[:] for c in ob.bound_box], np.float32), 0.0), _np_matrix(ob.matrix_world))]
    elif ob.type == "EMPTY":
        found = [(i.object.original, _np_matrix(i.matrix_world)) for i in dg.object_instances
                 if i.is_instance and i.parent is not None and i.parent.original == ob
                 and i.object.type in _RENDERS and not i.object.original.hide_render]
        shapes = {}
        for src, _m in found:
            if src.name_full not in shapes:
                shapes[src.name_full] = _shape(src.evaluated_get(dg))
        parts = [(shapes[src.name_full], m) for src, m in found]
    else:
        parts = [(_shape(ob.evaluated_get(dg)), _np_matrix(ob.matrix_world))]
    ext = bnd = None
    for (pts, r), m in parts:
        if not len(pts):
            continue
        e = framing.shifted(framing.extents(pts, m[:3, :3].T @ basis, tx, ty), m[:3, 3] @ basis, tx, ty)
        w = np.asarray(pts, np.float64) @ m[:3, :3].T + m[:3, 3]
        if r > 0 and e is not None:
            r *= float(max(np.linalg.norm(m[:3, :3], axis=0)))       # in world units (the largest scale)
            gx, gy = r * math.sqrt(1 + tx * tx), r * math.sqrt(1 + ty * ty)
            e = e + np.array([gx, gx, gy, gy, -r, r])
            w = np.vstack([w.min(0) - r, w.max(0) + r])
        ext = framing.merge(ext, e)
        bnd = framing.merge_bounds(bnd, framing.bounds(w))
    return ext, bnd


def _measure(ob, basis, tx, ty, depsgraph=None, scene=None, now_only=False):
    """(extents in camera axes, world bounds) of one model object: what renders of it now and, for
    a mesh with a history (unless `now_only`), every earlier and later shape at its transform now;
    other types: _measure_other. The mesh is measured in its own space, so
    no world-space copy of it is made. `depsgraph`: a callable (refresh=False) that gives the
    evaluated depsgraph (only asked for when the mesh has modifiers or shape keys)."""
    if ob.type != "MESH":
        if depsgraph is None:
            sc = scene if scene is not None else (ob.users_scene[0] if ob.users_scene else None)
            return _measure_other(ob, basis, tx, ty, _depsgraph(sc) if sc is not None else None)
        return _measure_other(ob, basis, tx, ty, depsgraph())
    mw = _np_matrix(ob.matrix_world)
    local_basis = mw[:3, :3].T @ basis                 # local points @ local_basis == world @ basis
    pts = None
    if ob.type == "MESH" and ob.data is not None:
        pts = _positions(ob.data) if ob.mode != "EDIT" else _entry_positions(ob)
    if pts is not None and len(pts):
        if has_stack(ob):
            if scene is None:
                scene = ob.users_scene[0] if ob.users_scene else None
            if depsgraph is None:
                def depsgraph(refresh=False):
                    return _depsgraph(scene, refresh)
            ext, pts = _measure_stack(ob, pts, local_basis, tx, ty, scene, depsgraph, now_only)
        else:
            ext = framing.extents(pts, local_basis, tx, ty)
            if not now_only:
                past = _history_extents(ob, runtime.obj_uid(ob), local_basis[None], np.array([(tx, ty)]))
                ext = framing.merge(ext, None if past is None else past[0])
        if ob.mode == "EDIT":                          # and the mesh being edited: its evaluated box
            _BOUND_HIT[0] = True
            box = np.array([c[:] for c in ob.bound_box], np.float32)
            ext = framing.merge(ext, framing.extents(box, local_basis, tx, ty))
            pts = np.vstack([np.asarray(pts, np.float32).reshape(-1, 3), box])
    else:                                              # other types: the evaluated box
        _BOUND_HIT[0] = True
        pts = np.array([c[:] for c in ob.bound_box], np.float32)
        ext = framing.extents(pts, local_basis, tx, ty)
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


def _at_every_step(measure) -> bool:
    """#112: measure() at every position of the global timeline (what the time lapse plays), then back
    to where it was: every object as its recorded transform, parent and data place it at each step,
    objects added late or deleted midway where they exist. False (nothing walked) without a timeline,
    in Edit Mode (a jump there would leave it: what is edited is measured as it is), and in the movie's
    frame change, a render or playback (where it would jump away from the step being shown).
    Once Create time lapse has keyed it (#117 review 3), every frame of the scene's range instead, each
    showing the step the movie shows there: what changes with the frame (an object's own keys, Grease
    Pencil keys, a Build modifier) is measured as it renders on that frame.
    shortcut: walks the whole movie on every frame(); keep each frame's hull if reframing gets slow."""
    ctx = bpy.context
    if getattr(ctx, "mode", "OBJECT").startswith("EDIT") or runtime.IN_HANDLER[0] or _render_running() or _playing():
        return False
    vis = runtime._visible_uids()
    groups = runtime._groups(vis)
    if not groups:
        return False
    was = {h.uid: h.cursor for h in runtime.reg().histories.values()}
    here = runtime._position(vis, groups)
    vl = ctx.view_layer
    act = vl.objects.active
    act, mode = (act.name, act.mode) if act is not None else (None, "OBJECT")
    sel = {o.name for o in vl.objects if o.select_get()}
    from . import movie as mv
    sc = ctx.scene
    keyed = mv._step_fcurve(sc) is not None
    n = len(groups)

    def plays():
        if not keyed:
            yield from range(n + 1)
            return
        for f in range(sc.frame_start, sc.frame_end + 1):
            if f != sc.frame_current:
                sc.frame_set(f)
            yield max(0, min(n, sc.hindsight.movie_step))    # the step the movie shows on this frame

    frame_was = (sc.frame_current, sc.frame_subframe)
    busy = mv._BUSY[0]
    mv._BUSY[0] = True                  # a frame change here doesn't play the movie's own jump
    movie = getattr(runtime.goto, "_movie", False)
    runtime.goto._movie = True          # #113: as the time lapse plays: the scene's camera and frame range stay
    with runtime.suspended():
        try:
            for g in plays():
                runtime.global_goto(ctx, g, False)        # not final: no undo entry, no mode switch
                measure()
        finally:
            try:
                if (sc.frame_current, sc.frame_subframe) != frame_was:
                    sc.frame_set(frame_was[0], subframe=frame_was[1])
                runtime.global_goto(ctx, here, False)
                for h in runtime.reg().histories.values():     # a cursor the timeline can't name: as it was
                    if h.cursor != was.get(h.uid, h.cursor):
                        runtime._goto_history(h, was[h.uid], False)
            finally:
                runtime.goto._movie = movie
                mv._BUSY[0] = busy
            vl.update()                                    # its objects again, after those made and removed
            for o in vl.objects:                           # one made again on the way: selected,
                if o.name in sel and not o.select_get():   # active and in its mode as before
                    o.select_set(True)
            ob = vl.objects.get(act) if act is not None else None
            if ob is not None and vl.objects.active != ob:
                vl.objects.active = ob
            if ob is not None and ob.mode != mode:
                try:
                    bpy.ops.object.mode_set(mode=mode)
                except RuntimeError as e:
                    log.error_once(("camrig_mode", str(e)), f"framing could not go back to {mode}: {e}", e)
    return True


def _same_extents(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and np.array_equal(a, b))


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
    dg = []
    hit = set()

    def depsgraph(refresh=False):
        if refresh or not dg:
            dg[:] = [_depsgraph(scene, refresh)]
        return dg[0]

    walked = []                                        # per step of the walk: {name: (ext, bnd, bound)}
                                                       # of the objects that render without a history
    def measure(now_only=False):
        nonlocal ext, bnd
        if now_only:                                   # a jump changed the scene: evaluate it again
            depsgraph(True)                            # (matrix_world too, which a mesh is placed by)
        obs = model_objects(scene)
        for ob in obs:
            _BOUND_HIT[0] = False
            e, b = _measure(ob, basis, tx, ty, depsgraph, scene, now_only)
            if _BOUND_HIT[0]:
                hit.add(ob.name)
            ext = framing.merge(ext, e)
            bnd = framing.merge_bounds(bnd, b)
        names = {o.name for o in obs}
        shown = _rendered_objects(scene)               # #117 review 3: not in a collection disabled in renders
        here = {}
        walked.append(here)
        for ob in scene.objects:
            if ob.name not in names and renders(ob) and ob.as_pointer() in shown:
                _BOUND_HIT[0] = False
                e, b = _measure(ob, basis, tx, ty, depsgraph, scene, True)
                here[ob.name] = (e, b, _BOUND_HIT[0])

    walk = _at_every_step(lambda: measure(True))
    if not walk:
        measure()
    # D181: an object without a history of its own counts when the history plays it back: a step makes
    # or deletes it (it isn't there at every step), or moves or reshapes it (a parent or modifier
    # object that is recorded). One that is the same at every step (a backdrop) is left out.
    # Without a walk (Edit Mode, the movie's frame change: #117 review 2), the ones the last walk counted
    # count as they are now, and before any walk every one (one step can't tell a backdrop apart).
    # shortcut: measures every such object at every step; skip the unchanged ones if framing gets slow.
    known = _COUNTED.get(scene.name_full)
    counted = set()
    for name in {n for here in walked for n in here}:
        rows = [here.get(name) for here in walked]
        first = rows[0]
        if walk and first is not None and all(r is not None and _same_extents(r[0], first[0]) for r in rows):
            continue
        if not walk and known is not None and name not in known:
            continue
        counted.add(name)
        for e, b, bound in filter(None, rows):
            ext = framing.merge(ext, e)
            bnd = framing.merge_bounds(bnd, b)
            if bound:
                hit.add(name)
    if walk:
        _COUNTED[scene.name_full] = counted
    _BOUNDED[scene.name_full] = tuple(sorted(hit))
    fitted = framing.fit(ext, basis, tx, ty) if ext is not None else None
    with runtime.suspended():
        if fitted is None or bnd is None:
            back = basis[:, 2]
            if cam is not None:
                cam.matrix_world = _basis_matrix(basis, back * 10.0)
                _camera_data(cam.data, 1.0, 11.0)
            _LIT[scene.name_full] = ((0.0, 0.0, 0.0), 1.0)
            _place_lights(have, basis, (0.0, 0.0, 0.0), 1.0)
            return False
        if cam is not None:
            cam.matrix_world = _basis_matrix(basis, fitted["location"])
            _camera_data(cam.data, float(fitted["near"]), float(fitted["far"]))
        lo, hi = np.asarray(bnd[0], np.float64), np.asarray(bnd[1], np.float64)
        center = (lo + hi) * 0.5
        radius = max(float(np.linalg.norm(hi - lo)) * 0.5, 1e-3)
        _LIT[scene.name_full] = (center, radius)
        _place_lights(have, basis, center, radius)
    return True


def _camera_data(cd, near, far) -> None:
    cd.type = 'PERSP'
    clip_start = max(near * 0.1, 1e-4)      # well in front of the model, whatever its size
    cd.clip_start = clip_start
    cd.clip_end = max(far * 2.0, clip_start * 10.0)
    with contextlib.suppress(AttributeError):
        cd.dof.use_dof = False


# ----------------------------------------------------------------------------- entry points
_HOLD = [False]         # True while several settings are set at once (one framing at the end)


@guard.guarded(FEATURE)
def on_settings_changed(context) -> None:
    scene = getattr(context, "scene", None)
    if scene is None or _props(scene) is None or _HOLD[0]:
        return
    ensure(scene, reframe=True, take=True)
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
    from . import datarec
    if runtime.IN_HANDLER[0] or _render_running() or _playing() or datarec._operation_running():
        return                              # never in the middle of a render, playback, a stroke or a drag
    keep_user_camera(scene)
    if needs_attention(scene):
        res = ensure(scene, reframe=False)
        if res["created"] or res["removed"]:
            log.info(f"Movie Mode camera and lighting: added {res['created']}, removed {res['removed']}")
            runtime.tag_redraw_all()


def _timer():
    try:
        tick()
    except Exception as e:                     # Blender drops a timer that raises
        log.error_once(("camrig_tick", str(e)), f"Movie Mode camera error: {e}", e)
    return INTERVAL


def start() -> None:
    """#113: the check that puts missing pieces back runs from Movie Mode going on (movie.start), never
    before. Not persistent: loading a file stops it."""
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=INTERVAL)


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


def _has_settings(scene) -> bool:
    """True when the scene stores Hindsight's settings. Reading scene.hindsight would add them."""
    get = getattr(scene, "bl_system_properties_get", None)       # Blender 5; Blender 4: custom properties
    sp = get() if get is not None else scene
    return sp is not None and "hindsight" in sp.keys()


@persistent
def _on_save_pre(*_args) -> None:
    """Before a save: a user camera deleted since the last tick isn't kept in the file by the
    memory of it."""
    try:
        for sc in bpy.data.scenes:
            if sc.library is None and _has_settings(sc):
                keep_user_camera(sc)
    except Exception as e:      # never stop a save
        log.error_once(("camrig_save_pre", type(e).__name__), f"Movie Mode camera before save: {e}", e)


@persistent
def _on_load_post(*_args) -> None:
    """Another file: its histories may have the same ids, lengths and step numbers as the ones
    the caches were made for, with other shapes."""
    _PAST.clear()
    _REACH.clear()
    _BOUNDED.clear()                   # its scenes may have the same names
    _COUNTED.clear()
    _LIT.clear()


def register():
    _LAST[0] = 0.0
    _PAST.clear()
    _REACH.clear()
    reg.register_classes(classes)
    if _on_save_pre not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_on_save_pre)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    while _on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_on_save_pre)
    while _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    _PAST.clear()
    _REACH.clear()
    reg.unregister_classes(classes)
