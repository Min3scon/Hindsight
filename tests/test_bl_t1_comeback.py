"""D122: with the scene recorded, a deleted light, camera, curve, text, lattice or metaball comes back
with its settings and shape even after its data left the file (saving and opening drops data that
nothing uses), not only while the data is still in the session."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def make():
    bpy.ops.object.light_add(type="SPOT", location=(0, 0, 4))
    lamp = C.active_object
    lamp.data.energy, lamp.data.spot_size = 1234.0, 0.9
    bpy.ops.object.camera_add(location=(0, -6, 2))
    cam = C.active_object
    cam.data.lens, cam.data.dof.use_dof = 70.0, True
    bpy.ops.object.text_add(location=(3, 0, 0))
    txt = C.active_object
    txt.data.body, txt.data.extrude = "Back", 0.1
    bpy.ops.curve.primitive_bezier_curve_add(location=(-3, 0, 0))
    crv = C.active_object
    crv.data.bevel_depth = 0.2
    crv.data.splines[0].bezier_points[0].co = (-1.5, 0.5, 0)
    bpy.ops.object.add(type="LATTICE", location=(0, 3, 0))
    lat = C.active_object
    lat.data.points_u = 3
    lat.data.points[0].co_deform = (-0.9, -0.9, -0.9)
    bpy.ops.object.metaball_add(type="CAPSULE", location=(3, 3, 0))
    mb = C.active_object
    mb.data.elements[0].radius = 1.3
    return [lamp, cam, txt, crv, lat, mb]


def state(name):
    ob = bpy.data.objects.get(name)
    if ob is None:
        return None
    d = ob.data
    t = ob.type
    if t == "LIGHT":
        return (t, d.type, round(d.energy, 3), round(d.spot_size, 3))
    if t == "CAMERA":
        return (t, round(d.lens, 3), d.dof.use_dof)
    if t == "FONT":
        return (t, d.body, round(d.extrude, 3))
    if t == "CURVE":
        return (t, round(d.bevel_depth, 3), tuple(round(x, 3) for x in d.splines[0].bezier_points[0].co))
    if t == "LATTICE":
        return (t, d.points_u, tuple(round(x, 3) for x in d.points[0].co_deform))
    return (t, round(d.elements[0].radius, 3), d.elements[0].type)


def test_deleted_objects_come_back_after_their_data_is_gone():
    L.fresh_scene()
    obs = make()
    names = [o.name for o in obs]
    L.track_all()
    L.tick_like("baseline")
    want = {n: state(n) for n in names}
    datas = [(o.data.name, type(o.data)) for o in obs]
    with C.temp_override(selected_objects=obs, active_object=obs[0], object=obs[0]):
        bpy.ops.object.delete()
    L.tick_like("delete")
    assert all(bpy.data.objects.get(n) is None for n in names)
    for dn, dt in datas:                       # what saving and opening does to data nothing uses
        coll = {bpy.types.Camera: bpy.data.cameras, bpy.types.Lattice: bpy.data.lattices,
                bpy.types.MetaBall: bpy.data.metaballs}.get(dt)
        if coll is None:
            coll = bpy.data.lights if issubclass(dt, bpy.types.Light) else bpy.data.curves
        d = coll.get(dn)
        if d is not None:
            coll.remove(d)
    h = runtime.history_of(C.scene)
    runtime._goto_history(h, h.cursor - 1, True)
    got = {n: state(n) for n in names}
    assert got == want, (got, want)
    runtime._goto_history(h, h.cursor + 1, True)
    assert all(bpy.data.objects.get(n) is None for n in names)


if __name__ == "__main__":
    H.run(test_deleted_objects_come_back_after_their_data_is_gone)
    H.finish()
