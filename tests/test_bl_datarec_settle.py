"""D147: a drag on a property field in a panel (no modal operator to see) is one step, though redraws
can be so slow that the settle timer fires in the middle of it. The depsgraph updates are fed to the
recorder with a fake clock (datarec._now): a drag with gaps of 0.44, 0.24, 0.23, ... seconds (a real
window on llvmpipe) must give ONE "Change" step, a single change still commits after 0.3 s, the wait
after an update follows the gap between the last two updates, and a step is only continued while it
is the newest step of its history."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from tests.addons import hs_fake_addon as FAKE
from hindsight.bl import datarec, idmap, runtime

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
    try:
        FAKE.unregister()
    except Exception:
        pass


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


if __name__ == "__main__":
    for t in (test_a_slow_drag_is_one_step, test_a_single_change_commits_after_the_settle_time,
              test_the_wait_follows_the_gap_between_updates, test_a_drag_back_to_where_it_started_leaves_no_step,
              test_only_the_newest_step_is_continued):
        H.run(t)
    H.finish()
