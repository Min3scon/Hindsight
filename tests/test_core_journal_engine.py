"""#130 (24.2), first part (#152): the crash journal runs in the engine. hindsight/core/journal.py against its reference
(tests/reference/journal.py), bit for bit through tests/equiv.py: the corpus's journals and their damaged copies, a
journal cut at every byte and with every byte flipped, records and calls drawn by hypothesis, and hostile calls
(results, the bytes left in the file, exception class and message; an OSError by class and errno, since the C
runtime's words for it are not the engine's). The write faults are in test_core_saveload_storage.py.
Run: <bpy python> tests/test_core_journal_engine.py"""
import array
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tests.harness as H  # noqa: E402
from tests import corpus as C, equiv  # noqa: E402
from tests.reference import journal as ref  # noqa: E402
from hindsight.core import journal  # noqa: E402


def _err(e):
    if isinstance(e, OSError):
        return ("OSError", type(e).__name__, e.errno)
    return ("raised", type(e).__qualname__, str(e))


def drive(mod, path, data, keep=False, discard=False, ops=()):
    """Open a journal on `path` (holding `data` first, unless None) and run `ops` on it: what each call gave."""
    if data is not None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    try:
        j = mod.Journal(path, keep=keep, discard=discard)
    except Exception as e:       # noqa: BLE001 - compared
        return [_err(e)]
    out = [(j.path == os.path.abspath(path), j.size(), j.closed)]
    try:
        for op, *a in ops:
            try:
                r = getattr(j, op)(*a)
            except Exception as e:   # noqa: BLE001 - compared
                r = _err(e)
            out.append((op, r, j.size(), j.closed))
    finally:
        j.close()
    return out


def same(data, keep=False, discard=False, ops=(("read",),)):
    equiv.check(lambda *a: drive(ref, *a), lambda *a: drive(journal, *a), equiv.Out("j.hsj"), data, keep, discard,
                ops)


def _record(code, header: bytes, payload: bytes, magic=b"HSJ1"):
    prefix = struct.pack("<4sBIQ", magic, code, len(header), len(payload)) + header
    return prefix + payload + struct.pack("<I", zlib.crc32(payload, zlib.crc32(prefix)))


def _small():
    """A journal of a few short records, as the add-on writes them."""
    return b"".join([_record(7, b'{"blend":"a","save_id":null,"s":"x"}', b""),
                     _record(1, b'{"hist":"h","at":0}', bytes(range(40))),
                     _record(2, b'{"hist":"h","cursor":1}', b""),
                     _record(1, b"", b"payload"),
                     _record(3, b'{"hist":"h","keep":1}', b"")])


AFTER = (("read",), ("append", "CURSOR", {"hist": "h", "cursor": 2}), ("flush", False), ("read",), ("read", 30),
         ("size",))


def test_constants_are_the_references():
    assert (journal.MAGIC, journal.RTYPES, journal.RTYPE_NAMES) == (ref.MAGIC, ref.RTYPES, ref.RTYPE_NAMES)


def test_corpus_journals():
    n = 0
    datas = [C.payload(p, k) for p, k, _f in C.FILES if k == "journal"] + [C.make("every_record.hsj")]
    for data in datas:
        for _name, d in [("whole", data)] + C.damaged(data, "journal"):
            for keep, discard in ((False, False), (True, False), (False, True), (True, True)):
                same(d, keep, discard, AFTER)
                n += 1
    print(f"  test_corpus_journals ({n} opens)")


def test_cut_at_every_byte():
    data = _small()
    for at in range(len(data) + 1):
        same(data[:at], True, False, AFTER)
        same(data[:at], False, False, AFTER)


def test_every_byte_flipped():
    data = _small()
    for at in range(len(data)):
        for x in (0x01, 0x80, 0xFF):
            same(data[:at] + bytes([data[at] ^ x]) + data[at + 1:], True, False, AFTER)


def test_hostile_records():
    deep = b"[" * 100000 + b"]" * 100000
    for recs in ([_record(2, b"{bad", b"")], [_record(2, b"\xff\xfe", b"")], [_record(2, b"[1,2]", b"")],
                 [_record(2, b"5", b"")], [_record(2, b'"x"', b""), _record(2, b"{}", b"")],
                 [_record(2, b"NaN", b"")], [_record(2, deep, b"")], [_record(9, b"{}", b"")],
                 [_record(0, b"{}", b"")], [_record(2, b"{}", b"", magic=b"HSJ2")],
                 [_record(2, b"{bad", b""), _record(2, b"{}", b"x")], [_record(1, b"{}", b"p" * 70000)]):
        data = b"".join(recs + [_record(2, b'{"after":1}', b"")])
        for keep in (False, True):
            same(data, keep, False, AFTER)
    big = struct.pack("<4sBIQ", b"HSJ1", 2, 64 * 2 ** 20 + 1, 0)
    same(big + b"x" * 100, True)
    same(struct.pack("<4sBIQ", b"HSJ1", 2, 2, 2 ** 64 - 1) + b"{}", True)


def test_hostile_calls():
    class Odd:
        def __init__(self):      # an attribute: equiv compares an object with nothing on it by ==
            self.x = 1

    cyc = {}
    cyc["me"] = cyc
    calls = [("append", "NOPE", {}), ("append", 1, {}), ("append", ["STEP"], {}), ("append", "STEP", None),
             ("append", "STEP", {"o": Odd()}), ("append", "STEP", {1: 2, None: 3, 2.5: True}), ("append", "STEP", cyc),
             ("append", "STEP", {"x": float("nan"), "y": float("inf"), "s": "é\ud800"}),
             ("append", "STEP", [1, 2]), ("append", "STEP", {}, None), ("append", "STEP", {}, 5),
             ("append", "STEP", {}, "text"), ("append", "STEP", {}, array.array("B", b"view")),
             ("append", "STEP", {}, bytearray(b"ba")), ("append", "STEP", {}, [1, 2, 3]),
             ("read", -5), ("read", 10 ** 30), ("read", "12"), ("read", 7.9), ("read", "x"), ("read", None),
             ("truncate", -1), ("truncate", 10 ** 30), ("truncate", "20"), ("truncate", 3.5), ("truncate", "x"),
             ("flush",), ("size",), ("close",), ("append", "STEP", {}), ("flush",), ("read",), ("truncate", 10),
             ("append", "CURSOR", {"after": "truncate"}), ("read",)]
    same(_small(), False, False, calls)
    same(None, False, False, calls)


def test_hostile_paths():
    d = equiv.Out("j.hsj")
    equiv.check(lambda p: drive(ref, os.path.dirname(p), None), lambda p: drive(journal, os.path.dirname(p), None), d)

    def as_dir(mod, p):
        os.makedirs(p)
        return drive(mod, p, None)
    equiv.check(lambda p: as_dir(ref, p), lambda p: as_dir(journal, p), d)

    def under_file(mod, p):
        with open(p, "wb") as fh:
            fh.write(b"a file")
        return drive(mod, os.path.join(p, "j.hsj"), None)
    equiv.check(lambda p: under_file(ref, p), lambda p: under_file(journal, p), d)


def test_hypothesis_calls():
    from hypothesis import HealthCheck, given, settings, strategies as hs
    names = hs.sampled_from(list(ref.RTYPES))
    val = hs.recursive(hs.none() | hs.booleans() | hs.integers() | hs.floats() | hs.text(),
                       lambda v: hs.lists(v, max_size=4) | hs.dictionaries(hs.text(), v, max_size=4), max_leaves=12)
    op = hs.one_of(hs.tuples(hs.just("append"), names, hs.dictionaries(hs.text(), val, max_size=5),
                             hs.binary(max_size=3000)),
                   hs.tuples(hs.just("flush"), hs.booleans()), hs.tuples(hs.just("read")),
                   hs.tuples(hs.just("read"), hs.integers(-10, 9000)), hs.tuples(hs.just("truncate"), hs.integers(-10, 9000)),
                   hs.tuples(hs.just("size")), hs.tuples(hs.just("close")))

    @settings(max_examples=300, deadline=None, suppress_health_check=list(HealthCheck))
    @given(hs.lists(op, max_size=25), hs.integers(0, len(_small())), hs.booleans())
    def run(ops, cut, keep):
        same(_small()[:cut], keep, False, ops + [("read",)])
    run()


def test_journal_holds_no_algorithm():
    """journal.py names the engine's Journal: no JSON, framing or file work of its own, and nothing under hindsight/
    reaches the reference."""
    src = open(journal.__file__, encoding="utf-8").read()
    for word in ("json.", "struct.", "open(", "os.", "zlib", "from .crc"):
        assert word not in src, f"journal.py still does {word}"
    imports = [ln.strip() for ln in src.splitlines() if ln.strip().startswith(("import ", "from "))]
    assert imports == ["from __future__ import annotations", "from .native import ENGINE as _N"], imports
    assert journal.Journal is journal._N.Journal


for t in (test_constants_are_the_references, test_journal_holds_no_algorithm, test_corpus_journals,
          test_cut_at_every_byte, test_every_byte_flipped, test_hostile_records, test_hostile_calls, test_hostile_paths,
          test_hypothesis_calls):
    H.run(t)
H.finish()
