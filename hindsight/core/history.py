"""Restore engine: per-object mesh baselines and the undo timeline.

Pure Python + numpy. No bpy imports.

A ``History`` is a list of ``Entry`` slots plus a cursor; the mesh shows the
state after ``entries[:cursor]``. ``goto`` walks the crossed entries and folds
them into ``MeshState`` objects (one per object uid). DELTA steps are bit XORs,
so the same record works for undo and redo; TOPO/JOIN steps carry full
before/after snapshots.
"""
from __future__ import annotations

import numpy as np

from .types import (
    DELTA, JOIN,
    ChannelDelta, Entry, Snapshot, Step,
    bits_dtype, key_dtype, key_width, split_key,
)

TOPO_MARK = "__TOPO__"


class MeshState:
    """Current channel values of ONE object (the baseline shown at the cursor)."""

    __slots__ = ("counts", "arrays", "removed")

    def __init__(self, counts: dict, arrays: dict):
        self.counts = dict(counts)
        self.arrays = arrays          # key -> flat native array (owned, not copied)
        self.removed = set()          # keys whose attribute must be deleted from the mesh

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
            width = key_width(key)
            bits = arr.view(bits_dtype(arr.dtype)).reshape(-1, width)
            xor = d.xor
            if xor.ndim != 2 or xor.shape[1] != width:
                xor = xor.reshape(-1, width)
            # idx is unique, so fancy-index read/xor/write is exact
            bits[d.idx] ^= xor
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
        if pos < cur:
            for i in range(cur - 1, pos - 1, -1):
                self._cross(self.entries[i], False, states, join_hook, changed)
        elif pos > cur:
            for i in range(cur, pos):
                self._cross(self.entries[i], True, states, join_hook, changed)
        self.cursor = pos
        return changed

    @staticmethod
    def _cross(entry: Entry, forward: bool, states: dict, join_hook, changed: dict) -> None:
        target = entry.target
        st = states.get(target)
        if st is None:
            return  # skipped without unpacking
        step = entry.load()
        kind = step.kind
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
