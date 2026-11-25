"""D147: a drag on a property field in a panel (no modal operator to see) is one step, though redraws
can be so slow that the settle timer fires in the middle of it. The depsgraph updates are fed to the
recorder with a fake clock (datarec._now): a drag with gaps of 0.44, 0.24, 0.23, ... seconds (a real
window on llvmpipe) must give ONE "Change" step, a single change still commits after 0.3 s, the wait
after an update follows the gap between the last two updates, and a step is only continued while it
is the newest step of its history."""
import contextlib
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests.addons import hs_fake_addon as FAKE
from hindsight.bl import datarec, idmap, runtime
from hindsight.bl import prefs

C = bpy.context
clock = [0.0]


def setup():
    L.fresh_scene()
    FAKE.register()
    L.track_all()
    L.tick_like("baseline")
    datarec._RECENT.clear()
    datarec._CLOCK.update(last=None, gap=0.0, batch=None)
    clock[0] = 100.0
    datarec._now = lambda: clock[0]
    ob = bpy.data.objects["A"]
    return ob, idmap.uid_of(ob)


def update(ob, uid, value):
    """What the depsgraph handler does for a change of the object at the clock's time."""
    ob.hsfake.strength = value
    datarec._DIRTY[uid] = clock[0]
    datarec._note_update(clock[0])


def advance(dt, step=0.05):
    """The timer: tick every `step` seconds for dt seconds."""
    end = clock[0] + dt
    while clock[0] < end - 1e-9:
        clock[0] = min(end, clock[0] + step)
        datarec.tick()


def steps_of(ob):
    h = runtime.reg().history_for(idmap.uid_of(ob))
    return [] if h is None else h.entries


def teardown():
    datarec._now = lambda: __import__("time").time()
    with contextlib.suppress(Exception):
        FAKE.unregister()


def test_a_slow_drag_is_one_step():
    ob, uid = setup()
    try:
        start = ob.hsfake.strength
        gaps = [0.44, 0.24, 0.23, 0.25, 0.23, 0.24]
        update(ob, uid, 0.6)
        for i, g in enumerate(gaps):
            advance(g)
            update(ob, uid, 0.6 + 0.05 * (i + 1))
        advance(1.0)
        e = steps_of(ob)
        assert len(e) == 1, f"a drag made {len(e)} steps"
        h = runtime.reg().history_for(uid)
        assert round(ob.hsfake.strength, 4) == 0.9
        runtime._goto_history(h, 0, True)
        assert round(ob.hsfake.strength, 4) == round(start, 4), "back did not undo the whole drag"
        runtime._goto_history(h, 1, True)
        assert round(ob.hsfake.strength, 4) == 0.9
    finally:
        teardown()
    print("PASS test_a_slow_drag_is_one_step")


def test_a_single_change_commits_after_the_settle_time():
    ob, uid = setup()
    try:
        update(ob, uid, 0.7)
        advance(0.25)
        assert not steps_of(ob), "committed before the settle time"
        advance(0.1)
        assert len(steps_of(ob)) == 1, "an isolated change did not commit after 0.3 s"
    finally:
        teardown()
    print("PASS test_a_single_change_commits_after_the_settle_time")


def test_the_wait_follows_the_gap_between_updates():
    ob, uid = setup()
    try:
        for v in (0.6, 0.65, 0.7):
            update(ob, uid, v)
            advance(0.24)
        # the gap is 0.24 s: the wait is 1.5 x 0.24 = 0.36 s after the last update; 0.24 has gone by
        advance(0.07)
        assert not steps_of(ob), "committed 0.31 s after an update that followed a 0.24 s gap"
        advance(0.1)
        assert len(steps_of(ob)) == 1
    finally:
        teardown()
    print("PASS test_the_wait_follows_the_gap_between_updates")


def test_a_drag_back_to_where_it_started_leaves_no_step():
    ob, uid = setup()
    try:
        ob.hsfake.strength = 0.3                 # a stored value (an unset one reads as the default)
        datarec._DIRTY[uid] = clock[0]
        datarec._note_update(clock[0])
        advance(1.0)
        n0 = len(steps_of(ob))
        clock[0] += 5.0
        start = ob.hsfake.strength
        update(ob, uid, 0.9)
        advance(0.44)                           # the timer committed it in the middle of the drag
        assert len(steps_of(ob)) == n0 + 1
        update(ob, uid, start)
        advance(1.0)
        assert len(steps_of(ob)) == n0, "a drag that ended where it began left a step"
    finally:
        teardown()
    print("PASS test_a_drag_back_to_where_it_started_leaves_no_step")


def test_only_the_newest_step_is_continued():
    ob, uid = setup()
    try:
        update(ob, uid, 0.9)
        advance(0.5)
        assert len(steps_of(ob)) == 1
        ob.location.x += 1.0                     # another step on the same history, by an operator
        datarec.finish_operation("Move")
        assert len(steps_of(ob)) == 2
        update(ob, uid, 0.95)
        advance(1.0)
        assert len(steps_of(ob)) == 3, "a change after another step was merged into the step before it"
        clock[0] += 5.0                           # a later, separate change
        update(ob, uid, 0.1)
        advance(1.0)
        assert len(steps_of(ob)) == 4
    finally:
        teardown()
    print("PASS test_only_the_newest_step_is_continued")


def _interrupted_after(drag_values, tag):
    """Save, drag (merged by the settle timer), then open the file again without saving (a crash): the
    history the journal brings back. Returns (history, the strength the file has, the drag's last)."""
    import tempfile
    from hindsight.core.types import SETTINGS
    ob, uid = setup()
    SETTINGS.crash_recovery = "INTERRUPTED"
    update(ob, uid, 0.5)                             # a stored value (an unset one reads as the default)
    advance(1.0)
    clock[0] += 5.0
    path = os.path.join(tempfile.mkdtemp(prefix="hs_settle_"), f"{tag}.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    saved = round(ob.hsfake.strength, 4)
    update(ob, uid, drag_values[0])
    for i, v in enumerate(drag_values[1:]):
        advance([0.44, 0.24, 0.23, 0.25][i % 4])
        update(ob, uid, v)
    advance(1.0)
    live = [(e.seq, e.meta.get("label")) for e in steps_of(ob)]
    datarec._now = lambda: __import__("time").time()
    bpy.ops.wm.open_mainfile(filepath=path)          # interrupted: the journal is replayed
    prefs.FORCE["record_scope"] = "EVERYTHING"
    ob = bpy.data.objects["A"]
    h = runtime.reg().history_for(idmap.uid_of(ob))
    return ob, h, live, saved


def test_a_merged_step_comes_back_after_a_crash():
    """D147: the merged step is written to the crash journal as a TRUNC and a STEP at the same position.
    After an interrupted session the history has that one step, at the file's state, and going forward
    gives the end of the drag (driven end to end, not reasoned from the replay rules)."""
    try:
        ob, h, live, saved = _interrupted_after([0.6, 0.65, 0.7, 0.75, 0.8], "merged")
        assert len(live) == 2, live                  # the stored value, then the whole drag
        assert h is not None and [(e.seq, e.meta.get("label")) for e in h.entries] == live, \
            (None if h is None else [(e.seq, e.meta.get("label")) for e in h.entries], live)
        assert h.cursor == 1 and round(ob.hsfake.strength, 4) == saved, (h.cursor, ob.hsfake.strength, saved)
        runtime._goto_history(h, 2, True)
        assert round(ob.hsfake.strength, 4) == 0.8, ob.hsfake.strength
        runtime._goto_history(h, 1, True)
        assert round(ob.hsfake.strength, 4) == saved
    finally:
        teardown()
    print("PASS test_a_merged_step_comes_back_after_a_crash")


def test_a_drag_back_to_the_start_leaves_no_step_after_a_crash():
    """D147: a drag that ends where it began takes its merged step away (TRUNC only): nothing comes back."""
    try:
        ob, h, live, saved = _interrupted_after([0.6, 0.7, 0.6, 0.5], "back")      # 0.5: where it began
        assert len(live) == 1, live                  # only the stored value's step
        assert h is not None and [(e.seq, e.meta.get("label")) for e in h.entries] == live, \
            [(e.seq, e.meta.get("label")) for e in h.entries]
        assert round(ob.hsfake.strength, 4) == saved
    finally:
        teardown()
    print("PASS test_a_drag_back_to_the_start_leaves_no_step_after_a_crash")


def test_a_drag_after_an_operator_is_one_step():
    """D147 in a real window (tests_ui/scenarios/s_addon.py): after any operator, the undo step a UI drag
    pushes clears Blender's list of operators. That change of the list was taken for an operator ending,
    so the end of the drag became a step of its own. A list that only got emptied is no operation."""
    ob, uid = setup()
    real = datarec._ops_marker
    marker = [(1, 111)]                           # an operator ran before the drag (Bump, a menu item)
    datarec._ops_marker = lambda: marker[0]
    try:
        datarec._OPS["marker"] = marker[0]
        n0 = len(steps_of(ob))
        update(ob, uid, 0.6)
        for i, g in enumerate([0.44, 0.24, 0.23]):
            advance(g)                            # the first gap: the timer commits in the middle
            update(ob, uid, 0.65 + 0.05 * i)
        marker[0] = (0, 0)                        # the drag's undo push cleared the operator list
        advance(1.0)
        e = steps_of(ob)
        assert len(e) == n0 + 1, f"the drag made {len(e) - n0} steps: {[x.meta.get('label') for x in e]}"
    finally:
        datarec._ops_marker = real
        teardown()
    print("PASS test_a_drag_after_an_operator_is_one_step")


if __name__ == "__main__":
    for t in (test_a_slow_drag_is_one_step, test_a_single_change_commits_after_the_settle_time,
              test_the_wait_follows_the_gap_between_updates, test_a_drag_back_to_where_it_started_leaves_no_step,
              test_only_the_newest_step_is_continued, test_a_merged_step_comes_back_after_a_crash,
              test_a_drag_back_to_the_start_leaves_no_step_after_a_crash, test_a_drag_after_an_operator_is_one_step):
        H.run(t)
    H.finish()
