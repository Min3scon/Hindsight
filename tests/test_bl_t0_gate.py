"""Tier 0 gate, headless: each sculpting row's sequence (tests/t0_ops.py) recorded through the real
timer path, checked with tests/t0_gate_lib.py:
  REPLAY  the meshes after each operation equal those of the same run without Hindsight;
  STEPS   each operation gives exactly one step in the history of the object it changed and none in
          any other (an Edit Mode visit that changes nothing gives none);
  EXACT   every object's sculpt history scrubbed to every position, shuffled and in sweeps both ways,
          matches the oracle bit for bit; so does the global timeline at every position.
Sculpt operators can't run in background mode (they crash Blender without a sculpt session), so
their real-operator runs are in the real-interface scenarios; the rows that use Vertex Paint, Edit
Mode and remesh operators here count as OPS.

Run: blender -b --factory-startup --python tests/test_bl_t0_gate.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tests.harness as H
from tests import t0_gate_lib as L
from tests import t0_ops as T0
from tests.gate import covers


def gate(row):
    L.gate(row, T0.sequences()[row])


@covers("sculpt.positions", "EXACT", "ORACLE", "REPLAY", "STEPS")
def test_gate_positions():
    gate("sculpt.positions")


@covers("sculpt.masks", "EXACT", "ORACLE", "REPLAY", "STEPS")
def test_gate_masks():
    gate("sculpt.masks")


@covers("sculpt.colors", "OPS", "EXACT", "ORACLE", "REPLAY", "STEPS")
def test_gate_colors():
    gate("sculpt.colors")


@covers("sculpt.topology", "OPS", "EXACT", "ORACLE", "REPLAY", "STEPS")
def test_gate_topology():
    gate("sculpt.topology")


@covers("sculpt.joinsep", "OPS", "EXACT", "ORACLE", "REPLAY", "STEPS")
def test_gate_joinsep():
    gate("sculpt.joinsep")


@covers("sculpt.editvisit", "OPS", "EXACT", "ORACLE", "REPLAY", "STEPS")
def test_gate_editvisit():
    gate("sculpt.editvisit")


for fn in (test_gate_positions, test_gate_masks, test_gate_colors, test_gate_topology, test_gate_joinsep,
           test_gate_editvisit):
    H.run(fn)
H.finish()
