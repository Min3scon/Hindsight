"""D-d: custom normals (Blender 5.0+: the `custom_normal` attribute, INT16_2D on corners) are recorded.
Before, meshio._recordable dropped the type, so going back over Set Custom Normals left the new
normals on the old mesh.

Blender 4.2 and 4.4 keep custom normals in a hidden layer, not an attribute: the Blender part of the
test is skipped there (printed), the channel type part (INT16_2D through types, deltas, steps and the
engine) runs everywhere."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import numpy as np
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import meshio, runtime
from hindsight.core import delta, step as stepmod
from hindsight.core.types import ADD, CHANNEL_TYPES, DELTA, Step, key_dtype, key_width, make_key

KEY = make_key("custom_normal", "CORNER", "INT16_2D")


def test_int16_2d_channel_type():
    assert CHANNEL_TYPES["INT16_2D"][:2] == (np.int16, 2) and CHANNEL_TYPES["INT16_2D"][2] == "value"
    assert key_dtype(KEY) == np.int16 and key_width(KEY) == 2
    rng = np.random.default_rng(3)
    old = rng.integers(-32768, 32768, 400, dtype=np.int16)
    new = old.copy()
    new[20:90] = rng.integers(-32768, 32768, 70, dtype=np.int16)
    d = delta.diff(KEY, old, new)
    assert d is not None and d.vals.dtype == np.uint16 and d.vals.shape[1] == 2
    for codec in ("zstd", "zlib", "none"):
        st = Step(1, "Set Custom Normals", "OBJECT", "o", 1.0, DELTA, [d])
        back = stepmod.step_from_bytes(stepmod.step_to_bytes(st, codec))
        arr = old.copy()
        delta.apply(arr, back.deltas[0], True)
        assert arr.tobytes() == new.tobytes(), codec
        delta.apply(arr, back.deltas[0], False)
        assert arr.tobytes() == old.tobytes(), codec
    print("PASS test_int16_2d_channel_type")


def test_custom_normals_come_back():
    if bpy.app.version < (5, 0, 0):
        print(f"SKIP test_custom_normals_come_back: Blender {bpy.app.version_string} keeps custom normals "
              "in a hidden layer, not an attribute (5.0+ only)")
        return
    L.fresh_scene()
    L.track_all()
    ob = bpy.data.objects["A"]
    me = ob.data

    def state():
        a = me.attributes.get("custom_normal")
        if a is None:
            return None
        out = np.empty(len(a.data) * 2, np.int16)
        a.data.foreach_get("value", out)
        return out.tobytes()

    assert state() is None
    assert runtime.history_of(ob) is None or not runtime.history_of(ob).entries
    n0 = 0
    loops = len(me.loops)
    me.normals_split_custom_set([(0.0, 0.0, 1.0)] * loops)
    L.tick_like("Set Custom Normals")
    one = state()
    assert one is not None
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) >= 1, "Set Custom Normals made no step"
    me.normals_split_custom_set([(1.0, 0.0, 0.0)] * loops)
    L.tick_like("Set Custom Normals")
    two = state()
    assert two != one and len(h.entries) >= n0 + 2, len(h.entries)
    runtime._goto_history(h, len(h.entries) - 1, True)
    assert state() == one, "going back one step kept the newest normals"
    runtime._goto_history(h, n0, True)
    assert state() is None, "going back to the start kept custom normals on the old mesh"
    runtime._goto_history(h, len(h.entries), True)
    assert state() == two
    print("PASS test_custom_normals_come_back")


if __name__ == "__main__":
    H.run(test_int16_2d_channel_type)
    H.run(test_custom_normals_come_back)
    H.finish()
