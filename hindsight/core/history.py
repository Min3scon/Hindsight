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
from . import native as _native
from .types import (
    DATA, DELTA, JOIN, MISSING, TOPO,
    ChannelDelta, DataChange, Entry, Snapshot, Step,
    key_dtype, key_width, split_key,
)

TOPO_MARK = "__TOPO__"
DATA_MARK = "@"
SNAPSHOTS_KEY = "_snapshots"   # same key as step.SNAPSHOTS_KEY (not imported: step imports history types)       # changed-key tuples ("@", adapter, path) for data records

class MeshState:
    """Current recorded state of ONE datablock (the baseline shown at the cursor): mesh channels
    for meshes, plus the values data adapters record ((adapter, path) -> value)."""

    __slots__ = ("counts", "_arrays", "removed", "values", "snaps", "_topo")

    def __init__(self, counts: dict, arrays: dict, values: dict | None = None):
        self.counts = dict(counts)
        self._arrays = arrays         # key -> flat native array (owned, not copied)
        self._topo = None             # R069: {topology key: topopack.pack_array(...)} while packed
        self.removed = set()          # keys whose attribute must be deleted from the mesh
        self.values = dict(values or {})   # (adapter, path) -> value; absent = MISSING
        self.snaps = {}               # uid -> Snapshot carried by crossed data steps (objects to recreate)

    # ------------------------------------------------------------------ arrays (R069)
    @property
    def arrays(self) -> dict:
        """Every array of the baseline, the topology unpacked if it was packed (it stays unpacked until
        the next idle packing)."""
        if self._topo is not None:
            self._unpack_topo()
        return self._arrays

    @arrays.setter
    def arrays(self, value: dict) -> None:
        self._arrays = value
        self._topo = None

    @property
    def light(self) -> dict:
        """The arrays without unpacking: every channel; the topology keys only while not packed."""
        return self._arrays

    def is_packed(self) -> bool:
        return self._topo is not None

    def topo_hashes(self) -> dict | None:
        """{topology key: xxh3-128 checksum} while packed, else None."""
        t = self._topo
        return None if t is None else {k: p[3] for k, p in t.items()}

    def all_keys(self) -> set:
        return set(self._arrays) | set(self._topo or ())

    def pack_job(self) -> dict | None:
        """The topology arrays to pack now ({key: array}), or None (packed already, or none)."""
        if self._topo is not None:
            return None
        from .topopack import TOPO_KEYS
        job = {k: self._arrays[k] for k in TOPO_KEYS if k in self._arrays}
        return job or None

    def finish_pack(self, job: dict, packed: dict) -> bool:
        """Put the packed form in place of the arrays packed, unless any of them was replaced
        meanwhile (then the job is dropped). Main thread only."""
        if self._topo is not None or set(job) != set(packed):
            return False
        for k, a in job.items():
            if self._arrays.get(k) is not a:
                return False
        for k in job:
            del self._arrays[k]
        self._topo = packed
        return True

    def _unpack_topo(self) -> None:
        from .topopack import unpack_array
        t, self._topo = self._topo, None
        for k, p in t.items():
            self._arrays[k] = unpack_array(p)

    def nbytes(self) -> int:
        from .topopack import packed_bytes
        return int(sum(a.nbytes for a in self._arrays.values())) + (packed_bytes(self._topo) if self._topo else 0)

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
        arrays = self._arrays            # channels only: a delta never targets the topology
        arr = arrays.get(key)
        if arr is None:
            domain = split_key(key)[1]
            arr = np.zeros(int(self.counts[domain]) * key_width(key), dtype=key_dtype(key))
            arrays[key] = arr
        elif not arr.flags.c_contiguous:
            arr = np.ascontiguousarray(arr)
            arrays[key] = arr
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
            self._arrays.pop(key, None)
            self.removed.add(key)

    # --------------------------------------------------------------- snapshots
    def set_snapshot(self, snap: Snapshot) -> None:
        old_keys = self.all_keys()        # no unpacking only to be replaced
        self.counts = dict(snap.counts)
        self.arrays = {k: np.array(v, copy=True) for k, v in snap.arrays.items()}
        new_keys = set(self._arrays)
        self.removed |= old_keys - new_keys
        self.removed -= new_keys

    def to_snapshot(self) -> Snapshot:
        return Snapshot(dict(self.counts), {k: v.copy() for k, v in self.arrays.items()})


def _share_chain(prev: Entry, step: Step) -> None:
    """A TOPO step's `before` is the mesh the TOPO step before it left (its `after`): where the two
    are bit for bit the same, the new step holds the older step's arrays, not a second copy of them.
    Nothing writes into a step's arrays (the state takes copies, set_snapshot), so sharing is safe;
    only raw neighbours share (a packed one has no arrays)."""
    ps = prev.step
    if (ps is None or ps.kind != TOPO or step.kind != TOPO or ps.after is None or step.before is None
            or ps.target != step.target):
        return
    for key, a in list(step.before.arrays.items()):
        b = ps.after.arrays.get(key)
        if b is None or b is a or b.dtype != a.dtype or b.shape != a.shape:
            continue
        if np.array_equal(_delta.view_bits(a), _delta.view_bits(b)):
            step.before.arrays[key] = b


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
        if isinstance(step, Step) and self.entries:
            _share_chain(self.entries[-1], step)
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
            self._cross_runs(_native.ENGINE.apply_steps, entries, forward, states, join_hook, changed)
        self.cursor = pos
        return changed

    @staticmethod
    def _cross_runs(run, entries: list, forward: bool, states: dict, join_hook, changed: dict) -> None:
        """The loop of goto: consecutive packed DELTA steps of one object are crossed by one engine
        call (apply_steps), which stops before any step it doesn't take (other kinds and formats,
        extras, channels that appear or go, damage); that step is then crossed here, one at a time,
        so results and exceptions are those of tests/reference/history.py. The engine call releases
        the GIL; its helper threads (decompressing ahead) run no Python, so they don't compete with
        Blender's main thread for the interpreter as Python threads did. Steps of an object that is
        not being moved are skipped without being decoded."""
        i, n = 0, len(entries)
        while i < n:
            e = entries[i]
            target = e.target
            if target not in states:
                i += 1                 # object not being moved: skipped without unpacking
                continue
            blobs = []
            j = i
            while j < n:
                x = entries[j]
                if x.step is not None or x.target != target or x.meta.get("kind", DELTA) != DELTA:
                    break
                b = x.blob
                if type(b) is not bytes:
                    break
                blobs.append(b)
                j += 1
            if blobs:
                st = states[target]
                done, keys = run(blobs, st.light, forward)      # deltas: channels only (R069)
                if keys:
                    changed.setdefault(target, set()).update(keys)
                    st.removed.difference_update(keys)
                i += done
                if done < len(blobs):
                    e = entries[i]     # the step it stopped at, crossed below
                else:
                    continue
            step = e.load()
            History._cross(e, step, forward, states, join_hook, changed)
            i += 1

    @staticmethod
    def _cross(entry: Entry, step: Step, forward: bool, states: dict, join_hook, changed: dict) -> None:
        target = entry.target
        st = states[target]
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
