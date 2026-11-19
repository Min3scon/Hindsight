"""D108: with Record everything, adding, removing, renaming and reordering vertex groups and shape
keys, and which UV map is active or renders, come back when going back and forward. Before, the step
was made but going back left the lists as they were (a removed group didn't come back, a renamed one
kept its new name and its weights went to no group)."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import numpy as np
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import runtime

C = bpy.context


def snap(ob):
    me = ob.data
    vg = [(g.name, g.lock_weight) for g in ob.vertex_groups]
    w = {}
    for g in ob.vertex_groups:
        arr = np.full(len(me.vertices), -1.0, np.float32)
        for v in me.vertices:
            for x in v.groups:
                if x.group == g.index:
                    arr[v.index] = round(x.weight, 6)
        w[g.name] = arr.tobytes()
    sk = None
    if me.shape_keys is not None:
        sk = []
        for kb in me.shape_keys.key_blocks:
            co = np.empty(len(kb.data) * 3, np.float32)
            kb.data.foreach_get("co", co)
            sk.append((kb.name, round(kb.value, 6), kb.mute, kb.relative_key.name, co.tobytes()))
    uv = [(u.name, u.active, u.active_render) for u in me.uv_layers]
    return {"vg": vg, "w": w, "vga": ob.vertex_groups.active_index, "sk": sk, "uv": uv}


def op(label, fn, states, ob):
    fn()
    L.tick_like(label)
    states.append(snap(ob))


def test_lists_come_back():
    L.fresh_scene()
    L.track_all()
    ob = bpy.data.objects["A"]
    n = len(ob.data.vertices)
    states = [snap(ob)]
    op("add group", lambda: ob.vertex_groups.new(name="Arm").add(list(range(0, n, 2)), 0.5, "REPLACE"), states, ob)
    op("add group 2", lambda: ob.vertex_groups.new(name="Leg").add(list(range(1, n, 3)), 0.25, "REPLACE"), states, ob)
    op("rename", lambda: setattr(ob.vertex_groups["Arm"], "name", "Hand"), states, ob)
    op("lock", lambda: setattr(ob.vertex_groups["Leg"], "lock_weight", True), states, ob)

    def move_up():
        ob.vertex_groups.active_index = 1
        with C.temp_override(object=ob, active_object=ob):
            bpy.ops.object.vertex_group_move(direction="UP")
    op("reorder", move_up, states, ob)
    op("remove", lambda: ob.vertex_groups.remove(ob.vertex_groups["Hand"]), states, ob)
    op("basis", lambda: ob.shape_key_add(name="Basis", from_mix=False), states, ob)

    def smile():
        kb = ob.shape_key_add(name="Smile", from_mix=False)
        co = np.empty(n * 3, np.float32)
        kb.data.foreach_get("co", co)
        co[:30] += 0.2
        kb.data.foreach_set("co", co)
        kb.value = 0.4
    op("smile", smile, states, ob)
    op("rename key", lambda: setattr(ob.data.shape_keys.key_blocks["Smile"], "name", "Grin"), states, ob)
    op("uv", lambda: ob.data.uv_layers.new(name="Second"), states, ob)

    def uv_active():
        ob.data.uv_layers.active = ob.data.uv_layers["Second"]
        ob.data.uv_layers["Second"].active_render = True
    op("uv active", uv_active, states, ob)
    op("remove key", lambda: ob.shape_key_remove(ob.data.shape_keys.key_blocks["Grin"]), states, ob)
    h = runtime.history_of(ob)
    assert len(h.entries) >= len(states) - 1 - 1, (len(h.entries), len(states))
    # every position of the object's own history is one of the states, in order
    for pos in list(range(len(h.entries), -1, -1)) + list(range(len(h.entries) + 1)):
        runtime._goto_history(h, pos, True)
        got = snap(ob)
        assert got in states, f"position {pos}: {got['vg']} {got['sk'] and [k[0] for k in got['sk']]} {got['uv']}"
    assert snap(ob) == states[-1]
    runtime._goto_history(h, 0, True)
    assert snap(ob) == states[0], "back at the start: no groups, no keys, one UV map"


if __name__ == "__main__":
    H.run(test_lists_come_back)
    H.finish()
