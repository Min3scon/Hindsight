"""Regression tests for the findings of the 2026-11-18 code audit (speed and memory on long
sessions, and two notes that never went away). Each test fails without its fix."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bpy
import numpy as np

import tests.harness as H
from hindsight.bl import datarec, idmap, log, runtime
from hindsight.core.types import POSITION_KEY, TOPO, Snapshot, Step

C = bpy.context


def test_error_once_stays_once_after_many_errors():
    """After 256 different errors a repeating one was logged every time (10 times a second from the
    timer, each with a traceback)."""
    H.fresh()
    log.forget()
    for i in range(300):
        assert log.error_once(("audit", i), f"error {i}")
    assert log.error_once(("audit", "again"), "the 301st")
    assert not log.error_once(("audit", "again"), "the 301st"), "logged twice"
    log.forget()


def test_jumps_kept_only_as_far_as_blender_can_undo():
    """Every jump on Blender's undo stack kept a copy of all recorded values: 512 of them, though
    Blender keeps 32 undo steps by default."""
    H.fresh()
    edit = C.preferences.edit
    before = edit.undo_steps
    try:
        edit.undo_steps = 4
        for i in range(10):
            datarec.note_jump({"h": [i, i + 1, 10]}, {"v": i}, {"v": i + 1})
        assert len(datarec._JUMPS) == 4, len(datarec._JUMPS)
        assert [j["vb"]["v"] for j in datarec._JUMPS] == [6, 7, 8, 9], "the newest are kept"
        edit.undo_steps = 0                      # no limit in Blender: the add-on's own cap applies
        for i in range(20):
            datarec.note_jump({"h": [i, i + 1, 10]}, {"v": i}, {"v": i + 1})
        assert len(datarec._JUMPS) == 24
    finally:
        edit.undo_steps = before
        datarec._JUMPS.clear()


def test_exclusion_check_is_free_without_collection_filtering():
    """The timer asked Collection Filtering about every recorded object 10 times a second, with the
    filter off."""
    H.fresh()
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    obs = [H.make_mesh(60, name=f"O{i}", location=(i * 3, 0, 0)) for i in range(4)]
    for ob in obs:
        datarec.set_tracked(ob, True)
    assert len(datarec.tracked_uids()) >= 4
    calls = []
    real = idmap.find
    idmap.find = lambda uid: calls.append(uid) or real(uid)
    try:
        datarec.check_exclusion()
        assert not calls, f"walked {len(calls)} objects with the filter off"
        # with the filter on it still looks at every recorded object (D54 depends on it)
        sh = C.scene.hindsight
        coll = bpy.data.collections.new("Skip")
        C.scene.collection.children.link(coll)
        coll.objects.link(obs[0])
        sh.filter_collections.add().collection = coll
        sh.filter_mode = 'BLACKLIST'
        datarec.check_exclusion()
        assert len(calls) >= 4
        assert idmap.uid_of(obs[0]) in datarec._EXCLUDED
        # and after the filter is switched off again, once more to notice the object is back
        sh.filter_mode = 'NONE'
        calls.clear()
        datarec.check_exclusion()
        assert calls and idmap.uid_of(obs[0]) not in datarec._EXCLUDED
        calls.clear()
        datarec.check_exclusion()
        assert not calls
    finally:
        idmap.find = real
        datarec.FORCE_SCOPE[0] = None


def _topo(before, after):
    return Step(0, "Remesh", "SCULPT", "A", kind=TOPO, before=before, after=after)


def _snap(values):
    a = np.array(values, np.float32)
    return Snapshot({"POINT": len(a) // 3, "EDGE": 0, "FACE": 0, "CORNER": 0}, {POSITION_KEY: a})


def test_topology_undo_match_is_bit_exact():
    """Matching Blender's undo of a topology step compares the two meshes bit for bit (it made a
    bytes copy of each to do so)."""
    a = _snap([0, 1, 2, 3, 4, 5])
    b = _snap([9, 8, 7, 6, 5, 4, 3, 2, 1])
    old = _topo(a, b)
    assert runtime._inverse_matches(_topo(_snap([9, 8, 7, 6, 5, 4, 3, 2, 1]), _snap([0, 1, 2, 3, 4, 5])),
                                    old, None, True), "the step undone"
    assert runtime._inverse_matches(_topo(_snap([0, 1, 2, 3, 4, 5]), _snap([9, 8, 7, 6, 5, 4, 3, 2, 1])),
                                    old, None, False), "the step redone"
    assert not runtime._inverse_matches(_topo(b, _snap([0, 1, 2, 3, 4, 5.5])), old, None, True)
    # bit for bit: NaN equals the same NaN, -0.0 is not 0.0
    nan = _snap([np.nan, 1, 2])
    assert runtime._inverse_matches(_topo(_snap([5, 5, 5]), _snap([np.nan, 1, 2])), _topo(nan, _snap([5, 5, 5])),
                                    None, True)
    assert not runtime._inverse_matches(_topo(_snap([5, 5, 5]), _snap([-0.0, 1, 2])),
                                        _topo(_snap([0.0, 1, 2]), _snap([5, 5, 5])), None, True)
    # same numbers in another type are not the same data
    other = Snapshot(dict(a.counts), {POSITION_KEY: a.arrays[POSITION_KEY].astype(np.float64)})
    assert not runtime._inverse_matches(_topo(b, other), old, None, True)


def test_clamped_weights_note_goes_away():
    """The note "weights were clamped" stayed for the whole session, and for the same object after
    another file was opened."""
    H.fresh()
    ob = H.make_mesh(60)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    assert H.commit(ob) is not None
    uid = runtime.obj_uid(ob)
    st = runtime._STATES[uid]
    runtime.WEIGHTS_CLAMPED.add(uid)
    runtime._clamp_state_weights(ob, st)             # a restore that needed no clamping
    assert uid not in runtime.WEIGHTS_CLAMPED
    runtime.WEIGHTS_CLAMPED.add(uid)
    runtime.set_registry(runtime.Registry())         # what loading a file does
    assert not runtime.WEIGHTS_CLAMPED


def _journal_and_live(strokes, verts=2000, n=50):
    """Record strokes with the crash journal captured; return (live step numbers, replayed ones)."""
    from hindsight.core import persist
    from hindsight.core.registry import Registry
    recs = []
    hook = runtime.journal_hook
    runtime.journal_hook = lambda rtype, header, fn=None: recs.append((rtype, dict(header), fn() if fn else b""))
    try:
        ob = H.make_mesh(verts)
        H.enter(ob, "SCULPT")
        for i in range(strokes):
            H.fake_stroke(ob, i, n=n)
            assert runtime.commit(ob) is not None
        live = [e.seq for e in runtime.history_of(ob).entries]
    finally:
        runtime.journal_hook = hook
    r = Registry()
    persist.replay(r, recs)
    return live, [e.seq for e in list(r.histories.values())[0].entries], recs


def test_crash_journal_knows_what_the_step_cap_dropped():
    """Steps dropped by the step cap were never written to the crash journal, so recovery after a
    crash put later steps on top of the wrong ones (history [4..8] came back as [1,2,3,4,5,8])."""
    from hindsight.core.types import SETTINGS
    H.fresh()
    cap = SETTINGS.max_steps
    SETTINGS.max_steps = 5
    try:
        live, replayed, recs = _journal_and_live(8)
    finally:
        SETTINGS.max_steps = cap
    assert len(live) == 5 and replayed == live, (live, replayed)
    assert sum(1 for r in recs if r[0] == "DROP") == 3


def test_crash_journal_knows_what_the_memory_limit_dropped():
    from hindsight.core.types import SETTINGS
    H.fresh()
    limit, raw = SETTINGS.memory_limit_mb, SETTINGS.raw_steps
    SETTINGS.memory_limit_mb = 1
    SETTINGS.raw_steps = 1000              # steps stay unpacked, so 1 MB is passed after a few dozen
    try:
        live, replayed, recs = _journal_and_live(90, verts=20000, n=2000)
    finally:
        SETTINGS.memory_limit_mb, SETTINGS.raw_steps = limit, raw
    assert len(live) < 90, "the memory limit dropped nothing: the test needs bigger strokes"
    assert replayed == live, (live[:5], replayed[:5], len(live), len(replayed))


def test_global_timeline_is_built_once_until_it_changes():
    """With global history on, every redraw walked and sorted every step several times (18 ms per
    call at 10,000 steps). It is kept now, and what the slider shows always equals a fresh build."""
    from hindsight.core import globaltl
    H.fresh()
    a = H.make_mesh(300, name="A", location=(-2, 0, 0))
    b = H.make_mesh(300, name="B", location=(2, 0, 0))
    for i, ob in enumerate((a, b, a, b, a)):
        H.enter(ob, "SCULPT")
        H.fake_stroke(ob, i)
        assert H.commit(ob) is not None
    runtime.set_global(C, True)
    built = []
    real = globaltl.global_groups

    def fresh():                                     # the truth, built from scratch
        vis = runtime._visible_uids()
        g = real(runtime.reg(), vis)
        return globaltl.global_position(runtime.reg(), vis, g), len(g)

    def shown():
        info = runtime.scrub_info(C)
        return info["pos"], info["len"]

    globaltl.global_groups = lambda reg, visible=None: built.append(1) or real(reg, visible)
    try:
        assert shown() == fresh() == (5, 5)
        n = len(built)
        for _ in range(5):
            assert shown() == (5, 5)
            runtime.step_list(C)
        assert len(built) == n, f"built {len(built) - n} more times for the same history"
        runtime.goto(C, 2)                           # a jump moves the position, not the timeline
        assert shown() == fresh() and shown()[0] == 2
        runtime.goto(C, 5)
        assert shown() == fresh() == (5, 5)
        H.fake_stroke(a, 50)                         # a new step
        assert H.commit(a) is not None
        assert shown() == fresh() == (6, 6)
        runtime.goto(C, 3)                           # going back and sculpting: a new branch
        H.enter(b, "SCULPT")
        H.fake_stroke(b, 60)
        assert H.commit(b) is not None
        assert shown() == fresh()
        runtime.delete_past(C, a)                    # steps dropped from the start
        assert shown() == fresh()
        H.enter(b, "OBJECT")                         # an object excluded by Collection Filtering
        sh = C.scene.hindsight
        coll = bpy.data.collections.new("Skip")
        C.scene.collection.children.link(coll)
        coll.objects.link(b)
        sh.filter_collections.add().collection = coll
        sh.filter_mode = 'BLACKLIST'
        assert shown() == fresh() and shown()[1] == len(runtime.history_of(a).entries)
        sh.filter_mode = 'NONE'
        assert shown() == fresh()
        runtime.reset_history(C, a)
        assert shown() == fresh()
    finally:
        globaltl.global_groups = real
        runtime.set_global(C, False)


def test_diagnostics_say_which_engine_runs():
    """Copy diagnostics names the engine in use, so a bug report shows whether the Rust engine loaded."""
    from hindsight.bl import selfcheck
    from hindsight.core import native
    H.fresh()
    line = next(x for x in selfcheck.diagnostics_text().splitlines() if x.startswith("Engine: "))
    assert line == "Engine: Rust" if native.AVAILABLE else line.startswith("Engine: Python ("), line


for fn in (test_diagnostics_say_which_engine_runs, test_global_timeline_is_built_once_until_it_changes, test_crash_journal_knows_what_the_step_cap_dropped, test_crash_journal_knows_what_the_memory_limit_dropped,
           test_error_once_stays_once_after_many_errors, test_jumps_kept_only_as_far_as_blender_can_undo,
           test_exclusion_check_is_free_without_collection_filtering, test_topology_undo_match_is_bit_exact,
           test_clamped_weights_note_goes_away):
    H.run(fn)
H.finish()
