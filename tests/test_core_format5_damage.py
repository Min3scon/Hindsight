import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R
"""#69 (11.6): damage to one part of a format 5 history loses as little as dev/FORMAT5_DAMAGE.md says it must.
A step part that fails its CRC-32 is checked finer: its metas, its step table and each step's CRC-32, and its
steps are found by their prefix when the step table is damaged. Every step kept reopens bit-exact, the load
report says how many steps of which history were lost, and the next save leaves a history that reopens with
nothing more lost, rebuilding only the step parts the design says.

Plain Python plus numpy (no bpy needed); also runs inside Blender with `blender -b --python`.
"""
import json

import numpy as np

from hindsight.core import delta as D
from hindsight.core import native
from hindsight.core import persist as P
from hindsight.core.crc import crc32
from hindsight.core.history import History
from hindsight.core.registry import Registry
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step

N = native.ENGINE
A, B = "0123456789abcdef", "fedcba9876543210"


def _blobs(seqs):
    """Saved strokes of one size each (the same change, labels of one length): (bytes, CRC-32, meta)."""
    rng = np.random.default_rng(69)
    old = rng.random(900, dtype=np.float32)
    new = old.copy()
    new[rng.choice(900, 60, replace=False)] += 0.5
    out = []
    for seq in seqs:
        b = Entry(step=Step(seq=seq, label=f"Stroke {seq:02d}", mode="SCULPT", target="objA", time=float(seq),
                            kind=DELTA, deltas=[D.diff(POSITION_KEY, old, new, (True, True))])).to_bytes("auto", 0)
        out.append((b, crc32(b) & 0xFFFFFFFF, {"seq": seq, "label": f"Stroke {seq:02d}"}))
    return out


def _saved():
    """History A: 9 steps in 3 step parts of 3 (cursor 6, morph target 7); history B: 4 steps (cursor 3).
    (index part, {uid: [step parts]}, {uid: [step bytes]})."""
    reg = Registry()
    sa, sb = _blobs(range(1, 10)), _blobs(range(11, 15))
    assert len({len(b) for b, _c, _m in sa + sb}) == 1
    a, b = History(A, ["objA"]), History(B, ["objB"])
    a.entries = [Entry(blob=x, meta=m, crc=c) for x, c, m in sa]
    b.entries = [Entry(blob=x, meta=m, crc=c) for x, c, m in sb]
    a.cursor, a.morph_target, b.cursor = 6, 7, 3
    reg.histories, reg.by_object, reg.next_seq = {A: a, B: b}, {"objA": A, "objB": B}, 20
    old = P.PART_LIMIT
    P.PART_LIMIT = 3 * len(sa[0][0])
    try:
        index, parts = P.registry_to_format5(reg)
    finally:
        P.PART_LIMIT = old
    assert [len(N.part_read(p)[1]) for p in parts[A]] == [3, 3, 3]
    return index, parts, {A: [x for x, _c, _m in sa], B: [x for x, _c, _m in sb]}


def _open(index, parts, copy=None):
    group = {uid: {str(i): p for i, p in enumerate(ps) if p is not None} for uid, ps in parts.items()}
    return P.registry_from_format5(index, index if copy is None else copy, lambda uid, i: group[uid].pop(str(i), None))


def _flip(b, at):
    b = bytearray(b)
    b[at] ^= 0x40
    return bytes(b)


def _table_end(n):
    return 24 + 12 * n          # the step table and its CRC-32 (native/src/format5.rs)


def _step_at(part, k):
    _first, offs, lens, _crcs, _metas = N.part_read(part)
    return offs[k], lens[k]


def _check(reg, want, kept, cursor, morph=None):
    """History A keeps the steps `kept` (indices into its saved steps), bit-exact, with their CRC-32s."""
    h = reg.histories[A]
    assert [e.blob for e in h.entries] == [want[A][k] for k in kept], [e.meta.get("seq") for e in h.entries]
    assert [e.blob_crc()[1] for e in h.entries] == [crc32(want[A][k]) & 0xFFFFFFFF for k in kept]
    assert [e.meta["seq"] for e in h.entries] == [k + 1 for k in kept]
    assert (h.cursor, h.morph_target) == (cursor, morph), (h.cursor, h.morph_target)
    assert [e.blob for e in reg.histories[B].entries] == want[B], "history B lost steps to A's damage"


def _saved_again(reg):
    """The next save of an opened registry, opened again: (registry, step parts built, steps encoded)."""
    old = P.PART_LIMIT
    P.PART_LIMIT = 3 * len(reg.histories[A].entries[0].blob)
    try:
        index, parts = P.registry_to_format5(reg)
    finally:
        P.PART_LIMIT = old
    work = dict(P.SAVE_WORK)
    back = _open(index, parts)
    assert back.load_report["damaged"] == [] and back.load_report["mended"] == [], back.load_report
    for uid, h in reg.histories.items():
        g = back.histories[uid]
        assert [e.blob for e in g.entries] == [e.blob for e in h.entries], uid
        assert (g.cursor, g.morph_target) == (h.cursor, h.morph_target), uid
    return back, work["built"], work["encoded"]


# ---------------------------------------------------------------- the engine: a damaged step part, checked finer
def _part(blobs):
    crcs = [crc32(b) & 0xFFFFFFFF for b in blobs]
    metas = json.dumps([{"seq": i} for i in range(len(blobs))]).encode()
    part, begin, _crc = N.part_build(0, blobs, crcs, metas)
    return part, begin, crcs, metas


def _rows(part, begin, crcs, blobs):
    offs = [begin + sum(len(b) for b in blobs[:k]) for k in range(len(blobs))]
    return [(o, len(b), c) for o, b, c in zip(offs, blobs, crcs)]


def test_salvage_damaged_metas_or_table_finds_every_step():
    blobs = [b for b, _c, _m in _blobs(range(1, 6))]
    part, begin, crcs, metas = _part(blobs)
    rows = _rows(part, begin, crcs, blobs)
    assert N.part_salvage(part, 5, begin) == (metas, rows)                    # nothing damaged
    assert N.part_salvage(_flip(part, _table_end(5) + 2), 5, begin) == (None, rows)    # the metas
    for at in (0, 5, 13, 20, 33, _table_end(5) - 1):                            # the step table: found by prefix
        assert N.part_salvage(_flip(part, at), 5, begin) == (metas, rows), at


def test_salvage_damaged_steps():
    blobs = [b for b, _c, _m in _blobs(range(1, 6))]
    part, begin, crcs, metas = _part(blobs)
    rows = _rows(part, begin, crcs, blobs)
    for bad in ((0,), (2,), (4,), (1, 3)):
        p = part
        for k in bad:
            p = _flip(p, rows[k][0] + 20)
        # the step table says where each step is: only the damaged steps go
        assert N.part_salvage(p, 5, begin) == (metas, [None if k in bad else r for k, r in enumerate(rows)]), bad
        # the step table damaged too: forward from the start to the first gap, backward from the end to the last
        lo, hi = min(bad), max(bad)
        assert N.part_salvage(_flip(p, 21), 5, begin) == (
            metas, [None if lo <= k <= hi else r for k, r in enumerate(rows)]), bad
    for magic in (2, 3):        # a step's prefix damaged: the search can't tell where the step before it ends
        p = _flip(_flip(part, rows[magic][0]), 21)
        assert N.part_salvage(p, 5, begin) == (metas, rows[:magic - 1] + [None, None] + rows[magic + 1:]), magic


def test_salvage_cut_short_or_missing_bytes():
    blobs = [b for b, _c, _m in _blobs(range(1, 6))]
    part, begin, crcs, metas = _part(blobs)
    rows = _rows(part, begin, crcs, blobs)
    cut = rows[3][0] + 7
    assert N.part_salvage(part[:cut], 5, begin) == (metas, rows[:3] + [None, None])
    assert N.part_salvage(_flip(part[:cut], 21), 5, begin) == (metas, rows[:3] + [None, None])
    assert N.part_salvage(part[:begin - 2], 5, begin) == (None, [None] * 5)
    assert N.part_salvage(b"", 5, begin) == (None, [None] * 5)
    assert N.part_salvage(part + b"xyz", 5, begin) == (metas, rows)     # bytes after the last step: not a step's


def test_salvage_encoding_1_needs_its_step_table():
    """Encoding 1 (formats 1 and 2) has no CRC-32 of its own, so its steps can't be found by prefix."""
    blobs = [b"HSS1" + bytes([k]) * 300 for k in range(4)]
    part, begin, crcs, metas = _part(blobs)
    rows = _rows(part, begin, crcs, blobs)
    assert N.part_salvage(_flip(part, rows[1][0] + 9), 4, begin) == (metas, [rows[0], None, rows[2], rows[3]])
    assert N.part_salvage(_flip(part, 21), 4, begin) == (metas, [None] * 4)


def test_salvage_wrong_arguments():
    blobs = [b for b, _c, _m in _blobs(range(1, 3))]
    part, begin, crcs, metas = _part(blobs)
    rows = _rows(part, begin, crcs, blobs)
    assert N.part_salvage(part, 2, begin + 1) == (metas, rows)     # an intact step table says where they are
    assert N.part_salvage(_flip(part, 21), 2, 10 ** 12) == (None, [None, None])
    assert N.part_salvage(_flip(part, 21), 2, 0)[0] is None


for fn in (test_salvage_damaged_metas_or_table_finds_every_step, test_salvage_damaged_steps,
           test_salvage_cut_short_or_missing_bytes, test_salvage_encoding_1_needs_its_step_table,
           test_salvage_wrong_arguments):
    R.run(fn)


# ---------------------------------------------------------------- the registry: what is lost and what is kept
def test_damaged_metas_lose_nothing():
    index, parts, want = _saved()
    parts[A][1] = _flip(parts[A][1], _table_end(3) + 2)
    reg = _open(index, parts)
    _check(reg, want, range(9), 6, 7)
    assert reg.load_report["damaged"] == [] and reg.load_report["mended"] == [{"uid": A, "part": 1}]
    assert [e.meta["label"] for e in reg.histories[A].entries[3:6]] == ["Stroke 04", "Stroke 05", "Stroke 06"]
    _back, built, encoded = _saved_again(reg)
    assert (built, encoded) == (1, 0), (built, encoded)         # that step part, once


def test_damaged_step_table_loses_nothing():
    index, parts, want = _saved()
    parts[A][0] = _flip(parts[A][0], 33)
    reg = _open(index, parts)
    _check(reg, want, range(9), 6, 7)
    assert reg.load_report["damaged"] == [] and reg.load_report["mended"] == [{"uid": A, "part": 0}]
    _back, built, encoded = _saved_again(reg)
    assert (built, encoded) == (1, 0), (built, encoded)


def test_one_index_copy_damaged_loses_nothing():
    index, parts, want = _saved()
    reg = _open(_flip(index, 30), parts, copy=index)
    _check(reg, want, range(9), 6, 7)
    assert reg.load_report["damaged"] == [] and reg.load_report["mended"] == [{"index": 0}]


def test_damaged_step_after_the_cursor():
    """Step 8 (after the cursor) damaged: it and step 9 go, steps 1 to 7 are kept; the next save rebuilds only the
    step part that holds the last reachable step."""
    index, parts, want = _saved()
    o, _n = _step_at(parts[A][2], 1)
    parts[A][2] = _flip(parts[A][2], o + 20)
    reg = _open(index, parts)
    _check(reg, want, range(7), 6, 7)
    assert reg.load_report["damaged"] == [{"uid": A, "members": ["objA"], "kept": 7, "lost": 2,
                                           "before_saved": False, "lost_before": 0}], reg.load_report
    assert reg.load_report["mended"] == []
    _back, built, encoded = _saved_again(reg)
    assert (built, encoded) == (1, 0), (built, encoded)


def test_damaged_step_and_step_table_before_the_cursor():
    """Step 5 (before the cursor) and its step table damaged: steps 4 and 6 are found by their prefix; steps 1 to 5
    can't be reached any more. The next save rebuilds only the step part of the first reachable step: the step
    parts after it keep their numbers (#65)."""
    index, parts, want = _saved()
    o, _n = _step_at(parts[A][1], 1)
    parts[A][1] = _flip(_flip(parts[A][1], o + 20), 21)
    reg = _open(index, parts)
    _check(reg, want, range(5, 9), 1, 2)
    assert reg.load_report["damaged"] == [{"uid": A, "members": ["objA"], "kept": 4, "lost": 5,
                                           "before_saved": True, "lost_before": 5}], reg.load_report
    _back, built, encoded = _saved_again(reg)
    assert (built, encoded) == (1, 0), (built, encoded)


def test_step_part_cut_short_keeps_the_steps_before_the_cut():
    index, parts, want = _saved()
    o, _n = _step_at(parts[A][2], 1)
    parts[A][2] = parts[A][2][:o + 50]
    reg = _open(index, parts)
    _check(reg, want, range(7), 6, 7)
    assert [d["lost"] for d in reg.load_report["damaged"]] == [2]
    _saved_again(reg)


def test_step_part_missing_loses_its_steps():
    index, parts, want = _saved()
    parts[A][2] = None
    reg = _open(index, parts)
    _check(reg, want, range(6), 6, None)
    _saved_again(reg)


def test_damage_in_two_histories_each_loses_its_own():
    index, parts, want = _saved()
    nb = len(want[B]) - len(N.part_read(parts[B][-1])[1])
    o, _n = _step_at(parts[A][2], 2)
    parts[A][2] = _flip(parts[A][2], o + 20)              # A: step 9 (after its cursor)
    o, _n = _step_at(parts[B][-1], 0)
    parts[B][-1] = _flip(_flip(parts[B][-1], o + 20), 21)  # B: its last step part's first step, and its step table
    reg = _open(index, parts)
    h, g = reg.histories[A], reg.histories[B]
    assert [e.blob for e in h.entries] == want[A][:8] and h.cursor == 6
    assert [e.blob for e in g.entries] == want[B][:nb] and g.cursor == nb
    assert sorted((d["uid"], d["lost"]) for d in reg.load_report["damaged"]) == [(A, 1), (B, len(want[B]) - nb)]
    _saved_again(reg)


for fn in (test_damaged_metas_lose_nothing, test_damaged_step_table_loses_nothing,
           test_one_index_copy_damaged_loses_nothing, test_damaged_step_after_the_cursor,
           test_damaged_step_and_step_table_before_the_cursor, test_step_part_cut_short_keeps_the_steps_before_the_cut,
           test_step_part_missing_loses_its_steps, test_damage_in_two_histories_each_loses_its_own):
    R.run(fn)
R.finish()
