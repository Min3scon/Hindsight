"""D121: with Record everything, the shape of curves (Bezier and NURBS splines and their points),
lattices (point positions and resolution) and metaballs (their elements) is recorded and comes back,
including adding and removing splines, points and elements."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def _r(v):
    return tuple(round(x, 4) for x in v)


def curve_state(ob):
    out = []
    for sp in ob.data.splines:
        if sp.type == "BEZIER":
            pts = tuple((_r(p.co), _r(p.handle_left), _r(p.handle_right), p.handle_left_type, round(p.radius, 4))
                        for p in sp.bezier_points)
        else:
            pts = tuple((_r(p.co), round(p.tilt, 4)) for p in sp.points)
        out.append((sp.type, sp.use_cyclic_u, sp.resolution_u, pts))
    return tuple(out)


def walk(h, states, read):
    assert all(x != y for x, y in zip(states, states[1:])), states
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert read() in states, (pos, read())
    runtime._goto_history(h, 0, True)
    assert read() == states[0], (read(), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


def run(ob, read, ops):
    L.track_all()
    L.tick_like("baseline")
    states = [read()]
    for label, fn in ops:
        fn()
        L.tick_like(label)
        states.append(read())
    walk(runtime.history_of(ob), states, read)


def test_curve_shape():
    L.fresh_scene()
    bpy.ops.curve.primitive_bezier_curve_add(location=(0, 3, 0))
    ob = C.active_object
    c = ob.data

    def move_point():
        c.splines[0].bezier_points[0].co = (-2.0, 0.5, 0.25)

    def handle_type():
        c.splines[0].bezier_points[1].handle_left_type = "VECTOR"

    def add_point():
        c.splines[0].bezier_points.add(1)
        c.splines[0].bezier_points[-1].co = (3, 1, 0)

    def add_spline():
        sp = c.splines.new("POLY")
        sp.points.add(2)
        for i, p in enumerate(sp.points):
            p.co = (i, -1, 0, 1)
            p.tilt = 0.1 * i

    def remove_spline():
        c.splines.remove(c.splines[0])
    run(ob, lambda: curve_state(ob), (
        ("move point", move_point), ("radius", lambda: setattr(c.splines[0].bezier_points[0], "radius", 2.0)),
        ("handle type", handle_type), ("cyclic", lambda: setattr(c.splines[0], "use_cyclic_u", True)),
        ("add point", add_point), ("add spline", add_spline), ("remove spline", remove_spline)))


def test_lattice_and_metaball():
    L.fresh_scene()
    bpy.ops.object.add(type="LATTICE", location=(0, -3, 0))
    lat = C.active_object
    bpy.ops.object.metaball_add(type="BALL", location=(3, 0, 0))
    mb = C.active_object
    read = lambda: (lat.data.points_u, tuple(_r(p.co_deform) for p in lat.data.points),
                    tuple((e.type, _r(e.co), round(e.radius, 4)) for e in mb.data.elements))
    L.track_all()
    L.tick_like("baseline")
    states = [read()]

    def bend():
        lat.data.points[0].co_deform = (-1.0, -1.0, -1.0)

    def add_el():
        e = mb.data.elements.new(type="CAPSULE")
        e.co = (1, 0, 0)
    for label, fn in (("bend", bend), ("resolution", lambda: setattr(lat.data, "points_u", 3)),
                      ("bend again", lambda: setattr(lat.data.points[4], "co_deform", (0.0, 0.7, 0.0))),
                      ("radius", lambda: setattr(mb.data.elements[0], "radius", 1.5)),
                      ("element", add_el)):
        fn()
        L.tick_like(label)
        states.append(read())
    assert all(x != y for x, y in zip(states, states[1:])), states
    hists = [runtime.history_of(lat), runtime.history_of(mb)]
    for h in hists:
        while h.cursor > 0:
            runtime._goto_history(h, h.cursor - 1, True)
    assert read() == states[0], (read(), states[0])
    for h in hists:
        runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


if __name__ == "__main__":
    H.run(test_curve_shape)
    H.run(test_lattice_and_metaball)
    H.finish()
