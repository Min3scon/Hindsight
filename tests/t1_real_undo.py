"""Blender's real undo and redo in headless tests (bpy.ops.ed.undo / redo work with -b).

The interface's order is kept: an operator runs, Blender pushes its undo step when it finishes, then
Hindsight's timer commits (and gives out ids). Hindsight's own jumps are pushed for real too. The
stack below mirrors Blender's: for every entry, the data Blender will put back and the cursor of
every history that belongs to that data.
"""
import bpy

from tests import t1_gate_lib as L
from hindsight.bl import datarec, runtime

C = bpy.context


def cursors() -> dict:
    return {hid: h.cursor for hid, h in runtime.reg().histories.items()}


def lengths() -> dict:
    return {hid: len(h.entries) for hid, h in runtime.reg().histories.items()}


class RealUndo:
    def __init__(self):
        self.stack, self.at = [], -1
        self._orig = datarec.push_undo
        datarec.PUSH_IN_BACKGROUND[0] = True
        datarec.push_undo = self._push_jump
        self.push("start")

    def close(self):
        datarec.push_undo = self._orig
        datarec.PUSH_IN_BACKGROUND[0] = False

    def push(self, label):
        bpy.ops.ed.undo_push(message=label)
        del self.stack[self.at + 1:]
        self.stack.append([label, cursors(), L.snap()])
        self.at = len(self.stack) - 1

    def _push_jump(self, message="Hindsight jump"):
        self.push(message)
        return True

    def op(self, label, fn):
        """A user operation: it runs, Blender pushes its undo step, Hindsight's timer commits."""
        fn()
        bpy.ops.ed.undo_push(message=label)
        del self.stack[self.at + 1:]
        snap = L.snap()
        L.tick_like(label)
        self.stack.append([label, cursors(), snap])
        self.at = len(self.stack) - 1

    def move(self, direction):
        """Ctrl+Z or Ctrl+Shift+Z for real. Returns (label, problems): problems lists what's wrong."""
        n0 = lengths()
        if direction == "undo":
            bpy.ops.ed.undo()
            self.at -= 1
        else:
            bpy.ops.ed.redo()
            self.at += 1
        label, want, snap = self.stack[self.at]
        problems = []
        now = L.snap()
        if now != snap:
            from tests import oracle as O
            problems.append(("Blender's data isn't that entry's", O.diff(snap, now)[:3]))
        got = cursors()
        bad = {h: (got.get(h), want.get(h, 0)) for h in got if got.get(h) != want.get(h, 0)}
        if bad:
            problems.append(("cursors don't follow", bad))
        if lengths() != n0:
            problems.append(("something was recorded", lengths(), n0))
        return label, problems
