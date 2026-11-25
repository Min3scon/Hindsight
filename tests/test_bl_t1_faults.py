"""Tier 1 fault injection and animated values, headless.

FAULT: each Tier 1 adapter (rna, object, scene) made to raise in capture, in diff and in apply
switches off only itself: one log line, a message in the sidebar, the history kept and still
playable, the other adapters and sculpt recording still working, and Self-check switches it back on
without recording what changed meanwhile as one big step.
D15: animated or driven constraint settings change with the frame; that is never a step.

Run: blender -b --factory-startup --python tests/test_bl_t1_faults.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_ops as T
from hindsight.bl import adapters, datarec, guard, idmap, runtime, selfcheck
from hindsight.bl import log, prefs
from hindsight.core.types import SETTINGS

C = bpy.context
ROWS = {"rna": ["obj.transforms", "obj.parenting"], "object": ["obj.constraints", "obj.collections"],
        "scene": ["obj.addremove"]}


def setup():
    H.fresh()
    T.build_scene()
    prefs.FORCE["record_scope"] = "EVERYTHING"
    for ob in list(C.scene.objects):
        datarec.set_tracked(ob, True)
    datarec.set_tracked(C.scene, True)


def tick(label="Op"):
    C.view_layer.update()
    datarec.refresh_mesh_users()
    return datarec.commit(label=label, uids=datarec.tracked_uids() | set(datarec._DIRTY), mesh=True)


def hist(name):
    return runtime.reg().history_for(idmap.uid_of(bpy.data.objects[name]))


def scene_hist():
    return runtime.reg().history_for(idmap.uid_of(C.scene))


def one_of_each():
    """An operation for each adapter: a move (rna), a constraint (object), a new object (scene)."""
    T.translate("A", (1, 0, 0)).run()
    T.add_constraint("B", "COPY_ROTATION").run()
    T.add_primitive("cube", (0, -5, 0)).run()
    return tick("three changes")


def boom(*_a, **_k):
    raise RuntimeError("injected fault")


def sculpt_still_records():
    ob = H.make_mesh(400, name="Sculpt me")
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 5)
    step = H.commit(ob)
    H.enter(ob, "OBJECT")
    return step is not None


def check_fault(aid, where):
    setup()
    one_of_each()
    before = {n: len(hist(n).entries) for n in ("A", "B")}
    sh_len = len(scene_hist().entries)
    a = adapters.ADAPTERS[aid]
    attr = {"capture": "capture", "diff": "changed", "apply": "write"}[where]
    orig = getattr(a, attr)
    setattr(a, attr, boom)
    try:
        if where == "apply":
            runtime._goto_history(hist("A") if aid == "rna" else (hist("B") if aid == "object" else scene_hist()),
                                  0, True)
        else:
            T.translate("A", (0, 1, 0)).run()
            T.add_constraint("B", "LIMIT_LOCATION").run()
            T.add_primitive("cone", (0, -8, 0)).run()
            tick("after the fault")
    finally:
        setattr(a, attr, orig)
    failed = {n for n, _l, _e, _t in guard.failed()}
    assert failed == {"adapter:" + aid}, f"{where} fault in {aid}: switched off {failed}"
    label = guard.LABELS["adapter:" + aid]
    assert any(lbl == label for _n, lbl, _e, _t in guard.failed()), "the sidebar lists it by name"
    # the other adapters kept recording (when the fault came while recording)
    if where != "apply":
        others = {"rna": hist("B"), "object": hist("A"), "scene": hist("A")}[aid]
        assert others is not None and len(others.entries) > {"rna": before["B"], "object": before["A"],
                                                             "scene": before["A"]}[aid], \
            f"{where} fault in {aid}: the other adapters stopped recording too"
    # the history is still there and playable
    assert len(hist("A").entries) >= before["A"] and len(scene_hist().entries) >= sh_len
    for h in (hist("A"), hist("B"), scene_hist()):
        runtime._goto_history(h, 0, True)
        runtime._goto_history(h, len(h.entries), True)
    assert sculpt_still_records(), "sculpt recording stopped"
    # Self-check switches it back on, with a fresh baseline (no step for what changed while off)
    n_before = {n: len(hist(n).entries) for n in ("A", "B")}
    selfcheck.run_checks(retry_failed=True)            # what the Self-check button runs
    assert not guard.failed(), guard.failed()
    tick("after self-check")
    assert {n: len(hist(n).entries) for n in ("A", "B")} == n_before, "a step for changes made while off"
    T.translate("A", (0, 0, 1)).run()
    tick("move")
    assert len(hist("A").entries) == n_before["A"] + 1, "recording works again"


def test_rna_adapter_faults():
    for where in ("capture", "diff", "apply"):
        check_fault("rna", where)


def test_object_adapter_faults():
    for where in ("capture", "diff", "apply"):
        check_fault("object", where)


def test_scene_adapter_faults():
    for where in ("capture", "diff", "apply"):
        check_fault("scene", where)


def test_edit_steps_fault():
    """Edit Mode step by step raising: only it switches off; the object's other data and sculpting
    still record; the history plays back."""
    setup()
    for op in T.edit_ops("E", 3)[:2]:
        op.run()
        datarec._edit_step(op.label)
    T.select()
    tick("leave")
    he = hist("E")
    n = len(he.entries)
    orig = runtime.commit_edit
    runtime.commit_edit = boom
    try:
        op = T.edit_ops("E", 5)[0]
        op.run()
        datarec._edit_step(op.label)
    finally:
        runtime.commit_edit = orig
    assert {f for f, _l, _e, _t in guard.failed()} == {"adapter:edit_steps"}, guard.failed()
    T.select()                       # leaving Edit Mode: the edit is kept as one step per visit
    labels = [e.meta.get("label") for e in hist("E").entries]
    assert len(labels) == n + 1 and labels[-1] == "Edit Mode", labels
    T.translate("E", (0, 0, 1)).run()
    tick("move")
    assert len(hist("E").entries) == n + 2, "the object's other changes still record"
    for pos in (0, n, n + 1, n + 2):
        runtime._goto_history(hist("E"), pos, True)
    assert sculpt_still_records()
    selfcheck.run_checks(retry_failed=True)
    assert not guard.failed()


def test_object_mode_mesh_check_fault():
    """The Object Mode mesh check (Apply, Shade Smooth) raising: only it switches off; transforms
    still record."""
    setup()
    orig = datarec._mesh_step
    datarec._mesh_step = boom
    try:
        T.shade("A", True).run()
        tick("shade")
    finally:
        datarec._mesh_step = orig
    assert {f for f, _l, _e, _t in guard.failed()} == {"adapter:object_mesh"}, guard.failed()
    n = len(hist("A").entries) if hist("A") else 0
    T.translate("A", (0, 1, 0)).run()
    tick("move")
    assert hist("A") is not None and len(hist("A").entries) == n + 1
    assert sculpt_still_records()
    selfcheck.run_checks(retry_failed=True)
    assert not guard.failed()


def test_mesh_change_without_an_operator_is_recorded():
    """D32: a change to a recorded mesh in Object Mode that comes without an operator (a script, or
    an operator's update that arrives after the timer saw it finish) reaches the recorder through the
    settle path, which must compare the mesh too. One step, exact both ways."""
    setup()
    a = bpy.data.objects["A"]
    tick("baseline")
    h0 = len(hist("A").entries) if hist("A") else 0
    before = O.object_state(a)
    co = [c for v in a.data.vertices for c in v.co]
    co[0] += 0.5
    a.data.vertices.foreach_set("co", co)
    a.data.update()
    C.view_layer.update()                           # the update reaches the recorder (on_update)
    datarec._OPS["marker"] = datarec._ops_marker()  # no operator finished
    for uid in list(datarec._DIRTY):
        datarec._DIRTY[uid] -= 10                   # the settle time has passed
    datarec.tick()
    h = hist("A")
    assert h is not None and len(h.entries) == h0 + 1, None if h is None else [e.meta.get("label") for e in h.entries]
    after = O.object_state(a)
    runtime._goto_history(h, h0, True)
    assert O.object_state(a) == before
    runtime._goto_history(h, h0 + 1, True)
    assert O.object_state(a) == after


def test_animated_constraint_settings_are_not_steps():
    """D15: keyframed or driven constraint settings change with the frame: never a step."""
    setup()
    T.add_constraint("A", "COPY_LOCATION").run()
    tick("add constraint")
    a = bpy.data.objects["A"]
    c = a.constraints[0]
    c.influence = 0.2
    c.keyframe_insert("influence", frame=1)
    c.influence = 0.9
    c.keyframe_insert("influence", frame=20)
    d = a.constraints.new("LIMIT_LOCATION")
    fc = d.driver_add("min_x")
    fc.driver.expression = "frame / 10"
    tick("keys and a driver")                         # adding them may be one step
    n = len(hist("A").entries)
    for f in (5, 12, 19, 3):
        C.scene.frame_set(f)
        tick("frame change")
    assert len(hist("A").entries) == n, [e.meta.get("label") for e in hist("A").entries[n:]]
    # a real change to the same constraint (not animated) is still a step
    d.max_y = 3.0
    tick("edit")
    assert len(hist("A").entries) == n + 1


def test_strokes_while_the_recorder_is_off_are_one_step():
    """#118 (D46, D95): strokes made while a fault had the recorder switched off become one step of
    their own when Self-check switches it back on; the next stroke is the step after it, and going back
    over it gives the mesh from before the fault. A recorded object not changed meanwhile gets no step."""
    H.fresh()
    other = H.make_mesh(400, name="Other", location=(3, 0, 0))
    ob = H.make_mesh(400, name="Blob")
    H.enter(other, "SCULPT")
    H.fake_stroke(other, 9)
    H.commit(other)
    H.enter(ob, "SCULPT")
    ho = runtime.reg().history_for(runtime.obj_uid(other))
    n_other = len(ho.entries)
    for seed in (0, 1):
        H.fake_stroke(ob, seed)
        H.commit(ob)
    h = runtime.reg().history_for(runtime.obj_uid(ob))
    assert len(h.entries) == 2
    before = H.snapshot(ob)
    real = runtime._observe
    runtime._observe = boom
    try:
        H.fake_stroke(ob, 2)
        runtime._DIRTY[runtime.obj_uid(ob)] = 0.0   # the stroke has settled
        runtime._tick_once()                        # the recorder's timer commits it and fails
    finally:
        runtime._observe = real
    assert not guard.ok("recorder") and len(h.entries) == 2
    H.fake_stroke(ob, 3)                            # made while the recorder is off
    off = H.snapshot(ob)
    selfcheck.run_checks(retry_failed=True)         # what the Self-check button runs
    assert guard.ok("recorder")
    assert len(ho.entries) == n_other               # nothing changed on it: no step
    H.fake_stroke(ob, 4)
    H.commit(ob)
    assert len(h.entries) == 4, [e.meta.get("label") for e in h.entries]
    assert h.entries[2].meta.get("label") == "Changes While Off"
    runtime._goto_history(h, 3, True)               # back one step: only the last stroke is undone
    assert all((H.snapshot(ob)[k] == v).all() for k, v in off.items())
    runtime._goto_history(h, 2, True)               # back over "Changes While Off": the mesh before the fault
    assert all((H.snapshot(ob)[k] == v).all() for k, v in before.items())


def test_changes_while_off_never_ask_to_drop_steps():
    """#119 review: with 10 or more steps ahead of the cursor, Self-check's "Changes While Off" step drops them
    without asking and keeps the strokes made while the recorder was off (never puts the mesh back)."""
    H.fresh()
    ob = H.make_mesh(400, name="Blob")
    H.enter(ob, "SCULPT")
    for seed in range(12):
        H.fake_stroke(ob, seed)
        H.commit(ob)
    h = runtime.reg().history_for(runtime.obj_uid(ob))
    runtime._goto_history(h, 1, True)               # 11 steps ahead of the cursor
    SETTINGS.truncation_threshold = 10
    guard.fail("recorder", RuntimeError("injected fault"))
    H.fake_stroke(ob, 20)                           # made while the recorder is off
    off = H.snapshot(ob)
    selfcheck.run_checks(retry_failed=True)
    assert guard.ok("recorder") and SETTINGS.truncation_threshold == 10
    assert not runtime._PENDING_TRUNCATE
    assert [e.meta.get("label") for e in h.entries][1:] == ["Changes While Off"] and h.cursor == 2
    assert all((H.snapshot(ob)[k] == v).all() for k, v in off.items())



def test_changes_while_off_go_on_past_an_object_that_fails():
    """#119 review: if one object's "Changes While Off" step raises, the other objects still get theirs and
    the failure is logged."""
    H.fresh()
    a = H.make_mesh(400, name="A", location=(3, 0, 0))
    b = H.make_mesh(400, name="B")
    for ob in (a, b):
        H.enter(ob, "SCULPT")
        H.fake_stroke(ob, 0)
        H.commit(ob)
    ua, ub = runtime.obj_uid(a), runtime.obj_uid(b)
    assert [u for u in runtime._STATES if u in (ua, ub)] == [ua, ub]     # A's step is tried first
    hb = runtime.reg().history_for(ub)
    n = len(hb.entries)
    guard.fail("recorder", RuntimeError("injected fault"))
    H.fake_stroke(a, 1)
    H.fake_stroke(b, 1)                             # both changed while the recorder is off
    real = runtime.commit

    def flaky(ob, **kw):
        if ob == a:
            raise RuntimeError("no step for A")
        return real(ob, **kw)

    runtime.commit = flaky
    try:
        selfcheck.run_checks(retry_failed=True)
    finally:
        runtime.commit = real
    assert len(hb.entries) == n + 1 and hb.entries[-1].meta.get("label") == "Changes While Off"
    assert any("no step for A" in line for line in log.tail(400))


def test_changes_while_off_hold_the_values_too():
    """#119 review (D46): with Record everything, Self-check's "Changes While Off" steps hold the values
    changed while the recorder was off as well as the mesh, as Turn On does: going back over them gives
    both from before the fault, in Object Mode and in Sculpt Mode."""
    H.fresh()
    prefs.FORCE["record_scope"] = "EVERYTHING"
    a = H.make_mesh(400, name="A", location=(3, 0, 0))     # stays in Object Mode
    b = H.make_mesh(400, name="B")                          # sculpted
    for ob in (a, b):
        datarec.set_tracked(ob, True)
    a.location.x = 4.0
    tick("move")
    H.enter(b, "SCULPT")
    H.fake_stroke(b, 0)
    H.commit(b)
    hs = {ob: runtime.reg().history_for(runtime.obj_uid(ob)) for ob in (a, b)}
    n0 = {ob: len(h.entries) for ob, h in hs.items()}
    assert all(n0.values()), n0
    before = {ob: (H.snapshot(ob), ob.location.x) for ob in (a, b)}
    guard.fail("recorder", RuntimeError("injected fault"))
    for ob, seed in ((a, 1), (b, 2)):                       # values and mesh changed while it is off
        ob.location.x += 1.5
        H.fake_stroke(ob, seed)
    off = {ob: (H.snapshot(ob), ob.location.x) for ob in (a, b)}
    selfcheck.run_checks(retry_failed=True)
    assert guard.ok("recorder")
    for ob, h in hs.items():
        n1 = len(h.entries)
        labels = [e.meta.get("label") for e in h.entries[n0[ob]:]]
        assert n1 > n0[ob] and set(labels) == {"Changes While Off"}, (ob.name, labels)
        for pos, (snap, x) in ((n0[ob], before[ob]), (n1, off[ob])):
            runtime._goto_history(h, pos, True)
            assert abs(ob.location.x - x) < 1e-6, (ob.name, pos, ob.location.x, x)
            assert all((H.snapshot(ob)[k] == v).all() for k, v in snap.items()), (ob.name, pos)

for fn in (test_rna_adapter_faults, test_object_adapter_faults, test_scene_adapter_faults, test_edit_steps_fault,
           test_object_mode_mesh_check_fault, test_mesh_change_without_an_operator_is_recorded,
           test_animated_constraint_settings_are_not_steps, test_strokes_while_the_recorder_is_off_are_one_step,
           test_changes_while_off_never_ask_to_drop_steps, test_changes_while_off_go_on_past_an_object_that_fails,
           test_changes_while_off_hold_the_values_too):
    H.run(fn)
H.finish()
