import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Saved-history format: version 2 checksums, migration from version 1, damage and future formats.

Plain Python plus numpy (no bpy needed); also runs inside Blender with `blender -b --python`.
"""
import json
import struct
import traceback
import zlib

import numpy as np

from hindsight.core import persist
from hindsight.core.history import History
from hindsight.core.registry import Registry
from hindsight.core.types import DELTA, Entry, Step
from hindsight.core import delta as D

ok = True
U32 = struct.Struct("<I")
U64 = struct.Struct("<Q")


def _step(seq, rng):
    old = rng.random(300, dtype=np.float32)
    new = old.copy()
    idx = rng.choice(300, 20, replace=False)
    new[idx] += 0.5
    d = D.diff("position|POINT|FLOAT_VECTOR", old, new, (True, True))
    return Step(seq=seq, label=f"s{seq}", mode="SCULPT", target="objA", time=float(seq), kind=DELTA, deltas=[d])


def _reg(n=5):
    rng = np.random.default_rng(1)
    reg = Registry()
    h = History("H1", ["objA"])
    h.entries = [Entry(_step(i + 1, rng)) for i in range(n)]
    h.cursor = n
    reg.histories = {"H1": h}
    reg.by_object = {"objA": "H1"}
    reg.next_seq = n + 1
    return reg


def _split(blob):
    (jlen,) = U32.unpack_from(blob, 4)
    doc = json.loads(blob[8:8 + jlen])
    return doc, blob[8 + jlen:]


def _join(doc, rest):
    js = json.dumps(doc).encode()
    return persist.REG_MAGIC + U32.pack(len(js)) + js + rest


def _blob_offsets(rest, n):
    out, pos = [], 0
    for _ in range(n):
        (size,) = U64.unpack_from(rest, pos)
        out.append((pos + 8, size))
        pos += 8 + size
    return out


def test_writes_format_3_with_checksums():
    blob = persist.registry_to_bytes(_reg())
    doc, rest = _split(blob)
    assert doc["format"] == persist.FORMAT_VERSION == 3 and doc["version"] == 3
    crcs = doc["histories"][0]["crcs"]
    offs = _blob_offsets(rest, 5)
    assert crcs == [zlib.crc32(rest[o:o + n]) & 0xFFFFFFFF for o, n in offs]
    reg = persist.registry_from_bytes(blob)
    assert reg.load_report == {"format": 3, "writer": persist.WRITER, "migrated": False, "damaged": []}


def test_reads_format_1():
    """Beta 1 files: no 'format', version 1, no checksums. They load as they are. (Real files written
    by beta 1's own code are checked step by step in test_core_migration.py.)"""
    blob = persist.registry_to_bytes(_reg())
    doc, rest = _split(blob)
    doc.pop("format"), doc.pop("writer")
    doc["version"] = 1
    for h in doc["histories"]:
        h.pop("crcs")
    reg = persist.registry_from_bytes(_join(doc, rest))
    h = reg.histories["H1"]
    assert len(h.entries) == 5 and h.cursor == 5
    assert reg.load_report["migrated"] and reg.load_report["format"] == 1
    assert not reg.load_report["damaged"]
    # saving again writes the current format with checksums
    doc2, _ = _split(persist.registry_to_bytes(reg))
    assert doc2["format"] == persist.FORMAT_VERSION and len(doc2["histories"][0]["crcs"]) == 5


def test_damaged_step_is_never_applied():
    blob = bytearray(persist.registry_to_bytes(_reg()))
    doc, rest = _split(bytes(blob))
    o, n = _blob_offsets(rest, 5)[2]
    start = len(blob) - len(rest) + o
    blob[start + n // 2] ^= 0xFF           # flip one byte inside step 3
    reg = persist.registry_from_bytes(bytes(blob))
    h = reg.histories["H1"]
    assert len(h.entries) == 2, len(h.entries)        # steps 1-2 kept, 3-5 dropped
    assert h.cursor == 2
    d = reg.load_report["damaged"]
    assert d == [{"uid": "H1", "members": ["objA"], "kept": 2, "lost": 3}], d
    for e in h.entries:
        e.load()                                      # what is kept decodes fine


def test_cut_off_file_keeps_complete_steps():
    blob = persist.registry_to_bytes(_reg())
    trailer = len(persist.TRAILER) + 4
    for cut in (1, 30, 200):
        reg = persist.registry_from_bytes(blob[:-(cut + trailer)])
        h = reg.histories["H1"]
        assert 0 < len(h.entries) < 5 and reg.load_report["damaged"][0]["lost"] == 5 - len(h.entries)


def test_header_checksum_trailer():
    blob = persist.registry_to_bytes(_reg())
    assert blob[-8:-4] == persist.TRAILER
    # losing only the trailer keeps every step
    reg = persist.registry_from_bytes(blob[:-8])
    assert len(reg.histories["H1"].entries) == 5 and not reg.load_report["damaged"]
    # a header change that still parses as JSON (cursor 5 -> 4) is caught by the checksum
    doc, _rest = _split(blob)
    assert b'"cursor":5' in blob
    bad = blob.replace(b'"cursor":5', b'"cursor":4', 1)
    try:
        persist.registry_from_bytes(bad)
    except persist.NewerFormatError:
        raise AssertionError("wrong error")
    except ValueError as e:
        assert "checksum" in str(e), e
    else:
        raise AssertionError("damaged header was accepted")


def test_two_histories_stay_aligned_after_damage():
    rng = np.random.default_rng(7)
    reg = _reg(3)
    h2 = History("H2", ["objB"])
    h2.entries = [Entry(_step(10 + i, rng)) for i in range(3)]
    h2.cursor = 3
    reg.histories["H2"] = h2
    reg.by_object["objB"] = "H2"
    blob = bytearray(persist.registry_to_bytes(reg))
    doc, rest = _split(bytes(blob))
    o, n = _blob_offsets(rest, 6)[0]                  # damage H1 step 1
    blob[len(blob) - len(rest) + o + 5] ^= 0x55
    out = persist.registry_from_bytes(bytes(blob))
    assert len(out.histories["H1"].entries) == 0
    assert len(out.histories["H2"].entries) == 3      # untouched history fully loaded


def test_newer_format_is_refused():
    blob = persist.registry_to_bytes(_reg())
    doc, rest = _split(blob)
    doc["format"] = doc["version"] = 99
    try:
        persist.registry_from_bytes(_join(doc, rest))
    except persist.NewerFormatError as e:
        assert e.found == 99
    else:
        raise AssertionError("expected NewerFormatError")


def test_not_a_history():
    for bad in (b"", b"HSR1", b"HSR1" + U32.pack(5) + b"{oops", b"PK\x03\x04" + b"\x00" * 20):
        try:
            persist.registry_from_bytes(bad)
        except persist.NewerFormatError:
            raise AssertionError("garbage is not a newer format")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for %r" % bad[:8])


for fn in (test_writes_format_3_with_checksums, test_reads_format_1, test_damaged_step_is_never_applied,
           test_cut_off_file_keeps_complete_steps, test_header_checksum_trailer,
           test_two_histories_stay_aligned_after_damage, test_newer_format_is_refused, test_not_a_history):
    try:
        fn()
        print("PASS", fn.__name__)
    except Exception:
        ok = False
        print("FAIL", fn.__name__)
        traceback.print_exc()
sys.stdout.flush()
os._exit(0 if ok else 1)
