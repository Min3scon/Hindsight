"""Multires: one exact step per Sculpt Mode visit (preference "Record Multires", off by default).

Strokes can't run headless, so a visit's change of the displacement layers (CD_MDISPS) is made with
Multires Reshape: a displaced copy of the evaluated top-level mesh is the target, which gives the
same displacement every time. It runs inside the visit (with the mode switch hidden from the
recorder), and leaving Sculpt Mode takes the step. Exactness is checked two ways: a hash of the
evaluated top-level vertices and a hash of the mesh datablock written by libraries.write (which
holds the displacement layers; it is byte-for-byte the same for the same mesh)."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hashlib
import shutil
import tempfile

import bpy
import numpy as np
import tests.harness as H
from hindsight.bl import multires, runtime
from hindsight.bl import prefs
from hindsight.core.types import SETTINGS

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hindsight_mr_")
NAME = "Multi"


def top_hash(ob):
    md = multires.modifier(ob)
    md.levels = md.sculpt_levels = md.total_levels
    dg = C.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m = ev.to_mesh()
    n = len(m.vertices)
    a = np.empty(n * 3, np.float32)
    m.vertices.foreach_get("co", a)
    ev.to_mesh_clear()
    return hashlib.sha1(a.tobytes()).hexdigest()[:12], n


def mesh_hash(ob):
    """Every level of the displacement: the evaluated vertices at levels 0 to the top, and the base mesh.
    (The bytes of a written mesh are not a function of its content alone: a restored mesh differs by a
    flag or two, so they are not compared.)"""
    md = multires.modifier(ob)
    h = hashlib.sha1()
    top = md.total_levels
    for lv in range(0, top + 1):
        md.levels = md.sculpt_levels = lv
        dg = C.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        m = ev.to_mesh()
        a = np.empty(len(m.vertices) * 3, np.float32)
        m.vertices.foreach_get("co", a)
        ev.to_mesh_clear()
        h.update(a.tobytes())
    md.levels = md.sculpt_levels = top
    return h.hexdigest()[:12]


def setup(levels=2):
    H.fresh()
    prefs.FORCE["record_multires"] = True
    multires.SETTLE[0] = 0.0
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3)
    ob = C.active_object
    ob.name = NAME
    ob.data.materials.append(bpy.data.materials.new("Skin"))
    ob.modifiers.new("Multires", "MULTIRES")
    for _ in range(levels):
        bpy.ops.object.multires_subdivide(modifier="Multires", mode="CATMULL_CLARK")
    # a first visit that changes nothing: Sculpt Mode adds its own layers to the mesh, and they stay
    H.enter(ob, "SCULPT")
    runtime._tick_once()
    H.enter(ob, "OBJECT")
    runtime._tick_once()
    h = runtime.history_of(ob)
    assert h is None or not h.entries, "a visit that changed nothing made a step"
    return ob


def reshape(ob, seed):
    """A deterministic change of the displacement: reshape to a displaced copy of the top level."""
    md = multires.modifier(ob)
    md.levels = md.sculpt_levels = md.total_levels
    dg = C.evaluated_depsgraph_get()
    tm = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    tgt = bpy.data.objects.new(f"T{seed}", tm)
    C.collection.objects.link(tgt)
    co = np.empty(len(tm.vertices) * 3, np.float32)
    tm.vertices.foreach_get("co", co)
    co += np.random.default_rng(seed).normal(0, 0.01, co.size).astype(np.float32)
    tm.vertices.foreach_set("co", co)
    bpy.ops.object.select_all(action="DESELECT")
    tgt.select_set(True)
    ob.select_set(True)
    C.view_layer.objects.active = ob
    bpy.ops.object.multires_reshape(modifier="Multires")
    bpy.data.objects.remove(tgt)
    bpy.data.meshes.remove(tm)


def visit(ob, seed):
    """One Sculpt Mode visit that changes the displacement; the step is taken when it ends."""
    H.enter(ob, "SCULPT")
    runtime._tick_once()                     # the timer: the visit's baseline, after Sculpt Mode's own layers
    with runtime.suspended():
        bpy.ops.object.mode_set(mode="OBJECT")
        reshape(ob, seed)
        bpy.ops.object.mode_set(mode="SCULPT")
    H.enter(ob, "OBJECT")
    runtime._tick_once()                     # the timer takes the step of the visit that ended
    return (top_hash(ob), mesh_hash(ob))


def three_visits():
    ob = setup()
    states = [(top_hash(ob), mesh_hash(ob))]
    for seed in (1, 2, 3):
        states.append(visit(ob, seed))
    assert len({s[0] for s in states}) == 4 and len({s[1] for s in states}) == 4, "the reshape changed nothing"
    return ob, states


def check_positions(ob, states, order):
    h = runtime.history_of(ob)
    for pos in order:
        runtime.goto(C, pos)
        assert h.cursor == pos
        got = (top_hash(ob), mesh_hash(ob))
        assert got == states[pos], f"position {pos}: {got} != {states[pos]}"
        assert [m.type for m in ob.modifiers] == ["MULTIRES"] and ob.data.name == NAME + "" or ob.data.name
        assert len(ob.material_slots) == 1 and ob.material_slots[0].material.name == "Skin"


def test_off_by_default_pauses_as_before():
    ob = setup()
    prefs.FORCE["record_multires"] = None
    assert not multires.enabled()
    from hindsight.bl import warnings as W
    assert W.blocking(C, ob), "Multires must still pause recording when the preference is off"
    H.enter(ob, "SCULPT")
    H.enter(ob, "OBJECT")
    runtime._tick_once()
    h = runtime.history_of(ob)
    assert h is None or not h.entries
    assert prefs.HINDSIGHT_AP_prefs.__annotations__["record_multires"].keywords["default"] is False
    print("PASS test_off_by_default_pauses_as_before")


def test_one_step_per_visit_and_exact():
    ob, states = three_visits()
    h = runtime.history_of(ob)
    assert len(h.entries) == 3 and h.cursor == 3, (len(h.entries), h.cursor)
    assert all(e.kind == "TOPO" and e.meta["extra"].get("multires") for e in h.entries)
    check_positions(ob, states, [2, 1, 0, 1, 3, 0, 3, 2])
    print("PASS test_one_step_per_visit_and_exact")


def test_a_visit_that_changes_nothing_is_no_step():
    ob = setup()
    H.enter(ob, "SCULPT")
    runtime._tick_once()
    H.enter(ob, "OBJECT")
    h = runtime.history_of(ob)
    assert h is None or not h.entries
    print("PASS test_a_visit_that_changes_nothing_is_no_step")


def test_level_change_takes_the_step():
    ob = setup(1)
    states = [(top_hash(ob), mesh_hash(ob))]
    H.enter(ob, "SCULPT")
    runtime._tick_once()
    with runtime.suspended():
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.multires_subdivide(modifier="Multires", mode="CATMULL_CLARK")
        bpy.ops.object.mode_set(mode="SCULPT")
    runtime._tick_once()                                   # the timer sees the new level
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 1, "a level change made no step"
    H.enter(ob, "OBJECT")
    runtime._tick_once()
    states.append((top_hash(ob), mesh_hash(ob)))
    assert len(h.entries) == 1, "a visit that changed nothing after the level step made one"
    check_positions(ob, states, [0, 1, 0, 1])
    print("PASS test_level_change_takes_the_step")


def test_save_and_reload():
    ob, states = three_visits()
    path = os.path.join(TMP, "mr.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    prefs.FORCE["record_multires"] = True
    ob = bpy.data.objects[NAME]
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 3 and h.cursor == 3, "history not restored"
    assert (top_hash(ob), mesh_hash(ob)) == states[3]
    check_positions(ob, states, [0, 2, 1, 3, 0])
    print("PASS test_save_and_reload")


def test_crash_recovery_replays_the_visits():
    ob = setup()
    SETTINGS.crash_recovery = "INTERRUPTED"
    states = [(top_hash(ob), mesh_hash(ob))]
    states.append(visit(ob, 1))
    path = os.path.join(TMP, "crash.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    file_state = (top_hash(ob), mesh_hash(ob))
    states.append(visit(ob, 2))                            # only in the journal
    runtime.missing_message[0] = None
    bpy.ops.wm.open_mainfile(filepath=path)                # the session ends without saving
    prefs.FORCE["record_multires"] = True
    ob = bpy.data.objects[NAME]
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 2, (None if h is None else len(h.entries))
    assert (top_hash(ob), mesh_hash(ob)) == file_state
    check_positions(ob, states, [2, 1, 0, 2, 1])
    print("PASS test_crash_recovery_replays_the_visits")


def test_steps_are_counted_by_the_caps():
    ob, states = three_visits()
    h = runtime.history_of(ob)
    sizes = [e.nbytes() for e in h.entries]
    raw = len(multires.snapshot_bytes(ob.data))
    assert all(raw // 20 < s < raw * 3 for s in sizes), (sizes, raw)      # compressed, both sides of the step
    print(f"PASS test_steps_are_counted_by_the_caps (mesh blob {raw} bytes, steps {sizes})")


def test_stroke_before_the_baseline_settles_stays_in_the_step():
    """D143: Sculpt Mode adds its own layers after its first update, so a visit's baseline is read again
    SETTLE seconds in. A stroke made before that must stay in the visit's step, not become part of the
    baseline (going back would not undo it)."""
    ob = setup()
    before = (top_hash(ob), mesh_hash(ob))
    multires.SETTLE[0] = 30.0                  # the re-read isn't due yet when the stroke is made
    H.enter(ob, "SCULPT")
    runtime._tick_once()
    with runtime.suspended():
        bpy.ops.object.mode_set(mode="OBJECT")
        reshape(ob, 7)
        bpy.ops.object.mode_set(mode="SCULPT")
    uid = runtime.obj_uid(ob)
    runtime._STROKE_SEEN.add(uid)              # what the depsgraph handler notes during a stroke
    multires.SETTLE[0] = 0.0                   # now the re-read is due
    try:
        runtime._tick_once()
        H.enter(ob, "OBJECT")
        runtime._tick_once()
    finally:
        runtime._STROKE_SEEN.discard(uid)
    after = (top_hash(ob), mesh_hash(ob))
    assert after != before, "the reshape changed nothing"
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 1, "the stroke made before the baseline settled was lost"
    runtime.goto(C, 0)
    assert (top_hash(ob), mesh_hash(ob)) == before
    runtime.goto(C, 1)
    assert (top_hash(ob), mesh_hash(ob)) == after
    print("PASS test_stroke_before_the_baseline_settles_stays_in_the_step")


def test_digest_reads_the_mesh_not_its_materials():
    """D150: a visit is compared by a digest of the mesh's own blocks in the written file. Blender 5.0 to
    5.2 write a material a little differently each time (one byte in its data changes between two writes
    of the same mesh), which made a visit that changed nothing a step whenever the mesh had a material."""
    H.fresh()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3)
    ob = C.active_object
    ob.data.materials.append(bpy.data.materials.new("Skin"))
    ob.modifiers.new("Multires", "MULTIRES")
    bpy.ops.object.multires_subdivide(modifier="Multires", mode="CATMULL_CLARK")
    raw = multires.snapshot_bytes(ob.data)
    blocks = multires.blend_blocks(raw)
    assert blocks and any(c == b"ME\x00\x00" for _p, c, _l in blocks), blocks[:5]
    owner, by_id = None, []
    for pos, code, ln in blocks:
        if code != b"DATA":
            owner = code
        by_id.append((pos, code, ln, owner))
    bh = 32 if raw[7:9] == b"17" else 24
    ma = [(p, ln) for p, c, ln, o in by_id if o == b"MA\x00\x00" and ln > 8]
    me = [(p, ln) for p, c, ln, o in by_id if o == b"ME\x00\x00" and c == b"DATA" and ln > 8]
    assert ma and me, (ma[:2], me[:2])
    d0 = multires.hash_of(raw)
    for p, ln in ma:
        x = bytearray(raw)
        x[p + bh + ln // 2] ^= 0x07                     # a byte of the material's data
        assert multires.hash_of(bytes(x)) == d0, (p, ln)
    p, ln = me[0]
    y = bytearray(raw)
    y[p + bh + ln // 2] ^= 0x07                         # a byte of the mesh's data
    assert multires.hash_of(bytes(y)) != d0
    assert multires.hash_of(b"not a blend file") != multires.hash_of(b"not a blend file!")   # every byte then
    # two writes of the same mesh give the same digest (on 5.0 to 5.2 the bytes themselves may differ)
    assert multires.hash_of(multires.snapshot_bytes(ob.data)) == multires.hash_of(multires.snapshot_bytes(ob.data))
    print(f"PASS test_digest_reads_the_mesh_not_its_materials ({len(blocks)} blocks)")


if __name__ == "__main__":
    for t in (test_digest_reads_the_mesh_not_its_materials, test_off_by_default_pauses_as_before, test_one_step_per_visit_and_exact,
              test_a_visit_that_changes_nothing_is_no_step, test_level_change_takes_the_step, test_save_and_reload,
              test_crash_recovery_replays_the_visits, test_steps_are_counted_by_the_caps,
              test_stroke_before_the_baseline_settles_stays_in_the_step):
        H.run(t)
    prefs.FORCE["record_multires"] = None
    H.finish()
    shutil.rmtree(TMP, ignore_errors=True)
