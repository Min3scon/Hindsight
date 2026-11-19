"""A part of what an object records that can't be read (a bug, an add-on's broken data) is left out
with one log line; the rest of the object is still recorded, and the object recorder isn't switched
off for every object."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import adapters, datarec, guard, runtime


def test_one_broken_part_leaves_the_rest():
    L.fresh_scene()
    L.track_all()
    L.tick_like("baseline")
    a = bpy.data.objects["A"]
    real = adapters.settings_of

    def broken(idb, *args):
        if getattr(idb, "name", "") == "A":
            raise RuntimeError("simulated")
        return real(idb, *args)
    adapters.settings_of = broken
    try:
        a.location.x += 1
        bpy.data.objects["B"].location.y += 1
        L.tick_like("move")
    finally:
        adapters.settings_of = real
    assert guard.ok("adapter:object"), "the object recorder stays on"
    h = runtime.history_of(a)
    assert h is not None and h.entries, "the move of A is recorded"
    hb = runtime.history_of(bpy.data.objects["B"])
    assert hb is not None and hb.entries


if __name__ == "__main__":
    H.run(test_one_broken_part_leaves_the_rest)
    H.finish()
