"""D-f: a Weight Paint stroke is a step. Vertex group weights of every group are recorded per stroke
(a vertex that is not in a group differs from a member at weight 0), going back and forward over
them is exact, they survive save and reload, and Blender's own undo moves the cursor instead of
recording a step (as for sculpt and vertex paint strokes).

Strokes are made through the API (vertex_group.add / remove while the object is in Weight Paint
mode, since brush operators can't run headless), then the recorder's stroke-end path
(runtime.commit, which the timer runs when a stroke ends) is driven as the vertex paint tests do."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile

import bpy
import tests.harness as H
from hindsight.bl import runtime
from hindsight.core.types import SETTINGS

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hindsight_wp_")
NAME = "Painty"


def weights(ob):
    """Independent oracle: {group name: {vertex: weight}} read vertex by vertex."""
    names = {g.index: g.name for g in ob.vertex_groups}
    out = {n: {} for n in names.values()}
    for v in ob.data.vertices:
        for g in v.groups:
            out[names[g.group]][v.index] = round(g.weight, 6)
    return out


def setup(n=1500):
    H.fresh()
    ob = H.make_mesh(n, name=NAME)
    nv = len(ob.data.vertices)
    a, b, c = (ob.vertex_groups.new(name=x) for x in ("Arm", "Leg", "Empty"))
    a.add(list(range(0, nv, 3)), 0.5, "REPLACE")
    b.add(list(range(1, nv, 4)), 0.25, "REPLACE")
    H.enter(ob, "WEIGHT_PAINT")
    return ob, nv


def strokes(ob, nv):
    """Four strokes: change weights, add members (one at weight 0), remove members, touch two groups."""
    ga, gb, gc = (ob.vertex_groups[x] for x in ("Arm", "Leg", "Empty"))
    return [
        lambda: ga.add(list(range(0, 90, 3)), 0.9, "REPLACE"),
        lambda: gb.add([6, 7, 8], 0.0, "REPLACE"),                       # members at weight 0
        lambda: (ga.remove(list(range(0, 30, 3))), gc.add([11, 12], 1.0, "REPLACE")),
        lambda: (gb.add(list(range(40, 60)), 0.75, "ADD"), ga.add([0, 3], 0.1, "REPLACE")),
    ]


def drive(ob, ops):
    states = [weights(ob)]
    for op in ops:
        op()
        assert H.commit(ob) is not None, "a weight stroke made no step"
        states.append(weights(ob))
    return states


def test_weight_paint_strokes_are_steps_and_exact():
    ob, nv = setup()
    h0 = runtime.history_of(ob)
    assert h0 is None or not h0.entries
    states = drive(ob, strokes(ob, nv))
    h = runtime.history_of(ob)
    assert len(h.entries) == 4 and h.cursor == 4, (len(h.entries), h.cursor)
    assert all(e.meta["label"] == "Weight Paint" or e.meta["mode"] == "WEIGHT_PAINT" for e in h.entries)
    assert states[1] != states[0] and states[2] != states[1] and states[3] != states[2]
    # membership is exact: a member at weight 0 is not a non-member
    assert states[2]["Leg"].get(6) == 0.0 and 6 not in states[0]["Leg"]
    for pos in [3, 2, 1, 0, 1, 4, 0, 2, 4]:
        runtime.goto(C, pos)
        assert h.cursor == pos
        assert weights(ob) == states[pos], f"position {pos}"
    print("PASS test_weight_paint_strokes_are_steps_and_exact")


def test_a_stroke_after_going_back_branches_like_other_modes():
    ob, nv = setup()
    states = drive(ob, strokes(ob, nv)[:2])
    h = runtime.history_of(ob)
    runtime.goto(C, 1)
    assert weights(ob) == states[1]
    ob.vertex_groups["Arm"].add([200, 201], 0.33, "REPLACE")
    SETTINGS.truncation_threshold = 10
    assert H.commit(ob) is not None
    assert len(h.entries) == 2 and h.cursor == 2          # the one undone step was replaced, not kept
    print("PASS test_a_stroke_after_going_back_branches_like_other_modes")


def test_save_and_reload():
    ob, nv = setup()
    states = drive(ob, strokes(ob, nv))
    H.enter(ob, "OBJECT")
    SETTINGS.save_mode = "SIDECAR"
    path = os.path.join(TMP, "wp.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    ob = bpy.data.objects[NAME]
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 4 and h.cursor == 4, "history not restored"
    assert weights(ob) == states[4]
    H.enter(ob, "WEIGHT_PAINT")
    for pos in [0, 2, 1, 4, 3, 0, 4]:
        runtime.goto(C, pos)
        assert weights(ob) == states[pos], f"after reload, position {pos}"
    print("PASS test_save_and_reload")


def test_journal_replay_restores_weight_steps():
    ob, nv = setup()
    seen = []
    runtime.journal_hook = lambda t, header, fn=None: seen.append((t, header, fn() if fn else None))
    try:
        drive(ob, strokes(ob, nv))
    finally:
        runtime.journal_hook = None
    steps = [x for x in seen if x[0] == "STEP"]
    assert len(steps) == 4 and all(isinstance(x[2], bytes) and len(x[2]) > 20 for x in steps)
    from hindsight.core import step as stepmod
    for t, hd, blob in steps:
        st = stepmod.step_from_bytes(blob)
        assert st.kind == "DELTA" and any(d.key.startswith("vg:") for d in st.deltas), [d.key for d in st.deltas]
    print("PASS test_journal_replay_restores_weight_steps")


def test_native_undo_moves_the_cursor():
    ob, nv = setup()
    states = drive(ob, strokes(ob, nv)[:3])
    h = runtime.history_of(ob)
    # Blender's undo puts the weights of the previous stroke back
    runtime._STATES[runtime.obj_uid(ob)]
    runtime.goto(C, 2)
    assert weights(ob) == states[2]
    runtime.goto(C, 3)
    for g in ob.vertex_groups:                       # a native undo: weights of step 2 are written back
        g.remove(list(range(nv)))
    for name, d in states[2].items():
        for vi, w in d.items():
            ob.vertex_groups[name].add([vi], w, "REPLACE")
    runtime.on_undo()
    assert len(h.entries) == 3 and h.cursor == 2, (len(h.entries), h.cursor)
    assert weights(ob) == states[2]
    print("PASS test_native_undo_moves_the_cursor")


def test_without_vertex_groups_nothing_breaks():
    H.fresh()
    ob = H.make_mesh(800, name=NAME)
    H.enter(ob, "WEIGHT_PAINT")
    assert H.commit(ob) is None
    g = ob.vertex_groups.new(name="First")          # Blender makes a group on the first stroke
    g.add(list(range(0, 50)), 0.4, "REPLACE")
    assert H.commit(ob) is not None
    runtime.history_of(ob)
    runtime.goto(C, 0)
    assert weights(ob).get("First", {}) == {}, "going back kept the weights of the first stroke"
    runtime.goto(C, 1)
    assert len(weights(ob)["First"]) == 50
    print("PASS test_without_vertex_groups_nothing_breaks")


def wnorm(ob, names=None):
    """weights(ob) with groups that have no member left out (a group can't be told from one with no weights)."""
    w = weights(ob)
    return {k: v for k, v in w.items() if v and (names is None or k in names)}


def test_group_added_between_strokes():
    ob, nv = setup()
    ga = ob.vertex_groups["Arm"]
    states = [wnorm(ob)]
    ga.add(list(range(0, 60, 3)), 0.8, "REPLACE")
    assert H.commit(ob) is not None
    states.append(wnorm(ob))
    hand = ob.vertex_groups.new(name="Hand")                  # a group made between two strokes
    hand.add(list(range(100, 140)), 0.6, "REPLACE")
    assert H.commit(ob) is not None
    states.append(wnorm(ob))
    hand.add(list(range(120, 160)), 0.2, "REPLACE")
    assert H.commit(ob) is not None
    states.append(wnorm(ob))
    h = runtime.history_of(ob)
    assert len(h.entries) == 3
    for pos in [2, 1, 0, 3, 1, 3, 0, 2]:
        runtime.goto(C, pos)
        assert wnorm(ob) == states[pos], f"position {pos}"
    print("PASS test_group_added_between_strokes")


def test_group_removed_between_strokes():
    ob, nv = setup()
    ga, _ = ob.vertex_groups["Arm"], ob.vertex_groups["Leg"]
    states = [weights(ob)]
    ga.add(list(range(0, 60, 3)), 0.8, "REPLACE")
    assert H.commit(ob) is not None
    states.append(weights(ob))
    ob.vertex_groups.remove(ob.vertex_groups["Leg"])         # a group removed between two strokes
    ob.vertex_groups["Arm"].add(list(range(300, 330)), 0.3, "REPLACE")
    assert H.commit(ob) is not None
    states.append(weights(ob))
    ob.vertex_groups["Arm"].add(list(range(310, 350)), 0.9, "REPLACE")
    assert H.commit(ob) is not None
    states.append(weights(ob))
    runtime.history_of(ob)
    for pos in [2, 1, 0, 3, 1, 3, 0, 2]:
        runtime.goto(C, pos)
        got = weights(ob)
        # the group that is gone can't come back (a list step does that); the others are exact
        assert got == {k: v for k, v in states[pos].items() if k in got}, f"position {pos}"
        assert set(got) == {"Arm", "Empty"}
    print("PASS test_group_removed_between_strokes")


def test_crash_recovery_replays_weight_steps():
    from hindsight.core.types import SETTINGS as S
    ob, nv = setup()
    S.save_mode = "SIDECAR"
    S.crash_recovery = "INTERRUPTED"
    ops = strokes(ob, nv)
    states = drive(ob, ops[:2])
    H.enter(ob, "OBJECT")
    path = os.path.join(TMP, "crash.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    file_state = weights(ob)
    H.enter(ob, "WEIGHT_PAINT")
    states += drive(ob, ops[2:])[1:]                         # two more strokes: only in the journal
    H.enter(ob, "OBJECT")
    runtime.missing_message[0] = None
    bpy.ops.wm.open_mainfile(filepath=path)                  # the session ends without saving
    ob = bpy.data.objects[NAME]
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 4, (None if h is None else len(h.entries))
    H.enter(ob, "WEIGHT_PAINT")
    assert weights(ob) == file_state, "mesh after recovery is the file's"
    for pos in [4, 3, 2, 1, 0, 2, 4, 1]:
        runtime.goto(C, pos)
        assert h.cursor == pos
        assert weights(ob) == states[pos], f"recovered position {pos}"
    print("PASS test_crash_recovery_replays_weight_steps")


if __name__ == "__main__":
    for t in (test_weight_paint_strokes_are_steps_and_exact, test_a_stroke_after_going_back_branches_like_other_modes,
              test_save_and_reload, test_journal_replay_restores_weight_steps, test_native_undo_moves_the_cursor,
              test_without_vertex_groups_nothing_breaks, test_group_added_between_strokes,
              test_group_removed_between_strokes, test_crash_recovery_replays_weight_steps):
        H.run(t)
    H.finish()
    shutil.rmtree(TMP, ignore_errors=True)
