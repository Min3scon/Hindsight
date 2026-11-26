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


def test_engine_api_12():
    # API 12 (#66): step_encoding, ENCODINGS, UnknownEncoding; decoding a later encoding raises UnknownEncoding
    assert native.API == 12 and N.API == 12, (native.API, N.API)


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


for fn in (test_engine_api_12, test_step_part_round_trip_bit_exact, test_step_part_bytes, test_empty_step_part,
           test_damaged_step_part, test_wrong_arguments_refused, test_index_part_round_trip_bit_exact):
    R.run(fn)


# ---------------------------------------------------------------- #64 64b: a registry saved as format 5 and read back
from hindsight.core import persist as P
from hindsight.core.history import History
from hindsight.core.registry import Registry


def _registry():
    """Two histories holding every kind of step: the five saved steps (bytes, CRC, meta) and a raw stroke that a
    save still has to encode; settings, cursor, morph target and save id set."""
    reg = Registry()
    steps = _steps()
    a = History("0123456789abcdef", ["objA"])
    a.entries = [Entry(blob=b, meta=m, crc=c) for b, c, m in steps]
    rng = np.random.default_rng(7)
    old = rng.random(300, dtype=np.float32)
    raw = Step(seq=9, label="Stroke 9", mode="SCULPT", target="objA", time=9.0, kind=DELTA,
               deltas=[D.diff(POSITION_KEY, old, old + 1, (True, True))])
    a.entries.append(Entry(step=raw))
    a.cursor, a.morph_target = 4, 2
    b = History("fedcba9876543210", ["objB", "objC"])
    b.entries = [Entry(blob=bb, meta=m, crc=c) for bb, c, m in steps[:2]]
    b.cursor, b.enabled = 2, False
    reg.histories = {a.uid: a, b.uid: b}
    reg.by_object = {"objA": a.uid, "objB": b.uid, "objC": b.uid}
    reg.next_seq, reg.use_global, reg.preserve_modes = 12, True, False
    reg.disabled, reg.everything, reg.saved = {"objD"}, {"matE"}, {"objA": {"mesh": "f1"}}
    reg.save_id = "a1b2c3d4e5f6"
    return reg


def _taker(parts):
    """What the add-on's open does with Blender's copy: hands each step part over once, then it is gone."""
    asked = []

    def take(uid, i):
        asked.append((uid, i))
        return parts.get(uid, {}).pop(str(i), None)
    return take, asked


def _group(parts):
    return {uid: {str(i): p for i, p in enumerate(ps)} for uid, ps in parts.items()}


def test_registry_format5_round_trip_bit_exact():
    reg = _registry()
    want = {h.uid: [e.blob_crc()[0] or e.to_bytes("auto", 0) for e in h.entries] for h in reg.histories.values()}
    index, parts = P.registry_to_format5(reg)
    doc = json.loads(N.index_read(index))
    assert doc["format"] == 5 and doc["save_id"] == "a1b2c3d4e5f6", doc        # it says it is format 5
    group = _group(parts)
    take, asked = _taker(group)
    back = P.registry_from_format5(index, index, take)
    assert all(not g for g in group.values()), "a step part was left in Blender's copy"
    assert len(asked) == sum(len(ps) for ps in parts.values())
    assert back.load_report["format"] == 5 and back.load_report["damaged"] == []
    assert back.save_id == reg.save_id and back.next_seq == 12 and back.use_global and not back.preserve_modes
    assert back.by_object == reg.by_object and back.disabled == {"objD"} and back.everything == {"matE"}
    assert back.saved == reg.saved
    for uid, h in reg.histories.items():
        g = back.histories[uid]
        assert g.members == h.members and g.cursor == h.cursor and g.enabled == h.enabled
        assert g.morph_target == h.morph_target
        assert [e.blob for e in g.entries] == want[uid], uid                   # every step bit-exact
        assert [e.blob_crc()[1] for e in g.entries] == [crc32(b) & 0xFFFFFFFF for b in want[uid]]
        assert [e.meta for e in g.entries] == [P._meta_for_json(e.meta) for e in h.entries]
    raw = back.histories["0123456789abcdef"].entries[-1].load()
    assert raw.label == "Stroke 9" and raw.kind == DELTA


def test_registry_format5_step_parts_split():
    """Steps fill a step part up to the limit; a step bigger than it gets a step part of its own."""
    reg = _registry()
    h = reg.histories["0123456789abcdef"]
    sizes = [len(e.blob_crc()[0] or e.to_bytes("auto", 0)) for e in h.entries]
    old = P.PART_LIMIT
    P.PART_LIMIT = max(sizes) - 1
    try:
        index, parts = P.registry_to_format5(reg)
    finally:
        P.PART_LIMIT = old
    ps = parts[h.uid]
    assert len(ps) > 1, sizes
    listed = [hd for hd in json.loads(N.index_read(index))["histories"] if hd["uid"] == h.uid][0]["parts"]
    assert [p["n"] for p in listed] and sum(p["n"] for p in listed) == len(h.entries)
    assert [p["len"] for p in listed] == [len(p) for p in ps]
    assert [p["crc"] for p in listed] == [crc32(p) & 0xFFFFFFFF for p in ps]
    take, _ = _taker(_group(parts))
    back = P.registry_from_format5(index, None, take)
    assert [e.blob for e in back.histories[h.uid].entries] == [e.blob_crc()[0] or e.to_bytes("auto", 0)
                                                               for e in h.entries]


def test_registry_format5_index_copy():
    """`index` damaged: `index_copy` is read. Both damaged: nothing is guessed."""
    index, parts = P.registry_to_format5(_registry())
    bad = bytearray(index)
    bad[20] ^= 1
    take, _ = _taker(_group(parts))
    assert P.registry_from_format5(bytes(bad), index, take).save_id == "a1b2c3d4e5f6"
    take, asked = _taker(_group(parts))
    try:
        P.registry_from_format5(bytes(bad), None, take)
    except ValueError:
        pass
    else:
        raise AssertionError("a damaged index part was read")
    assert not asked


def test_registry_format5_newer_refused():
    doc = json.dumps({"format": 6, "histories": []}).encode()
    try:
        P.registry_from_format5(N.index_build(doc), None, lambda uid, i: None)
    except P.NewerFormatError as e:
        assert e.found == 6
    else:
        raise AssertionError("format 6 was read")


def test_registry_format5_damaged_step_part():
    """A step part whose CRC-32 doesn't match the index part: its steps are never applied; the history keeps the
    steps reachable from its saved cursor, as formats 3 and 4 do (#69 adds the finer checks)."""
    reg = _registry()
    old = P.PART_LIMIT
    P.PART_LIMIT = 1
    try:
        index, parts = P.registry_to_format5(reg)          # one step per step part
    finally:
        P.PART_LIMIT = old
    uid = "0123456789abcdef"
    p = bytearray(parts[uid][5])
    p[-1] ^= 1
    parts[uid][5] = bytes(p)
    take, _ = _taker(_group(parts))
    back = P.registry_from_format5(index, None, take)
    h = back.histories[uid]
    assert len(h.entries) == 5 and h.cursor == 4
    assert back.load_report["damaged"] == [{"uid": uid, "members": ["objA"], "kept": 5, "lost": 1,
                                            "before_saved": False, "lost_before": 0}]
    group = _group(parts)
    del group[uid]["0"]                                     # a missing step part counts as damaged too
    take, _ = _taker(group)
    h = P.registry_from_format5(index, None, take).histories[uid]
    assert (len(h.entries), h.cursor, h.morph_target) == (4, 3, 1)


for fn in (test_registry_format5_round_trip_bit_exact, test_registry_format5_step_parts_split,
           test_registry_format5_index_copy, test_registry_format5_newer_refused,
           test_registry_format5_damaged_step_part):
    R.run(fn)
R.finish()
