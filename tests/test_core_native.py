"""The native engine (hindsight_native, Rust) against the Python code it replaces, bit for bit.

Every case runs twice in this process, once through the engine and once through the numpy code of
tests/reference/delta.py (the reference), and the two must agree exactly: the same bytes, dtypes and shapes,
or the same exception type and message, and an array left the same way after a refused apply.
Inputs: every channel type, NaN payloads, -0.0, denormals, infinities, extreme integers, empty,
one element, every element changed, widths 1 to 4 (and item sizes 1, 2, 4, 8 for the transforms),
a 1M-element position channel once, and damaged records made the way test_core_codec makes them.

The missing-engine test checks that a missing module or one with the wrong API number is refused.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import contextlib
import struct
import subprocess
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from reference import pure, delta as rdelta                   # noqa: E402
import test_core_codec as tc                      # noqa: E402  (its damaged-input generator and cases)
from hindsight.core import delta, native, step as stepmod
from hindsight.core.types import ADD, CHANNEL_TYPES, DELTA, XOR, ChannelDelta, Step, make_key   # noqa: E402

RNG = np.random.default_rng(20261118)
LIB = native.ENGINE
WEIRD_F32 = np.array([0x00000000, 0x80000000, 0x7F800000, 0xFF800000, 0x00000001, 0x80000001, 0x007FFFFF,
                      0x7FC00000, 0x7FC00001, 0xFFC12345, 0x7F800001, 0xFFFFFFFF, 0x7FFFFFFF, 0x3F800000],
                     dtype=np.uint32)


@contextlib.contextmanager
def python_path():
    """The Python reference (tests/reference/delta.py) in place of the engine's delta functions."""
    with pure():
        yield


def outcome(fn):
    try:
        return ("ok", fn())
    except Exception as e:                          # compared by type and message
        return ("err", type(e).__name__, str(e))


def both(fn):
    """(native outcome, python outcome) of fn()."""
    a = outcome(fn)
    with python_path():
        b = outcome(fn)
    return a, b


def arr_key(a):
    a = np.asarray(a)
    return (a.dtype.str, a.shape, a.tobytes())


def delta_key(d):
    if d is None:
        return None
    return (d.key, d.op, tuple(d.exists), arr_key(d.idx), arr_key(d.vals))


def same(a, b, conv):
    if a[0] != b[0]:
        return False
    if a[0] == "err":
        return a == b
    return conv(a[1]) == conv(b[1])


# ------------------------------------------------------------------------------ inputs
def native_values(dt, n):
    dt = np.dtype(dt)
    if dt == np.bool_:
        return RNG.integers(0, 2, n).astype(np.bool_)
    if dt.kind == "f":
        a = RNG.standard_normal(n).astype(dt)
        if n:
            m = RNG.random(n) < 0.3
            a.view(np.uint32)[m] = RNG.choice(WEIRD_F32, int(m.sum()))
        return a
    info = np.iinfo(dt)
    a = RNG.integers(info.min, info.max, n, endpoint=True).astype(dt)
    if n >= 2:
        a[0], a[-1] = info.min, info.max
    return a


def changed(old, w, how):
    new = old.copy()
    n = old.size // w
    if n == 0 or how == "none":
        return new
    bits = delta.view_bits(new).reshape(-1, w)
    if how == "one":
        rows = [int(RNG.integers(n))]
    elif how == "all":
        rows = np.arange(n)
    elif how == "run":
        a = int(RNG.integers(n))
        rows = np.arange(a, min(n, a + 1 + int(RNG.integers(40))))
    else:                                           # a sparse stroke: about 2 %
        rows = np.flatnonzero(RNG.random(n) < 0.02)
    for r in rows:
        c = int(RNG.integers(w))
        if bits.dtype == np.uint32:
            pick = RNG.integers(4)
            if pick == 0:
                bits[r, c] = RNG.choice(WEIRD_F32)
            elif pick == 1:
                bits[r, c] ^= np.uint32(0x80000000)            # 0.0 <-> -0.0, sign flips
            elif pick == 2:
                bits[r, c] = np.uint32((int(bits[r, c]) + 1 + int(RNG.integers(3))) & 0xFFFFFFFF)  # next floats
            else:
                bits[r, c] = np.uint32(RNG.integers(0, 2 ** 32))
        else:
            if new.dtype == np.bool_:
                bits[r, c] = 1 - int(bits[r, c])
            else:
                bits[r, c] = (int(bits[r, c]) + 1 + int(RNG.integers(255))) & 255
    return new


def all_keys():
    return [make_key(f"a_{t.lower()}", "POINT", t) for t in CHANNEL_TYPES]


# ------------------------------------------------------------------------------ diff
def test_diff_matches_python():
    count = 0
    for key in all_keys():
        dt, w, _ = CHANNEL_TYPES[key.rsplit("|", 1)[1]]
        for n in (0, 1, 2, 31, 32, 33, 700):
            old = native_values(dt, n * w)
            for how in ("none", "one", "all", "run", "sparse"):
                new = changed(old, w, how)
                for exists in ((True, True), (False, True), (True, False)):
                    a, b = both(lambda: delta.diff(key, old, new, exists))
                    assert same(a, b, delta_key), (key, n, how, exists, a[:2], b[:2])
                    count += 1
            # not contiguous (Python path in both) and wrong sizes (same error)
            if n:
                strided = np.repeat(old, 2)[::2]
                other = changed(old, w, "sparse")
                a, b = both(lambda: delta.diff(key, strided, other))
                assert same(a, b, delta_key)
                a, b = both(lambda: delta.diff(key, old, old[:-1] if old.size > 1 else old[:0]))
                assert same(a, b, delta_key) and a[0] == "err", (a, b)
                count += 2
    # float32 data under an INT key, and different dtypes of the same size
    k = make_key("x", "POINT", "INT")
    f = native_values(np.float32, 50)
    i = changed(f, 1, "sparse").view(np.int32)
    a, b = both(lambda: delta.diff(k, f, i))
    assert same(a, b, delta_key)
    print(f"PASS test_diff_matches_python ({count} cases)")


# ------------------------------------------------------------------------------ apply
def _apply_outcome(arr0, d, forward):
    arr = arr0.copy()
    r = outcome(lambda: delta.apply(arr, d, forward))
    return r[0] if r[0] == "ok" else r, arr.tobytes()


def apply_both(arr0, d, forward):
    a = _apply_outcome(arr0, d, forward)
    with python_path():
        b = _apply_outcome(arr0, d, forward)
    return a, b


def test_apply_matches_python():
    count = 0
    for key in all_keys():
        dt, w, _ = CHANNEL_TYPES[key.rsplit("|", 1)[1]]
        for n in (1, 2, 33, 700):
            old = native_values(dt, n * w)
            for how in ("one", "all", "run", "sparse"):
                new = changed(old, w, how)
                with python_path():
                    d = delta.diff(key, old, new)
                if d is None:
                    continue
                xd = ChannelDelta(key, d.idx, delta.as_xor(d, old), d.exists, XOR)
                for dd in (d, xd):
                    for forward, start in ((True, old), (False, new)):
                        a, b = apply_both(start, dd, forward)
                        assert a == b, (key, n, how, dd.op, forward)
                        want = new if forward else old
                        assert a[1] == want.tobytes()
                        count += 1
    # adversarial deltas: unsorted, repeated, past the end, int64 indices, one row of values
    # (broadcast by numpy), wrong value dtype, a read-only or non-contiguous array
    key = "position|POINT|FLOAT_VECTOR"
    base = native_values(np.float32, 30)
    bad = [
        ([3, 1, 2], np.ones((3, 3), np.uint32)),
        ([2, 2, 5], np.arange(9, dtype=np.uint32).reshape(3, 3)),
        ([0, 4, 10], np.ones((3, 3), np.uint32)),
        ([8, 9, 10], np.ones((3, 3), np.uint32)),
        ([1, 2, 3], np.full((1, 3), 7, np.uint32)),
        ([1, 5, 9], np.ones((3, 3), np.uint16)),
        ([1, 5, 9], np.ones((2, 3), np.uint32)),
        ([9], np.full((1, 3), 0xFFFFFFFF, np.uint32)),
    ]
    for idx, vals in bad:
        for idt in (np.uint32, np.int64):
            for op in (ADD, XOR):
                d = ChannelDelta(key, np.array(idx, idt), vals, (True, True), op)
                for forward in (True, False):
                    a, b = apply_both(base, d, forward)
                    if isinstance(b[0], tuple) and "could not be broadcast" in b[0][2]:
                        # numpy's own message for values that don't fit the indices: the engine's
                        # says "N indices but M values"; same type, array untouched
                        assert a[0][:2] == b[0][:2] == ("err", "ValueError") and a[1] == b[1], (idx, a, b)
                    else:
                        assert a == b, (idx, idt, op, forward, a[0], b[0])
                    count += 1
    ro = base.copy()
    ro.flags.writeable = False
    d = ChannelDelta(key, np.array([1, 4], np.uint32), np.ones((2, 3), np.uint32), (True, True), ADD)
    a, b = both(lambda: delta.apply(ro, d))
    assert same(a, b, lambda x: x) and a[0] == "err"
    nc = np.repeat(base, 2)[::2]
    a, b = both(lambda: delta.apply(nc, d))
    assert same(a, b, lambda x: x) and a[0] == "err"
    print(f"PASS test_apply_matches_python ({count} cases)")


# ------------------------------------------------------------------------------ pack
def _pack_cases():
    for key in all_keys():
        for d, _old, _new in tc.deltas_for(key, 300):
            yield d
    # item sizes 1, 2, 4, 8, widths 1 to 4, k from 1 to 60, values that suit each predictor order
    for trial in range(2500):
        isz = int(RNG.choice([1, 2, 4, 8]))
        w = int(RNG.integers(1, 5))
        k = int(RNG.integers(1, 61)) if trial % 50 else int(RNG.integers(1, 5))
        dt = {1: np.uint8, 2: np.uint16, 4: np.uint32, 8: np.uint64}[isz]
        kind = trial % 6
        t = np.arange(k, dtype=np.float64)[:, None]
        if kind == 0:
            v = RNG.integers(0, 2 ** (8 * isz), (k, w), dtype=np.uint64 if isz == 8 else np.int64).astype(dt)
        elif kind == 1:
            v = (RNG.integers(-3, 4, (k, w)).astype(np.int64)).astype(dt)          # small of both signs
        elif kind == 2:
            v = (np.round(t * RNG.integers(1, 9, w)) + 5).astype(np.int64).astype(dt)   # linear
        elif kind == 3:
            v = (np.round(t ** 2 * RNG.integers(1, 5, w))).astype(np.int64).astype(dt)  # quadratic
        elif kind == 4:
            v = (np.round(t ** 3)).astype(np.int64).astype(dt) * np.ones((1, w), dt)    # cubic
        else:
            v = np.full((k, w), RNG.integers(0, 3), dt)                                 # constant
        gaps = RNG.choice([1, 1, 1, 2, 7, 300, 70000, 2 ** 31], k)
        idx = np.cumsum(gaps) - gaps[0] + int(RNG.choice([0, 5, 2 ** 31]))
        if idx[-1] > 0xFFFFFFFF:
            idx = np.arange(k) + 9
        op = ADD if trial % 7 else XOR
        yield ChannelDelta(make_key("q", "POINT", "FLOAT"), idx.astype(np.uint32), v, (True, True), op)
    # long smooth records (thousands of elements, past the decoder's 1024-element chunks), where the
    # encoder picks orders 1 to 3: the running sums must carry from one chunk to the next
    for k in (1023, 1024, 1025, 4097):
        t = np.arange(k, dtype=np.int64)[:, None]
        v = np.concatenate([t * 3 + 1, t * t, t ** 3 // 7, (t * 37) % 11], 1)
        for dt in (np.uint16, np.uint32, np.uint64):
            yield ChannelDelta(make_key("s", "POINT", "QUATERNION"), np.arange(k, dtype=np.uint32) * 2,
                               v.astype(dt), (True, True), ADD)
    # extreme indices, and indices that aren't sorted and unique
    k1 = make_key("e", "POINT", "FLOAT")
    one = np.ones((2, 1), np.uint32)
    for idx in ([0, 0xFFFFFFFF], [0xFFFFFFFE, 0xFFFFFFFF], [3, 3], [5, 2], [0, 256], [0, 257], [0, 65537]):
        yield ChannelDelta(k1, np.array(idx, np.uint32), one, (True, True), ADD)
    yield ChannelDelta(k1, np.zeros(0, np.uint32), np.zeros((0, 1), np.uint32), (False, True), ADD)
    yield ChannelDelta(k1, np.array([4, 1, 9], np.int64), np.ones((3, 1), np.uint32), (True, True), ADD)
    yield ChannelDelta(make_key("b", "POINT", "INT8"), np.arange(4, dtype=np.uint32),
                       np.array([[1], [255], [0], [128]], np.uint8), (True, True), ADD)


def test_pack_matches_python():
    count = 0
    for d in _pack_cases():
        for effort in (delta.QUICK, delta.NORMAL):
            a, b = both(lambda: delta.pack_delta(d, effort))
            assert same(a, b, bytes), (d.key, d.idx[:5], d.vals.dtype, effort, a[0], b[0], a[1:] if a[0] == "err" else "")
            count += 1
    print(f"PASS test_pack_matches_python ({count} records)")


# ------------------------------------------------------------------------------ unpack
def _unpack_both(rec):
    f = lambda: delta.unpack_at(memoryview(rec), 0)          # noqa: E731
    conv = lambda r: (delta_key(r[0]), r[1])                   # noqa: E731
    a, b = both(f)
    return same(a, b, conv), a, b


def _recode(rec, code, ib):
    """The same record with another index coding (runs over several runs, bitmap)."""
    m, flags, isz, w, _c, klen, k, ilen = delta._HDR.unpack_from(rec, 0)
    body = rec[delta._HDR.size + klen + ilen:]
    return delta._HDR.pack(m, flags, isz, w, code, klen, k, len(ib)) + rec[delta._HDR.size:delta._HDR.size + klen] + ib + body


def _records():
    with python_path():
        for d in _pack_cases():
            for effort in (delta.QUICK, delta.NORMAL):
                try:
                    rec = delta.pack_delta(d, effort)
                except ValueError:
                    continue
                yield rec
                if d.idx.size >= 1 and d.vals.size:
                    idx = np.asarray(d.idx, np.uint32)
                    yield _recode(rec, delta.IDX_RUNS, rdelta._idx_runs(idx))
                    if int(idx[-1]) - int(idx[0]) < 100000:
                        yield _recode(rec, delta.IDX_BITMAP, rdelta._idx_bitmap(idx))


def test_unpack_matches_python():
    count = 0
    for rec in _records():
        ok, a, b = _unpack_both(rec)
        assert ok, (rec[:24], a[0], b[0], a[1:] if a[0] == "err" else "", b[1:] if b[0] == "err" else "")
        assert a[0] == "ok", a
        count += 1
    print(f"PASS test_unpack_matches_python ({count} records, gaps, runs and bitmaps)")


def _raw_record(code, ib, k, add=True):
    """A format 3 record (one FLOAT component, values stored as they are) with the given index data."""
    key = b"p|POINT|FLOAT"
    flags = 3 | (4 if add else 0)
    vals = bytes((delta.TAG_RAW,)) + bytes(range(4 * k))
    return delta._HDR.pack(delta._MAGIC, flags, 4, 1, code, len(key), k, len(ib)) + key + ib + vals


def test_index_limits():
    """Indices right at the top of the uint32 range decode, one past it is refused: gaps (every
    gap width), several runs, one run, bitmap."""
    top = 0xFFFFFFFF
    count = 0
    for over in (0, 1, 2, 300):
        cases = []
        for wc, fmt in ((1, "<B"), (2, "<H"), (4, "<I")):
            g = min(200, (1 << (8 * wc)) - 1)
            first = top - g - 1 + over                 # idx = first, first + g + 1
            if first <= top:
                cases.append(_raw_record(delta.IDX_GAPS, struct.pack("<BI", wc, first) + struct.pack(fmt, g), 2))
        for g in (0, 5, 70000):                          # two runs of one: first, first + g + 2
            first = top - g - 2 + over
            if first <= top:
                cases.append(_raw_record(delta.IDX_RUNS, struct.pack("<BII", 4 | (1 << 4), 2, first)
                                         + struct.pack("<I", g) + bytes(2), 2))
        first = top - 2 + over                          # one run of three
        if first <= top:
            cases.append(_raw_record(delta.IDX_RUNS, struct.pack("<BIIB", 1 | (1 << 4), 1, first, 2), 3))
        lo = top - 9 + over                             # bitmap: elements lo and lo + 9
        if lo <= top:
            cases.append(_raw_record(delta.IDX_BITMAP, struct.pack("<II", lo, 10) + bytes((1, 2)), 2))
        for rec in cases:
            ok, a, b = _unpack_both(rec)
            assert ok, (over, rec, a, b)
            assert (a[0] == "ok") == (over == 0), (over, rec, a)
            if a[0] == "ok":
                assert int(a[1][0].idx[-1]) == top
            count += 1
    print(f"PASS test_index_limits ({count} records)")


def test_damaged_records_fail_the_same_way():
    """test_core_codec's damage on every kind of record, plus edits of each header field, the
    index data and the predictor tags: native and Python must decode to the same delta or refuse
    with the same CorruptDelta message."""
    rng = np.random.default_rng(77)
    recs = list(_records())
    stats = {"decoded": 0, "refused": 0}
    for i in range(6000):
        rec = recs[int(rng.integers(len(recs)))]
        if i % 3 == 0:
            bad = tc._damaged(rec, rng)
        else:
            b = bytearray(rec)
            m, flags, isz, w, code, klen, k, ilen = delta._HDR.unpack_from(b, 0)
            p = delta._HDR.size + klen
            pick = i % 9
            if pick == 0:          # another index coding, or an unknown one
                b[6] = int(rng.integers(0, 5))
            elif pick == 1 and ilen:          # a byte of the index data
                b[p + int(rng.integers(ilen))] = int(rng.integers(256))
            elif pick == 2 and k:             # a predictor tag
                b[p + ilen + int(rng.integers(w))] = int(rng.choice([0, 1, 2, 3, 4, 7, 254, 255]))
            elif pick == 3:                    # the op flag (ADD <-> XOR)
                b[2] ^= 4
            elif pick == 4:                    # the element count
                struct.pack_into("<I", b, 10, int(rng.choice([0, 1, k + 1, max(k - 1, 0), 0xFFFFFFFF])))
            elif pick == 5:                    # the index length
                struct.pack_into("<I", b, 14, int(rng.choice([0, 4, 5, 8, 9, ilen + 1, max(ilen - 1, 0)])))
            elif pick == 6:                    # item size and width
                b[3] = int(rng.choice([0, 1, 2, 3, 4, 8, 16]))
                b[4] = int(rng.choice([0, 1, 2, 3, 4, 16, 17]))
            elif pick == 7 and ilen >= 9:      # the run / gap / bitmap header fields
                o = p + int(rng.integers(ilen - 4))
                struct.pack_into("<I", b, o, int(rng.choice([0, 1, 2, k, 0xFFFFFFFF, 0x7FFFFFFF])))
            else:                              # cut or extended
                b = b[:int(rng.integers(len(b)))] if rng.integers(2) else b + bytes(int(rng.integers(1, 20)))
            bad = bytes(b)
        ok, a, b2 = _unpack_both(bad)
        assert ok, (i, bad[:24], a[0], b2[0], a[1:] if a[0] == "err" else "", b2[1:] if b2[0] == "err" else "")
        if a[0] == "err":
            assert a[1] in ("CorruptDelta", "error", "ValueError", "UnicodeDecodeError"), a
        stats["decoded" if a[0] == "ok" else "refused"] += 1
    print(f"PASS test_damaged_records_fail_the_same_way (6000 records: {stats})")


def test_damaged_steps_fail_the_same_way():
    """Whole steps (checksum patched so the damage reaches the record decoder) through every
    codec: the same step or the same refusal on both paths."""
    rng = np.random.default_rng(5)
    parts = [next(x for x in tc.deltas_for(key, 300) if x[0].idx.size > 20)[0] for key in all_keys()[:5]]
    st = Step(7, "Stroke", "SCULPT", "obj", 1.0, DELTA, parts[:4])
    n = 0
    for name in tc.CODECS:
        with python_path():
            good = [stepmod.step_to_bytes(st, name, e) for e in (delta.QUICK, delta.NORMAL)]
        for i in range(400):
            bad = tc._patch_crc(tc._damaged(good[i % 2], rng))
            a, b = both(lambda: stepmod.step_from_bytes(bad))
            conv = lambda s: [delta_key(d) for d in s.deltas] + [s.seq, s.label]   # noqa: E731
            assert same(a, b, conv), (name, i, a[0], b[0], a[1:] if a[0] == "err" else "", b[1:] if b[0] == "err" else "")
            n += 1
    print(f"PASS test_damaged_steps_fail_the_same_way ({n} blobs, codecs {tc.CODECS})")


# ------------------------------------------------------------------------------ big, and the switch
def test_one_million_vertices():
    key = "position|POINT|FLOAT_VECTOR"
    n = 1_000_000
    old = native_values(np.float32, 3 * n)
    new = changed(old, 3, "sparse")
    new.view(np.uint32)[3 * 500_000:3 * 500_100] ^= np.uint32(0x80000000)
    a, b = both(lambda: delta.diff(key, old, new))
    assert same(a, b, delta_key) and a[0] == "ok"
    d = a[1]
    for effort in (delta.QUICK, delta.NORMAL):
        pa, pb = both(lambda: delta.pack_delta(d, effort))
        assert same(pa, pb, bytes)
        ua, ub = both(lambda: delta.unpack_delta(pa[1]))
        assert same(ua, ub, delta_key)
    for forward, start, want in ((True, old, new), (False, new, old)):
        x, y = apply_both(start, d, forward)
        assert x == y and x[1] == want.tobytes()
    print(f"PASS test_one_million_vertices ({d.idx.size} of {n} changed)")


def test_missing_engine():
    """A missing or mismatched engine is one clear exception, EngineMissing: there is no Python path."""
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    fake = ("import sys, types; m = types.ModuleType('hindsight_native'); m.API = 999; "
            "sys.modules['hindsight_native'] = m; ")
    gone = "import sys; sys.modules['hindsight_native'] = None; "
    probe = "from hindsight.core import native; print(native.ENGINE is not None)"

    def run(code):
        return subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)

    r = run(probe)
    assert r.returncode == 0 and r.stdout.startswith("True"), r
    r = run(fake + probe)
    assert r.returncode != 0 and "EngineMissing" in r.stderr and "API 999" in r.stderr, r
    r = run(gone + probe)
    assert r.returncode != 0 and "EngineMissing" in r.stderr and "could not load" in r.stderr, r
    r = run("import sys; sys.modules['hindsight_native'] = None; from hindsight.core import step")
    assert r.returncode != 0 and "EngineMissing" in r.stderr, r
    print("PASS test_missing_engine")


def main():
    tests = [test_missing_engine, test_diff_matches_python, test_apply_matches_python, test_pack_matches_python,
             test_unpack_matches_python, test_index_limits, test_damaged_records_fail_the_same_way,
             test_damaged_steps_fail_the_same_way, test_one_million_vertices]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    sys.stdout.flush()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
