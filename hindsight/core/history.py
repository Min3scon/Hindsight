"""Restore engine: per-object mesh baselines and the undo timeline: the engine's MeshState and History (24.4, #132;
their Python is tests/reference/history.py).

A ``History`` is a list of ``Entry`` slots plus a cursor; the datablock shows the state after ``entries[:cursor]``.
``goto`` walks the crossed entries and folds them into ``MeshState`` objects (one per recorded datablock uid): runs of
packed DELTA steps of one object in one engine call (apply_steps, the GIL released), any other step one at a time.
DELTA steps hold exact per-element changes of the bit patterns (see delta.py), applied forwards or backwards;
TOPO/JOIN steps carry full before/after snapshots; data records (DataChange, any step kind) set recorded values from
adapters other than the mesh. Both classes run the reference's Python operations in order, so results and exceptions
are the reference's; the arrays, values and entries stay Python objects.
"""
from __future__ import annotations

from . import delta as _delta, step as _step, topopack as _topopack
from .native import ENGINE as _N

TOPO_MARK = "__TOPO__"
DATA_MARK = "@"                # changed-key tuples ("@", adapter, path) for data records
SNAPSHOTS_KEY = "_snapshots"   # same key as step.SNAPSHOTS_KEY

_N.history_setup(_delta, _step, _topopack)
MeshState = _N.MeshState
History = _N.History
