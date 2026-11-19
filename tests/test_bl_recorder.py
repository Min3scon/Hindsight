import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time
import bpy
import numpy as np
import tests.harness as H
from hindsight.bl import runtime
from hindsight.core.types import SETTINGS

C = bpy.context


def record(obj, channels, seeds):
    states = [H.snapshot(obj)]
    for ch, s in zip(channels, seeds):
        H.fake_stroke(obj, s, n=60, channel=ch)
        step = H.commit(obj)
        assert step is not None, f"no step for {ch}"
        states.append(H.snapshot(obj))
    return states


def test_first_stroke_starts_history():
    H.fresh()
    ob = H.make_mesh(2000)
    assert runtime.history_of(ob) is None
    H.enter(ob, "SCULPT")
    assert runtime.history_of(ob) is None, "entering a mode must record nothing"
    assert runtime.scrub_info(C) is None, "scrubber hidden before first stroke"
    assert H.commit(ob) is None, "no change -> no step"
    H.fake_stroke(ob, 1)
    assert H.commit(ob) is not None
    h = runtime.history_of(ob)
    assert len(h.entries) == 1 and h.cursor == 1
    info = runtime.scrub_info(C)
    assert info["pos"] == 1 and info["len"] == 1 and info["cap"] == 10000


def test_all_channels_scrub_exact():
    H.fresh()
    ob = H.make_mesh(3000)
    H.enter(ob, "SCULPT")
    chans = ["position", "mask", "fset", "hide", "color", "bytecolor", "position", "mask", "position"] * 3
    states = record(ob, chans, range(100, 100 + len(chans)))
    h = runtime.history_of(ob)
    assert len(h.entries) == len(chans)
    rng = np.random.default_rng(5)
    targets = list(rng.integers(0, len(chans) + 1, 40)) + [0, len(chans), 0, 1, len(chans) - 1, len(chans)]
    for t in targets:
        runtime.goto(C, int(t))
        assert h.cursor == t
        H.assert_same(H.snapshot(ob), states[t], f"goto {t}")


def test_vertex_paint_starts_history():
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "VERTEX_PAINT")
    s0 = H.snapshot(ob)
    H.fake_stroke(ob, 3, channel="bytecolor")
    assert H.commit(ob) is not None
    runtime.step(C, -1)
    H.assert_same(H.snapshot(ob), s0)


def test_vertex_paint_layer_added_late():
    """D71: Blender 5.3 adds the colour layer of a mesh without one after Vertex Paint's first
    update. Entering and leaving must record nothing, and the first stroke's step back keeps the layer."""
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "OBJECT")
    with runtime.suspended():
        bpy.ops.object.mode_set(mode="VERTEX_PAINT")
        layer = ob.data.color_attributes[0]
        name, dtype, domain = layer.name, layer.data_type, layer.domain
        ob.data.color_attributes.remove(layer)              # as 5.3 has it at the first update
    runtime.check_mode_transition()
    ob.data.color_attributes.new(name, dtype, domain)       # Blender adds it
    ob.data.color_attributes.active_color_name = name
    C.view_layer.update()
    runtime.check_mode_transition()
    for uid in list(runtime._DIRTY):
        runtime._DIRTY[uid] = time.time() - 1.0
    runtime.tick()
    assert runtime.history_of(ob) is None or not runtime.history_of(ob).entries, "entering recorded a step"
    s0 = H.snapshot(ob)
    H.fake_stroke(ob, 3, channel="bytecolor")
    assert H.commit(ob) is not None
    runtime.step(C, -1)
    H.assert_same(H.snapshot(ob), s0)
    H.enter(ob, "OBJECT")


def test_restored_colour_layer_is_active():
    """D74: a colour layer taken away and put back by scrubbing is the active and default colour
    again (Blender 5.0 forgot them, so entering Vertex Paint then added a layer of its own)."""
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 4, channel="color")
    assert H.commit(ob) is not None
    ca = ob.data.color_attributes
    assert ca.active_color_name == "Col" and ca.default_color_name == "Col"
    runtime.step(C, -1)
    assert "Col" not in ca
    runtime.step(C, 1)
    assert ca.active_color_name == "Col" and ca.default_color_name == "Col", (ca.active_color_name,
                                                                            ca.default_color_name)
    H.enter(ob, "VERTEX_PAINT")
    assert [a.name for a in ca] == ["Col"], "Blender added a colour layer"
    H.enter(ob, "OBJECT")


def test_step_back_forward_ops():
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "SCULPT")
    st = record(ob, ["position"] * 3, [1, 2, 3])
    bpy.ops.hindsight.step_back()
    H.assert_same(H.snapshot(ob), st[2])
    bpy.ops.hindsight.step_back()
    bpy.ops.hindsight.step_forward()
    H.assert_same(H.snapshot(ob), st[2])
    # keymaps exist for Sculpt mode (Ctrl and Cmd variants)
    km = C.window_manager.keyconfigs.addon.keymaps.get("Sculpt")
    ids = [(k.idname, k.ctrl, k.oskey, k.shift) for k in km.keymap_items] if km else []
    assert ("hindsight.step_back", True, False, False) in ids
    assert ("hindsight.step_forward", False, True, True) in ids
    assert ("hindsight.step_back", False, True, False) in ids
    assert ("hindsight.step_forward", True, False, True) in ids
    km = C.window_manager.keyconfigs.addon.keymaps.get("Vertex Paint")
    assert km is not None and any(k.idname == "hindsight.step_back" for k in km.keymap_items)


def test_branch_silent_and_confirm():
    H.fresh()
    SETTINGS.truncation_threshold = 3
    ob = H.make_mesh(1500)
    H.enter(ob, "SCULPT")
    st = record(ob, ["position"] * 6, range(10, 16))
    h = runtime.history_of(ob)
    runtime.goto(C, 4)            # 2 future steps < threshold -> silent truncation
    H.fake_stroke(ob, 99)
    assert H.commit(ob) is not None
    assert len(h.entries) == 5 and h.cursor == 5
    runtime.goto(C, 1)            # 4 future steps >= threshold -> stroke reverted, confirmation pending
    before = H.snapshot(ob)
    H.fake_stroke(ob, 98)
    assert H.commit(ob) is None
    H.assert_same(H.snapshot(ob), before, "stroke must be reverted")
    assert len(h.entries) == 5
    n = runtime.confirm_truncate(ob)
    assert n == 4 and len(h.entries) == 1
    H.fake_stroke(ob, 98)         # user repeats the stroke
    assert H.commit(ob) is not None and len(h.entries) == 2
    SETTINGS.truncation_threshold = 10


def test_native_undo_sync_and_f9_replace():
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "SCULPT")
    st = record(ob, ["position"] * 3, [1, 2, 3])
    h = runtime.history_of(ob)
    # simulate native undo restoring the previous state
    from hindsight.bl import meshio
    meshio.write_changes(ob.data, st[2], ["position|POINT|FLOAT_VECTOR"])
    runtime.on_undo()                         # Blender calls this after its own undo
    assert len(h.entries) == 3 and h.cursor == 2, "native undo must move the cursor, not add a step"
    # F9: operator re-executed with new settings -> replaces the same step silently
    SETTINGS.truncation_threshold = 1
    H.fake_stroke(ob, 77)
    assert H.commit(ob) is not None
    assert len(h.entries) == 3 and h.cursor == 3
    SETTINGS.truncation_threshold = 10


def test_edit_mode_grouping():
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    H.commit(ob)
    h = runtime.history_of(ob)
    H.enter(ob, "EDIT")
    H.enter(ob, "OBJECT")
    assert len(h.entries) == 1, "edit mode without changes records nothing"
    s1 = H.snapshot(ob)
    H.enter(ob, "EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.transform.translate(value=(0.1, 0, 0))
    bpy.ops.mesh.faces_shade_flat()
    bpy.ops.transform.translate(value=(0, 0.1, 0))
    H.enter(ob, "OBJECT")
    assert len(h.entries) == 2, f"edit mode changes = one step, got {len(h.entries)}"
    s2 = H.snapshot(ob)
    runtime.goto(C, 1)
    H.assert_same(H.snapshot(ob), s1)
    runtime.goto(C, 2)
    H.assert_same(H.snapshot(ob), s2)


def test_topology_snapshot():
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    H.commit(ob)
    s1 = H.snapshot(ob)
    H.enter(ob, "EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide()
    H.enter(ob, "SCULPT")
    h = runtime.history_of(ob)
    assert len(h.entries) == 2 and h.entries[1].kind == "TOPO"
    s2 = H.snapshot(ob)
    H.fake_stroke(ob, 2)
    H.commit(ob)
    s3 = H.snapshot(ob)
    runtime.goto(C, 1)
    assert ob.mode == "SCULPT"
    H.assert_same(H.snapshot(ob), s1)
    runtime.goto(C, 3)
    H.assert_same(H.snapshot(ob), s3)
    runtime.goto(C, 2)
    H.assert_same(H.snapshot(ob), s2)


def test_topology_step_owns_its_before():
    """Memory work: a topology step takes the state's arrays as its `before` instead of copying
    them; the state must get its own arrays right after, so later strokes never touch the step."""
    import numpy as np
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    H.commit(ob)
    s0 = H.snapshot(ob)
    H.enter(ob, "EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide()
    H.enter(ob, "SCULPT")
    h = runtime.history_of(ob)
    step = h.entries[-1].step
    assert step is not None and step.kind == "TOPO"
    st = runtime._STATES[runtime.obj_uid(ob)]
    from hindsight.core.topopack import TOPO_KEYS
    for k, a in st.arrays.items():
        if k in TOPO_KEYS:
            # R080: the state holds the step's own topology (`after`, the same key) read-only, never a copy;
            # a write fails instead of changing the step, and nothing else is shared
            assert not a.flags.writeable, k
            try:
                a[...] = a
                wrote = True
            except ValueError:
                wrote = False
            assert not wrote, ("a write into the shared topology went through", k)
            others = list(step.before.arrays.values()) + [b for kk, b in step.after.arrays.items() if kk != k]
        else:
            others = list(step.before.arrays.values()) + list(step.after.arrays.values())
        for b in others:
            assert not np.shares_memory(a, b), k
    for seed in (3, 4, 5):
        H.fake_stroke(ob, seed)
        H.commit(ob)
    runtime.goto(C, 1)
    H.assert_same(H.snapshot(ob), s0)


def test_fingerprint_streamed_same_digest():
    """Memory work: meshio.fingerprint hashes one array at a time; the digest must be the one of
    the whole snapshot (fingerprints are saved in files, D46), and it must not copy the whole mesh.
    R060: the value is xxh3-128 with the tag "x3:"; the blake2b form older builds saved is still made
    from the same bytes (to compare with such a value)."""
    import hashlib
    import tracemalloc
    import numpy as np
    from hindsight.bl import meshio
    from hindsight.core.native import ENGINE

    def reference(me, ob=None, algo="x3"):
        snap = meshio.read_snapshot(me, ob=ob, extras=ob is not None)
        h = hashlib.blake2b(digest_size=16) if algo == "blake2b" else ENGINE.Hasher128()
        h.update(repr(sorted(snap.counts.items())).encode())
        for key in sorted(snap.arrays):
            a = np.ascontiguousarray(snap.arrays[key])
            h.update(key.encode() + b"\0" + str(a.dtype).encode() + b"\0")
            h.update(a.view(np.uint8))
        return ("x3:" if algo == "x3" else "") + h.hexdigest()
    H.fresh()
    ob = H.make_mesh(20000)
    for ch in ("mask", "fset", "hide", "color", "bytecolor"):
        H.fake_stroke(ob, 7, n=300, channel=ch)
    g = ob.vertex_groups.new(name="G")
    g.add(list(range(0, 4000, 3)), 0.25, "REPLACE")
    ob.shape_key_add(name="Basis")
    ob.shape_key_add(name="K").data[5].co.x += 1.0
    me = ob.data
    assert meshio.fingerprint(me) == reference(me)
    assert meshio.fingerprint(me, ob) == reference(me, ob)
    assert meshio.fingerprint(me, ob) != meshio.fingerprint(me)
    assert meshio.fingerprint(me, None, "blake2b") == reference(me, None, "blake2b")
    assert meshio.fingerprint(me, ob, "blake2b") == reference(me, ob, "blake2b")
    whole = meshio.read_snapshot(me).nbytes()
    tracemalloc.start()
    meshio.fingerprint(me)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert peak < 0.5 * whole, peak / whole


def test_turn_off_on_and_reset():
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "SCULPT")
    st = record(ob, ["position"] * 3, [1, 2, 3])
    h = runtime.history_of(ob)
    runtime.turn_off(C, ob)
    H.fake_stroke(ob, 50)
    assert H.commit(ob) is None and len(h.entries) == 3
    runtime.turn_on(C, ob)
    assert len(h.entries) == 4, "changes while off become one bridging step"
    runtime.goto(C, 3)
    H.assert_same(H.snapshot(ob), st[3])
    runtime.goto(C, 4)
    n = runtime.delete_past(C, ob)
    assert n == 4 and h.cursor == 0 and len(h.entries) == 0
    st2 = record(ob, ["position"] * 2, [7, 8])
    runtime.reset_history(C, ob)
    assert len(h.entries) == 0 and runtime.scrub_info(C)["len"] == 0
    runtime.reset_and_turn_off(C, ob)
    assert runtime.history_of(ob) is None
    H.fake_stroke(ob, 9)
    assert H.commit(ob) is None, "reset and turn off records nothing"
    runtime.turn_on(C, ob)
    H.fake_stroke(ob, 10)
    assert H.commit(ob) is not None


def test_delete_past_keeps_later():
    H.fresh()
    ob = H.make_mesh(1500)
    H.enter(ob, "SCULPT")
    st = record(ob, ["position"] * 6, range(6))
    h = runtime.history_of(ob)
    runtime.goto(C, 3)
    from hindsight.core.persist import registry_to_bytes
    size_before = len(registry_to_bytes(runtime.reg()))
    assert runtime.delete_past(C, ob) == 3
    assert len(registry_to_bytes(runtime.reg())) < size_before, "saved history must shrink"
    assert len(h.entries) == 3 and h.cursor == 0
    runtime.goto(C, 3)
    H.assert_same(H.snapshot(ob), st[6])
    runtime.goto(C, 0)
    H.assert_same(H.snapshot(ob), st[3])


def test_per_object_and_global():
    H.fresh()
    a = H.make_mesh(1200, "A", (-2, 0, 0))
    b = H.make_mesh(1200, "B", (2, 0, 0))
    H.enter(a, "SCULPT")
    sa = [H.snapshot(a)]
    H.fake_stroke(a, 1); H.commit(a); sa.append(H.snapshot(a))
    H.enter(b, "SCULPT")                     # like Alt+Q
    sb = [H.snapshot(b)]
    H.fake_stroke(b, 2); H.commit(b); sb.append(H.snapshot(b))
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 3); H.commit(a); sa.append(H.snapshot(a))
    assert len(runtime.history_of(a).entries) == 2 and len(runtime.history_of(b).entries) == 1
    # global timeline: A1, B1, A2
    runtime.set_global(C, True)
    runtime.set_preserve_modes(C, False)
    info = runtime.scrub_info(C)
    assert info["global"] and info["len"] == 3 and info["pos"] == 3
    runtime.goto(C, 1)
    H.assert_same(H.snapshot(a), sa[1]); H.assert_same(H.snapshot(b), sb[0])
    assert C.view_layer.objects.active == a, "preserve modes off: no object switch"
    runtime.goto(C, 2)
    H.assert_same(H.snapshot(b), sb[1])
    runtime.set_preserve_modes(C, True)
    runtime.goto(C, 1)                       # undoes B1 -> switches to B in sculpt mode
    assert C.view_layer.objects.active == b and b.mode == "SCULPT"
    runtime.goto(C, 3)
    H.assert_same(H.snapshot(a), sa[2]); H.assert_same(H.snapshot(b), sb[1])
    runtime.set_global(C, False)


def test_excluded_objects():
    H.fresh()
    ob = H.make_mesh(1000)
    coll = bpy.data.collections.new("Skip")
    C.scene.collection.children.link(coll)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1); H.commit(ob)
    assert runtime.history_of(ob) is not None
    H.enter(ob, "OBJECT")
    coll.objects.link(ob)
    p = C.scene.hindsight
    p.filter_mode = "BLACKLIST"
    it = p.filter_collections.add(); it.collection = coll
    assert runtime.history_of(ob) is None, "history hidden"
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 2)
    assert H.commit(ob) is None, "excluded objects record nothing"
    p.filter_mode = "NONE"
    assert len(runtime.history_of(ob).entries) == 1, "history returns, not deleted"


def test_read_buffers_follow_the_last_mesh():
    """meshio kept a read buffer for every attribute name ever read, each the size of its mesh,
    for the whole session (a cache that never shrank)."""
    from hindsight.bl import meshio
    H.fresh()
    a = H.make_mesh(3000, name="A")
    a.data.color_attributes.new("PaintA", "FLOAT_COLOR", "POINT")
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 1)
    assert runtime.commit(a) is not None
    key = "PaintA|POINT|FLOAT_COLOR"
    assert key in meshio._bufs
    H.enter(a, "OBJECT")
    b = H.make_mesh(500, name="B", location=(4, 0, 0))
    H.enter(b, "SCULPT")
    H.fake_stroke(b, 2)
    assert runtime.commit(b) is not None
    assert key not in meshio._bufs, sorted(meshio._bufs)
    assert set(meshio._bufs) <= set(meshio.channel_keys(b.data, sculpt_only=True))


def test_cap_and_memory_eviction():
    H.fresh()
    SETTINGS.max_steps = 5
    ob = H.make_mesh(1000)
    H.enter(ob, "SCULPT")
    for i in range(8):
        H.fake_stroke(ob, i); H.commit(ob)
    h = runtime.history_of(ob)
    assert len(h.entries) == 5 and h.cursor == 5
    SETTINGS.max_steps = 10000
    runtime.goto(C, 0)
    runtime.goto(C, 5)


def test_stroke_end_debounce():
    import time
    H.fresh()
    ob = H.make_mesh(1000)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    uid = runtime.obj_uid(ob)
    runtime._DIRTY[uid] = time.time()          # an update just arrived: stroke may still be running
    runtime.tick()
    assert runtime.history_of(ob) is None, "must wait for the debounce"
    runtime._DIRTY[uid] = time.time() - 1.0    # quiet for a while: stroke finished
    runtime.tick()
    assert len(runtime.history_of(ob).entries) == 1, "one step per finished stroke"
    runtime.tick()
    assert len(runtime.history_of(ob).entries) == 1


def test_materials_warning():
    H.fresh()
    ob = H.make_mesh(500)
    ob.data.materials.append(bpy.data.materials.new("M"))
    from hindsight.bl import warnings
    assert any("Materials" in t for _s, t in warnings.collect(C, ob))


for f in [test_stroke_end_debounce, test_materials_warning, test_first_stroke_starts_history, test_all_channels_scrub_exact, test_vertex_paint_starts_history,
          test_vertex_paint_layer_added_late, test_restored_colour_layer_is_active,
          test_step_back_forward_ops, test_branch_silent_and_confirm, test_native_undo_sync_and_f9_replace,
          test_edit_mode_grouping, test_topology_snapshot, test_topology_step_owns_its_before,
          test_fingerprint_streamed_same_digest, test_turn_off_on_and_reset,
          test_delete_past_keeps_later, test_per_object_and_global, test_excluded_objects,
          test_cap_and_memory_eviction, test_read_buffers_follow_the_last_mesh]:
    H.run(f)
H.finish()
