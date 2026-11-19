"""D137: with Record everything, a particle system's settings (count, hair length, emission, display,
...) are recorded with the object, and settings several objects share are recorded once, by the first
of them by name."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def add_system(ob):
    m = ob.modifiers.new("Hair", "PARTICLE_SYSTEM")
    return m.particle_system.settings


def test_particle_settings_come_back():
    L.fresh_scene()
    a = bpy.data.objects["A"]
    st = add_system(a)
    st.type = "HAIR"
    L.track_all()
    L.tick_like("baseline")
    read = lambda: (st.count, round(st.hair_length, 4), st.display_step, st.emit_from)
    states = [read()]
    for label, fn in (("count", lambda: setattr(st, "count", 321)),
                      ("length", lambda: setattr(st, "hair_length", 0.6)),
                      ("display", lambda: setattr(st, "display_step", 4)),
                      ("emit", lambda: setattr(st, "emit_from", "VERT"))):
        fn()
        L.tick_like(label)
        states.append(read())
    assert all(x != y for x, y in zip(states, states[1:])), states
    h = runtime.history_of(a)
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        assert read() in states, (pos, read())
    runtime._goto_history(h, 0, True)
    assert read() == states[0], (read(), states[0])
    runtime._goto_history(h, len(h.entries), True)
    assert read() == states[-1], (read(), states[-1])


def test_shared_settings_recorded_once():
    L.fresh_scene()
    a, b = bpy.data.objects["A"], bpy.data.objects["B"]
    st = add_system(a)
    mb = b.modifiers.new("Hair", "PARTICLE_SYSTEM")
    mb.particle_system.settings = st
    L.track_all()
    L.tick_like("baseline")
    hb = runtime.history_of(b)
    nb = len(hb.entries) if hb else 0
    st.count = 77
    L.tick_like("count")
    hb = runtime.history_of(b)
    assert (len(hb.entries) if hb else 0) == nb, "recorded once, with A"
    h = runtime.history_of(a)
    runtime._goto_history(h, h.cursor - 1, True)
    assert st.count != 77


if __name__ == "__main__":
    H.run(test_particle_settings_come_back)
    H.run(test_shared_settings_recorded_once)
    H.finish()
