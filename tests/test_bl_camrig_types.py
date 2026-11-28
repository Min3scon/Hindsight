"""#111 (23.1): the time lapse camera holds every recorded object that renders, of any type, as a
render shows it: curves, text, surfaces, metaballs, Grease Pencil, point clouds, hair curves,
volumes and an empty instancing a collection."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import math

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import camrig, datarec, prefs, runtime

C = bpy.context
_K = np.arange(2000) + 0.5                   # directions spread evenly over the sphere (Fibonacci)
_Z = 1 - 2 * _K / len(_K)
_DIRS = np.stack([np.sqrt(1 - _Z * _Z) * np.cos(np.pi * (1 + 5 ** 0.5) * _K),
                  np.sqrt(1 - _Z * _Z) * np.sin(np.pi * (1 + 5 ** 0.5) * _K), _Z], axis=1)


def _mw(ob):
    return np.array([list(r) for r in ob.matrix_world], np.float64)


def _world(pts, m):
    return np.asarray(pts, np.float64).reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]


def _balls(pts, r):
    """Points on spheres of radius r around each point: what a render shows of points with a radius."""
    pts = np.asarray(pts, np.float64).reshape(-1, 3)
    return (pts[:, None, :] + r * _DIRS[None]).reshape(-1, 3)


def _record(ob):
    """Give the object a history (one recorded move), as Record = Everything does."""
    prefs.FORCE["record_scope"] = "EVERYTHING"
    datarec.set_tracked(ob, True)
    ob.location.x += 0.25
    L.tick_like("move")
    h = runtime.history_of(ob)
    assert h is not None and h.entries, f"{ob.name} has a history"


def _link(name, data):
    ob = bpy.data.objects.new(name, data)
    C.scene.collection.objects.link(ob)
    return ob


def _mesh_points(ob):
    dg = C.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
    finally:
        ev.to_mesh_clear()
    return _world(co, _mw(ob))


def _attr_points(data, default_radius=0.01):
    pos = data.attributes["position"].data
    co = np.empty(len(pos) * 3, np.float32)
    pos.foreach_get("vector", co)
    r = default_radius
    if "radius" in data.attributes:
        rr = np.empty(len(data.attributes["radius"].data), np.float32)
        data.attributes["radius"].data.foreach_get("value", rr)
        r = float(rr.max())
    return co.reshape(-1, 3), r


def check_in_frame(pts, tight=True, msg=""):
    """Every point inside the frame with the margin free; tight: the content reaches the margin on one
    side at least (a camera any closer, or off-centre, would cut it off)."""
    sc = C.scene
    cam = camrig.rig(sc)["camera"]
    assert sc.camera is cam, msg
    v = np.array([world_to_camera_view(sc, cam, Vector(p))[:] for p in pts])
    assert (v[:, 2] > 0).all(), f"{msg} behind the camera"
    eps = 1e-4
    assert v[:, 0].min() >= -eps and v[:, 0].max() <= 1 + eps, f"{msg} x out {v[:, 0].min()} {v[:, 0].max()}"
    assert v[:, 1].min() >= -eps and v[:, 1].max() <= 1 + eps, f"{msg} y out {v[:, 1].min()} {v[:, 1].max()}"
    sx, sy = np.ptp(v[:, 0]), np.ptp(v[:, 1])
    assert sx <= 1 - camrig.MARGIN + 1e-3 and sy <= 1 - camrig.MARGIN + 1e-3, f"{msg} margin not free {sx} {sy}"
    if tight:                                   # the content touches the margin on one side at least
        half = camrig.MARGIN / 2
        gap = min(v[:, 0].min() - half, 1 - half - v[:, 0].max(), v[:, 1].min() - half, 1 - half - v[:, 1].max())
        assert gap < 2e-3, f"{msg} not tight: {gap} from the margin"


def _frame_and_check(ob, truth, tight=True):
    sc = C.scene
    assert ob in camrig.model_objects(sc), (ob.name, [o.name for o in camrig.model_objects(sc)])
    sc.hindsight.movie_mode = True
    camrig.frame(sc)
    check_in_frame(truth, tight=tight, msg=ob.type)


def _start():
    H.fresh()
    runtime.set_global(C, True)


def test_a_curve_with_a_bevel():
    _start()
    cu = bpy.data.curves.new("Rope", "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(3)
    for p, co in zip(sp.points, ((0, 0, 0), (3, 0, 1), (3, 2, 0), (0, 4, 2))):
        p.co = (*co, 1.0)
    cu.bevel_depth = 0.6
    ob = _link("Rope", cu)
    _record(ob)
    _frame_and_check(ob, _mesh_points(ob))


def test_a_text():
    _start()
    cu = bpy.data.curves.new("Words", "FONT")
    cu.body = "Hindsight"
    cu.extrude = 0.2
    ob = _link("Words", cu)
    ob.rotation_euler = (math.radians(90), 0, 0)
    _record(ob)
    _frame_and_check(ob, _mesh_points(ob))


def test_a_surface():
    _start()
    bpy.ops.surface.primitive_nurbs_surface_torus_add(radius=2.0, location=(1, 2, 0))
    ob = C.object
    _record(ob)
    _frame_and_check(ob, _mesh_points(ob))


def test_a_metaball():
    _start()
    mb = bpy.data.metaballs.new("Blob")
    for co in ((0, 0, 0), (2.5, 0, 0.5)):
        el = mb.elements.new()
        el.co = co
        el.radius = 1.5
    ob = _link("Blob", mb)
    _record(ob)
    _frame_and_check(ob, _mesh_points(ob))


def test_grease_pencil():
    _start()
    g = bpy.data.grease_pencils.new("Sketch")
    layer = g.layers.new("Lines")
    fr = layer.frames.new(C.scene.frame_current)
    co = np.array([(0, 0, 0), (2, 0, 1), (2, 0, 3), (-1, 0, 2), (0, 1, 4)], np.float32)
    ob = _link("Sketch", g)
    if hasattr(fr, "drawing"):                   # Grease Pencil 3 (4.3 and later): points with a radius
        dr = fr.drawing
        dr.add_strokes([len(co)])
        dr.attributes["position"].data.foreach_set("vector", co.reshape(-1))
        _record(ob)
        local, r = _attr_points(dr)
        _frame_and_check(ob, _world(_balls(local, r), _mw(ob)))
    else:                                       # 4.2's Grease Pencil: held by its box, a bound
        s = fr.strokes.new()
        s.points.add(len(co))
        s.points.foreach_set("co", co.reshape(-1))
        _record(ob)
        _frame_and_check(ob, _world(co, _mw(ob)), tight=False)


def test_hair_curves():
    _start()
    hc = bpy.data.hair_curves.new("Hair")
    hc.add_curves([3, 3])
    co = np.array([(0, 0, 0), (0, 0.5, 2), (0, 1, 3), (2, 0, 0), (2.5, 0, 1.5), (3, 0, 2.5)], np.float32)
    hc.attributes["position"].data.foreach_set("vector", co.reshape(-1))
    rad = hc.attributes.new("radius", "FLOAT", "POINT")
    rad.data.foreach_set("value", np.full(len(co), 0.3, np.float32))
    ob = _link("Hair", hc)
    _record(ob)
    local, r = _attr_points(hc)
    assert abs(r - 0.3) < 1e-6
    _frame_and_check(ob, _world(_balls(local, r), _mw(ob)))


def test_a_point_cloud():
    _start()
    pc = bpy.data.pointclouds.new("Dots")
    if not hasattr(pc, "resize"):
        print("  (4.2 has no way to add points to a point cloud from Python: nothing to frame)")
        return
    co = np.array([(0, 0, 0), (3, 1, 0), (1, 3, 2), (-1, 1, 1)], np.float32)
    pc.resize(len(co))
    pc.attributes["position"].data.foreach_set("vector", co.reshape(-1))
    ob = _link("Dots", pc)
    _record(ob)
    local, r = _attr_points(pc)
    _frame_and_check(ob, _world(_balls(local, r), _mw(ob)))


def test_a_volume():
    """A volume is held by its box: a safe bound."""
    _start()
    src = H.make_mesh(300, "Source", (0, 0, 0))
    src.hide_render = True
    vol = _link("Fog", bpy.data.volumes.new("Fog"))
    m = vol.modifiers.new("ToVolume", "MESH_TO_VOLUME")
    m.object = src
    _record(vol)
    dg = C.evaluated_depsgraph_get()
    box = np.array([c[:] for c in vol.evaluated_get(dg).bound_box], np.float64)
    assert np.ptp(box, axis=0).max() > 0.5, "the volume has grids"
    _frame_and_check(vol, _world(box, _mw(vol)), tight=False)


def test_an_empty_instancing_a_collection():
    _start()
    coll = bpy.data.collections.new("Parts")
    a = H.make_mesh(300, "PartA", (0, 0, 0))
    b = H.make_mesh(300, "PartB", (4, 0, 1))
    for o in (a, b):
        C.scene.collection.objects.unlink(o)
        coll.objects.link(o)
    e = _link("Instancer", None)
    e.instance_type = "COLLECTION"
    e.instance_collection = coll
    e.location = (10, 5, 0)
    _record(e)
    dg = C.evaluated_depsgraph_get()
    truth = [_world(np.array([v.co[:] for v in i.object.original.data.vertices]), np.array(i.matrix_world))
             for i in dg.object_instances if i.is_instance and i.parent and i.parent.original == e]
    assert len(truth) == 2, len(truth)
    _frame_and_check(e, np.vstack(truth))


def test_an_instanced_collection_with_lights_empties_and_hidden_objects():
    """Review of #117 (2): of an instanced collection only what renders is framed: its light, empty
    and an object hidden from renders (40 m away) are left out, as they are when not instanced."""
    _start()
    coll = bpy.data.collections.new("Parts")
    a = H.make_mesh(300, "PartA", (0, 0, 0))
    hidden = H.make_mesh(300, "Hidden", (40, 0, 0))
    hidden.hide_render = True
    lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
    lamp.location = (0, 30, 0)
    marker = bpy.data.objects.new("Marker", None)
    marker.location = (0, 0, 25)
    for o in (a, hidden):
        C.scene.collection.objects.unlink(o)
    for o in (a, hidden, lamp, marker):
        coll.objects.link(o)
    e = _link("Instancer", None)
    e.instance_type = "COLLECTION"
    e.instance_collection = coll
    e.location = (10, 5, 0)
    _record(e)
    dg = C.evaluated_depsgraph_get()
    truth = [_world(np.array([v.co[:] for v in i.object.original.data.vertices]), np.array(i.matrix_world))
             for i in dg.object_instances if i.is_instance and i.parent and i.parent.original == e
             and i.object.original == a]
    assert len(truth) == 1, len(truth)
    _frame_and_check(e, np.vstack(truth))



def test_every_rendering_type_with_a_history_counts():
    """Not only meshes: a curve with a history is framed next to a mesh with one; an object with no
    history, cameras, lights and objects hidden from renders are not."""
    _start()
    me = H.make_mesh(300, "Mesh", (0, 0, 0))
    cu = bpy.data.curves.new("Path", "CURVE")
    cu.splines.new("POLY").points.add(1)
    cur = _link("Path", cu)
    other = _link("Other", bpy.data.curves.new("Other", "CURVE"))
    hidden = _link("Hidden", bpy.data.curves.new("Hidden", "CURVE"))
    hidden.hide_render = True
    lamp = _link("Lamp", bpy.data.lights.new("Lamp", "POINT"))
    for ob in (me, cur, hidden, lamp):
        _record(ob)
    got = set(camrig.model_objects(C.scene))
    assert got == {me, cur}, sorted(o.name for o in got)
    assert other not in got


if __name__ == "__main__":
    H.run(test_a_curve_with_a_bevel)
    H.run(test_a_text)
    H.run(test_a_surface)
    H.run(test_a_metaball)
    H.run(test_grease_pencil)
    H.run(test_hair_curves)
    H.run(test_a_point_cloud)
    H.run(test_a_volume)
    H.run(test_an_empty_instancing_a_collection)
    H.run(test_an_instanced_collection_with_lights_empties_and_hidden_objects)
    H.run(test_every_rendering_type_with_a_history_counts)
    H.finish()
