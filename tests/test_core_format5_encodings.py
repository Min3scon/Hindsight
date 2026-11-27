import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R
"""#66 (11.3): every step says which encoding it uses, in its step prefix (dev/FORMAT5_ENCODINGS.md), and the reader
picks its decoder from it. A step in an encoding this build doesn't know (a later build's) is kept as its bytes:
the history opens whole, the other steps play back up to it, it is saved back byte for byte, and the load report
says so, apart from damage.

Plain Python plus numpy (no bpy needed); also runs inside Blender with `blender -b --python`.
"""
import hashlib
import json
import zlib

import numpy as np

from hindsight.core import delta as D
from hindsight.core import native
from hindsight.core import persist as P
from hindsight.core import step as S
from hindsight.core.history import History, MeshState
from hindsight.core.registry import Registry
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step
from tests.f5_unknown import UNKNOWN, as_encoding, steps as _steps, swap_step

N = native.ENGINE
UID = "0123456789abcdef"
RNG = np.random.default_rng(66)
POS = [RNG.random(300, dtype=np.float32)]
for _i in range(6):
    POS.append(POS[-1] + RNG.random(300, dtype=np.float32))


def _history(cursor=6):
    """Six packed strokes, each moving every value from POS[i] to POS[i + 1]."""
    h = History(UID, ["objA"])
    for i in range(6):
        e = Entry(step=Step(seq=i + 1, label=f"Stroke {i + 1}", mode="SCULPT", target="objA", time=float(i),
                            kind=DELTA, deltas=[D.diff(POSITION_KEY, POS[i], POS[i + 1], (True, True))]))
        e.pack()
        h.entries.append(e)
    h.cursor = cursor
    reg = Registry()
    reg.histories = {UID: h}
    reg.by_object = {"objA": UID}
    reg.save_id = "66"
    return reg


def _reopen(index, parts):
    group = {uid: {str(i): p for i, p in enumerate(ps)} for uid, ps in parts.items()}
    return P.registry_from_format5(index, None, lambda uid, i: group.get(uid, {}).pop(str(i), None))


def _state(pos):
    return {"objA": MeshState({"POINT": 100, "EDGE": 0, "FACE": 0, "CORNER": 0}, {POSITION_KEY: POS[pos].copy()})}


def _saved_with_unknown(at, cursor):
    """A history saved, then its step `at` swapped for one in an unknown encoding (as a later build saves it)."""
    reg = _history(cursor)
    index, parts = P.registry_to_format5(reg)
    unknown = as_encoding(reg.histories[UID].entries[at].blob)
    index, parts = swap_step(index, parts, UID, at, unknown)
    return index, parts, unknown


def test_every_saved_step_says_its_encoding():
    """Every step a save writes carries its encoding number at byte 10 of its step prefix (3 here: steps without
    data records), and an encoding 1 step (HSS1) is read as 1."""
    reg = _history()
    _index, parts = P.registry_to_format5(reg)
    blobs = _steps(parts[UID])
    assert len(blobs) == 6
    assert [N.step_encoding(b) for b in blobs] == [3] * 6, [N.step_encoding(b) for b in blobs]
    assert all(b[:4] == b"HSS3" and b[10] == 3 for b in blobs)
    assert set(N.ENCODINGS) == {1, 3, 4}, N.ENCODINGS
    assert N.step_encoding(b"HSS1" + bytes(20)) == 1
    for bad in (b"HSS9" + bytes(20), b"HSS3" + bytes(3), as_encoding(blobs[0], 2)):
        try:
            N.step_encoding(bad)
        except S.CorruptStep:
            continue
        raise AssertionError(f"{bad[:12]!r} is not a step, yet it gave an encoding")


def test_reading_picks_the_decoder_from_the_encoding():
    """The decoder comes from the encoding number: 3 decodes; 4 is the data records' decoder, which this step
    doesn't fit (damage); 255 has no decoder here, so it is an unknown step, apart from damage; byte 10 changed
    without its prefix CRC is damage. The packer never re-packs an unknown step."""
    b = _history().histories[UID].entries[0].blob
    assert S.step_from_bytes(b).seq == 1
    cases = ((as_encoding(b, 4), S.CorruptStep), (as_encoding(b, UNKNOWN), S.UnknownEncoding),
             (as_encoding(b, 200), S.UnknownEncoding), (as_encoding(b, UNKNOWN, fix_crc=False), S.CorruptStep))
    for blob, want in cases:
        try:
            S.step_from_bytes(blob)
        except Exception as e:
            assert type(e) is want, (blob[10], type(e).__name__, e)
            continue
        raise AssertionError(f"encoding {blob[10]} decoded")
    assert not issubclass(S.UnknownEncoding, S.CorruptStep)
    assert S.is_unknown(as_encoding(b)) and not S.is_unknown(b) and not S.is_unknown(b"HSS9" + bytes(20))
    assert N.recompress(as_encoding(b)) is None


def test_unknown_step_opens_plays_back_and_saves_back_byte_for_byte():
    """A history saved with its cursor at the end, one step in an unknown encoding: it opens whole (nothing reported
    damaged, the step listed with its meta), the report says one step is unknown, the cursor plays back the steps
    after it and stops at it, and the next save gives the step back byte for byte without encoding a step."""
    index, parts, unknown = _saved_with_unknown(at=2, cursor=6)
    reg = _reopen(index, parts)
    h = reg.histories[UID]
    rep = reg.load_report
    assert rep["damaged"] == [], rep
    assert rep["unknown"] == [{"uid": UID, "members": ["objA"], "n": 1, "held": False}], rep["unknown"]
    assert len(h.entries) == 6 and h.cursor == 6 and h.entries[2].meta["label"] == "Stroke 3"
    assert h.reach() == (3, 6) and not h.held()
    states = _state(6)
    h.goto(4, states)
    assert h.cursor == 4 and np.array_equal(states["objA"].arrays[POSITION_KEY], POS[4])
    h.goto(0, states)
    assert h.cursor == 3 and np.array_equal(states["objA"].arrays[POSITION_KEY], POS[3]), h.cursor
    h.goto(6, states)
    assert h.cursor == 6 and np.array_equal(states["objA"].arrays[POSITION_KEY], POS[6])
    index2, parts2 = P.registry_to_format5(reg)
    assert P.SAVE_WORK["encoded"] == 0, P.SAVE_WORK
    saved = _steps(parts2[UID])
    assert hashlib.sha256(saved[2]).digest() == hashlib.sha256(unknown).digest()
    again = _reopen(index2, parts2)
    assert [e.blob for e in again.histories[UID].entries] == saved
    assert again.load_report["unknown"][0]["n"] == 1


def test_unknown_step_after_the_cursor_holds_the_history():
    """Saved with the cursor before the unknown step: recording would cut it, so the history is held (reported so);
    the steps before it still play back, and it stops there."""
    index, parts, _unknown = _saved_with_unknown(at=4, cursor=3)
    reg = _reopen(index, parts)
    h = reg.histories[UID]
    assert reg.load_report["unknown"] == [{"uid": UID, "members": ["objA"], "n": 1, "held": True}]
    assert h.held() and h.reach() == (0, 4)
    states = _state(3)
    h.goto(0, states)
    assert h.cursor == 0 and np.array_equal(states["objA"].arrays[POSITION_KEY], POS[0])
    h.goto(6, states)
    assert h.cursor == 4 and np.array_equal(states["objA"].arrays[POSITION_KEY], POS[4]), h.cursor
    plain = _reopen(*P.registry_to_format5(_history(3)))
    assert plain.load_report["unknown"] == [] and not plain.histories[UID].held()
    assert plain.histories[UID].reach() == (0, 6)


def _metas_damaged(index, parts):
    """(index part, parts) with the metas of the history's first step part damaged (the index part lists the
    damaged step part's CRC-32, as a part damaged after it was written in the .blend would not be: its metas'
    own CRC-32 is what fails)."""
    p = bytearray(parts[UID][0])
    n, ml = int.from_bytes(p[12:16], "little"), int.from_bytes(p[16:20], "little")
    at = 24 + 12 * n + 4            # dev/FORMAT5_LAYOUT.md: the metas start after the step table and its CRC
    assert ml > 0
    p[at] ^= 0xFF
    doc = json.loads(bytes(N.index_read(index)).decode("utf-8"))
    doc["histories"][0]["parts"][0].update(len=len(p), crc=zlib.crc32(bytes(p)) & 0xFFFFFFFF)
    return N.index_build(json.dumps(doc).encode("utf-8")), dict(parts, **{UID: [bytes(p)] + list(parts[UID][1:])})


def test_unknown_step_whose_meta_is_lost_is_listed_not_damaged():
    """#11 review 3 (dev/FORMAT5_DAMAGE.md): the metas damaged, every step's meta comes from its own bytes; the
    unknown step, which can't give one, is listed as a step saved by a newer Hindsight, not cut as damage."""
    index, parts, unknown = _saved_with_unknown(at=2, cursor=6)
    reg = _reopen(*_metas_damaged(index, parts))
    h = reg.histories[UID]
    assert reg.load_report["damaged"] == [], reg.load_report
    assert reg.load_report["unknown"] == [{"uid": UID, "members": ["objA"], "n": 1, "held": False}]
    assert len(h.entries) == 6 and h.cursor == 6 and h.entries[2].blob == unknown
    assert [e.meta["label"] for e in h.entries[:2]] == ["Stroke 1", "Stroke 2"]
    e = h.entries[2]
    assert "newer Hindsight" in e.meta["label"] and e.target == "objA" and e.seq == 2, e.meta
    assert h.reach() == (3, 6)


for fn in (test_every_saved_step_says_its_encoding, test_reading_picks_the_decoder_from_the_encoding,
           test_unknown_step_opens_plays_back_and_saves_back_byte_for_byte,
           test_unknown_step_after_the_cursor_holds_the_history,
           test_unknown_step_whose_meta_is_lost_is_listed_not_damaged):
    R.run(fn)
R.finish()
