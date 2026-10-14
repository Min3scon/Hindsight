"""Restore engine: per-object mesh baselines and the undo timeline.

Pure Python + numpy. No bpy imports.

A ``History`` is a list of ``Entry`` slots plus a cursor; the datablock shows the
state after ``entries[:cursor]``. ``goto`` walks the crossed entries and folds
them into ``MeshState`` objects (one per recorded datablock uid). DELTA steps hold
exact per-element changes of the bit patterns (see delta.py), applied forwards or
backwards; TOPO/JOIN steps carry full before/after snapshots; data records
(DataChange, any step kind) set recorded values from adapters other than the mesh.
"""
from __future__ import annotations

import numpy as np

from . import delta as _delta
from .types import (
    DATA, DELTA, JOIN, MISSING,
    ChannelDelta, DataChange, Entry, Snapshot, Step,
    key_dtype, key_width, split_key,
)

TOPO_MARK = "__TOPO__"
DATA_MARK = "@"
SNAPSHOTS_KEY = "_snapshots"   # same key as step.SNAPSHOTS_KEY (not imported: step imports history types)       # changed-key tuples ("@", adapter, path) for data records

def _steps(entries: list, states: dict):
    """Yield the Step of each entry in order, or None when its object isn't being moved.

    Whether an object is being moved is checked when its step comes up, not before the jump: a
    join step crossed backwards brings its members back (and into `states`) in the middle of it.
    (Decoding ahead on worker threads was tried: faster in plain Python, but inside Blender the
    threads compete with the main thread for the interpreter and long jumps got 70% slower.)
    """
    for e in entries:
        yield e.load() if e.target in states else None


class MeshState:
    """Current recorded state of ONE datablock (the baseline shown at the cursor): mesh channels
    for meshes, plus the values data adapters record ((adapter, path) -> value)."""

    __slots__ = ("counts", "arrays", "removed", "values", "snaps")

    def __init__(self, counts: dict, arrays: dict, values: dict | None = None):
        self.counts = dict(counts)
        self.arrays = arrays          # key -> flat native array (owned, not copied)
        self.removed = set()          # keys whose attribute must be deleted from the mesh
        self.values = dict(values or {})   # (adapter, path) -> value; absent = MISSING
        self.snaps = {}               # uid -> Snapshot carried by crossed data steps (objects to recreate)

    # ------------------------------------------------------------------ data records
    def apply_record(self, rec: DataChange, forward: bool) -> set:
        """Set the recorded values to the side of the change we move to. Returns the changed keys."""
        out = set()
        a = rec.adapter
        for path, (old, new) in rec.changes.items():
            v = new if forward else old
            k = (a, path)
            if v is MISSING:
                self.values.pop(k, None)
            else:
                self.values[k] = v
            out.add((DATA_MARK, a, path))
        return out

    # ------------------------------------------------------------------ deltas
    def _ensure(self, key: str) -> np.ndarray:
        arr = self.arrays.get(key)
        if arr is None:
            domain = split_key(key)[1]
            arr = np.zeros(int(self.counts[domain]) * key_width(key), dtype=key_dtype(key))
            self.arrays[key] = arr
        elif not arr.flags.c_contiguous:
            arr = np.ascontiguousarray(arr)
            self.arrays[key] = arr
        return arr

    def apply_delta(self, d: ChannelDelta, forward: bool) -> None:
        key = d.key
        arr = self._ensure(key)
        if len(d.idx):
            # idx is unique, so the fancy-index read/modify/write is exact
            _delta.apply(arr, d, forward)
        exists = d.exists[1] if forward else d.exists[0]
        if exists:
            self.removed.discard(key)
        else:
            self.arrays.pop(key, None)
            self.removed.add(key)

    # --------------------------------------------------------------- snapshots
    def set_snapshot(self, snap: Snapshot) -> None:
        old_keys = set(self.arrays)
        self.counts = dict(snap.counts)
        self.arrays = {k: np.array(v, copy=True) for k, v in snap.arrays.items()}
        new_keys = set(self.arrays)
        self.removed |= old_keys - new_keys
        self.removed -= new_keys

    def to_snapshot(self) -> Snapshot:
        return Snapshot(dict(self.counts), {k: v.copy() for k, v in self.arrays.items()})


class History:
    """Undo timeline of one object (or of several after a join)."""

    def __init__(self, uid: str, members: list | None = None):
        self.uid = uid
        self.members = list(members) if members else [uid]
        self.entries: list = []
        self.cursor = 0
        self.enabled = True
        self.morph_target = None

    def __len__(self) -> int:
        return len(self.entries)

    def __repr__(self) -> str:
        return f"<History {self.uid!r} {self.cursor}/{len(self.entries)}>"

    def future_count(self) -> int:
        return len(self.entries) - self.cursor

    # ----------------------------------------------------------- editing list
    def truncate_future(self) -> list:
        dropped = self.entries[self.cursor:]
        del self.entries[self.cursor:]
        if self.morph_target is not None and self.morph_target > self.cursor:
            self.morph_target = None
        return dropped

    def push(self, step: Step) -> list:
        dropped = self.truncate_future()
        self.entries.append(step if isinstance(step, Entry) else Entry(step))
        self.cursor = len(self.entries)
        return dropped

    def drop_oldest(self, n: int) -> int:
        n = max(0, min(int(n), len(self.entries)))
        if n == 0:
            return 0
        del self.entries[:n]
        self.cursor = max(0, self.cursor - n)
        if self.morph_target is not None:
            mt = self.morph_target - n
            self.morph_target = mt if mt >= 0 else None
        return n

    def delete_past(self) -> int:
        return self.drop_oldest(self.cursor)

    def reset(self) -> None:
        self.entries = []
        self.cursor = 0
        self.morph_target = None

    def positions_of(self, target: str) -> list:
        return [i for i, e in enumerate(self.entries) if e.target == target]

    # ------------------------------------------------------------------ goto
    def goto(self, pos: int, states: dict, join_hook=None) -> dict:
        n = len(self.entries)
        pos = max(0, min(int(pos), n))
        changed: dict = {}
        cur = self.cursor
        if pos != cur:
            forward = pos > cur
            order = range(cur, pos) if forward else range(cur - 1, pos - 1, -1)
            entries = [self.entries[i] for i in order]
            for entry, step in zip(entries, _steps(entries, states)):
                if step is not None:       # None: object not being moved, skipped without unpacking
                    self._cross(entry, step, forward, states, join_hook, changed)
        self.cursor = pos
        return changed

    @staticmethod
    def _cross(entry: Entry, step: Step, forward: bool, states: dict, join_hook, changed: dict) -> None:
        target = entry.target
        st = states[target]
        kind = step.kind
        if step.records:
            # data records: applied before the mesh part going forward, after it going back, so a
            # step is undone in the reverse order of how it was done
            if forward:
                History._records(step, True, states, changed)
            else:
                History._mesh(entry, step, False, st, states, join_hook, changed)
                History._records(step, False, states, changed)
                return
        History._mesh(entry, step, forward, st, states, join_hook, changed)

    @staticmethod
    def _records(step: Step, forward: bool, states: dict, changed: dict) -> None:
        snaps = (step.extra or {}).get(SNAPSHOTS_KEY) if step.kind == DATA else None
        for rec in (step.records if forward else reversed(step.records)):
            rs = states.get(rec.target)
            if rs is not None:
                if snaps:
                    rs.snaps.update(snaps)
                changed.setdefault(rec.target, set()).update(rs.apply_record(rec, forward))

    @staticmethod
    def _mesh(entry: Entry, step: Step, forward: bool, st, states: dict, join_hook, changed: dict) -> None:
        target = entry.target
        kind = step.kind
        if kind == DATA:
            return
        if kind == DELTA:
            if step.deltas:
                keys = changed.setdefault(target, set())
                for d in step.deltas:
                    st.apply_delta(d, forward)
                    keys.add(d.key)
            return
        if kind == JOIN and join_hook is not None:
            res = join_hook(step, forward, states)
            changed.setdefault(target, set()).add(TOPO_MARK)
            if isinstance(res, dict):
                for uid, keys in res.items():
                    changed.setdefault(uid, set()).update(keys)
            elif res is not None:
                for uid in res:
                    changed.setdefault(uid, set()).add(TOPO_MARK)
            return
        # TOPO, or JOIN without hook
        snap = step.after if forward else step.before
        if snap is not None:
            st.set_snapshot(snap)
        changed.setdefault(target, set()).add(TOPO_MARK)
