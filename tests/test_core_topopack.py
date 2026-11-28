"""#148 (24.3c): a recorded mesh's topology packed, hashed and unpacked (R069) in the engine, against
tests/reference/topopack.py bit for bit through tests/equiv.py: random arrays of every dtype and shape, hypothesis,
the topology of every snapshot in the corpus, hostile packed tuples and wrong types (results, exception class and
message). And a step's bytes don't change after it is handed to the journal writer or the packer: its JSON is taken
on the main thread at the hand-off (owner point 1, dev/RUST_PLAN.md). Run: <bpy python> tests/test_core_topopack.py"""
import ast
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

import tests.harness as H  # noqa: E402
from tests import corpus as C, equiv  # noqa: E402
from tests.reference import codec as rcodec, topopack as ref  # noqa: E402
from hindsight.core import native, step as S, store, topopack as T  # noqa: E402
from hindsight.core.types import DATA, MISSING, TOPO, ChannelDelta, DataChange, Entry, Snapshot, Step  # noqa: E402

RNG = np.random.default_rng(148)
try:
    import zstandard as _zs
    SAME_ZSTD = tuple(_zs.ZSTD_VERSION) == (1, 5, 7)     # the engine's libzstd (test_core_reference.ENGINE_ZSTD)
except Exception:
    SAME_ZSTD = False
DTYPES = (np.int32, np.uint32, np.int8, np.uint8, np.int16, np.uint16, np.int64, np.uint64, np.bool_, np.float32,
          np.float64)


def _packed(p):
    """A packed tuple, its blob as the payload where the codecs write other bytes for the same data: python-zstandard
    with another libzstd, or over 1 MB, where the engine's zstd takes long-distance matching (native/src/codec.rs)."""
    if not (isinstance(p, tuple) and len(p) == 5 and isinstance(p[0], bytes)):
        return p
    raw = rcodec.decompress(p[0])
    return p if SAME_ZSTD and len(raw) <= 1 << 20 else (raw,) + p[1:]


def outcome(fn, *a):
    try:
        return ("ok", fn(*a))
    except Exception as e:                          # noqa: BLE001
        return ("err", type(e).__name__, str(e))


def same_unpack(t, codec=False):
    """same("unpack_array", t). The codec's own errors (codec: a damaged or wrong blob) are the codec library's text,
    python-zstandard's or the engine's, so there the class is compared, as test_core_reference's
    test_codec_reads_like_the_reference does."""
    try:
        return same("unpack_array", t)
    except AssertionError:
        r, g = outcome(ref.unpack_array, t), outcome(T.unpack_array, t)
        if not (codec and r[0] == g[0] == "err" and r[1] == g[1]):
            raise


def key(r):
    return {k: _packed(v) for k, v in r.items()} if isinstance(r, dict) else _packed(r)


def same(fn, *args):
    return equiv.check(getattr(ref, fn), getattr(T, fn), *args, key=key)


def arrays():
    out = [np.zeros(0, np.int32), np.array([7], np.int32), np.zeros((0, 2), np.int32), np.array(5, np.int32)]
    for dt in DTYPES:
        info = np.iinfo(dt) if np.dtype(dt).kind in "iu" else None
        for n in (1, 2, 3, 257, 5000):
            if info is not None:
                a = RNG.integers(info.min, info.max, n, dtype=dt, endpoint=True)
            elif dt is np.bool_:
                a = RNG.integers(0, 2, n).astype(np.bool_)
            else:
                a = RNG.standard_normal(n).astype(dt)
            out += [a, np.sort(a)]
        out.append(np.arange(4000, dtype=dt).reshape(-1, 2) if dt is not np.bool_ else np.ones((3, 2), dt))
    big = np.arange(600000, dtype=np.int32).reshape(-1, 2)
    out += [big, big[::2], big.T, big.astype(">i4"), np.asfortranarray(big[:1000])]
    return out


def check_array(a):
    p = same("pack_array", a)
    same("hash_of", a)
    back = same("unpack_array", p)
    c = np.ascontiguousarray(a)
    if c.dtype.isnative:            # a byte-swapped array doesn't come back as it was from the reference either
        assert back.dtype == c.dtype and back.shape == c.shape and back.tobytes() == c.tobytes(), a.dtype
        got = T.unpack_array(T.pack_array(a))
        assert got.flags.writeable and got.tobytes() == c.tobytes()


def test_arrays_are_the_reference():
    for a in arrays():
        check_array(a)
    d = {k: a for k, a in zip(T.TOPO_KEYS, arrays()[8:12])}
    packed = same("pack_all", d)
    same("packed_bytes", T.pack_all(d))
    assert same("packed_bytes", packed) == sum(len(p[0]) for p in ref.pack_all(d).values())


def test_hypothesis():
    from hypothesis import given, settings, strategies as st
    from hypothesis.extra import numpy as hnp

    @settings(max_examples=300, deadline=None, database=None)
    @given(hnp.arrays(st.sampled_from(DTYPES), hnp.array_shapes(min_dims=0, max_dims=2, min_side=0, max_side=40)))
    def one(a):
        check_array(a)
    one()


def test_corpus_topology():
    n = 0
    for b in C.steps():
        try:
            st = S.step_from_bytes(b)
        except Exception:
            continue
        snaps = [s for s in (st.before, st.after) if s is not None]
        snaps += list(((st.extra or {}).get(S.SNAPSHOTS_KEY) or {}).values())
        for s in snaps:
            topo = {k: a for k, a in s.arrays.items() if k in T.TOPO_KEYS}
            for a in topo.values():
                check_array(a)
                n += 1
            if topo:
                same("pack_all", topo)
                same("packed_bytes", T.pack_all(topo))
    assert n > 20, n


def test_hostile():
    a = np.arange(1000, dtype=np.int32).reshape(-1, 2)
    blob, dt, shape, h, delta = p = ref.pack_array(a)
    cases = [blob[:-3], blob[:1], b"", bytes([blob[0] ^ 1]) + blob[1:], blob[:5] + bytes([blob[5] ^ 0xFF]) + blob[6:],
             bytearray(blob), None, 5]
    for c in cases:
        same_unpack((c, dt, shape, h, delta), codec=True)
    tuples = []
    tuples += [(blob, d, shape, h, delta) for d in ("x9", "<i8", "<f4", "|b1", ">i4", b"<i4", None, "<U4", "O")]
    tuples += [(blob, dt, s, h, delta) for s in ((501, 2), (499, 2), (1000,), (-500, 2), (2 ** 40,), (2 ** 62, 4),
                                               [500, 2], 1000, (500.0, 2), ("500", 2), None, (), (0,))]
    tuples += [(blob, dt, shape, h, dd) for dd in (False, 1, 0, None, "yes")]
    tuples += [(blob, "<f4", shape, h, True), p[:4], p + (1,), list(p), None, 5, "abcde", (), (b"", "<i4", (0,), h, True)]
    for t in tuples:
        same_unpack(t)
    for x in (None, 5, "abc", [1, 2, 3], [[1, 2], [3, 4]], np.array(["a", "b"]), np.array([None, 1], dtype=object), b"abcd",
              np.zeros(3, np.complex64), np.float32(3.0), np.arange(6, dtype=np.int32).reshape(2, 3)[:, ::2]):
        same("pack_array", x)
        same("hash_of", x)
    for d in (None, 5, [], {}, {"k": None}, {"k": [1, 2]}, {"k": "abc"}, {1: a}, {"a": a, "b": np.array([None], dtype=object)}):
        same("pack_all", d)
    good = ref.pack_all({"a": a})
    for d in (None, 5, [], {}, {"k": None}, {"k": ()}, {"k": (b"abc",)}, {"k": (5, 1)}, {"k": ("ab", 1)},
              {"k": [b"abc"]}, {"k": (bytearray(b"ab"),)}, good, {"a": good["a"], "b": 5}):
        same("packed_bytes", d)


def test_names_are_the_engines():
    """topopack.py is a facade: its names are the engine's and it holds no algorithm (no numpy, no def)."""
    N = native.ENGINE
    assert (T.pack_array, T.unpack_array, T.hash_of, T.pack_all, T.packed_bytes) == (
        N.topo_pack, N.topo_unpack, N.topo_hash, N.topo_pack_all, N.topo_packed_bytes)
    tree = ast.parse(open(os.path.join(ROOT, "hindsight", "core", "topopack.py"), encoding="utf-8").read())
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.Lambda, ast.For, ast.comprehension))]
    assert "numpy" not in {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}


# --------------------------------------------------------------------------- the hand-off (owner point 1)
def handed_step():
    """A step with everything Python in it: extra, records with MISSING, snapshots, deltas."""
    snap = Snapshot({"POINT": 4}, {"position|POINT|FLOAT_VECTOR": np.arange(12, dtype=np.float32)})
    d = ChannelDelta(".sculpt_mask|POINT|FLOAT", np.array([1, 3], np.uint32), np.array([[5], [9]], np.uint32),
                     (True, True), 1)
    recs = [DataChange("object", "Cube", {"location": ((0.0, 1.5, 2.0), (1.0, 1.5, 2.0)), "x": (MISSING, 3)})]
    return Step(seq=7, label="Stroke", mode="SCULPT", target="Cube", time=12.5, kind=TOPO, deltas=[d], before=snap,
                after=Snapshot({"POINT": 4}, {"position|POINT|FLOAT_VECTOR": np.ones(12, np.float32)}),
                extra={"note": [1, 2.5, "a"], S.SNAPSHOTS_KEY: {"u1": snap}}, records=recs)


def edit(st):
    """What a later edit can do to the Python side of a step that was handed over."""
    st.extra["note"].append("late")
    st.extra["more"] = {"k": 1.25}
    st.extra[S.SNAPSHOTS_KEY]["u2"] = st.after
    st.records[0].changes["late"] = (1, 2)
    st.records.append(DataChange("scene", "Scene", {"frame": (1, 2)}))
    st.label, st.mode, st.seq, st.kind = "Edited", "OBJECT", 99, DATA
    st.deltas.append(st.deltas[0])
    st.before = None


def test_bytes_fixed_at_the_journal_hand_off():
    from hindsight.bl import runtime
    from hindsight.core.types import JOURNAL_CODEC
    st = handed_step()
    want = S.step_to_bytes(handed_step(), JOURNAL_CODEC, 0)
    fn = runtime.step_payload(Entry(st))
    edit(st)
    assert S.step_to_bytes(st, JOURNAL_CODEC, 0) != want     # the edit does change a step's bytes
    assert fn() == want, "the journal's payload encoded the step as it was edited after the hand-off"


def test_bytes_fixed_at_the_packer_hand_off():
    st = handed_step()
    want = S.step_to_bytes(handed_step(), "zstd")
    p, e = store.Packer(recompress_idle=False), Entry(st)
    try:
        p.submit(e, "zstd")
        edit(st)                    # the worker waits QUIET_S after a submit, so this is before it encodes
        t0 = time.monotonic()
        p.drain(10.0)
        assert time.monotonic() - t0 < 10.0 and p.errors == 0, p.last_error
    finally:
        p.stop()
    assert e.step is None and bytes(e.blob) == want, "the packer encoded the step as it was edited after submit"


if __name__ == "__main__":     # test_core_journal_writer.py takes handed_step and edit
    for t in (test_arrays_are_the_reference, test_hypothesis, test_corpus_topology, test_hostile,
              test_names_are_the_engines, test_bytes_fixed_at_the_journal_hand_off,
              test_bytes_fixed_at_the_packer_hand_off):
        H.run(t)
    H.finish()
