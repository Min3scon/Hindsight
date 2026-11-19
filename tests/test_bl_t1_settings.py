"""D117: with Record everything, an object's own settings (viewport display, ray visibility,
instancing, empty display, locks, ...) and its data's settings (lights, cameras with depth of field,
curves and text, metaballs, a mesh's remesh settings) are recorded and come back. Sculpt symmetry and
other tool options are not steps."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.bl import runtime

C = bpy.context


def _r(v):
    if isinstance(v, str) or v is None:
        return v
    return tuple(round(x, 5) for x in v) if hasattr(v, "__len__") else round(v, 5)


def walk(h, states, read):
    assert all(x != y for x, y in zip(states, states[1:])), states
    assert len(h.entries) >= len(states) - 1, (len(h.entries), len(states))
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert read() in states, (pos, read(), states)
    runtime._goto_history(h, 0, True)
    assert read() == states[0], (read(), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


def run_ops(ob, read, ops):
    L.track_all()
    L.tick_like("baseline")
    states = [read()]
    for label, fn in ops:
        fn()
        L.tick_like(label)
        states.append(read())
    walk(runtime.history_of(ob), states, read)


def test_object_settings():
    L.fresh_scene()
    a = bpy.data.objects["A"]
    read = lambda: (a.display_type, a.show_wire, a.show_name, _r(a.color), a.pass_index, a.visible_shadow,
                    tuple(a.lock_location), a.show_in_front)
    run_ops(a, read, (
        ("display", lambda: setattr(a, "display_type", "WIRE")),
        ("wire", lambda: setattr(a, "show_wire", True)),
        ("name", lambda: setattr(a, "show_name", True)),
        ("colour", lambda: setattr(a, "color", (1, 0, 0, 1))),
        ("pass", lambda: setattr(a, "pass_index", 3)),
        ("shadow", lambda: setattr(a, "visible_shadow", False)),
        ("lock", lambda: setattr(a, "lock_location", (True, False, True))),
        ("in front", lambda: setattr(a, "show_in_front", True))))


def test_light_camera_curve_text_data():
    L.fresh_scene()
    bpy.ops.object.light_add(type="SPOT", location=(0, 0, 4))
    lamp = C.active_object
    bpy.ops.object.camera_add(location=(0, -6, 2))
    cam = C.active_object
    bpy.ops.object.text_add(location=(3, 0, 0))
    txt = C.active_object
    bpy.ops.curve.primitive_bezier_curve_add(location=(-3, 0, 0))
    crv = C.active_object
    read = lambda: (_r(lamp.data.energy), _r(lamp.data.color), _r(lamp.data.spot_size), _r(cam.data.lens),
                    cam.data.dof.use_dof, _r(cam.data.dof.aperture_fstop), txt.data.body,
                    _r(txt.data.size), _r(crv.data.bevel_depth), crv.data.resolution_u)
    L.track_all()
    L.tick_like("baseline")
    states = [read()]
    ops = (("energy", lambda: setattr(lamp.data, "energy", 2500.0)),
           ("light colour", lambda: setattr(lamp.data, "color", (0.2, 0.5, 1.0))),
           ("spot", lambda: setattr(lamp.data, "spot_size", 1.0)),
           ("lens", lambda: setattr(cam.data, "lens", 85.0)),
           ("dof", lambda: setattr(cam.data.dof, "use_dof", True)),
           ("fstop", lambda: setattr(cam.data.dof, "aperture_fstop", 1.4)),
           ("body", lambda: setattr(txt.data, "body", "Hindsight")),
           ("size", lambda: setattr(txt.data, "size", 2.0)),
           ("bevel", lambda: setattr(crv.data, "bevel_depth", 0.1)),
           ("resolution", lambda: setattr(crv.data, "resolution_u", 24)))
    for label, fn in ops:
        fn()
        L.tick_like(label)
        states.append(read())
    hists = [runtime.history_of(o) for o in (lamp, cam, txt, crv)]
    assert all(h is not None for h in hists)
    for h in hists:
        while h.cursor > 0:
            runtime._goto_history(h, h.cursor - 1, True)
    assert read() == states[0], (read(), states[0])
    for h in hists:
        runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


def test_mesh_settings_and_symmetry():
    L.fresh_scene()
    a = bpy.data.objects["A"]
    L.track_all()
    L.tick_like("baseline")
    h0 = runtime.history_of(a)
    n = len(h0.entries) if h0 else 0
    a.use_mesh_mirror_x = True
    a.data.use_mirror_y = not a.data.use_mirror_y
    L.tick_like("symmetry")
    h = runtime.history_of(a)
    assert (len(h.entries) if h else 0) == n, "sculpt symmetry is a tool option: no step"
    T.select("A")                       # a panel shows the active object's settings
    a.data.remesh_voxel_size = 0.05     # (this one doesn't tell the depsgraph)
    L.tick_like("voxel size")
    h = runtime.history_of(a)
    assert len(h.entries) == n + 1
    runtime._goto_history(h, h.cursor - 1, True)
    assert round(a.data.remesh_voxel_size, 5) != 0.05


if __name__ == "__main__":
    H.run(test_object_settings)
    H.run(test_light_camera_curve_text_data)
    H.run(test_mesh_settings_and_symmetry)
    H.finish()
