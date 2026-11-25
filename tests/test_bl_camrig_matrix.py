"""#115 (23.5): the rest of #23's test matrix, each case checked at every step of the global timeline
against the time lapse camera (everything that renders inside the frame with the margin free, the
content reaching the margin at one step at least): the model changing halfway, the modifiers, the
camera and render settings, and random histories."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import math

import bpy
import numpy as np

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.test_bl_camrig_steps import _frame_and_check_every_step as every_step, _run, _start
from tests.test_bl_camrig_steps import _rendered_points, glen
from tests import test_bl_camrig_types as TY
from hindsight.bl import runtime

C = bpy.context


def _ob(name):
    return bpy.data.objects[name]


# ----------------------------------------------------------------------------- the model changing
def _sculpt(ob, factor, label):
    """A sculpt step that moves every vertex `factor` times as far from the origin (grown or shrunk)."""
    H.enter(ob, "SCULPT")
    co = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", co)
    ob.data.vertices.foreach_set("co", co * factor)
    ob.data.update()
    assert H.commit(ob) is not None, label
    L.tick_like(label)


def test_sculpted_outward_then_shrunk():
    _start()
    ob = H.make_mesh(400, "Blob")
    L.track_all()
    runtime.set_global(C, True)
    _sculpt(ob, 3.0, "grow")
    _sculpt(ob, 0.2, "shrink")
    H.enter(ob, "OBJECT")
    every_step()


def test_subdivided_and_grown_in_edit_mode():
    _start(("A", (0, 0, 0), (1, 1, 1)))
    _run(T.edit("A", "subdivide", lambda: bpy.ops.mesh.subdivide(number_cuts=2)),
         T.edit("A", "grow", lambda: bpy.ops.transform.resize(value=(4, 4, 4))),
         T.leave_edit("A"))
    assert len(_ob("A").data.vertices) > 8
    every_step()


def test_joined_and_separated():
    _start(("A", (0, 0, 0), (1, 1, 1)), ("B", (20, 0, 0), (1, 1, 1)))

    def join():
        T.select("A", "B", active="A")
        return bpy.ops.object.join()

    _run(T.Op("join", join, ["scene"]),
         T.edit("A", "separate", lambda: bpy.ops.mesh.separate(type="LOOSE")),
         T.leave_edit("A"))
    apart = [o.name for o in C.scene.objects if o.type == "MESH" and o.name != "A"]
    assert len(apart) == 1, apart
    _run(T.translate(apart[0], (0, 30, 0)))
    every_step()


def test_a_flat_plane_stood_up():
    _start()
    bpy.ops.mesh.primitive_plane_add(size=10)
    C.active_object.name = "Plane"
    L.track_all()
    runtime.set_global(C, True)
    _run(T.rotate("Plane", math.radians(90), "X"), T.translate("Plane", (0, 0, 5)))
    every_step()


def test_a_100_to_1_shape_turned():
    _start(("Rod", (0, 0, 0), (50, 0.5, 0.5)))
    _run(T.rotate("Rod", math.radians(90), "Z"), T.rotate("Rod", math.radians(45), "Y"))
    every_step()


def test_far_from_the_origin():
    _start(("A", (100000, -50000, 2000), (1, 1, 1)))
    _run(T.translate("A", (5, 0, 0)), T.resize("A", 3.0))
    every_step()


# ----------------------------------------------------------------------------- modifiers
def _with_modifier(kind, **props):
    _start(("A", (0, 0, 0), (1, 1, 1)))
    m = _ob("A").modifiers.new(kind.title(), kind)
    for k, v in props.items():
        setattr(m, k, v)
    L.track_all()
    return m


def test_mirror():
    _with_modifier("MIRROR")
    pivot = bpy.data.objects.new("Pivot", None)
    C.scene.collection.objects.link(pivot)
    _ob("A").modifiers[0].mirror_object = pivot
    _run(T.translate("A", (6, 0, 0)), T.translate("A", (4, 2, 0)))
    every_step()


def test_array():
    _with_modifier("ARRAY", count=5)
    _run(T.resize("A", 2.0), T.rotate("A", math.radians(60), "Z"))
    every_step()


def test_subdivision():
    _with_modifier("SUBSURF", levels=2, render_levels=3)
    _run(T.edit("A", "pull a corner", lambda: bpy.ops.transform.translate(value=(0, 0, 6)), seed=3),
         T.leave_edit("A"), T.translate("A", (3, 0, 0)))
    every_step()


def test_displace():
    _with_modifier("DISPLACE", strength=2.0)
    _run(T.resize("A", 3.0), T.translate("A", (0, 4, 0)))
    every_step()


def test_displace_by_a_texture():
    m = _with_modifier("DISPLACE", strength=1.5)
    m.texture = bpy.data.textures.new("Clouds", "CLOUDS")
    _run(T.resize("A", 3.0), T.translate("A", (0, 4, 0)))
    every_step()


def test_geometry_nodes():
    m = _with_modifier("NODES")
    ng = bpy.data.node_groups.new("Grow", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    tr = ng.nodes.new("GeometryNodeTransform")
    tr.inputs["Translation"].default_value = (0, 0, 5)
    tr.inputs["Scale"].default_value = (3, 3, 3)
    ng.links.new(gi.outputs[0], tr.inputs["Geometry"])
    ng.links.new(tr.outputs["Geometry"], go.inputs[0])
    m.node_group = ng
    _run(T.translate("A", (4, 0, 0)), T.rotate("A", math.radians(90), "X"))
    every_step()


def test_armature():
    """A recorded armature carries a mesh with no history of its own 30 m away and back."""
    _start(("A", (0, 0, 0), (1, 1, 1)))
    arm = bpy.data.armatures.new("Rig")
    rig = bpy.data.objects.new("Rig", arm)
    C.scene.collection.objects.link(rig)
    C.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    b = arm.edit_bones.new("Bone")
    b.head, b.tail = (0, 0, -1), (0, 0, 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    a = _ob("A")
    a.parent = rig
    a.modifiers.new("Armature", "ARMATURE").object = rig
    vg = a.vertex_groups.new(name="Bone")
    vg.add(list(range(len(a.data.vertices))), 1.0, "REPLACE")
    L.track_all()
    _run(T.translate("Rig", (30, 0, 0)), T.translate("Rig", (-25, 5, 0)), T.resize("A", 2.0))
    every_step()


def test_shape_keys():
    _start(("A", (0, 0, 0), (1, 1, 1)))
    a = _ob("A")
    a.shape_key_add(name="Basis")
    big = a.shape_key_add(name="Big")
    for p in big.data:
        p.co = p.co * 5
    big.value = 0.0                         # D183: a key made from Python starts at 1.0 in 5.0 and later
    L.track_all()
    for v in (1.0, 0.3, 0.6):
        a.data.shape_keys.key_blocks["Big"].value = v
        C.view_layer.update()
        L.tick_like("shape key")
    assert glen() == 3, glen()              # each value change is a step
    every_step()


def test_shape_key_values_going_back():
    """#117 review 5, D183's other symptom ("going back shows 0 where 1.0 was"): a key left at the value
    it was made with (1.0 in 5.0 and later, 0.0 in 4.2) shows that value again at step 0, and each
    later value at its step."""
    _start(("A", (0, 0, 0), (1, 1, 1)))
    a = _ob("A")
    a.shape_key_add(name="Basis")
    a.shape_key_add(name="Big")
    start = a.data.shape_keys.key_blocks["Big"].value
    L.track_all()
    for v in (0.3, 0.6):
        a.data.shape_keys.key_blocks["Big"].value = v
        C.view_layer.update()
        L.tick_like("shape key")
    assert glen() == 2, glen()
    for g, want in ((0, start), (1, 0.3), (2, 0.6), (0, start)):
        runtime.global_goto(C, g, True)
        got = _ob("A").data.shape_keys.key_blocks["Big"].value
        assert math.isclose(got, want, abs_tol=1e-6), ("step", g, got, want)


# ----------------------------------------------------------------------------- camera and render
def _two_cubes():
    _start(("A", (0, 0, 0), (1, 1, 1)), ("B", (6, 2, 0), (2, 1, 1)))
    _run(T.translate("A", (0, 0, 4)), T.translate("B", (-10, 0, 0)))


def test_image_shapes_and_sensor_fits():
    for rx, ry in ((1920, 1080), (1080, 1920), (1000, 1000)):
        for fit in ("AUTO", "HORIZONTAL", "VERTICAL"):
            _two_cubes()
            C.scene.render.resolution_x, C.scene.render.resolution_y = rx, ry

            def setup(cam, fit=fit):
                cam.data.sensor_fit = fit
            every_step(setup)


def test_lenses_18_to_135_mm():
    for lens in (18.0, 35.0, 50.0, 85.0, 135.0):
        _two_cubes()

        def setup(cam, lens=lens):
            cam.data.lens = lens
        every_step(setup)


def test_angle_and_height_at_their_extremes():
    for angle in (-math.pi, 0.0, math.pi):
        for height in (-1.5533430342749532, 0.0, 1.5533430342749532):
            _two_cubes()
            C.scene.hindsight.movie_view_angle = angle
            C.scene.hindsight.movie_view_height = height
            every_step()


# ----------------------------------------------------------------------------- random histories
_KINDS = ("cube", "uv_sphere", "cone", "cylinder", "monkey")


def _random_op(rng, names):
    """One random change of the kinds in #23's matrix, to a random object of `names` (a list it updates)."""
    name = names[int(rng.integers(len(names)))]
    k = int(rng.integers(8))
    if k == 0:
        return T.translate(name, tuple(rng.uniform(-30, 30, 3)))
    if k == 1:
        return T.rotate(name, float(rng.uniform(-math.pi, math.pi)), "XYZ"[int(rng.integers(3))])
    if k == 2:
        return T.resize(name, float(rng.choice([0.2, 0.5, 2.0, 5.0])))
    if k == 3:
        return T.add_primitive(_KINDS[int(rng.integers(len(_KINDS)))], tuple(rng.uniform(-40, 40, 3)))
    if k == 4 and len(names) > 1:
        names.remove(name)
        return T.delete([name])
    if k == 5:
        return T.edit(name, "grow", lambda: bpy.ops.transform.resize(value=tuple(rng.uniform(0.3, 4, 3))),
                      seed=int(rng.integers(100)))
    if k == 6:
        return T.edit(name, "subdivide", lambda: bpy.ops.mesh.subdivide(number_cuts=1))
    other = [n for n in names if n != name]
    if other:
        return T.parent(name, other[0], True)
    return T.translate(name, (0, 0, float(rng.uniform(-10, 10))))


def _random_history(seed):
    rng = np.random.default_rng(seed)
    shapes = [(f"S{i}", tuple(rng.uniform(-15, 15, 3)), tuple(rng.uniform(0.2, 4, 3)))
              for i in range(int(rng.integers(1, 4)))]
    _start(*shapes)
    names = [s[0] for s in shapes]
    _run(*(T.translate(n, tuple(rng.uniform(-5, 5, 3))) for n in names))   # each has a history: none
                                                                            # is a backdrop left out
    for _ in range(int(rng.integers(3, 7))):
        op = _random_op(rng, names)
        op.run()
        if C.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        L.tick_like(op.label)
        names[:] = [n for n in names if n in C.scene.objects]
        names += [o.name for o in C.scene.objects if o.type == "MESH" and o.name not in names]
    return rng


def test_random_histories():
    """Random shapes, random sequences of moves, turns, scaling, adding, deleting, Edit Mode growth,
    subdividing and parenting: held at every step. 12 fixed seeds (a failure names its seed)."""
    for seed in range(12):
        _random_history(seed)
        try:
            every_step()
        except AssertionError as e:
            raise AssertionError(f"seed {seed}: {e}") from e


def test_random_histories_with_random_cameras():
    """The same with a random image shape, sensor fit, lens, angle and height."""
    for seed in range(100, 108):
        rng = _random_history(seed)
        r = C.scene.render
        r.resolution_x, r.resolution_y = int(rng.integers(200, 2000)), int(rng.integers(200, 2000))
        C.scene.hindsight.movie_view_angle = float(rng.uniform(-math.pi, math.pi))
        C.scene.hindsight.movie_view_height = float(rng.uniform(-1.55, 1.55))
        fit, lens = str(rng.choice(["AUTO", "HORIZONTAL", "VERTICAL"])), float(rng.uniform(18, 135))

        def setup(cam):
            cam.data.sensor_fit, cam.data.lens = fit, lens
        try:
            every_step(setup)
        except AssertionError as e:
            raise AssertionError(f"seed {seed}: {e}") from e


# ----------------------------------------------------------------------------- what changes with the frame
def _every_frame(points=_rendered_points, tight=True):
    """Create time lapse, then play every frame of the movie and check what renders there (points())
    against its camera: the frame the time lapse shows a step on, and the frames in between."""
    sc = C.scene
    sc.hindsight.frames_per_step = 2
    sc.frame_end = sc.frame_start                       # Create time lapse makes it the last key's frame
    sc.frame_set(sc.frame_start)
    assert bpy.ops.hindsight.create_time_lapse() == {'FINISHED'}
    pts = []
    for f in range(sc.frame_start, sc.frame_end + 1):
        sc.frame_set(f)
        here = points()
        TY.check_in_frame(here, tight=False, msg=f"frame {f}")
        pts.append(here)
    TY.check_in_frame(np.vstack(pts), tight=tight, msg="every frame")


def test_keyframe_animated_object():
    """#117 review point 3: an object moved by its own keys is framed where they take it on every frame
    the time lapse plays, between the frames that show a step too, not where it was when it was made."""
    H.fresh()
    for name, loc in (("A", (0, 0, 0)), ("B", (3, 0, 0))):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        C.active_object.name = name
    b = _ob("B")
    for f, x in ((1, 3.0), (4, 30.0), (6, 3.0)):        # out and back between the frames steps are shown on
        b.location.x = x
        b.keyframe_insert("location", index=0, frame=f)
    C.scene.frame_set(1)
    L.track_all()
    runtime.set_global(C, True)
    _run(T.translate("A", (0, 1, 0)), T.translate("B", (0, 0, 0.5)), T.translate("A", (0, 1, 1)))
    _every_frame()


def test_grease_pencil_keys():
    """#117 review point 3: a Grease Pencil drawing keyed on later frames is framed on every frame the
    time lapse plays it (4.2's Grease Pencil by its box, a bound)."""
    TY._start()
    g = bpy.data.grease_pencils.new("Sketch")
    layer = g.layers.new("Lines")
    ob = TY._link("Sketch", g)
    for f, x in ((1, 0.0), (4, 30.0), (6, 0.0)):
        fr = layer.frames.new(f)
        co = np.array([(x, 0, 0), (x + 2, 0, 1), (x, 0, 2)], np.float32)
        if hasattr(fr, "drawing"):
            fr.drawing.add_strokes([len(co)])
            fr.drawing.attributes["position"].data.foreach_set("vector", co.reshape(-1))
        else:
            s = fr.strokes.new()
            s.points.add(len(co))
            s.points.foreach_set("co", co.reshape(-1))
    C.scene.frame_set(1)
    TY._record(ob)
    _run(T.translate("Sketch", (0, 0, 1)), T.translate("Sketch", (0, 0, -1)))

    def points():
        if hasattr(layer, "current_frame"):
            local, r = TY._attr_points(layer.current_frame().drawing)
            return TY._world(TY._balls(local, r), TY._mw(ob))
        s = layer.active_frame.strokes[0]
        co = np.empty(len(s.points) * 3, np.float32)
        s.points.foreach_get("co", co)
        return TY._world(co.reshape(-1, 3), TY._mw(ob))
    _every_frame(points, tight=hasattr(layer, "current_frame"))


if __name__ == "__main__":
    for t in (test_sculpted_outward_then_shrunk, test_subdivided_and_grown_in_edit_mode,
              test_joined_and_separated, test_a_flat_plane_stood_up, test_a_100_to_1_shape_turned,
              test_far_from_the_origin, test_mirror, test_array, test_subdivision, test_displace,
              test_displace_by_a_texture, test_geometry_nodes, test_armature, test_shape_keys,
              test_shape_key_values_going_back,
              test_image_shapes_and_sensor_fits, test_lenses_18_to_135_mm,
              test_angle_and_height_at_their_extremes, test_random_histories,
              test_random_histories_with_random_cameras, test_keyframe_animated_object,
              test_grease_pencil_keys):
        H.run(t)
    H.finish()
