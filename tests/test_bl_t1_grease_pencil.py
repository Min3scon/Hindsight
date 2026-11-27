"""D131: with Record everything, Grease Pencil drawings are recorded: strokes added and removed, points
moved, stroke and point attributes (radius, opacity, pressure, colour), layers and frames. Blender 4.3
and later keep strokes as curves with attributes; 4.2 keeps legacy strokes and points: both covered."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context
V3 = bpy.app.version >= (4, 3, 0)


def add_gp():
    if V3:
        bpy.ops.object.grease_pencil_add(type="MONKEY")
    else:
        bpy.ops.object.gpencil_add(type="MONKEY")
    return C.active_object


def layer_name(l):
    return l.name if V3 else l.info


def strokes_of(f):
    return f.drawing.strokes if V3 else f.strokes


def first_frame(gp):
    l = next(l for l in gp.layers if len(l.frames))
    return l, l.frames[0]


def state(ob):
    gp = ob.data
    out = []
    for l in gp.layers:
        fr = []
        for f in l.frames:
            if V3:
                d = f.drawing
                pos = [0.0] * (3 * len(d.attributes["position"].data))
                d.attributes["position"].data.foreach_get("vector", pos)
                rad = d.attributes.get("radius")
                r = [0.0] * len(rad.data) if rad is not None else []
                if rad is not None:
                    rad.data.foreach_get("value", r)
                fr.append((int(f.frame_number), len(d.strokes), round(sum(pos), 3), round(sum(r), 3)))
            else:
                n = sum(len(st.points) for st in f.strokes)
                co = round(sum(sum(p.co) for st in f.strokes for p in st.points), 3)
                pr = round(sum(p.pressure for st in f.strokes for p in st.points), 3)
                fr.append((int(f.frame_number), len(f.strokes), co, pr, n))
        out.append((layer_name(l), round(l.opacity, 3), tuple(fr)))
    return tuple(out)


def move_points(ob):
    l, f = first_frame(ob.data)
    if V3:
        a = f.drawing.attributes["position"]
        pos = [0.0] * (3 * len(a.data))
        a.data.foreach_get("vector", pos)
        a.data.foreach_set("vector", [x + 0.1 for x in pos])
        f.drawing.tag_positions_changed()
    else:
        for st in f.strokes:
            for p in st.points:
                p.co.x += 0.1


def thicker(ob):
    l, f = first_frame(ob.data)
    if V3:
        a = f.drawing.attributes.get("radius") or f.drawing.attributes.new("radius", "FLOAT", "POINT")
        a.data.foreach_set("value", [0.05] * len(a.data))
    else:
        for st in f.strokes:
            for p in st.points:
                p.pressure = 0.5


def remove_stroke(ob):
    l, f = first_frame(ob.data)
    if V3:
        f.drawing.remove_strokes(indices=[0])
    else:
        f.strokes.remove(f.strokes[0])


def add_stroke(ob):
    l, f = first_frame(ob.data)
    if V3:
        f.drawing.add_strokes([4])
    else:
        st = f.strokes.new()
        st.points.add(4)
        for i, p in enumerate(st.points):
            p.co = (i * 0.2, 0, 0)


def new_frame(ob):
    l, f = first_frame(ob.data)
    l.frames.new(5)


def test_drawings_come_back():
    L.fresh_scene()
    ob = add_gp()
    L.track_all()
    L.tick_like("baseline")
    states = [state(ob)]
    for label, fn in (("move", move_points), ("thicker", thicker), ("remove stroke", remove_stroke),
                      ("add stroke", add_stroke), ("frame", new_frame),
                      ("layer opacity", lambda o: setattr(o.data.layers[0], "opacity", 0.5))):
        fn(ob)
        L.tick_like(label)
        states.append(state(ob))
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(ob)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert state(ob) in states, (pos, state(ob))
    runtime._goto_history(h, 0, True)
    assert state(ob) == states[0], (state(ob), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert state(ob) == states[-1], (state(ob), states[-1])


if __name__ == "__main__":
    H.run(test_drawings_come_back)
    H.finish()
