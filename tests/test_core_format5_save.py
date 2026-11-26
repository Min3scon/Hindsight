import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R
"""#65 (11.2): a save after N new steps does work for N steps only (dev/FORMAT5_LAYOUT.md, "What a save after N new
steps does"), counted, not timed: the steps it encodes and the step parts it builds. Every other step part is the
bytes Hindsight read at open or built at an earlier save. Every step round-trips bit-exact after each save.

Plain Python plus numpy (no bpy needed); also runs inside Blender with `blender -b --python`.
"""
import numpy as np

from hindsight.core import delta as D
from hindsight.core import native
from hindsight.core import persist as P
from hindsight.core.history import History
from hindsight.core.registry import Registry
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step

N = native.ENGINE
RNG = np.random.default_rng(65)
OLD = RNG.random(300, dtype=np.float32)


def _raw(seq):
    new = OLD.copy()
    new[seq % 300] += 1.0 + seq
    return Entry(step=Step(seq=seq, label=f"Stroke {seq}", mode="SCULPT", target="objA", time=float(seq),
                           kind=DELTA, deltas=[D.diff(POSITION_KEY, OLD, new, (True, True))]))


def _packed(seq):
    e = _raw(seq)
    e.pack()
    return e


def _bytes(e):
    """The bytes a save writes for a step: a packed step's own, a raw step's quick encoding (deterministic)."""
    return e.blob if e.step is None else Entry(step=e.step).to_bytes("auto", 0)


def _registry(m=12):
    """History A: m steps, packed and raw; history B: 2 packed steps. A step part holds about 3 steps."""
    reg = Registry()
    a = History("0123456789abcdef", ["objA"])
    a.entries = [_packed(s) if s % 3 else _raw(s) for s in range(1, m + 1)]
    a.cursor = len(a.entries)
    b = History("fedcba9876543210", ["objB"])
    b.entries = [_packed(100), _packed(101)]
    b.cursor = 2
    reg.histories = {a.uid: a, b.uid: b}
    reg.by_object = {"objA": a.uid, "objB": b.uid}
    reg.save_id = "65"
    return reg


def _limit():
    return 3 * max(len(_bytes(_raw(s))) for s in range(1, 4)) + 8


def _save(reg):
    index, parts = P.registry_to_format5(reg)
    return index, parts, dict(P.SAVE_WORK)


def _reopen(index, parts):
    group = {uid: {str(i): p for i, p in enumerate(ps)} for uid, ps in parts.items()}
    return P.registry_from_format5(index, None, lambda uid, i: group.get(uid, {}).pop(str(i), None))


def _check_bit_exact(reg, index, parts):
    back = _reopen(index, parts)
    for uid, h in reg.histories.items():
        got = back.histories[uid].entries
        assert [e.blob for e in got] == [_bytes(e) for e in h.entries], f"{uid}: a step isn't bit-exact"
        assert [e.meta["seq"] for e in got] == [e.meta["seq"] for e in h.entries]
    return back


def _same(old, new):
    """How many of `new`'s step parts are the very bytes objects of `old` (reused, not built)."""
    return sum(any(p is q for q in old) for p in new)


def _with_limit(fn):
    def run():
        old = P.PART_LIMIT
        P.PART_LIMIT = _limit()
        try:
            fn()
        finally:
            P.PART_LIMIT = old
    run.__name__ = fn.__name__
    return run


A, B = "0123456789abcdef", "fedcba9876543210"


@_with_limit
def test_save_after_n_new_steps_does_work_for_n_only():
    """M steps saved; then saves after 0, 1 and many new steps: each encodes only the new steps and builds only the
    open step part of the history that changed and the step parts it filled. History B, unchanged, builds none."""
    reg = _registry()
    raw = sum(e.step is not None for h in reg.histories.values() for e in h.entries)
    index, parts, work = _save(reg)
    assert len(parts[A]) >= 4, len(parts[A])
    assert work == {"encoded": raw, "built": len(parts[A]) + len(parts[B])}, work
    _check_bit_exact(reg, index, parts)
    for n in (0, 1, 7, 0):
        a = reg.histories[A]
        for k in range(n):
            a.entries.append(_raw(200 + len(a.entries) + k))
        a.cursor = len(a.entries)
        before = {uid: list(ps) for uid, ps in parts.items()}
        index, parts, work = _save(reg)
        assert work["encoded"] == n, (n, work)                     # no step saved before is encoded again
        assert parts[B][0] is before[B][0] and work["built"] == len(parts[A]) - _same(before[A], parts[A])
        assert _same(before[A], parts[A]) >= len(before[A]) - 1, n     # every full step part is reused
        if n == 0:
            assert work["built"] == 0, work
        else:
            assert 1 <= work["built"] <= 1 + n // 3 + 1, (n, work)  # the open step part and those it filled
        _check_bit_exact(reg, index, parts)


@_with_limit
def test_reopened_history_reuses_its_step_parts():
    """After opening, the step parts read are the ones the next save reuses: one new step builds one step part."""
    reg = _registry()
    index, parts, _ = _save(reg)
    read = {uid: list(ps) for uid, ps in parts.items()}
    back = _reopen(index, parts)
    back.save_id = "65b"
    a = back.histories[A]
    a.entries.append(_raw(300))
    a.cursor = len(a.entries)
    index, parts, work = _save(back)
    assert work == {"encoded": 1, "built": 1}, work
    assert parts[B][0] is read[B][0] and _same(read[A], parts[A]) >= len(read[A]) - 1
    _check_bit_exact(back, index, parts)


@_with_limit
def test_oldest_steps_dropped_rebuild_one_step_part():
    """The step cap or Delete earlier steps drops the oldest steps: only the step part they were in is built; the
    step parts after it keep their bytes, since a step part numbers its first step from the history's first step
    ever recorded, not from the first step it holds now."""
    reg = _registry()
    index, parts, _ = _save(reg)
    old = list(parts[A])
    a = reg.histories[A]
    a.entries = a.entries[2:]
    a.cursor -= 2
    index, parts, work = _save(reg)
    assert work == {"encoded": 0, "built": 1}, work
    assert parts[A][1:] == old[1:] and all(p is q for p, q in zip(parts[A][1:], old[1:]))
    firsts = [N.part_read(p)[0] for p in parts[A]]
    assert firsts[0] == 2 and firsts == sorted(firsts), firsts
    _check_bit_exact(reg, index, parts)
    a.entries = a.entries[N.part_read(old[2])[0] - 2:]          # every step of the first two step parts goes
    a.cursor = len(a.entries)
    index, parts, work = _save(reg)
    assert work == {"encoded": 0, "built": 0}, work
    assert all(p is q for p, q in zip(parts[A], old[2:]))
    _check_bit_exact(reg, index, parts)


@_with_limit
def test_going_back_and_recording_rebuilds_from_the_cursor():
    """Going back and recording cuts the newest steps: the step part where the cursor stood is built again with
    the new step, the step parts after it go, the ones before it are reused."""
    reg = _registry()
    index, parts, _ = _save(reg)
    old = list(parts[A])
    a = reg.histories[A]
    k = N.part_read(old[1])[0] + 1                  # inside the second step part
    a.entries = a.entries[:k] + [_raw(400)]
    a.cursor = len(a.entries)
    index, parts, work = _save(reg)
    assert work == {"encoded": 1, "built": 1}, work
    assert parts[A][0] is old[0] and len(parts[A]) == 2, len(parts[A])
    _check_bit_exact(reg, index, parts)


@_with_limit
def test_packer_packing_a_step_rebuilds_its_step_part():
    """A raw step the packer packs after a save: its step part is built again with the smaller bytes."""
    reg = _registry()
    index, parts, _ = _save(reg)
    old = list(parts[A])
    a = reg.histories[A]
    e = next(e for e in a.entries[3:] if e.step is not None)
    i = a.entries.index(e)
    e.pack()
    index, parts, work = _save(reg)
    assert work == {"encoded": 0, "built": 1}, work
    assert _same(old, parts[A]) == len(old) - 1
    back = _check_bit_exact(reg, index, parts)
    assert back.histories[A].entries[i].blob == e.blob


@_with_limit
def test_a_history_that_is_gone_loses_its_step_parts():
    reg = _registry()
    _save(reg)
    del reg.histories[B]
    index, parts, work = _save(reg)
    assert list(parts) == [A] and work == {"encoded": 0, "built": 0}, (list(parts), work)
    assert list(reg.parts) == [A]


for fn in (test_save_after_n_new_steps_does_work_for_n_only, test_reopened_history_reuses_its_step_parts,
           test_oldest_steps_dropped_rebuild_one_step_part, test_going_back_and_recording_rebuilds_from_the_cursor,
           test_packer_packing_a_step_rebuilds_its_step_part, test_a_history_that_is_gone_loses_its_step_parts):
    R.run(fn)
R.finish()
