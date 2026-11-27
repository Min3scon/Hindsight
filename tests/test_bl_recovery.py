"""Crash recovery (Interrupted session) leaves every history in step with the meshes in the file.

The file on disk holds each mesh as it was at the last save. After recovery the history must have
that state at its cursor, and every position must give the shape the session had there. The session
is "interrupted" by opening the file again without saving, with its journal left as it was.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile

import bpy
import tests.harness as H
from hindsight.bl import runtime, persistence, joinsep
from hindsight.core.types import SETTINGS, JOIN

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hindsight_recovery_")
NAME = "Sculpty"


def setup():
    H.fresh()
    SETTINGS.save_mode = "SIDECAR"
    SETTINGS.crash_recovery = "INTERRUPTED"
    SETTINGS.max_steps = 10000
    runtime.missing_message[0] = None


def stroke(ob, seed, after):
    """One recorded stroke; after[step number] is the mesh right after it."""
    H.fake_stroke(ob, seed, n=80)
    assert H.commit(ob) is not None, f"no step for seed {seed}"
    h = runtime.history_of(ob)
    after[h.entries[h.cursor - 1].seq] = H.snapshot(ob)


def start(tag, n, name=NAME):
    ob = H.make_mesh(1500, name=name)
    H.enter(ob, "SCULPT")
    after = {}
    for s in range(n):
        stroke(ob, 10 + s, after)
    return ob, after, os.path.join(TMP, f"{tag}.blend")


def save(ob, path):
    H.enter(ob, "OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    H.enter(ob, "SCULPT")
    return H.snapshot(ob)


def interrupt(path, name=NAME):
    """The session ends without saving; the file is opened again."""
    if C.view_layer.objects.active is not None and C.view_layer.objects.active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.wm.open_mainfile(filepath=path)
    return bpy.data.objects[name]


def seqs(ob):
    return [e.seq for e in runtime.history_of(ob).entries]


def check(ob, after, file_state):
    """The mesh is the file's, and every position of the recovered history is the session's shape."""
    h = runtime.history_of(ob)
    H.enter(ob, "SCULPT")
    H.assert_same(H.snapshot(ob), file_state, "mesh after recovery")
    cursor = h.cursor
    for i in list(range(len(h.entries) + 1)) + [0, len(h.entries), cursor]:
        runtime.goto(C, i)
        assert h.cursor == i, (h.cursor, i)
        if i > 0:
            H.assert_same(H.snapshot(ob), after[h.entries[i - 1].seq], f"position {i}")
    H.assert_same(H.snapshot(ob), file_state, "back at the saved position")


def notes():
    return [t for _sev, t in runtime.load_notes]


# D77: the step cap (or the memory limit) dropped more of the oldest steps than the file was saved at
def test_cap_drops_past_the_saved_step():
    setup()
    ob, after, path = start("cap", 5)
    file_state = save(ob, path)
    SETTINGS.max_steps = 5
    for s in range(6):
        stroke(ob, 50 + s, after)
    live = seqs(ob)
    assert len(live) == 5
    SETTINGS.max_steps = 10000
    ob = interrupt(path)
    h = runtime.history_of(ob)
    # the step that leads away from the saved state is kept, though the session had dropped it
    assert seqs(ob) == [live[0] - 1] + live and h.cursor == 0, (seqs(ob), h.cursor)
    check(ob, after, file_state)
    assert not notes(), notes()


def test_cap_drops_after_saving_scrubbed_back():
    setup()
    ob, after, path = start("cap_back", 5)
    all5 = seqs(ob)
    runtime.goto(C, 2)
    file_state = save(ob, path)
    runtime.goto(C, 5)
    SETTINGS.max_steps = 3
    stroke(ob, 50, after)
    live = seqs(ob)
    assert live == all5[3:] + [live[-1]], live
    SETTINGS.max_steps = 10000
    ob = interrupt(path)
    h = runtime.history_of(ob)
    assert seqs(ob) == all5[2:] + [live[-1]] and h.cursor == 0, (seqs(ob), h.cursor)
    check(ob, after, file_state)


def test_delete_earlier_steps_past_the_saved_step():
    setup()
    ob, after, path = start("delete_past", 4)
    all4 = seqs(ob)
    runtime.goto(C, 1)
    file_state = save(ob, path)
    runtime.goto(C, 3)
    assert runtime.delete_past(C, ob) == 3
    stroke(ob, 50, after)           # recorded at the state after step 3; step 4 goes
    live = seqs(ob)
    assert len(live) == 1
    ob = interrupt(path)
    h = runtime.history_of(ob)
    # the step before the saved state goes, the two that lead to where the session went on stay
    assert seqs(ob) == all4[1:3] + live and h.cursor == 0, (seqs(ob), h.cursor)
    check(ob, after, file_state)


# D78: going back past the saved state and recording there removes the steps that lead to the file's
# mesh; what was recorded from then on can't be reached from it and is left out, with a note
def test_new_branch_below_the_saved_step():
    setup()
    ob, after, path = start("branch", 5)
    saved = seqs(ob)
    file_state = save(ob, path)
    runtime.goto(C, 2)
    stroke(ob, 50, after)
    stroke(ob, 51, after)
    assert len(seqs(ob)) == 4
    ob = interrupt(path)
    h = runtime.history_of(ob)
    assert seqs(ob) == saved and h.cursor == 5, (seqs(ob), h.cursor)
    check(ob, after, file_state)
    assert any("2 steps of 'Sculpty'" in t and "could not be brought back" in t for t in notes()), notes()
    # the next session works on the recovered history; a second interruption brings its strokes back
    H.enter(ob, "SCULPT")
    runtime.goto(C, 5)
    stroke(ob, 60, after)
    live = seqs(ob)
    assert live[:5] == saved and len(live) == 6
    ob = interrupt(path)
    h = runtime.history_of(ob)
    assert seqs(ob) == live and h.cursor == 5, (seqs(ob), h.cursor)
    check(ob, after, file_state)
    assert not notes(), notes()                  # what the first recovery left out isn't reported again


def test_branch_at_or_after_the_saved_step_is_recovered():
    setup()
    ob, after, path = start("branch_ok", 3)
    file_state = save(ob, path)
    stroke(ob, 50, after)
    stroke(ob, 51, after)
    runtime.goto(C, 3)              # back to the saved state itself: nothing the file needs goes
    stroke(ob, 52, after)
    live = seqs(ob)
    ob = interrupt(path)
    h = runtime.history_of(ob)
    assert seqs(ob) == live and h.cursor == 3, (seqs(ob), h.cursor)
    check(ob, after, file_state)
    assert not notes(), notes()


# D79: clearing a history
def test_clear_at_the_saved_step_keeps_later_strokes():
    setup()
    ob, after, path = start("clear_same", 3)
    file_state = save(ob, path)
    runtime.reset_history(C, ob)
    stroke(ob, 50, after)
    stroke(ob, 51, after)
    live = seqs(ob)
    ob = interrupt(path)
    h = runtime.history_of(ob)
    assert seqs(ob) == live and h.cursor == 0, (seqs(ob), h.cursor)
    check(ob, after, file_state)
    assert not notes(), notes()


def test_clear_somewhere_else_leaves_later_strokes_out():
    setup()
    ob, after, path = start("clear_else", 3)
    file_state = save(ob, path)
    runtime.goto(C, 1)
    runtime.reset_history(C, ob)
    stroke(ob, 50, after)
    ob = interrupt(path)
    h = runtime.history_of(ob)
    assert h is None or (seqs(ob) == [] and h.cursor == 0), seqs(ob)
    H.assert_same(H.snapshot(ob), file_state, "mesh after recovery")
    assert any("1 step of 'Sculpty'" in t for t in notes()), notes()


# D80: a second interruption after a recovery that kept steps the session had dropped
def test_second_interruption_starts_from_the_recovered_history():
    setup()
    ob, after, path = start("twice", 5)
    file_state = save(ob, path)
    SETTINGS.max_steps = 5
    for s in range(6):
        stroke(ob, 50 + s, after)
    SETTINGS.max_steps = 10000
    ob = interrupt(path)
    first = seqs(ob)
    assert len(first) == 6 and runtime.history_of(ob).cursor == 0
    H.enter(ob, "SCULPT")
    runtime.goto(C, 3)
    stroke(ob, 70, after)           # replaces the last three recovered steps
    live = seqs(ob)
    assert live == first[:3] + [live[-1]], live
    ob = interrupt(path)
    h = runtime.history_of(ob)
    assert seqs(ob) == live and h.cursor == 0, (seqs(ob), h.cursor)
    check(ob, after, file_state)


# D81: a join made after the save, with strokes before and after it
def test_join_after_the_save():
    setup()
    A = H.make_mesh(1500, name="A", location=(0, 0, 0))
    B = H.make_mesh(1200, name="B", location=(3, 1, 0))
    C.view_layer.update()
    a_after, b_after = {}, {}
    H.enter(A, "SCULPT")
    stroke(A, 1, a_after)
    stroke(A, 2, a_after)
    H.enter(B, "SCULPT")
    stroke(B, 3, b_after)
    H.enter(A, "SCULPT")
    stroke(A, 4, a_after)
    path = os.path.join(TMP, "join.blend")
    H.enter(A, "OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    a_file, b_file = H.snapshot(A), H.snapshot(B)
    H.enter(B, "SCULPT")
    stroke(B, 5, b_after)           # not saved
    b_last = H.snapshot(B)
    H.enter(B, "OBJECT")
    for o in C.selected_objects:
        o.select_set(False)
    A.select_set(True); B.select_set(True)
    C.view_layer.objects.active = A
    assert joinsep.join_with_history(C) is True
    joined = H.snapshot(A)
    H.enter(A, "SCULPT")
    H.fake_stroke(A, 6, n=80)
    assert H.commit(A) is not None
    last = H.snapshot(A)
    live = seqs(A)
    assert len(live) == 7 and runtime.history_of(A).entries[5].kind == JOIN

    A = interrupt(path, "A")
    B = bpy.data.objects["B"]
    h = runtime.history_of(A)
    assert seqs(A) == live, (seqs(A), live)
    assert h.cursor == 4, h.cursor               # the four steps that are in the file
    H.assert_same(H.snapshot(A), a_file, "A after recovery")
    H.assert_same(H.snapshot(B), b_file, "B after recovery")
    H.enter(A, "SCULPT")
    runtime.goto(C, 5)                           # B's stroke that wasn't saved
    H.assert_same(H.snapshot(bpy.data.objects["B"]), b_last, "B at 5")
    H.assert_same(H.snapshot(bpy.data.objects["A"]), a_file, "A at 5")
    runtime.goto(C, 6)                           # the join
    assert bpy.data.objects.get("B") is None
    H.assert_same(H.snapshot(bpy.data.objects["A"]), joined, "joined")
    runtime.goto(C, 7)
    H.assert_same(H.snapshot(bpy.data.objects["A"]), last, "stroke after the join")
    runtime.goto(C, 4)
    H.assert_same(H.snapshot(bpy.data.objects["A"]), a_file, "A back at the saved position")
    H.assert_same(H.snapshot(bpy.data.objects["B"]), b_file, "B back at the saved position")
    runtime.goto(C, 0)
    runtime.goto(C, 4)
    H.assert_same(H.snapshot(bpy.data.objects["A"]), a_file, "A after a round trip")
    H.assert_same(H.snapshot(bpy.data.objects["B"]), b_file, "B after a round trip")


def change_without_hindsight(path, seed):
    """A session without Hindsight strokes the mesh and saves; no backup copy is left (D107 aside)."""
    H.fresh(register=False)
    bpy.ops.wm.open_mainfile(filepath=path)
    H.fake_stroke(bpy.data.objects[NAME], seed, n=80)
    bpy.ops.wm.save_mainfile()
    if os.path.isfile(path + "1"):
        os.remove(path + "1")


def assert_left_out(path):
    setup()
    bpy.ops.wm.open_mainfile(filepath=path)
    assert runtime.history_of(bpy.data.objects[NAME]) is None
    assert any(runtime.OUTSIDE_MESH_TEXT.format(name=NAME) == t for t in notes()), notes()


# D82: a mesh changed in a session without Hindsight: that object's history is left out when the
# file is opened. Strokes made after that must come back after an interruption.
def test_strokes_after_a_mesh_changed_without_hindsight():
    setup()
    ob, _after, path = start("outside_mesh", 3)
    save(ob, path)
    change_without_hindsight(path, 99)
    setup()
    bpy.ops.wm.open_mainfile(filepath=path)
    ob = bpy.data.objects[NAME]
    assert runtime.history_of(ob) is None
    assert any(runtime.OUTSIDE_MESH_TEXT.format(name=NAME) == t for t in notes()), notes()
    H.enter(ob, "SCULPT")
    file_state = H.snapshot(ob)
    after = {}
    stroke(ob, 50, after)
    stroke(ob, 51, after)
    live = seqs(ob)
    ob = interrupt(path)
    h = runtime.history_of(ob)
    assert h is not None and seqs(ob) == live and h.cursor == 0, (h and seqs(ob), h and h.cursor)
    assert not notes(), notes()
    check(ob, after, file_state)
    # the same file changed again without Hindsight is caught again
    change_without_hindsight(path, 98)
    assert_left_out(path)


# R060: the same, with the session before the interruption run by a build before R060: the file's
# fingerprints and the RESET written when the file was opened hold blake2b values without a tag. This build
# recovers it and compares them that way: the strokes come back, and a further change without Hindsight is
# still caught.
def test_strokes_after_a_mesh_changed_without_hindsight_by_an_older_build():
    real = runtime.mesh_fingerprint

    def older_build(ob, algo="x3"):
        return real(ob, "blake2b")               # every fingerprint as builds before R060 made it
    setup()
    runtime.mesh_fingerprint = older_build
    try:
        ob, _after, path = start("outside_mesh_old", 3)
        save(ob, path)
        change_without_hindsight(path, 99)
        setup()
        bpy.ops.wm.open_mainfile(filepath=path)
        ob = bpy.data.objects[NAME]
        assert runtime.history_of(ob) is None
        assert any(runtime.OUTSIDE_MESH_TEXT.format(name=NAME) == t for t in notes()), notes()
        H.enter(ob, "SCULPT")
        file_state = H.snapshot(ob)
        after = {}
        stroke(ob, 50, after)
        stroke(ob, 51, after)
        live = seqs(ob)
    finally:
        runtime.mesh_fingerprint = real
    ob = interrupt(path)
    # what was compared is the RESET's blake2b value of the mesh as the file holds it
    fp = runtime.reg().saved[runtime.obj_uid(ob)]["mesh"]
    assert fp == real(ob, "blake2b") and ":" not in fp, fp
    h = runtime.history_of(ob)
    assert h is not None and seqs(ob) == live and h.cursor == 0, (h and seqs(ob), h and h.cursor)
    assert not notes(), notes()
    check(ob, after, file_state)
    # the same file changed again without Hindsight is caught again
    change_without_hindsight(path, 98)
    assert_left_out(path)


# D107 with D82: the change is bridged from Blender's backup copy; strokes after it come back after an
# interruption, and the file's state is after the bridging step
def test_strokes_after_a_change_bridged_by_the_backup():
    setup()
    ob, after, path = start("outside_bridge", 3)
    save(ob, path)
    H.fresh(register=False)
    bpy.ops.wm.open_mainfile(filepath=path)
    H.fake_stroke(bpy.data.objects[NAME], 99, n=80)
    bpy.ops.wm.save_mainfile()
    setup()
    bpy.ops.wm.open_mainfile(filepath=path)
    ob = bpy.data.objects[NAME]
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == 4 and h.entries[-1].load().label == runtime.OUTSIDE_LABEL
    H.enter(ob, "SCULPT")
    file_state = H.snapshot(ob)
    after[h.entries[-1].seq] = file_state
    stroke(ob, 50, after)
    live = seqs(ob)
    ob = interrupt(path)
    h = runtime.history_of(ob)
    assert h is not None and seqs(ob) == live and h.cursor == 4, (h and seqs(ob), h and h.cursor)
    check(ob, after, file_state)


# D83: values changed in a session without Hindsight become one step when the file is opened. The
# file already holds them, so after an interruption its state is the position after that step, and
# what was recorded next comes back as a step ahead.
def test_steps_after_values_changed_without_hindsight():
    import hindsight
    from tests import oracle as O
    from tests import t1_gate_lib as L
    from tests import t1_ops as T
    from hindsight.bl import datarec

    def state():
        return O.object_state(bpy.data.objects["A"], derived=False)

    def op(o):
        o.run()
        L.tick_like(o.label)

    def configure():
        SETTINGS.save_mode = "SIDECAR"
        SETTINGS.crash_recovery = "INTERRUPTED"

    L.fresh_scene()
    L.track_all()
    runtime.reg().preserve_modes = False
    configure()
    states = [state()]
    for o in (T.translate("A", (1, 0, 0)), T.resize("A", 1.5)):
        op(o)
        states.append(state())
    path = os.path.join(TMP, "outside_values.blend")
    T.select()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    hindsight.unregister()
    H._registered[0] = False
    T.select("A")
    bpy.ops.transform.translate(value=(0, 5, 0))
    bpy.ops.wm.save_as_mainfile(filepath=path)
    hindsight.register()
    H._registered[0] = True
    configure()
    bpy.ops.wm.open_mainfile(filepath=path)
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    states.append(state())
    op(T.translate("A", (0, 0, 2)))              # not saved
    states.append(state())
    h = runtime.history_of(bpy.data.objects["A"])
    live = [e.seq for e in h.entries]
    assert len(live) == 4 and h.entries[2].meta.get("label") == runtime.OUTSIDE_LABEL

    bpy.ops.wm.open_mainfile(filepath=path)      # interrupted
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    h = runtime.history_of(bpy.data.objects["A"])
    assert [e.seq for e in h.entries] == live and h.cursor == 3, ([e.seq for e in h.entries], h.cursor)
    assert state() == states[3], "the file's state"
    for pos in (0, 1, 2, 3, 4, 3):
        runtime._goto_history(h, pos, True)
        assert state() == states[pos], f"position {pos}"


# R064: recovery takes the journal's step bytes as they are (meta from the step's header), so opening a
# file after a crash decodes no step; the steps come back packed and play exactly as the session's did.
def test_recovery_decodes_no_step():
    from hindsight.core import step as stepmod
    setup()
    ob, after, path = start("nodecode", 3)
    file_state = save(ob, path)
    for s in range(12):
        stroke(ob, 80 + s, after)
    live = seqs(ob)
    real = stepmod.step_from_bytes
    calls = []

    def spy(b):
        calls.append(1)
        return real(b)
    stepmod.step_from_bytes = spy
    try:
        ob = interrupt(path)
    finally:
        stepmod.step_from_bytes = real
    h = runtime.history_of(ob)
    assert seqs(ob) == live and h.cursor == 3, (seqs(ob), h.cursor)
    assert not calls, f"recovery decoded {len(calls)} steps"
    recovered = h.entries[3:]
    assert all(e.is_packed() and e.blob is not None for e in recovered)
    for e in recovered:
        st = e.load()
        assert st.seq == e.seq and st.target == e.target and st.meta()["label"] == e.meta["label"], e.meta
    check(ob, after, file_state)


# R067: with Crash Recovery set to Last saved (the default) the journal of an earlier session is let go
# when the file is opened: it isn't read first (it was read through, checksums and all, and then emptied)
def test_last_saved_doesn_t_read_the_old_journal():
    from hindsight.core import journal as J
    setup()
    ob, after, path = start("lastsaved", 2)
    file_state = save(ob, path)
    for s in range(15):
        stroke(ob, 90 + s, after)
    jp = persistence.journal_path(path)
    assert os.path.getsize(jp) > 10_000
    SETTINGS.crash_recovery = "LAST_SAVED"
    real = J._scan
    scans = []

    def spy(*a, **k):
        scans.append(a[1])
        return real(*a, **k)
    J._scan = spy
    try:
        ob = interrupt(path)
    finally:
        J._scan = real
        SETTINGS.crash_recovery = "INTERRUPTED"
    assert not [n for n in scans if n > 1000], scans
    h = runtime.history_of(ob)
    assert seqs(ob) == seqs(ob)[:2] and h.cursor == 2 and len(h.entries) == 2, (seqs(ob), h.cursor)
    H.enter(ob, "SCULPT")
    H.assert_same(H.snapshot(ob), file_state, "the saved mesh")
    recs = J.Journal(jp).read()
    assert recs and recs[0][0] == "SAVE" and not [r for r in recs if r[0] == "STEP"], [r[0] for r in recs]


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_") and callable(fn):
                H.run(fn)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    H.finish()
