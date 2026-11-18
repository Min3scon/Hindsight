"""D104: linked duplicates (Alt+D) share one mesh, so they share one history.

Before, with recording on for sculpting only, each object got a history of its own over the one mesh:
going back in A's history put the mesh back to before A's stroke and wiped B's stroke too, and going
back in B's played B's steps onto a mesh A had changed. Now a stroke on either is a step of the one
history, every position is a real state of the shared mesh, and both objects show that history.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from hindsight.bl import runtime

C = bpy.context


def stroke(ob, seed, states):
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, seed, n=60)
    assert runtime.commit(ob) is not None, f"no step for {ob.name} stroke {seed}"
    states.append(H.snapshot(ob))


def linked_dup(a, name="B"):
    H.enter(a, "OBJECT")
    for o in C.view_layer.objects:
        o.select_set(o == a)
    C.view_layer.objects.active = a
    bpy.ops.object.duplicate(linked=True)
    b = C.active_object
    b.name = name
    return b


def check_every_position(ob, states):
    h = runtime.history_of(ob)
    for i in list(range(len(h.entries) + 1)) + list(range(len(h.entries), -1, -1)):
        runtime._goto_history(h, i, True)
        H.assert_same(H.snapshot(ob), states[i], f"{ob.name} at {i}")


def test_strokes_on_both_are_one_history():
    H.fresh()
    a = H.make_mesh(1500, name="A")
    states = [H.snapshot(a)]
    stroke(a, 1, states)
    b = linked_dup(a)
    assert a.data == b.data
    stroke(b, 2, states)
    stroke(a, 3, states)
    stroke(b, 4, states)
    ha, hb = runtime.history_of(a), runtime.history_of(b)
    assert ha is hb and len(ha.entries) == 4, (ha, hb, ha and len(ha.entries))
    for ob in (a, b):
        H.enter(ob, "OBJECT")
        C.view_layer.objects.active = ob
        check_every_position(ob, states)
    # scrubbed back in B's slider, a stroke on A replaces what came after, as on one object
    H.enter(b, "SCULPT")
    runtime.goto(C, 2)
    assert ha.cursor == 2
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 9, n=60)
    assert runtime.commit(a) is not None
    assert len(ha.entries) == 3 and ha.cursor == 3, (len(ha.entries), ha.cursor)


def test_b_sculpted_first():
    H.fresh()
    a = H.make_mesh(1500, name="A")
    b = linked_dup(a)
    states = [H.snapshot(a)]
    stroke(b, 5, states)
    stroke(a, 6, states)
    assert runtime.history_of(a) is runtime.history_of(b)
    check_every_position(a, states)


def test_make_single_user_leaves_the_shared_history():
    H.fresh()
    a = H.make_mesh(1500, name="A")
    states = [H.snapshot(a)]
    stroke(a, 1, states)
    b = linked_dup(a)
    stroke(b, 2, states)
    H.enter(b, "OBJECT")
    b.data = b.data.copy()                        # Make Single User > Object Data
    b_start = H.snapshot(b)
    H.enter(b, "SCULPT")
    H.fake_stroke(b, 3, n=60)
    assert runtime.commit(b) is not None
    hb = runtime.history_of(b)
    ha = runtime.history_of(a)
    assert hb is not ha and len(hb.entries) == 1, "B's own mesh has a history of its own"
    runtime._goto_history(hb, 0, True)
    H.assert_same(H.snapshot(b), b_start, "B back to where its own mesh started")
    check_every_position(a, states)


if __name__ == "__main__":
    for t in (test_strokes_on_both_are_one_history, test_b_sculpted_first,
              test_make_single_user_leaves_the_shared_history):
        H.run(t)
    H.finish()
