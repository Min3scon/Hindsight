import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Storage engine tests: codec, delta, step, store. Plain script, no bpy."""
import threading
import time
import traceback

import numpy as np

from hindsight.core import codec, delta, step as stepmod, store
from hindsight.core.types import (CHANNEL_TYPES, DELTA, TOPO, JOIN, ChannelDelta, Entry, Settings,
                                  Snapshot, Step, POSITION_KEY, EDGE_VERTS_KEY, CORNER_VERT_KEY,
                                  CORNER_EDGE_KEY, FACE_OFFSETS_KEY, MASK_KEY, HIDE_VERT_KEY,
                                  bits_dtype, make_key)

RNG = np.random.default_rng(1234)


def rand_native(dtype, n):
    dt = np.dtype(dtype)
    if dt == np.bool_:
        return RNG.integers(0, 2, n).astype(np.bool_)
    if dt.kind == "f":
        a = RNG.standard_normal(n).astype(dt)
        # special bit patterns
        bits = a.view(np.uint32)
        if n >= 8:
            a[0] = np.nan
            a[1] = -0.0
            a[2] = np.inf
            a[3] = -np.inf
            bits[4] = 0x7FC00001  # NaN with payload
            bits[5] = 0xFFFFFFFF  # negative NaN, all bits
            bits[6] = 0x00000001  # denormal
        return a
    info = np.iinfo(dt)
    return RNG.integers(info.min, info.max, n, endpoint=True).astype(dt)


def mutate(arr, w, frac=0.1):
    new = arr.copy()
    count = arr.size // w
    k = max(1, int(count * frac))
    el = RNG.choice(count, k, replace=False)
    nb = delta.view_bits(new).reshape(-1, w)
    flip = RNG.integers(1, 255, (k, w)).astype(nb.dtype) if nb.dtype == np.uint8 else \
        RNG.integers(1, 2 ** 32 - 1, (k, w), dtype=np.uint64).astype(nb.dtype)
    if np.dtype(arr.dtype) == np.bool_:
        flip = np.ones((k, w), np.uint8)
    nb[el] ^= flip
    # special: turn some values into -0.0 / NaN patterns (float), bit exact
    if np.dtype(arr.dtype).kind == "f" and count > 10:
        nb[7, 0] = 0x80000000      # -0.0
        nb[8, 0] = 0x7FC00000      # canonical NaN
        nb[0, 0] = 0x7FA00000      # NaN -> different NaN payload (NaN != NaN but bits differ)
    return new


def bits_equal(a, b):
    return a.dtype == b.dtype and a.shape == b.shape and np.array_equal(delta.view_bits(a), delta.view_bits(b))


def deltas_equal(a: ChannelDelta, b: ChannelDelta):
    return (a.key == b.key and tuple(a.exists) == tuple(b.exists) and a.idx.dtype == b.idx.dtype == np.uint32
            and np.array_equal(a.idx, b.idx) and a.xor.dtype == b.xor.dtype and a.xor.shape == b.xor.shape
            and np.array_equal(a.xor, b.xor))


def all_keys():
    return [make_key(f"attr_{t.lower()}", "POINT", t) for t in CHANNEL_TYPES]


# ----------------------------------------------------------------------------- tests
def test_diff_apply_all_types():
    for key in all_keys():
        t = key.rsplit("|", 1)[1]
        dt, w, _ = CHANNEL_TYPES[t]
        old = rand_native(dt, 1000 * w)
        new = mutate(old, w)
        d = delta.diff(key, old, new)
        assert d is not None, key
        assert d.idx.dtype == np.uint32 and d.xor.dtype == bits_dtype(dt), (key, d.xor.dtype)
        assert d.xor.shape == (d.idx.size, w)
        assert np.all(np.diff(d.idx.astype(np.int64)) > 0)
        # exactly the changed elements
        ch = np.flatnonzero((delta.view_bits(old).reshape(-1, w) != delta.view_bits(new).reshape(-1, w)).any(1))
        assert np.array_equal(ch, d.idx), key
        buf = old.copy()
        delta.apply(buf, d)
        assert bits_equal(buf, new), key
        delta.apply(buf, d)  # twice restores
        assert bits_equal(buf, old), key
        assert delta.diff(key, old, old.copy()) is None
    print("PASS test_diff_apply_all_types")


def test_nan_negzero_bits():
    key = make_key("f", "POINT", "FLOAT")
    old = np.array([0.0, np.nan, 1.0, -0.0], np.float32)
    new = old.copy()
    new[0] = -0.0                                   # 0.0 == -0.0 numerically but bits differ
    new.view(np.uint32)[1] = 0x7FC00123             # other NaN payload
    d = delta.diff(key, old, new)
    assert d is not None and list(d.idx) == [0, 1], d
    buf = old.copy()
    delta.apply(buf, d)
    assert bits_equal(buf, new)
    # NaN unchanged bit-for-bit -> no delta (even though nan != nan)
    assert delta.diff(key, old, old.copy()) is None
    print("PASS test_nan_negzero_bits")


def test_exists_and_empty():
    key = MASK_KEY
    z = np.zeros(50, np.float32)
    d = delta.diff(key, z, z.copy(), exists=(False, True))
    assert d is not None and d.idx.size == 0 and d.xor.shape == (0, 1) and d.exists == (False, True)
    d2 = delta.unpack_delta(delta.pack_delta(d))
    assert deltas_equal(d, d2)
    buf = z.copy(); delta.apply(buf, d2); assert bits_equal(buf, z)
    new = z.copy(); new[3] = 0.5
    d = delta.diff(key, z, new, exists=(False, True))
    assert d.exists == (False, True) and list(d.idx) == [3]
    print("PASS test_exists_and_empty")


def test_pack_unpack_all_types():
    for key in all_keys():
        t = key.rsplit("|", 1)[1]
        dt, w, _ = CHANNEL_TYPES[t]
        for count, frac in ((1, 1.0), (300, 0.01), (70000, 0.3), (70000, 0.001)):
            old = rand_native(dt, count * w)
            new = mutate(old, w, frac)
            d = delta.diff(key, old, new, exists=(True, False))
            b = delta.pack_delta(d)
            assert delta.packed_size(b) == len(b)
            d2 = delta.unpack_delta(b)
            assert deltas_equal(d, d2), (key, count)
            buf = old.copy(); delta.apply(buf, d2); assert bits_equal(buf, new)
    # index gaps forcing uint8 / uint16 / uint32 diff storage
    for gap in (1, 255, 256, 65535, 65536, 3_000_000):
        idx = (np.arange(5, dtype=np.uint64) * gap + 7).astype(np.uint32)
        d = ChannelDelta(POSITION_KEY, idx, RNG.integers(0, 2 ** 32, (5, 3), dtype=np.uint64).astype(np.uint32))
        assert deltas_equal(d, delta.unpack_delta(delta.pack_delta(d))), gap
    # max uint32 index
    d = ChannelDelta(MASK_KEY, np.array([0, 2 ** 32 - 1], np.uint32), np.array([[1], [2]], np.uint32))
    assert deltas_equal(d, delta.unpack_delta(delta.pack_delta(d)))
    print("PASS test_pack_unpack_all_types")


def make_snapshot(nv=200, nf=50):
    counts = {"POINT": nv, "EDGE": nf * 2, "FACE": nf, "CORNER": nf * 4}
    arrays = {
        POSITION_KEY: rand_native(np.float32, nv * 3),
        EDGE_VERTS_KEY: RNG.integers(0, nv, nf * 4).astype(np.int32),
        CORNER_VERT_KEY: RNG.integers(0, nv, nf * 4).astype(np.int32),
        CORNER_EDGE_KEY: RNG.integers(0, nf * 2, nf * 4).astype(np.int32),
        FACE_OFFSETS_KEY: (np.arange(nf + 1) * 4).astype(np.int32),
        MASK_KEY: rand_native(np.float32, nv),
        HIDE_VERT_KEY: rand_native(np.bool_, nv),
        make_key("i8", "POINT", "INT8"): rand_native(np.int8, nv),
        make_key("col", "CORNER", "BYTE_COLOR"): rand_native(np.float32, nf * 16),
        make_key("uv", "CORNER", "FLOAT2"): rand_native(np.float32, nf * 8),
        make_key("ii", "EDGE", "INT32_2D"): rand_native(np.int32, nf * 4),
    }
    return Snapshot(counts, arrays)


def snaps_equal(a: Snapshot, b: Snapshot):
    if a.counts != b.counts or list(a.arrays) != list(b.arrays):
        return False
    return all(bits_equal(a.arrays[k], b.arrays[k]) and b.arrays[k].flags.writeable for k in a.arrays)


def make_delta_step(seq=1, n=500):
    deltas = []
    for key in all_keys():
        t = key.rsplit("|", 1)[1]
        dt, w, _ = CHANNEL_TYPES[t]
        old = rand_native(dt, n * w)
        deltas.append(delta.diff(key, old, mutate(old, w, 0.05)))
    deltas.append(delta.diff(MASK_KEY, np.zeros(n, np.float32), np.zeros(n, np.float32), (True, False)))
    return Step(seq, "Stroke", "SCULPT", "obj-a", time.time(), DELTA, deltas, extra={"brush": "Draw", "n": 3})


def steps_equal(a: Step, b: Step):
    assert (a.seq, a.label, a.mode, a.target, a.time, a.kind) == (b.seq, b.label, b.mode, b.target, b.time, b.kind)
    assert len(a.deltas) == len(b.deltas)
    for x, y in zip(a.deltas, b.deltas):
        assert deltas_equal(x, y), x.key
    for sa, sb in ((a.before, b.before), (a.after, b.after)):
        assert (sa is None) == (sb is None)
        if sa is not None:
            assert snaps_equal(sa, sb)
    ea, eb = dict(a.extra), dict(b.extra)
    sna, snb = ea.pop("_snapshots", None), eb.pop("_snapshots", None)
    assert ea == eb, (ea, eb)
    assert (sna is None) == (snb is None)
    if sna is not None:
        assert list(sna) == list(snb)
        for uid in sna:
            assert snaps_equal(sna[uid], snb[uid]), uid
    return True


def test_snapshot_roundtrip():
    s = make_snapshot()
    s2 = stepmod.snapshot_from_bytes(stepmod.snapshot_to_bytes(s))
    assert snaps_equal(s, s2)
    for k in s.arrays:
        assert s2.arrays[k].dtype == s.arrays[k].dtype, k
    empty = Snapshot({"POINT": 0, "EDGE": 0, "FACE": 0, "CORNER": 0}, {FACE_OFFSETS_KEY: np.zeros(1, np.int32)})
    assert snaps_equal(empty, stepmod.snapshot_from_bytes(stepmod.snapshot_to_bytes(empty)))
    print("PASS test_snapshot_roundtrip")


def test_step_roundtrip_all_codecs():
    names = codec.available()
    assert "zlib" in names and "none" in names
    for name in names + ["auto"]:
        st = make_delta_step()
        b = stepmod.step_to_bytes(st, name)
        assert b[:4] == b"HSS1"
        assert steps_equal(st, stepmod.step_from_bytes(b)), name
        topo = Step(2, "Voxel Remesh", "SCULPT", "obj-a", 1.5, TOPO, [], make_snapshot(), make_snapshot(300, 70))
        assert steps_equal(topo, stepmod.step_from_bytes(stepmod.step_to_bytes(topo, name)))
        join = Step(3, "Join", "OBJECT", "obj-a", 2.0, JOIN, [], make_snapshot(), make_snapshot(),
                    extra={"members": [{"uid": "m1", "name": "B", "matrix": [1.0] * 16}], "map_attr": "_hs_src",
                           "_snapshots": {"m1": make_snapshot(40, 10), "m2": make_snapshot(10, 3)}})
        jb = stepmod.step_to_bytes(join, name)
        assert steps_equal(join, stepmod.step_from_bytes(jb))
        assert isinstance(stepmod.step_from_bytes(jb).extra["_snapshots"]["m1"], Snapshot)
        assert join.extra["_snapshots"]["m1"].arrays  # input not mutated
        empty = Step(4, "Nothing", "SCULPT", "obj-a")
        assert steps_equal(empty, stepmod.step_from_bytes(stepmod.step_to_bytes(empty, name)))
    print("PASS test_step_roundtrip_all_codecs")


def test_entry_pack_load():
    st = make_delta_step(7)
    e = Entry(st)
    raw_n = e.nbytes()
    assert not e.is_packed()
    e.pack("auto")
    assert e.is_packed() and e.blob is not None and e.step is None
    assert e.nbytes() < raw_n
    assert steps_equal(st, e.load())
    assert e.to_bytes() is e.blob
    assert e.meta["seq"] == 7
    print("PASS test_entry_pack_load")


def test_codec_fallback():
    data = bytes(range(256)) * 100
    assert codec.resolve("bogus") == "zlib"
    assert codec.resolve("auto") == codec.available()[0]
    for name in ("zstd", "lz4", "zlib", "none", "bogus", "auto", "ZSTD"):
        blob = codec.compress(data, name)
        assert codec.decompress(blob) == data, name
        exp = codec.resolve(name)
        assert codec.codec_of(blob) == exp, (name, codec.codec_of(blob), exp)
    # unavailable codec simulated
    saved = codec._zstd, codec._lz4f
    try:
        codec._zstd = None
        codec._lz4f = None
        assert codec.available() == ["zlib", "none"]
        assert codec.resolve("zstd") == "zlib" and codec.resolve("lz4") == "zlib"
        assert codec.resolve("auto") == "zlib"
        b = codec.compress(data, "zstd")
        assert b[0] == 1 and codec.decompress(b) == data
        st = make_delta_step()
        assert steps_equal(st, stepmod.step_from_bytes(stepmod.step_to_bytes(st, "zstd")))
    finally:
        codec._zstd, codec._lz4f = saved
    print(f"PASS test_codec_fallback (available={codec.available()})")


class FakeHistory:
    def __init__(self):
        self.entries = []
        self.cursor = 0

    def push(self, st):
        self.entries.append(Entry(st))
        self.cursor = len(self.entries)

    def drop_oldest(self, n):
        n = min(n, len(self.entries))
        del self.entries[:n]
        self.cursor = max(0, self.cursor - n)
        return n


def tiny_step(seq, target="a", n=4):
    idx = np.arange(n, dtype=np.uint32)
    return Step(seq, "Stroke", "SCULPT", target, 0.0, DELTA,
                [ChannelDelta(MASK_KEY, idx, np.ones((n, 1), np.uint32))])


def test_packer_concurrent():
    p = store.Packer()
    try:
        steps = [make_delta_step(i, 200) for i in range(200)]
        entries = [Entry(s) for s in steps]
        stop = threading.Event()
        bad = []

        def reader():  # main-thread-like reads during packing must always see a valid step
            while not stop.is_set():
                for i in range(0, 200, 17):
                    try:
                        s = entries[i].load()
                        if s.seq != i:
                            bad.append(i)
                    except Exception as ex:
                        bad.append(repr(ex))

        th = threading.Thread(target=reader)
        th.start()
        for e in entries:
            p.submit(e, "auto")
        for e in entries[:50]:
            p.submit(e, "auto")  # duplicate submissions are ignored / harmless
        p.drain(timeout=60)
        stop.set(); th.join()
        assert p.pending() == 0 and p.errors == 0, p.last_error
        assert not bad, bad[:5]
        assert all(e.is_packed() for e in entries)
        for s, e in zip(steps, entries):
            assert steps_equal(s, e.load())
        # drain with nothing pending returns immediately
        t0 = time.perf_counter(); p.drain(); assert time.perf_counter() - t0 < 0.5
    finally:
        p.stop()
    assert not p._thread.is_alive()
    # after stop, submit packs synchronously
    e = Entry(tiny_step(1)); p.submit(e, "zlib"); assert e.is_packed()
    print("PASS test_packer_concurrent")


def test_enforce_tiers():
    h1, h2 = FakeHistory(), FakeHistory()
    for i in range(100):
        h1.push(tiny_step(i))
    for i in range(10):
        h2.push(tiny_step(100 + i))
    s = Settings(raw_steps=64)
    store.enforce_tiers([h1, h2], s)
    assert [e.is_packed() for e in h1.entries] == [True] * 36 + [False] * 64
    assert not any(e.is_packed() for e in h2.entries)
    h3 = FakeHistory()
    for i in range(100):
        h3.push(tiny_step(i))
    p = store.Packer()
    try:
        store.enforce_tiers([h3], Settings(raw_steps=10), p)
        p.drain(30)
    finally:
        p.stop()
    assert [e.is_packed() for e in h3.entries] == [True] * 90 + [False] * 10
    assert h3.entries[0].load().deltas[0].xor.sum() == 4
    print("PASS test_enforce_tiers")


def test_caps_max_steps():
    h = FakeHistory()
    for i in range(10_050):
        h.push(tiny_step(i))
    s = Settings(max_steps=10_000, memory_limit_mb=4096)
    n = store.enforce_caps([h], s)
    assert n == 50, n
    assert len(h.entries) == 10_000 and h.entries[0].seq == 50 and h.entries[-1].seq == 10_049
    assert h.cursor == 10_000
    assert store.enforce_caps([h], s) == 0
    print("PASS test_caps_max_steps")


def test_caps_memory():
    a, b = FakeHistory(), FakeHistory()
    # interleaved seqs: a gets even, b odd
    for i in range(400):
        (a if i % 2 == 0 else b).push(tiny_step(i, "a" if i % 2 == 0 else "b", n=1000))
    per = a.entries[0].nbytes()
    tot = store.total_bytes([a, b])
    assert tot == sum(e.nbytes() for e in a.entries + b.entries) and tot == per * 400
    limit_mb = 1
    s = Settings(max_steps=10_000, memory_limit_mb=limit_mb)
    n = store.enforce_caps([a, b], s)
    assert store.total_bytes([a, b]) <= limit_mb * 2 ** 20
    keep = (limit_mb * 2 ** 20) // per
    assert n == 400 - keep, (n, keep)
    remaining = sorted(e.seq for e in a.entries + b.entries)
    assert remaining == list(range(400 - keep, 400)), remaining[:5]  # oldest seqs dropped first
    print("PASS test_caps_memory")


def test_caps_memory_skewed():
    c, d = FakeHistory(), FakeHistory()
    for i in range(100):
        c.push(tiny_step(i, "c", 10_000))       # ~80 KB raw each
    for i in range(100, 130):
        d.push(tiny_step(i, "d", 10_000))
    per = c.entries[0].nbytes()
    n = store.enforce_caps([c, d], Settings(memory_limit_mb=8))
    keep = (8 * 2 ** 20) // per
    assert n == 130 - keep
    assert len(d.entries) == 30 and len(c.entries) == keep - 30   # all drops from the older history
    assert c.entries[0].seq == 100 - (keep - 30)
    assert c.cursor == len(c.entries)
    print("PASS test_caps_memory_skewed")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.stdout.flush()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
