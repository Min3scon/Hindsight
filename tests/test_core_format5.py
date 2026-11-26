import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R
"""Format 5's parts in the engine (#64, first half): the index part and the step parts, built and read back
bit-exact. Layout: dev/FORMAT5_LAYOUT.md and dev/FORMAT5_DAMAGE.md; the bytes are in native/src/format5.rs.

Plain Python plus numpy (no bpy needed); also runs inside Blender with `blender -b --python`.
"""
import json
import struct

import numpy as np

from hindsight.core import delta as D
from hindsight.core import native
from hindsight.core.crc import crc32
from hindsight.core.types import (DATA, DELTA, TOPO, CORNER_EDGE_KEY, CORNER_VERT_KEY, EDGE_VERTS_KEY,
                                  FACE_OFFSETS_KEY, POSITION_KEY, DataChange, Entry, Snapshot, Step)

N = native.ENGINE
U32 = struct.Struct("<I")
U64 = struct.Struct("<Q")


def _snapshot(rng, nv, nf):
    ne, nl = nf * 2, nf * 4
    return Snapshot({"POINT": nv, "EDGE": ne, "FACE": nf, "CORNER": nl}, {
        POSITION_KEY: rng.normal(size=nv * 3).astype(np.float32),
        EDGE_VERTS_KEY: rng.integers(0, nv, size=ne * 2).astype(np.int32),
        CORNER_VERT_KEY: rng.integers(0, nv, size=nl).astype(np.int32),
        CORNER_EDGE_KEY: rng.integers(0, ne, size=nl).astype(np.int32),
        FACE_OFFSETS_KEY: (np.arange(nf + 1) * 4).astype(np.int32),
    })


def _steps():
    """One step of each kind, as a save holds them: (bytes, CRC-32, meta)."""
    rng = np.random.default_rng(64)
    old = rng.random(900, dtype=np.float32)
    new = old.copy()
    new[rng.choice(900, 60, replace=False)] += 0.5
    stroke = lambda seq: Step(seq=seq, label=f"Stroke {seq}", mode="SCULPT", target="objA", time=float(seq),
                              kind=DELTA, deltas=[D.diff(POSITION_KEY, old, new, (True, True))])
    steps = [
        Entry(step=stroke(1)).to_bytes("auto", 0),                       # a raw stroke, quick encoding
        Entry(step=stroke(2)).to_bytes("auto", 1),                       # a packed stroke
        Entry(step=Step(seq=3, label="Extrude", mode="EDIT", target="objA", time=3.0, kind=TOPO, deltas=[],
                        before=_snapshot(rng, 30, 12), after=_snapshot(rng, 42, 20), extra={})).to_bytes("auto", 1),
        Entry(step=Step(seq=4, label="Move", mode="OBJECT", target="objA", time=4.0, kind=DATA, deltas=[],
                        records=[DataChange("xform", "objA", {"location": ([0.0, 0.0, 0.0], [1.0, 2.5, -3.0])})])
              ).to_bytes("auto", 1),
        b"HSS1" + bytes(range(256)) * 3,                                 # encoding 1 (V1's formats 1 and 2), as bytes
    ]
    return [(b, crc32(b) & 0xFFFFFFFF, {"seq": i + 1, "label": f"step {i + 1}"}) for i, b in enumerate(steps)]


def _metas(metas):
    return json.dumps(metas, separators=(",", ":")).encode()


def test_engine_api_11():
    assert native.API == 11 and N.API == 11, (native.API, N.API)


def test_step_part_round_trip_bit_exact():
    steps = _steps()
    blobs, crcs, metas = [s[0] for s in steps], [s[1] for s in steps], _metas([s[2] for s in steps])
    part, begin, pcrc = N.part_build(7, blobs, crcs, metas)
    assert isinstance(part, bytes) and pcrc == crc32(part) & 0xFFFFFFFF
    assert begin + sum(map(len, blobs)) == len(part)
    first, offs, lens, crcs2, metas2 = N.part_read(part)
    assert (first, crcs2, metas2) == (7, crcs, metas)
    assert offs[0] == begin and lens == [len(b) for b in blobs]
    for b, o, n in zip(blobs, offs, lens):
        assert part[o:o + n] == b
    for b, o, n in zip(blobs[:4], offs, lens):              # every kind of step decodes from its part
        e = Entry(blob=part[o:o + n])
        assert e.load().meta() == Entry(blob=b).load().meta()
    # a step part rebuilt from the steps it holds (memoryviews into it, as a save copies them) is the same bytes
    mv = memoryview(part)
    again = N.part_build(7, [mv[o:o + n] for o, n in zip(offs, lens)], crcs2, metas2)
    assert again == (part, begin, pcrc)


def test_step_part_bytes():
    part, begin, _ = N.part_build(3, [b"abc", b"de"], [11, 22], b'[{},{}]')
    table = b"HSP5" + U64.pack(3) + U32.pack(2) + U32.pack(7) + U64.pack(3) + U32.pack(11) + U64.pack(2) + U32.pack(22)
    want = table + U32.pack(crc32(table)) + b'[{},{}]' + U32.pack(crc32(b'[{},{}]')) + b"abcde"
    assert part == want, part
    assert begin == len(want) - 5


def test_empty_step_part():
    part, begin, pcrc = N.part_build(0, [], [], b"[]")
    assert begin == len(part) and N.part_read(part) == (0, [], [], [], b"[]")


def test_damaged_step_part():
    steps = _steps()
    blobs, crcs, metas = [s[0] for s in steps], [s[1] for s in steps], _metas([s[2] for s in steps])
    part, begin, _ = N.part_build(0, blobs, crcs, metas)
    table_end = 24 + 12 * len(blobs)

    def flip(i):
        b = bytearray(part)
        b[i] ^= 0x40
        return bytes(b)
    for bad in (flip(0), flip(10), flip(table_end - 1), part[:table_end - 3], b"", b"HSI5" + part[4:],
                part[:-1], part + b"x"):
        try:
            N.part_read(bad)
        except N.CorruptBlob:
            continue
        raise AssertionError(f"damaged step table read: {len(bad)} bytes")
    # damaged metas: the steps are still where the step table says, the metas are lost (None)
    first, offs, lens, crcs2, metas2 = N.part_read(flip(table_end + 2))
    assert metas2 is None and offs[0] == begin and crcs2 == crcs
    # a damaged step is not the step part's business: the step's CRC-32 in the table tells it
    first, offs, lens, crcs2, metas2 = N.part_read(flip(begin + 5))
    assert metas2 == metas and crcs2 == crcs


def test_wrong_arguments_refused():
    for args in ((0, [b"a"], [], b"[]"), (0, [b"a", b"b"], [1], b"[]")):
        try:
            N.part_build(*args)
        except ValueError:
            continue
        raise AssertionError(args)


def test_index_part_round_trip_bit_exact():
    doc = json.dumps({"format": 5, "save_id": "abc", "histories": [{"uid": "0123456789abcdef", "cursor": 4,
                      "parts": [[5, 1234, 99, 120]]}]}, separators=(",", ":")).encode()
    index = N.index_build(doc)
    assert index == b"HSI5" + U32.pack(len(doc)) + doc + U32.pack(crc32(b"HSI5" + U32.pack(len(doc)) + doc))
    assert N.index_build(doc) == index                     # `index` and `index_copy` are the same bytes
    assert N.index_read(index) == doc
    for i in range(0, len(index), 7):                      # any byte changed: not read
        b = bytearray(index)
        b[i] ^= 1
        assert N.index_read(bytes(b)) is None, i
    for bad in (index[:-1], index + b"\0", b"", b"HSP5" + index[4:]):
        assert N.index_read(bad) is None


for fn in (test_engine_api_11, test_step_part_round_trip_bit_exact, test_step_part_bytes, test_empty_step_part,
           test_damaged_step_part, test_wrong_arguments_refused, test_index_part_round_trip_bit_exact):
    R.run(fn)
R.finish()
