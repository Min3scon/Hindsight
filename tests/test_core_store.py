import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R
"""Storage engine tests: codec, delta, step, store. Plain script, no bpy."""
import threading
import time

import numpy as np

from hindsight.core import codec, delta, step as stepmod, store
from hindsight.core.types import (ADD, XOR, CHANNEL_TYPES, DELTA, TOPO, JOIN, ChannelDelta, Entry, Settings,
                                  Snapshot, Step, POSITION_KEY, EDGE_VERTS_KEY, CORNER_VERT_KEY,
                                  CORNER_EDGE_KEY, FACE_OFFSETS_KEY, MASK_KEY, HIDE_VERT_KEY,
                                  bits_dtype, make_key)

sys.path.insert(0, os.path.dirname(__file__))         # tests/: the reference copies (Blender's --python adds no path)
from reference import delta as rdelta                 # noqa: E402

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
            and np.array_equal(a.idx, b.idx) and a.vals.dtype == b.vals.dtype and a.vals.shape == b.vals.shape
            and np.array_equal(a.vals, b.vals))


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
        assert d.idx.dtype == np.uint32 and d.vals.dtype == bits_dtype(dt), (key, d.vals.dtype)
        assert d.vals.shape == (d.idx.size, w)
        assert np.all(np.diff(d.idx.astype(np.int64)) > 0)
        # exactly the changed elements
        ch = np.flatnonzero((delta.view_bits(old).reshape(-1, w) != delta.view_bits(new).reshape(-1, w)).any(1))
        assert np.array_equal(ch, d.idx), key
        assert d.op == ADD
        buf = old.copy()
        delta.apply(buf, d)
        assert bits_equal(buf, new), key
        delta.apply(buf, d, forward=False)  # backwards restores
        assert bits_equal(buf, old), key
        # format 2 deltas (XOR) still apply the same way in both directions
        x = ChannelDelta(key, d.idx, delta.as_xor(d, old), d.exists, XOR)
        delta.apply(buf, x)
        assert bits_equal(buf, new), key
        delta.apply(buf, x, forward=False)
        assert bits_equal(buf, old), key
        assert delta.diff(key, old, old.copy()) is None


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


def test_exists_and_empty():
    key = MASK_KEY
    z = np.zeros(50, np.float32)
    d = delta.diff(key, z, z.copy(), exists=(False, True))
    assert d is not None and d.idx.size == 0 and d.vals.shape == (0, 1) and d.exists == (False, True)
    d2 = delta.unpack_delta(delta.pack_delta(d))
    assert deltas_equal(d, d2)
    buf = z.copy(); delta.apply(buf, d2); assert bits_equal(buf, z)
    new = z.copy(); new[3] = 0.5
    d = delta.diff(key, z, new, exists=(False, True))
    assert d.exists == (False, True) and list(d.idx) == [3]


def test_pack_unpack_all_types():
    for key in all_keys():
        t = key.rsplit("|", 1)[1]
        dt, w, _ = CHANNEL_TYPES[t]
        for count, frac in ((1, 1.0), (300, 0.01), (70000, 0.3), (70000, 0.001)):
            old = rand_native(dt, count * w)
            new = mutate(old, w, frac)
            d = delta.diff(key, old, new, exists=(True, False))
            b = delta.pack_delta(d)
            assert rdelta.packed_size(b) == len(b)
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


def test_topology_step_written_without_full_copies():
    """Memory work (PERF_LOG "Memory, and the Rust question"): snapshot blobs are joined straight
    from the arrays, so they're the same bytes as before, and writing a topology step peaks at most
    3 times its size (it was 3.8 to 5 times: copies of each snapshot, the body, the blob)."""
    import tracemalloc
    s = make_snapshot()
    blob = stepmod.snapshot_to_bytes(s)
    head_len = int.from_bytes(blob[4:8], "little")
    assert blob[:4] == b"HSN1"
    assert blob[8 + head_len:] == b"".join(np.ascontiguousarray(a).tobytes() for a in s.arrays.values())
    big = make_snapshot(nv=400_000, nf=100_000)
    big2 = make_snapshot(nv=400_000, nf=100_000)
    st = Step(seq=3, label="Remesh", mode="SCULPT", target="o", kind=TOPO, before=big, after=big2,
              extra={"_snapshots": {"x": make_snapshot()}, "op": 2})
    size = big.nbytes() + big2.nbytes()
    verify, stepmod.VERIFY = stepmod.VERIFY, False
    try:
        for name in codec.available():
            for effort in (delta.QUICK, delta.NORMAL):
                tracemalloc.start()
                b = stepmod.step_to_bytes(st, name, effort)
                peak = tracemalloc.get_traced_memory()[1]
                tracemalloc.stop()
                assert steps_equal(st, stepmod.step_from_bytes(b)), name
                assert peak < 3.2 * size, (name, effort, peak / size)
    finally:
        stepmod.VERIFY = verify


def test_step_roundtrip_all_codecs():
    names = codec.available()
    assert "zlib" in names and "none" in names
    for name in names + ["auto"]:
        st = make_delta_step()
        b = stepmod.step_to_bytes(st, name)
        assert b[:4] == b"HSS3"
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


def test_codec_fallback():
    data = bytes(range(256)) * 100
    assert codec.resolve("bogus") == "zlib"
    assert codec.resolve("auto") == codec.available()[0]
    for name in ("zstd", "lz4", "zlib", "none", "bogus", "auto", "ZSTD"):
        blob = codec.compress(data, name)
        assert codec.decompress(blob) == data, name
        exp = codec.resolve(name)
        assert codec.codec_of(blob) == exp, (name, codec.codec_of(blob), exp)
    # the engine has every codec built in: nothing to simulate as missing, an unknown name is zlib
    assert codec.available() == ["zstd", "lz4", "zlib", "none"]
    assert codec.resolve("auto") == "zstd" and codec.resolve("bogus") == "zlib" and codec.resolve(None) == "zstd"
    b = codec.compress(data, "bogus")
    assert b[0] == 1 and codec.decompress(b) == data
    st = make_delta_step()
    for name in ("zstd", "lz4", "zlib", "none", "bogus"):
        assert steps_equal(st, stepmod.step_from_bytes(stepmod.step_to_bytes(st, name))), name
    print(f"  test_codec_fallback (available={codec.available()})")


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
    assert not p._alive
    # after stop, submit packs synchronously
    e = Entry(tiny_step(1)); p.submit(e, "zlib"); assert e.is_packed()


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
    assert h3.entries[0].load().deltas[0].vals.sum() == 4


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
    keep = int(limit_mb * 2 ** 20 * store.EVICT_TO) // per      # it drops to a little under the limit
    assert n == 400 - keep, (n, keep)
    assert store.enforce_caps([a, b], s) == 0, "under the limit again: nothing more to drop"
    remaining = sorted(e.seq for e in a.entries + b.entries)
    assert remaining == list(range(400 - keep, 400)), remaining[:5]  # oldest seqs dropped first


def test_caps_memory_skewed():
    c, d = FakeHistory(), FakeHistory()
    for i in range(100):
        c.push(tiny_step(i, "c", 10_000))       # ~80 KB raw each
    for i in range(100, 130):
        d.push(tiny_step(i, "d", 10_000))
    per = c.entries[0].nbytes()
    n = store.enforce_caps([c, d], Settings(memory_limit_mb=8))
    keep = int(8 * 2 ** 20 * store.EVICT_TO) // per
    assert n == 130 - keep
    assert len(d.entries) == 30 and len(c.entries) == keep - 30   # all drops from the older history
    assert c.entries[0].seq == 100 - (keep - 30)
    assert c.cursor == len(c.entries)


def test_caps_never_drop_at_or_past_the_cursor():
    """The memory limit dropped steps an object scrubbed back had not reached: its cursor was
    clamped to 0 while its mesh still showed a later state, and going forward gave a mesh that
    never existed."""
    from hindsight.core.history import History, MeshState
    key = POSITION_KEY
    n = 3000

    def make(uid, steps, seq0):
        h = History(uid)
        arr = np.zeros(n * 3, np.float32)
        states = [arr.copy()]
        for i in range(steps):
            new = arr.copy()
            new[i * 900:(i + 1) * 900] += 1.0
            h.push(Step(seq0 + i, "s", "SCULPT", uid, deltas=[delta.diff(key, arr, new)]))
            arr = new
            states.append(arr.copy())
        return h, states

    b, states_b = make("B", 6, 1)                    # the older history
    st = {"B": MeshState({"POINT": n}, {key: states_b[6].copy()})}
    b.goto(1, st)                                    # scrubbed back: the mesh shows the state after step 1
    a, _ = make("A", 3, 100)
    per = b.entries[0].nbytes()
    total = store.total_bytes([b, a])

    class Tight:
        max_steps = 10_000
        memory_limit_mb = (total - 3.5 * per) / (1 << 20)     # asks for 4 of B's steps

    stats = {}
    store.enforce_caps([b, a], Tight, stats)
    assert b.cursor == 0 and len(b.entries) == 5, (b.cursor, len(b.entries))    # only the step before the cursor went
    # the rest of what the limit asks for comes from A, which is at its end: its past may go
    assert a.cursor == len(a.entries) and stats["memory_dropped"] == 1 + (3 - len(a.entries)), stats
    b.goto(len(b.entries), st)
    assert np.array_equal(st["B"].arrays[key], states_b[6]), "going forward no longer reaches the real last state"
    # the step cap keeps to the cursor too (the cap lowered while an object is scrubbed back)
    c, states_c = make("C", 6, 200)
    st = {"C": MeshState({"POINT": n}, {key: states_c[6].copy()})}
    c.goto(2, st)
    assert store.enforce_caps([c], Settings(max_steps=3, memory_limit_mb=4096)) == 2
    assert c.cursor == 0 and len(c.entries) == 4
    c.goto(len(c.entries), st)
    assert np.array_equal(st["C"].arrays[key], states_c[6])


def _caps_world(seed, a_n=(5_000, 40_000)):
    """Histories for the CapsMemo tests: A, big and scrubbed back to the start (nothing in it may
    be dropped), B and C recording."""
    from hindsight.core.history import History
    rng = np.random.default_rng(seed)
    hs = []
    seq = [0]

    def step(target, n):
        seq[0] += 1
        idx = np.arange(n, dtype=np.uint32)
        return Step(seq[0], "Stroke", "SCULPT", target, 0.0, DELTA,
                    [ChannelDelta(MASK_KEY, idx, rng.integers(0, 2**32, (n, 1), dtype=np.uint32))])
    for uid, k in (("A", 30), ("B", 3), ("C", 2)):
        h = History(uid, [uid])
        for _ in range(k):
            h.push(step(uid, int(rng.integers(*a_n))))
        hs.append(h)
    hs[0].cursor = 0
    return hs, step, rng


def _caps_state(hs):
    return [(h.uid, h.cursor, [(e.seq, e.is_packed(), e.nbytes()) for e in h.entries]) for h in hs]


class _InlinePacker:
    """Packs at once and counts like store.Packer (CapsMemo watches the counts)."""
    def __init__(self):
        self.packed = self.recompressed = self.errors = 0

    def submit(self, entry, codec):
        if entry.step is not None:
            entry.pack(codec)
            self.packed += 1


def test_caps_memo_stuck_between_target_and_limit():
    """S4-3, the narrow case: what can't go is over 95% of the limit (the target) but under the
    limit. Two new steps whose newest alone still fits: the whole pass drops both (it drops down to
    the target), and so must the short pass."""
    import copy
    from hindsight.core.history import History
    settings = Settings(memory_limit_mb=1, raw_steps=64, compression="zlib")
    limit = 1 << 20
    target = int(limit * store.EVICT_TO)

    def step(seq, target_uid, n):
        idx = np.arange(n, dtype=np.uint32)
        return Step(seq, "Stroke", "SCULPT", target_uid, 0.0, DELTA,
                    [ChannelDelta(MASK_KEY, idx, np.full((n, 1), seq, np.uint32))])
    a = History("A", ["A"])
    for i in range(10):
        a.push(step(i + 1, "A", 1000))
    want = (target + limit) // 2                    # A's total: between the target and the limit
    extra = want - store.total_bytes([a])
    a.push(step(11, "A", 1000 + extra // 8))        # 8 bytes per element (index and value)
    a.cursor = 0
    b = History("B", ["B"])
    hs = [a, b]
    others = store.total_bytes(hs)
    assert target < others <= limit, (target, others, limit)
    small = (limit - others) // 16                  # elements: fits the limit on its own
    for memo in (store.CapsMemo(), None):
        h1 = copy.deepcopy(hs)
        h1[1].push(step(19, "B", 20_000))         # over the limit: B's step goes, A's can't
        st = {}
        store.enforce(h1, settings, None, st, memo, h1[1])
        assert st["total"] == others and not h1[1].entries
        assert memo is None or memo.state is not None, "this is the stuck case"
        h1[1].push(step(20, "B", small))
        h1[1].push(step(21, "B", small))
        assert store.total_bytes(h1) > limit and others + h1[1].entries[1].nbytes() <= limit
        st = {}
        store.enforce(h1, settings, None, st, memo, h1[1])
        assert len(h1[1].entries) == 0 and st["memory_dropped"] == 2, (memo, len(h1[1].entries), st)


def test_enforce_reports_drops_before_packing():
    """The crash journal must hear of every drop (runtime._journal_drop) even when packing then
    fails, as when the drops were reported between enforce_caps and enforce_tiers."""
    hs, step, rng = _caps_world(5)
    hs[0].cursor = len(hs[0].entries)

    class Broken:
        packed = recompressed = errors = 0

        def submit(self, entry, codec):
            raise RuntimeError("packing failed")
    heard = []
    try:
        store.enforce(hs, Settings(memory_limit_mb=1, raw_steps=0), Broken(), {}, store.CapsMemo(), hs[1],
                      on_caps=lambda drops, st: heard.extend((h.uid, n) for h, n in drops))
    except RuntimeError:
        pass
    else:
        raise AssertionError("packing should have failed")
    assert heard and heard[0][0] == "A", heard


def test_caps_memo_same_as_whole_pass():
    """S4-3: over the memory limit with nothing left that may go, a pass looks only at the history
    that changed (store.CapsMemo). It must drop exactly what the whole pass drops, in every case:
    strokes, a cursor moved forward or back, a new history, steps undone, other settings, the
    packer changing a step's size. As in runtime._enforce, only a new step is followed by a pass,
    told which history got it; everything else happens between passes, unannounced."""
    import copy
    from hindsight.core.history import History
    used = [0]
    real = store.CapsMemo.usable         # enforce takes the short pass exactly when the memo is usable

    def counted(self, *a, **k):
        r = real(self, *a, **k)
        used[0] += bool(r)
        return r
    store.CapsMemo.usable = counted
    try:
        for seed in range(16):
            # A's size: from well over the limit to just about it, so a stuck total lands both
            # above the limit and between the target (95%) and the limit
            hs, step, rng = _caps_world(seed, ((5_000, 40_000) if seed % 2 else (3_000, 6_000)))
            ref = copy.deepcopy(hs)                  # the same histories, always the whole pass
            pk1, pk2 = (_InlinePacker(), _InlinePacker()) if seed % 4 >= 2 else (None, None)
            settings = Settings(memory_limit_mb=1, max_steps=40, raw_steps=4, compression="zlib")
            memo = store.CapsMemo()
            for i in range(300):
                op = rng.random()
                k = None
                if op < 0.75:                                 # strokes, rarely on the scrubbed-back A
                    k = int(rng.integers(1, len(hs))) if rng.random() < 0.95 else 0
                    for _ in range(int(rng.integers(1, 4))):
                        st = step(hs[k].uid, int(rng.integers(200, 30_000)))
                        hs[k].push(st)
                        ref[k].push(copy.deepcopy(st))
                elif op < 0.79:                               # a jump, back or forward
                    j = int(rng.integers(0, len(hs))) if rng.random() < 0.5 else 0
                    c = int(rng.integers(0, len(hs[j].entries) + 1))
                    hs[j].cursor = ref[j].cursor = c
                elif op < 0.9:
                    uid = f"N{i}"
                    h1, h2 = History(uid, [uid]), History(uid, [uid])
                    st = step(uid, 20_000)
                    h1.push(st)
                    h2.push(copy.deepcopy(st))
                    hs.append(h1)
                    ref.append(h2)
                    k = len(hs) - 1
                elif op < 0.93:
                    j = int(rng.integers(0, len(hs)))
                    hs[j].truncate_future()
                    ref[j].truncate_future()
                elif op < 0.96 and pk1 is not None:           # the packer packs a step somewhere
                    j = int(rng.integers(0, len(hs)))
                    if hs[j].entries:
                        x = int(rng.integers(0, len(hs[j].entries)))
                        pk1.submit(hs[j].entries[x], "zlib")
                        pk2.submit(ref[j].entries[x], "zlib")
                else:
                    settings.memory_limit_mb = int(rng.integers(1, 3))
                    settings.raw_steps = int(rng.integers(0, 5))
                if k is None:
                    continue
                s1, s2 = {}, {}
                d1 = store.enforce(hs, settings, pk1, s1, memo, hs[k])
                d2 = store.enforce(ref, settings, pk2, s2)
                assert _caps_state(hs) == _caps_state(ref), (seed, i)
                assert [(h.uid, n) for h, n in d1] == [(h.uid, n) for h, n in d2], (seed, i)
                assert s1["memory_dropped"] == s2["memory_dropped"] and s1["total"] == s2["total"], (seed, i, s1, s2)
                if pk1 is None:
                    assert s1["total"] == store.total_bytes(hs)
        print("  short passes:", used[0])
        assert used[0] > 60, f"the short pass was hardly used ({used[0]}): the test doesn't test it"
    finally:
        store.CapsMemo.usable = real


def test_caps_memo_reads_only_the_changed_history():
    """S4-3: the pass after a stroke, while stuck over the limit, read the size of every step."""
    hs, step, rng = _caps_world(99)
    settings = Settings(memory_limit_mb=1, raw_steps=4, compression="zlib")
    memo = store.CapsMemo()
    store.enforce(hs, settings, None, {}, memo, None)
    a = hs[0]
    assert a.cursor == 0 and len(a.entries) == 30 and store.total_bytes(hs) > (1 << 20)
    for i in range(5):
        hs[1].push(step("B", 20_000))
        calls = []
        real = Entry.nbytes
        Entry.nbytes = lambda self: calls.append(id(self)) or real(self)
        try:
            store.enforce(hs, settings, None, {}, memo, hs[1])
        finally:
            Entry.nbytes = real
        mine = {id(e) for e in a.entries}
        assert not mine & set(calls), "the scrubbed-back history's steps were read again"
        assert len(hs[1].entries) == 0, "the new step still goes (the limit is not weakened)"
    a.cursor = 10                                    # scrubbed forward: its first 10 steps may go now
    hs[1].push(step("B", 20_000))
    st = {}
    store.enforce(hs, settings, None, st, memo, hs[1])
    assert st["memory_dropped"] > 0 and a.entries[0].seq > 1, "A's past must go first (oldest first)"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        R.run(t)
    R.finish()


if __name__ == "__main__":
    main()
