"""History jumps through the native engine's apply_steps against the Python path, bit for bit.

History.goto hands runs of packed DELTA steps of one object to hindsight_native.apply_steps, which
applies them in Rust and stops before any step it doesn't take; goto crosses that step in Python.
Every test here runs the same jumps on twin histories, once with the engine (apply_steps) and once
with tests/reference/history.py (the Python loop, one step at a time), and compares after each jump: the
cursor, the changed keys per object (and the order of the objects), every array (dtype, shape,
bytes), the removed channels, the recorded values, and any exception (type and message).

Histories mix every channel type, ADD and XOR deltas, every codec and both encoder efforts, steps
written quickly, recompressed steps, format 1 steps, steps with extras and data records, TOPO and
DATA steps, channels that appear and go, empty deltas, several objects, raw and packed entries, and
damaged blobs. apply_steps itself is also fed hostile input directly (any thread count).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import json
import struct
import subprocess
import traceback
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import test_core_codec as tc                                     # noqa: E402  (damaged-blob maker)
from hindsight.core import codec, delta, native, step as stepmod   # noqa: E402
from hindsight.core.history import History, MeshState           # noqa: E402
from reference.history import History as RefHistory                # noqa: E402
from hindsight.core.types import (                              # noqa: E402
    ADD, CHANNEL_TYPES, DATA, DELTA, MISSING, POSITION_KEY, TOPO, XOR,
    ChannelDelta, DataChange, Entry, Snapshot, Step, key_dtype, key_width, make_key,
)

LIB = native.lib
HAS = LIB is not None and hasattr(LIB, "apply_steps")
KEYS = [make_key(f"c_{t.lower()}", "POINT", t) for t in CHANNEL_TYPES]
WEIRD32 = tc.WEIRD32


ENGINES = [("native", History), ("reference", RefHistory)]       # History.goto against the Python loop


def skip(name):
    if not HAS:
        print(f"SKIP {name}: hindsight_native with apply_steps not loaded ({native.ERROR})")
        return True
    return False


# ------------------------------------------------------------------------------ values
def values(rng, key, n):
    dt = np.dtype(key_dtype(key))
    m = n * key_width(key)
    if dt == np.bool_:
        return rng.integers(0, 2, m).astype(np.bool_)
    bits = rng.integers(0, 2 ** 32, m, dtype=np.uint64).astype(np.uint32)
    if dt == np.float32:
        weird = rng.random(m) < 0.1
        bits[weird] = rng.choice(WEIRD32, int(weird.sum()))
        return bits.view(np.float32)
    if dt == np.int8:
        return bits.astype(np.uint8).view(np.int8)
    return bits.view(np.int32)


def stroke(rng, key, old, how):
    """A changed copy: a run, a scatter, everything, or tiny changes (good for prediction)."""
    w = key_width(key)
    n = old.size // w
    new = old.copy()
    r = new.reshape(-1, w)
    if how == "run":
        a = int(rng.integers(0, n))
        b = min(n, a + int(rng.integers(1, 200)))
        r[a:b] = values(rng, key, b - a).reshape(-1, w)
    elif how == "all":
        r[:] = values(rng, key, n).reshape(-1, w)
    elif how == "nudge" and new.dtype == np.float32:
        sel = np.unique(rng.integers(0, n, int(rng.integers(1, n + 1))))
        r[sel] = (r[sel].view(np.uint32) + rng.integers(0, 3, (sel.size, w)).astype(np.uint32)).view(np.float32)
    else:
        sel = np.unique(rng.integers(0, n, int(rng.integers(1, 60))))
        r[sel] = values(rng, key, sel.size).reshape(-1, w)
    return new


def xor_delta(key, old, new, exists=(True, True)):
    w = key_width(key)
    ob = delta.view_bits(old).reshape(-1, w)
    nb = delta.view_bits(new).reshape(-1, w)
    x = ob ^ nb
    idx = np.flatnonzero(x.any(axis=1)).astype(np.uint32)
    return ChannelDelta(key, idx, np.ascontiguousarray(x[idx]), exists, XOR)


# ------------------------------------------------------------------------------ format 1 blobs
def v1_record(d):
    """A format 1/2 record (b"HD", XOR) as old releases wrote it."""
    key = d.key.encode()
    k = int(d.idx.size)
    w = d.vals.shape[1]
    isz = d.vals.dtype.itemsize
    flags = (1 if d.exists[0] else 0) | (2 if d.exists[1] else 0)
    if k == 0:
        return struct.pack("<2sBBBBHII", b"HD", flags, isz, w, 4, len(key), 0, 0) + key
    dif = np.diff(d.idx.astype(np.int64)).astype(np.uint32)
    planes = np.ascontiguousarray(d.vals).view(np.uint8).reshape(k, isz * w).T.tobytes()
    return (struct.pack("<2sBBBBHII", b"HD", flags, isz, w, 4, len(key), k, int(d.idx[0])) + key
            + dif.tobytes() + planes)


def v1_blob(step):
    recs = [v1_record(d) for d in step.deltas]
    head = json.dumps({"meta": step.meta(), "deltas": [{"n": len(r)} for r in recs]}).encode()
    return b"HSS1" + struct.pack("<I", len(head)) + head + codec.compress(b"".join(recs), "zlib")


# ------------------------------------------------------------------------------ histories
CODEC_CYCLE = [c for c in ("zstd", "none", "zlib", "lz4") if c in codec.available()]


def build(seed, n_steps=120, n=300, targets=("A", "B"), mix=True):
    """-> (entries, start states per target). Steps of every kind, packed in every way."""
    rng = np.random.default_rng(seed)
    cur = {t: {k: values(rng, k, n) for k in KEYS[: 4 + (seed % 7)]} for t in targets}
    gone = {t: {} for t in targets}         # channels removed for now (key -> length)
    start = {t: MeshState({"POINT": n}, {k: v.copy() for k, v in cur[t].items()}) for t in targets}
    vals = {t: {} for t in targets}
    entries = []
    for i in range(n_steps):
        t = targets[0] if (len(targets) == 1 or rng.random() < 0.75) else str(rng.choice(targets[1:]))
        arrs = cur[t]
        kind = DELTA
        deltas, records, extra, before, after = [], [], {}, None, None
        r = rng.random() if mix else 1.0
        if r < 0.04:
            kind = TOPO
            before = Snapshot({"POINT": n}, {k: v.copy() for k, v in arrs.items()})
            for k in list(arrs):
                arrs[k] = stroke(rng, k, arrs[k], "all")
            after = Snapshot({"POINT": n}, {k: v.copy() for k, v in arrs.items()})
        elif r < 0.07:
            kind = DATA
            p = f"loc{int(rng.integers(3))}"
            old = vals[t].get(("xform", p), MISSING)
            new = float(rng.random())
            vals[t][("xform", p)] = new
            records = [DataChange("xform", t, {p: (old, new)})]
        else:
            if r < 0.10 and len(arrs) > 2:                       # a channel goes
                k = str(rng.choice(sorted(arrs)[1:]))
                old = arrs.pop(k)
                gone[t][k] = old.size
                d = delta.diff(k, old, np.zeros_like(old), (True, False))
                deltas.append(d)
            elif r < 0.13 and gone[t]:                           # and comes back
                k = sorted(gone[t])[0]
                z = np.zeros(gone[t].pop(k), key_dtype(k))
                new = stroke(rng, k, z, "scatter")
                deltas.append(delta.diff(k, z, new, (False, True)))
                arrs[k] = new
            ks = sorted(arrs)
            for k in rng.choice(ks, int(rng.integers(1, min(3, len(ks)) + 1)), replace=False):
                k = str(k)
                if any(d.key == k for d in deltas):
                    continue
                new = stroke(rng, k, arrs[k], str(rng.choice(["run", "scatter", "nudge", "all"])))
                d = xor_delta(k, arrs[k], new) if rng.random() < 0.2 else delta.diff(k, arrs[k], new)
                if d is not None:
                    deltas.append(d)
                    arrs[k] = new
            if mix and rng.random() < 0.05:
                deltas.append(ChannelDelta(ks[0], np.zeros(0, np.uint32),
                                           np.zeros((0, key_width(ks[0])), delta.view_bits(arrs[ks[0]]).dtype),
                                           (True, True), ADD))
            if mix and rng.random() < 0.05:
                extra = {"op": int(rng.integers(100))}
            if mix and rng.random() < 0.05:
                p = "hide"
                old = vals[t].get(("rna", p), MISSING)
                vals[t][("rna", p)] = [1, 2]
                records = [DataChange("rna", t, {p: (old, [1, 2])})]
        st = Step(i + 1, "Stroke", "SCULPT", t, float(i), kind, deltas, before, after, extra, records)
        entries.append(pack(rng, st, mix))
    for t in targets:
        start[t].values = {}
    return entries, start, cur


def pack(rng, st, mix):
    how = rng.random() if mix else 0.5
    c = CODEC_CYCLE[int(rng.integers(len(CODEC_CYCLE)))] if mix else "zstd"
    if how < 0.08:
        return Entry(st)                                         # raw
    if how < 0.13 and st.kind == DELTA and not st.records and not st.extra \
            and all(d.op == XOR for d in st.deltas):
        return Entry(blob=v1_blob(st), meta=st.meta())           # format 1
    effort = delta.QUICK if how < 0.25 else delta.NORMAL
    b = stepmod.step_to_bytes(st, c, effort)
    if how > 0.9:
        b = stepmod.recompress(b) or b
    return Entry(blob=b, meta=st.meta())


def twin(entries, start, cursor, cls=History):
    h = cls("A", members=sorted(start))
    h.entries = [Entry(e.step, e.blob, dict(e.meta)) for e in entries]
    h.cursor = cursor
    states = {t: MeshState(s.counts, {k: v.copy() for k, v in s.arrays.items()}, s.values) for t, s in start.items()}
    return h, states


def state_key(states):
    out = {}
    for t, s in states.items():
        out[t] = (s.counts, sorted((k, a.dtype.str, a.shape, a.tobytes()) for k, a in s.arrays.items()),
                  sorted(s.removed), sorted(s.values.items(), key=repr), sorted(s.snaps))
    return out


def outcome(fn):
    try:
        r = fn()
        return ("ok", [(t, sorted(v, key=repr)) for t, v in r.items()])   # keys mix str and tuple
    except Exception as e:
        return ("err", type(e).__name__, str(e))


def run_twins(entries, start, cursor, plan, label):
    """Runs the same gotos on one twin per engine; all must agree after every jump."""
    twins = []
    for name, cls in ENGINES:
        h, states = twin(entries, start, cursor, cls)
        twins.append((name, cls, h, states))
    for step_no, pos in enumerate(plan):
        res = []
        for name, cls, h, states in twins:
            o = outcome(lambda: h.goto(pos, states))
            res.append((name, o, h.cursor, state_key(states)))
        ref = res[-1]
        for r in res[:-1]:
            assert r[1] == ref[1], (label, step_no, pos, r[0], r[1][:3], ref[1][:3])
            assert r[2] == ref[2], (label, step_no, pos, r[0], r[2], ref[2])
            assert r[3] == ref[3], (label, step_no, pos, r[0], "state differs")
    return twins


def plan_for(rng, n, k=60):
    out = []
    for i in range(k):
        r = i % 4
        out.append(int(rng.integers(0, n + 1)) if r < 2 else (0 if r == 2 else n))
    return out


# ------------------------------------------------------------------------------ tests
def test_mixed_histories():
    if skip("test_mixed_histories"):
        return
    jumps = 0
    for seed in range(12):
        rng = np.random.default_rng(1000 + seed)
        targets = ("A",) if seed % 3 == 0 else ("A", "B")
        entries, start, cur = build(seed, targets=targets)
        n = len(entries)
        twins = run_twins(entries, start, 0, [n] + plan_for(rng, n), f"seed {seed}")
        jumps += n
        # only some objects moved (the others are skipped without unpacking)
        if len(targets) > 1:
            part = {"A": start["A"]}
            run_twins(entries, part, 0, [n] + plan_for(rng, n, 20), f"seed {seed} A only")
        assert twins[0][2].cursor == twins[-1][2].cursor
    print(f"PASS test_mixed_histories (12 histories, every engine agrees)")


def test_ends_are_exact():
    """Plain stroke histories end where they were recorded, both ways, at every channel type."""
    if skip("test_ends_are_exact"):
        return
    for seed in range(4):
        entries, start, cur = build(50 + seed, n_steps=200, n=2000, targets=("A",), mix=False)
        h, states = twin(entries, start, 0)
        h.goto(len(entries), states)
        for k, v in cur["A"].items():
            assert states["A"].arrays[k].tobytes() == v.tobytes(), (seed, k)
        h.goto(0, states)
        for k, v in start["A"].arrays.items():
            assert states["A"].arrays[k].tobytes() == v.tobytes(), (seed, k)
        run_twins(entries, start, 0, [200, 0, 137, 3, 200, 199, 0], f"plain {seed}")
    print("PASS test_ends_are_exact")


try:
    import zstandard as _zs
    ZSTD_PY = tuple(_zs.ZSTD_VERSION)
except Exception:
    ZSTD_PY = (0, 0, 0)


def test_damaged_blobs():
    """A damaged step in the middle of a run: the same exception and the same state on every
    engine (steps before it applied, cursor kept)."""
    if skip("test_damaged_blobs"):
        return
    rng = np.random.default_rng(77)
    entries, start, cur = build(9, n_steps=40, n=200, targets=("A",), mix=False)
    n = 0
    kinds = {}
    for i in range(300):
        at = int(rng.integers(5, 35))
        bad = list(entries)
        e = bad[at]
        blob = e.blob if e.blob is not None else stepmod.step_to_bytes(e.step, "zstd")
        b = tc._damaged(blob, rng)
        if i % 3:
            b = tc._patch_crc(b)
        bad[at] = Entry(blob=b, meta=dict(e.meta))
        try:
            twins = run_twins(bad, start, 0, [40, 0, at, 40], f"damaged {i}")
        except AssertionError as err:
            # Damage with its checksum made to match (never what a disk or a cut does) can leave a
            # zstd frame that libzstd 1.5.7 (in the engine) decodes and older ones refuse. Blender 4.2
            # bundles zstandard 0.16 with libzstd 1.5.0, so the Python reference refuses it there.
            # Only that difference is let through, and only with an older libzstd in Python.
            a = err.args[0] if err.args else ()
            if not (i % 3 and ZSTD_PY < (1, 5, 7) and len(a) > 5 and a[4][:1] == ("ok",)
                    and a[5][:2] == ("err", "CorruptStep") and "decompression error" in str(a[5][2])):
                raise
            continue
        kinds[twins[0][2].cursor == 40] = kinds.get(twins[0][2].cursor == 40, 0) + 1
        n += 1
    print(f"PASS test_damaged_blobs ({n} damaged steps; reached the end {kinds.get(True, 0)} times)")


def _crafted(st):
    """Blobs of step `st` damaged in ways a random flip rarely makes, checksum valid."""
    out = {}
    raw = stepmod.step_to_bytes(st, "none")
    zs = stepmod.step_to_bytes(st, "zstd")
    hl = struct.unpack_from("<H", raw, 8)[0]

    def fix(b):
        return tc._patch_crc(bytes(b))

    def with_raw_len(b, n):
        b = bytearray(b)
        struct.pack_into("<I", b, 10 + 20, n)
        return b
    # extras that aren't JSON (same length as a real one)
    ex = Step(st.seq, st.label, st.mode, st.target, st.time, st.kind, st.deltas, extra={"op": 12345})
    b = bytearray(stepmod.step_to_bytes(ex, "zstd"))
    j = bytes(b).index(b'{"op":12345}')
    out["extra not JSON"] = fix(b[:j] + b'{"op":12345]' + b[j + 12:])
    out["extra JSON ok"] = fix(b)
    # a label, mode or target that isn't UTF-8
    for name, field in (("label", 10 + 24 + 2), ("mode", 10 + 24 + 2 + len(st.label.encode()) + 1)):
        b = bytearray(zs)
        b[field] = 0xFF
        out[f"{name} not UTF-8"] = fix(b)
    # a body with a byte too many or too few (uncompressed, so the record parser sees it)
    body = 10 + hl + 1
    out["body byte too many"] = fix(with_raw_len(raw + b"\x00", len(raw) - body + 1))
    out["body byte too few"] = fix(with_raw_len(raw[:-1], len(raw) - body - 1))
    # zstd: a second frame after the first, trailing bytes, a frame that claims another size
    import zstandard
    zb = bytearray(zs)
    zbody = bytes(zb[body:])
    out["zstd two frames"] = fix(zb + zstandard.ZstdCompressor().compress(b""))
    out["zstd trailing byte"] = fix(zb + b"\x00")
    rl = struct.unpack_from("<I", zb, 30)[0]
    out["zstd size claim"] = fix(with_raw_len(zb, rl + 1))
    out["zstd no size"] = fix(zb[:body] + zstandard.ZstdCompressor(write_content_size=False).compress(
        stepmod.step_to_bytes(st, "none")[body:]))
    del zbody
    # record keys that aren't UTF-8, a record magic of format 1 inside a format 3 step
    b = bytearray(raw)
    k = bytes(b).index(st.deltas[0].key.encode())
    b[k] = 0xC3
    b[k + 1] = 0x28
    out["key not UTF-8"] = fix(b)
    b = bytearray(raw)
    b[body:body + 2] = b"HD"
    out["format 1 record magic"] = fix(b)
    # header flags and kinds it must leave to Python
    for name, at, val in (("kind TOPO", 11, 1), ("flag before", 12, 1), ("flag records", 12, 16),
                          ("version 4", 10, 4), ("version 2", 10, 2), ("reserved byte", 13, 7)):
        b = bytearray(zs)
        b[at] = val
        out[name] = fix(b)
    return out


def test_crafted_blobs():
    """Damage made on purpose in each field apply_steps checks, in the middle of a run: the same
    outcome on every engine."""
    if skip("test_crafted_blobs"):
        return
    entries, start, cur = build(4, n_steps=30, n=300, targets=("A",), mix=False)
    at = 17
    st = entries[at].load()
    assert st.deltas
    cases = _crafted(st)
    for name, b in cases.items():
        bad = list(entries)
        bad[at] = Entry(blob=b, meta=dict(entries[at].meta))
        run_twins(bad, start, 0, [30, 0, at + 1, at, 30, 5], name)
    print(f"PASS test_crafted_blobs ({len(cases)} cases)")


def test_empty_records_count_as_changed():
    """A record with no changed element still names its channel as changed (as the Python path
    does), inside a run, whichever way."""
    if skip("test_empty_records_count_as_changed"):
        return
    rng = np.random.default_rng(12)
    n = 50
    a = {KEYS[0]: values(rng, KEYS[0], n), KEYS[4]: values(rng, KEYS[4], n)}
    start = {"A": MeshState({"POINT": n}, {k: v.copy() for k, v in a.items()})}
    entries = []
    for i in range(6):
        new = stroke(rng, KEYS[0], a[KEYS[0]], "scatter")
        ds = [delta.diff(KEYS[0], a[KEYS[0]], new)]
        a[KEYS[0]] = new
        if i == 3:
            w = key_width(KEYS[4])
            ds.append(ChannelDelta(KEYS[4], np.zeros(0, np.uint32), np.zeros((0, w), np.uint32), (True, True), ADD))
        st = Step(i + 1, "Stroke", "SCULPT", "A", 0.0, DELTA, ds)
        entries.append(Entry(blob=stepmod.step_to_bytes(st, "zstd"), meta=st.meta()))
    twins = run_twins(entries, start, 0, [6, 0, 4, 3, 2, 6], "empty record")
    h, states = twin(entries, start, 0)
    assert set(h.goto(6, states)["A"]) == {KEYS[0], KEYS[4]}
    print("PASS test_empty_records_count_as_changed")


def test_hostile_calls():
    """apply_steps on its own: bad arguments and hostile data never crash, never write a step it
    refuses, and never allocate from an unchecked length."""
    if skip("test_hostile_calls"):
        return
    L = LIB
    rng = np.random.default_rng(3)
    key = POSITION_KEY
    pos = values(rng, key, 1000)
    new = stroke(rng, key, pos, "run")
    good = stepmod.step_to_bytes(Step(1, "S", "SCULPT", "A", 0.0, DELTA, [delta.diff(key, pos, new)]), "zstd")
    # plain call, every thread count
    for th in (None, 0, 1, 2, 3, 10 ** 6):
        a = {key: pos.copy()}
        assert L.apply_steps([good], a, True, th) == (1, [key])
        assert a[key].tobytes() == new.tobytes()
        assert L.apply_steps([good, good, good][:1], a, False, th)[0] == 1
        assert a[key].tobytes() == pos.tobytes()
    # not bytes: stops there
    a = {key: pos.copy()}
    assert L.apply_steps([good, bytearray(good), good], a, True) == (1, [key])
    assert L.apply_steps([memoryview(good)], a, True) == (0, [])
    # arrays it can't take: the step is left to Python
    for bad_arrays in ({key: pos.copy()[::2]}, {key: np.zeros(pos.size, np.float64)}, {key: list(pos[:5])},
                       {1: pos.copy()}, {}, {key: np.frombuffer(pos.tobytes(), np.float32)},
                       {key: pos.copy()[:-1]}, {key: pos.copy()[:30]}):
        snap = {k: (v.tobytes() if hasattr(v, "tobytes") else v) for k, v in bad_arrays.items()}
        assert L.apply_steps([good], bad_arrays, True) == (0, [])
        assert {k: (v.tobytes() if hasattr(v, "tobytes") else v) for k, v in bad_arrays.items()} == snap
    # two keys sharing memory: neither is used
    base = pos.copy()
    shared = {key: base, "x|POINT|FLOAT_VECTOR": base[:]}
    assert L.apply_steps([good], shared, True) == (0, [])
    # wrong argument types raise TypeError, not a crash
    for args in (([good], [], True), ((good,), {key: pos.copy()}, True), ([good], {key: pos.copy()}, True, -1)):
        try:
            L.apply_steps(*args)
        except (TypeError, OverflowError):
            pass
        else:
            raise AssertionError(args)
    # hostile blobs with a valid checksum: random bytes after a real header, oversized counts,
    # a body length claiming a gigabyte, a zstd frame claiming more than it holds
    hl = struct.unpack_from("<H", good, 8)[0]
    head = bytearray(good[:10 + hl])
    cases = []
    for i in range(3000):
        b = bytearray(good)
        kind = i % 6
        if kind == 0:
            b = head + bytes(rng.integers(0, 256, int(rng.integers(0, 400)), dtype=np.uint8))
        elif kind == 1:                                   # raw body length
            struct.pack_into("<I", b, 10 + 20, int(rng.choice([0, 1, 2 ** 30, 2 ** 32 - 1, 5000])))
        elif kind == 2:                                   # record count
            struct.pack_into("<H", b, 10 + hl - 2, int(rng.integers(0, 65536)))
        elif kind == 3:                                   # uncompressed body with a damaged record
            st = stepmod.step_from_bytes(good)
            raw = bytearray(stepmod.step_to_bytes(st, "none"))
            h2 = struct.unpack_from("<H", raw, 8)[0]
            body = 10 + h2 + 1
            j = body + int(rng.integers(0, 40))
            if j < len(raw):
                raw[j] = int(rng.integers(0, 256))
            b = raw
        elif kind == 4:
            b = tc._damaged(good, rng)
        else:                                             # header fields
            j = 10 + int(rng.integers(0, hl))
            b[j] = int(rng.integers(0, 256))
        cases.append(tc._patch_crc(bytes(b)))
    agree = 0
    for b in cases:
        a = {key: pos.copy()}
        ref = {key: pos.copy()}
        done, _ = L.apply_steps([b], a, True, int(rng.integers(0, 3)))
        if done:
            # whatever it applied must be exactly what Python makes of the blob
            stp = stepmod.step_from_bytes(b)
            hh = RefHistory("A")
            hh.entries = [Entry(stp)]
            hh.goto(1, {stp.target: MeshState({"POINT": 1000}, ref)})   # the header may name another
            assert a[key].tobytes() == ref[key].tobytes(), (stp.deltas, b[:80])
            agree += 1
        else:
            assert a[key].tobytes() == pos.tobytes()
    print(f"PASS test_hostile_calls ({len(cases)} hostile blobs, {agree} taken and matching Python)")


def test_threads_agree():
    """Any helper thread count gives the same arrays, and a step that isn't taken stops every
    thread count at the same place."""
    if skip("test_threads_agree"):
        return
    rng = np.random.default_rng(8)
    entries, start, cur = build(21, n_steps=150, n=3000, targets=("A",), mix=False)
    blobs = [e.blob for e in entries]
    stop = 97
    blobs2 = list(blobs)
    blobs2[stop] = stepmod.step_to_bytes(stepmod.step_from_bytes(blobs[stop]), "zlib")
    for bl, want in ((blobs, len(blobs)), (blobs2, stop)):
        res = []
        for th in (0, 1, 2, 3, None):
            a = {k: v.copy() for k, v in start["A"].arrays.items()}
            done, keys = LIB.apply_steps(bl, a, True, th)
            assert done == want, (th, done, want)
            back = {k: v.copy() for k, v in a.items()}
            assert LIB.apply_steps(bl[:done][::-1], back, False, th)[0] == done
            for k, v in back.items():
                assert v.tobytes() == start["A"].arrays[k].tobytes(), (th, k)
            res.append((sorted(keys), {k: v.tobytes() for k, v in a.items()}))
        assert all(r == res[0] for r in res), "thread counts disagree"
    print("PASS test_threads_agree")


def test_property():
    """Random small histories (hypothesis): every engine agrees on every jump."""
    if skip("test_property"):
        return
    try:
        from hypothesis import given, settings, strategies as hs, HealthCheck
    except ImportError:
        print("SKIP test_property: hypothesis not installed")
        return

    @settings(max_examples=60, deadline=None, suppress_health_check=list(HealthCheck))
    @given(hs.integers(0, 2 ** 31), hs.integers(1, 40), hs.integers(1, 120), hs.booleans(),
           hs.lists(hs.integers(0, 40), min_size=1, max_size=12))
    def prop(seed, n_steps, n, mix, plan):
        entries, start, cur = build(seed, n_steps=n_steps, n=n, targets=("A", "B") if seed % 2 else ("A",), mix=mix)
        run_twins(entries, start, 0, [min(p, n_steps) for p in plan] + [n_steps, 0], f"prop {seed}")

    prop()
    print("PASS test_property")


def test_old_module_and_loader():
    """The engine must be API 3: a module from an older release (API 1 or 2, no codecs or step
    container) or a newer one (or none at all) is refused with EngineMissing, in every mode. The
    engine is one module: there is no older-module mode."""
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

    def fake(api):
        return (f"import sys, types; m = types.ModuleType('hindsight_native'); m.API = {api}; "
                "sys.modules['hindsight_native'] = m; ")
    probe = "from hindsight.core import native; print(native.lib is not None, repr(native.ERROR))"

    def run(code, mode):
        env = dict(os.environ)
        env.pop("HINDSIGHT_NATIVE", None)
        if mode is not None:
            env["HINDSIGHT_NATIVE"] = mode
        return subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True)

    for api in (0, 1, 2, native.API + 1):
        for mode in (None, "require"):
            r = run(fake(api) + probe, mode)
            assert r.returncode != 0 and "EngineMissing" in r.stderr and f"found API {api}" in r.stderr, (api, mode, r)
    r = run(probe, None)
    assert r.returncode == 0 and r.stdout.startswith("True None"), r
    print("PASS test_old_module_and_loader")


def main():
    tests = [test_old_module_and_loader, test_ends_are_exact, test_threads_agree, test_mixed_histories,
             test_damaged_blobs, test_crafted_blobs, test_empty_records_count_as_changed, test_hostile_calls,
             test_property]
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
