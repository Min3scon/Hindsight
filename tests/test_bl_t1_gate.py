"""Tier 1 gate, headless: every row's operations through real operators, checked against the
independent oracle (tests/oracle.py) and a replay of the same operations without Hindsight.

For each row's sequence (tests/t1_ops.py), with the machinery in tests/t1_gate_lib.py:
  REPLAY  the states after each operation equal those of the same run without Hindsight;
  STEPS   each operation gives exactly one step in each history it says it changes and none in any
          other; no object changes without a step (a missed change shows up here); every position
          of the global timeline is a whole operation;
  EXACT   every object's own history scrubbed to every position, in a shuffled order and in full
          sweeps both ways, matches the oracle at that position (mesh bit-exact); the global
          timeline at every operation boundary matches the whole scene; Blender's undo and redo of
          the last operations (emulated) move every cursor exactly.
Step boundaries through the real timer (operator end, settle) are checked in the real-UI scenarios.

Run: blender -b --factory-startup --python tests/test_bl_t1_gate.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers


def gate(row):
    ops = T.sequences()[row]
    plain = L.replay(ops)
    r = L.Run(ops)
    L.check_replay(row, plain, r)
    L.check_steps(r)
    L.recheck(r)


@covers("obj.transforms", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_transforms():
    gate("obj.transforms")


@covers("obj.parenting", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_parenting():
    gate("obj.parenting")


@covers("obj.constraints", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_constraints():
    gate("obj.constraints")


@covers("obj.collections", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_collections():
    gate("obj.collections")


@covers("obj.addremove", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_addremove():
    gate("obj.addremove")


@covers("obj.editsteps", "OPS", "EXACT", "ORACLE", "REPLAY")
def test_gate_editsteps():
    gate("obj.editsteps")


for fn in (test_gate_transforms, test_gate_parenting, test_gate_constraints, test_gate_collections,
           test_gate_addremove, test_gate_editsteps):
    H.run(fn)
H.finish()
