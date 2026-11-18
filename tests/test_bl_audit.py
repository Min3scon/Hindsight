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


for fn in (test_error_once_stays_once_after_many_errors, test_jumps_kept_only_as_far_as_blender_can_undo,
           test_exclusion_check_is_free_without_collection_filtering, test_topology_undo_match_is_bit_exact,
           test_clamped_weights_note_goes_away):
    H.run(fn)
H.finish()
