import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R
"""#125 (11.8): between saves a format 5 history is held once (dev/FORMAT5_LAYOUT.md, "How it fits #9"). A saved
step's bytes are read out of its step part by offset, and a save hands the step parts over to Blender and takes
them back, so while it runs Blender's copy is the only one. Counted, not timed: the bytes objects Hindsight's
registry reaches. Every step round-trips bit-exact.

Plain Python plus numpy (no bpy needed); also runs inside Blender with `blender -b --python`.
"""
import gc
import types
import weakref

import numpy as np

from hindsight.core import delta as D
from hindsight.core import persist as P
from hindsight.core.crc import crc32
from hindsight.core.history import History
from hindsight.core.registry import Registry
from hindsight.core.store import Packer
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step

RNG = np.random.default_rng(125)
OLD = RNG.random(300, dtype=np.float32)
A, B = "0123456789abcdef", "fedcba9876543210"


def _raw(seq):
    new = OLD.copy()
    new[seq % 300] += 1.0 + seq
    return Entry(step=Step(seq=seq, label=f"Stroke {seq}", mode="SCULPT", target="objA", time=float(seq),
                           kind=DELTA, deltas=[D.diff(POSITION_KEY, OLD, new, (True, True))]))


def _packed(seq):
    e = _raw(seq)
    e.pack()
    return e


def _registry(m=12, raw=True):
    """History A: m steps (every third raw); history B: 2 packed steps. A step part holds about 3 steps."""
    reg = Registry()
    a = History(A, ["objA"])
    a.entries = [_raw(s) if raw and s % 3 == 0 else _packed(s) for s in range(1, m + 1)]
    a.cursor = len(a.entries)
    b = History(B, ["objB"])
    b.entries = [_packed(100), _packed(101)]
    b.cursor = 2
    reg.histories = {a.uid: a, b.uid: b}
    reg.by_object = {"objA": a.uid, "objB": b.uid}
    reg.save_id = "125"
    return reg


def _bytes(e):
    return e.blob if e.step is None else Entry(step=e.step).to_bytes("auto", 0)


def _held(reg) -> int:
    """Bytes in all the bytes objects the registry reaches (each counted once): what Hindsight holds."""
    seen, found, todo = set(), {}, [reg.histories, reg.parts]
    skip = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType, weakref.ref)
    while todo:
        o = todo.pop()
        if id(o) in seen or isinstance(o, skip):
            continue
        seen.add(id(o))
        if type(o) is bytes:
            found[id(o)] = len(o)
        else:
            todo += gc.get_referents(o)
    return sum(found.values())


def _parts_size(reg) -> int:
    return sum(len(p.bytes) for ps in reg.parts.values() for p in ps)


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


def _with_limit(fn):
    def run():
        old = P.PART_LIMIT
        P.PART_LIMIT = 3 * max(len(_bytes(_raw(s))) for s in range(1, 4)) + 8
        try:
            fn()
        finally:
            P.PART_LIMIT = old
    run.__name__ = fn.__name__
    return run


def _hand_over(reg, parts):
    group = {}
    P.hand_over(reg, parts, lambda uid, i, b: group.setdefault(uid, {}).__setitem__(str(i), b))
    return group


@_with_limit
def test_open_holds_each_saved_step_once():
    """After an open, the bytes Hindsight holds for a history's saved steps are its step parts alone."""
    reg = _registry(raw=False)
    index, parts, _ = _save(reg)
    back = _reopen(index, parts)
    del parts
    assert len(back.parts[A]) >= 4, back.parts
    assert _held(back) == _parts_size(back), (_held(back), _parts_size(back))
    for uid, h in reg.histories.items():
        assert [e.blob for e in back.histories[uid].entries] == [e.blob for e in h.entries], uid


@_with_limit
def test_save_holds_each_saved_step_once():
    """After a save, the packed steps it wrote give up their own bytes: the step parts alone hold them. Raw steps
    stay raw (the packer packs them). A save after that reuses every step part (#65)."""
    reg = _registry(raw=False)
    a = reg.histories[A]
    want = [e.blob for e in a.entries]
    index, parts, _ = _save(reg)
    del parts
    assert _held(reg) == _parts_size(reg), (_held(reg), _parts_size(reg))
    assert [e.blob for e in a.entries] == want
    a.entries += [_packed(200), _packed(201), _packed(202), _packed(203)]
    a.cursor = len(a.entries)
    assert _held(reg) > _parts_size(reg)                # new steps: their own bytes until they are saved
    index, parts, work = _save(reg)
    assert work["encoded"] == 0 and work["built"] >= 2, work
    _check_bit_exact(reg, index, parts)
    del parts
    assert _held(reg) == _parts_size(reg), (_held(reg), _parts_size(reg))
    index, parts, work = _save(reg)
    assert work == {"encoded": 0, "built": 0}, work


@_with_limit
def test_hand_over_leaves_blender_the_only_copy():
    """During a save, Hindsight lets go of each step part once Blender holds it; after the save it takes each one
    back: every step is bit-exact again and the next save still reuses every step part."""
    reg = _registry()
    index, parts, _ = _save(reg)
    size = _parts_size(reg)
    group = _hand_over(reg, parts)
    assert all(b is None for ps in parts.values() for b in ps), "the save's own list still holds a step part"
    del parts
    assert _held(reg) == 0, _held(reg)
    assert sum(len(b) for g in group.values() for b in g.values()) == size
    lost = P.take_back(reg, lambda uid, i: group.get(uid, {}).pop(str(i), None))
    assert lost == 0 and all(not g for g in group.values()), (lost, group)
    assert _held(reg) == _parts_size(reg) == size
    index, parts, work = _save(reg)
    assert work == {"encoded": 0, "built": 0}, work
    _check_bit_exact(reg, index, parts)


@_with_limit
def test_step_part_that_does_not_come_back_is_cut_as_damage():
    """A step part Blender no longer holds after the save (something deleted it), or holds other bytes: its
    packed steps are gone, so the history is cut as damage cuts it (raw steps keep their own data), and
    take_back says how many steps went. What is left is whole: it saves and reopens bit-exact."""
    for change in ("missing", "other bytes"):
        reg = _registry()
        a = reg.histories[A]
        index, parts, _ = _save(reg)
        group = _hand_over(reg, parts)
        del parts
        if change == "missing":
            del group[A]["1"]
        else:
            group[A]["1"] = bytes(len(group[A]["1"]))
        n = len(a.entries)
        lost = P.take_back(reg, lambda uid, i: group.get(uid, {}).pop(str(i), None))
        assert lost > 0 and len(a.entries) == n - lost, (change, lost, len(a.entries))
        assert a.cursor == len(a.entries), (change, a.cursor)
        assert all(e.step is not None or e.blob is not None for e in a.entries), change
        assert len(reg.histories[B].entries) == 2, change
        index, parts, _ = _save(reg)
        _check_bit_exact(reg, index, parts)


@_with_limit
def test_packer_step_cap_and_cut_give_own_bytes_where_needed():
    """The packer swapping a step's bytes gives that step its own bytes until the next save; the step cap and a
    cut (going back and recording) drop steps; after each save the history is held once again, bit-exact."""
    reg = _registry(raw=False)
    a = reg.histories[A]
    _save(reg)
    e = a.entries[4]
    e.blob = Entry(step=e.load()).to_bytes("zlib", 1)        # the packer's recompression swaps the bytes whole
    assert _held(reg) == _parts_size(reg) + len(e.blob)      # that step needs its own bytes until the next save
    for change in ("packer", "step cap", "cut"):
        if change == "step cap":
            a.entries = a.entries[2:]
            a.cursor = len(a.entries)
        elif change == "cut":
            a.cursor = 5
            a.entries = a.entries[:5] + [_packed(300)]
            a.cursor = len(a.entries)
        index, parts, work = _save(reg)
        assert work["built"] >= 1, (change, work)
        del parts
        assert _held(reg) == _parts_size(reg), (change, _held(reg), _parts_size(reg))
        index, parts, _ = _save(reg)
        _check_bit_exact(reg, index, parts)


@_with_limit
def test_packer_recompresses_a_step_read_out_of_its_step_part():
    """Idle recompression still applies to a step held in its step part, which then has its own bytes."""
    reg = _registry()
    old = RNG.random(3000, dtype=np.float32)
    reg.histories[A].entries[2] = Entry(step=Step(seq=3, label="Stroke 3", mode="SCULPT", target="objA", time=3.0,
                                                  kind=DELTA, deltas=[D.diff(POSITION_KEY, old, old + 1, (True, True))]))
    index, parts, _ = _save(reg)
    back = _reopen(index, parts)
    e = back.histories[A].entries[2]                  # saved raw: in the quick form, which recompression shrinks
    before = e.blob
    pk = Packer(recompress_idle=True)
    try:
        pk.note_activity()                            # the packer's own thread waits; this test runs the turn
        pk.queue_recompress([e])
        pk._recompress_one()
    finally:
        pk.stop()
    assert pk.recompressed == 1 and len(e.blob) < len(before), (pk.recompressed, pk.errors, pk.last_error)
    assert e.blob_crc()[1] == crc32(e.blob) & 0xFFFFFFFF


for fn in (test_open_holds_each_saved_step_once, test_save_holds_each_saved_step_once,
           test_hand_over_leaves_blender_the_only_copy, test_step_part_that_does_not_come_back_is_cut_as_damage,
           test_packer_step_cap_and_cut_give_own_bytes_where_needed,
           test_packer_recompresses_a_step_read_out_of_its_step_part):
    R.run(fn)
R.finish()
