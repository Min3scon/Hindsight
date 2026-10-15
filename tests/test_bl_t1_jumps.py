"""Tier 1: Blender's undo and redo of Hindsight jumps, headless.

Blender's undo stack is modelled from what Hindsight really pushes: each user operation is an entry,
and each jump Hindsight puts on Blender's undo (datarec.push_undo) is an entry. Ctrl+Z puts back the
data of the entry before the current one, Ctrl+Shift+Z the one after, and Hindsight's cursors and
stored states stay as they were, as in Blender. After every undo and redo each history's cursor must
be exactly the one the data belongs to, and nothing is recorded.

D35: a global jump that changed only meshes (Edit Mode steps played back in Object Mode) was not put
on Blender's undo, and only the newest jump was remembered. One Ctrl+Z then went back over several
jumps, the grid's cursor stayed at its last step while its mesh was the first one, and a later redo
was matched on the object values alone and claimed the end of the history.

Run: blender -b --factory-startup --python tests/test_bl_t1_jumps.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from hindsight.core import globaltl
from hindsight.core import importer as I
from hindsight.bl import datarec, idmap, meshio, runtime

C = bpy.context


def cursors() -> dict:
    return {hid: h.cursor for hid, h in runtime.reg().histories.items()}


def lengths() -> dict:
    return {hid: len(h.entries) for hid, h in runtime.reg().histories.items()}


class Stack:
    """Blender's undo stack, one entry per user operation or pushed jump: the cursors of every
    history when it was pushed (Hindsight is exact, so the cursors name the data)."""

    def __init__(self):
        self.entries = [(cursors(), gpos())]
        self.at = 0

    def push(self):
        del self.entries[self.at + 1:]
        self.entries.append((cursors(), gpos()))
        self.at += 1

    def move(self, direction):
        """Ctrl+Z or Ctrl+Shift+Z: the data of the neighbouring entry comes back; Hindsight's own
        cursors and states don't move (on_undo has to find them)."""
        self.at += -1 if direction == "undo" else 1
        want, g = self.entries[self.at]
        if C.object is not None and C.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")      # Blender's memfile undo is in Object Mode
        at = cursors()
        saved = {}
        for h in runtime.reg().histories.values():
            saved.update(L._saved_states(h))
        # put the data there with Hindsight's own exact playback (not a push: final=False)
        runtime.global_goto(C, g, False)
        assert {hid: c for hid, c in cursors().items()} == {hid: want.get(hid, 0) for hid in cursors()}
        for hid, c in at.items():
            runtime.reg().histories[hid].cursor = c
        L._restore_states(saved)
        runtime.UNDO_DIRECTION[0] = direction
        datarec.on_undo(direction)
        return want


def run_ops(stack):
    T.translate("A", (1, 0, 0)).run()
    L.tick_like("move A")
    stack.push()
    T.add_primitive("cube", (0, -5, 0)).run()
    L.tick_like("add a cube")
    stack.push()
    for op in T.edit_ops("E", 3)[:3] + T.edit_ops("E", 3)[4:6]:   # grab, rotate, scale, extrude, bevel
        op.run()
        L.tick_like(op.label)
        stack.push()
    T.select()                                          # leaving Edit Mode: not a step
    L.tick_like("leave Edit Mode")
    T.translate("B", (0, 1, 0)).run()
    L.tick_like("move B")
    stack.push()


def setup():
    H.fresh()
    T.build_scene()
    L.track_all()
    runtime.reg().preserve_modes = False
    stack = Stack()
    run_ops(stack)
    pushes = []
    orig = datarec.push_undo

    def push(message="Hindsight jump"):
        pushes.append(message)
        stack.push()
        return True
    datarec.push_undo = push
    datarec.PUSH_IN_BACKGROUND[0] = True
    return stack, orig


def teardown(orig):
    datarec.push_undo = orig
    datarec.PUSH_IN_BACKGROUND[0] = False


def gpos():
    return globaltl.global_position(runtime.reg(), runtime._visible_uids())


def glen():
    return globaltl.global_length(runtime.reg(), runtime._visible_uids())


def check_follows(stack, what):
    want = stack.entries[stack.at][0]
    got = cursors()
    assert got == {hid: want.get(hid, 0) for hid in got}, f"{what}: cursors {got}, the data is at {want}"


def check_exact(states_at):
    """Every object's data is the recorded state at the global position Hindsight shows."""
    want = states_at.get(gpos())
    if want is not None:
        now = {ob.name: O.object_state(ob, derived=False) for ob in C.scene.objects}
        assert now == want, O.diff(want, now)[:3]


@covers("obj.editsteps", "UNDO")
@covers("obj.transforms", "UNDO")
@covers("obj.addremove", "UNDO")
def test_undo_and_redo_across_a_global_walk():
    """The global timeline walked to every position and back (each jump one Blender undo entry),
    then Ctrl+Z and Ctrl+Shift+Z: every history follows each time, nothing is recorded."""
    stack, orig = setup()
    try:
        n = lengths()
        states_at = {}
        for g in list(range(glen(), -1, -1)) + list(range(glen() + 1)):
            runtime.global_goto(C, g, True)
            states_at.setdefault(gpos(), {ob.name: O.object_state(ob, derived=False) for ob in C.scene.objects})
        assert gpos() == glen()
        for direction in ["undo"] * 6 + ["redo"] * 6:
            stack.move(direction)
            check_follows(stack, direction)
            check_exact(states_at)
        assert lengths() == n, "a step was recorded"
        # a scrub of several steps at once, then Ctrl+Z and Ctrl+Shift+Z
        runtime.global_goto(C, gpos() - 4, True)
        for direction in ("undo", "redo", "undo", "undo", "redo", "redo"):
            stack.move(direction)
            check_follows(stack, f"after a scrub: {direction}")
            check_exact(states_at)
        assert lengths() == n, "a step was recorded"
        # two scrubs of three steps, both undone and redone: Blender's undo goes back over each
        runtime.global_goto(C, glen(), True)
        runtime.global_goto(C, glen() - 3, True)
        runtime.global_goto(C, glen() - 6, True)
        for direction in ("undo", "undo", "redo", "redo", "undo", "undo"):
            stack.move(direction)
            check_follows(stack, f"two scrubs: {direction}")
            check_exact(states_at)
        assert gpos() == glen()
        assert lengths() == n, "a step was recorded"
    finally:
        teardown(orig)


@covers("obj.editsteps", "UNDO.MODES")
def test_a_jump_from_edit_mode_is_undone_in_one_go():
    """A jump made while the grid is in Edit Mode is one Blender undo entry, pushed in Object Mode;
    Edit Mode comes back after it. Ctrl+Z then returns everything to before the jump."""
    stack, orig = setup()
    try:
        T.select("E")
        bpy.ops.object.mode_set(mode="EDIT")
        before = cursors()
        runtime.global_goto(C, gpos() - 3, True)
        assert C.object.name == "E" and C.object.mode == "EDIT", "Edit Mode is kept"
        assert stack.entries[stack.at][0] == cursors(), "the jump is one entry on Blender's undo"
        stack.move("undo")
        check_follows(stack, "undo of the jump")
        assert cursors() == before
        stack.move("redo")
        check_follows(stack, "redo of the jump")
    finally:
        teardown(orig)


@covers("obj.editsteps", "UNDO")
def test_a_redo_is_matched_on_meshes_too():
    """D35, the second half: a jump that moved the grid's mesh and nothing else. Blender's redo that
    brings back the object values but not that mesh is not that jump."""
    stack, orig = setup()
    try:
        b = bpy.data.objects["B"]
        runtime.global_goto(C, gpos() - 2, True)        # back over move B and the bevel of E
        b_after = tuple(b.location)
        stack.move("undo")
        check_follows(stack, "undo")
        j = datarec._jump_candidate("redo")
        assert j is not None and list(j["meshes"]) == [idmap.uid_of(bpy.data.objects["E"])], j["meshes"]
        he = runtime.reg().history_for(idmap.uid_of(bpy.data.objects["E"]))
        e_cursor = he.cursor
        # Blender's redo brings back where the jump put B, but the grid stays as it is now
        b.location = b_after
        C.view_layer.update()
        runtime.UNDO_DIRECTION[0] = "redo"
        datarec.on_undo("redo")
        assert he.cursor == e_cursor, "a redo was matched on B's values without the grid's mesh"
    finally:
        teardown(orig)


@covers("obj.editsteps", "UNDO.GLOBAL_OFF")
def test_blender_undo_never_records_a_step():
    """D40: with Global Undo off a jump isn't on Blender's undo, so Blender's own Ctrl+Z in Edit Mode
    can land on an edit state that isn't next to Hindsight's place. It was recorded as a new step,
    cutting off the steps after the cursor. Now the mesh is put back with a message, nothing is
    recorded and no step is lost."""
    H.fresh()
    T.build_scene()
    L.track_all()
    for op in T.edit_ops("E", 3)[:4]:
        op.run()
        L.tick_like(op.label)
    T.select()
    L.tick_like("leave Edit Mode")
    e = bpy.data.objects["E"]
    h = runtime.history_of(e)
    n = len(h.entries)
    end = meshio.read_snapshot(e.data)
    runtime._goto_history(h, 1, True)                    # back over three Edit Mode steps
    at_one = meshio.read_snapshot(e.data)
    T.select("E")
    bpy.ops.object.mode_set(mode="EDIT")
    runtime.check_mode_transition()
    # what Blender's Edit Mode undo does here: its own stack puts back the edit state of the end
    with runtime.suspended():
        bpy.ops.object.mode_set(mode="OBJECT")
        meshio.write_snapshot(e.data, end)
        bpy.ops.object.mode_set(mode="EDIT")
    runtime.UNDO_DIRECTION[0] = "undo"
    runtime._after_native_undo()
    assert len(h.entries) == n and h.cursor == 1, (len(h.entries), h.cursor, n)
    bpy.ops.object.mode_set(mode="OBJECT")
    got = meshio.read_snapshot(e.data)
    assert I.snapshot_digest(got) == I.snapshot_digest(at_one), "the mesh is back at the history's place"


for fn in (test_undo_and_redo_across_a_global_walk, test_a_jump_from_edit_mode_is_undone_in_one_go,
           test_a_redo_is_matched_on_meshes_too, test_blender_undo_never_records_a_step):
    H.run(fn)
H.finish()
