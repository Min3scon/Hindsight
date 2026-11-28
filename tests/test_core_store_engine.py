"""#154 (24.5a): the caps, the tiers and the caps memo run in the engine. hindsight/core/store.py's enforce_tiers,
enforce_caps, total_bytes, enforce, CapsMemo, _sig, _packer_key and _can_gain (the engine's) against
tests/reference/store.py, bit for bit: two worlds built alike, one per side, get the same calls drawn by hypothesis
(random histories, cursors, cap settings, a packer or none, the memo and the one changed history), and must give the
same result or exception (class and message), the same stats and memo state, the same calls on every history, entry
and packer in the same order (what is read, packed, submitted and dropped), and entries with the same bytes. Entries
are real (packed by the engine) or duck-typed with hostile sizes and seqs; histories answer drop_oldest with a count,
None, junk or an exception. _can_gain over the corpus's steps cut at every length and with flipped bytes, and hostile
input, through tests/equiv.py.
Run: <bpy python> tests/test_core_store_engine.py"""
import ast
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tests.runner as R  # noqa: E402
from tests import corpus as C, equiv  # noqa: E402
from tests.reference import store as ref  # noqa: E402
from hindsight.core import store  # noqa: E402
from hindsight.core.types import DELTA, ChannelDelta, Entry, MASK_KEY, Step  # noqa: E402

SIDES = (ref, store)


# ------------------------------------------------------------------------------ a world that logs every call
class TE(Entry):
    """A real entry that logs what the caps and tiers read and call on it."""
    def nbytes(self):
        r = Entry.nbytes(self)
        self.log.append(("nbytes", self.tag, r))
        return r

    def pack(self, codec=None):
        self.log.append(("pack", self.tag, codec))
        return Entry.pack(self, codec)

    @property
    def step(self):
        self.log.append(("step", self.tag))
        return Entry.step.__get__(self)

    @step.setter
    def step(self, v):
        Entry.step.__set__(self, v)

    @property
    def meta(self):
        self.log.append(("meta", self.tag))
        return Entry.meta.__get__(self)


class FE:
    """A duck-typed entry: its size and seq are whatever the test gives (numpy ints, floats, None, strings)."""
    def __init__(self, log, tag, size, seq, packed_size):
        self.log, self.tag, self._size, self._seq, self._packed = log, tag, size, seq, packed_size
        self._step = object()

    def nbytes(self):
        self.log.append(("nbytes", self.tag))
        if isinstance(self._size, BaseException):
            raise self._size
        return self._size

    def pack(self, codec=None):
        self.log.append(("pack", self.tag, codec))
        if codec == "bogus":
            raise ValueError(f"unknown codec {codec!r}")
        self._step, self._size = None, self._packed

    @property
    def step(self):
        self.log.append(("step", self.tag))
        return self._step

    @property
    def meta(self):
        self.log.append(("meta", self.tag))
        return {"seq": self._seq} if self._seq is not KeyError else {}


class TH:
    """A duck-typed history: entries, cursor, uid and drop_oldest, logged."""
    def __init__(self, log, uid, entries, cursor, ret):
        self.log, self._uid, self._entries, self._cursor, self._ret = log, uid, entries, cursor, ret

    @property
    def entries(self):
        self.log.append(("entries", self._uid))
        return self._entries

    @property
    def cursor(self):
        self.log.append(("cursor", self._uid))
        return self._cursor

    @property
    def uid(self):
        self.log.append(("uid", self._uid))
        return self._uid

    def drop_oldest(self, n):
        self.log.append(("drop_oldest", self._uid, n))
        k = min(int(n), len(self._entries))
        del self._entries[:k]
        self._cursor = max(0, int(self._cursor) - k)
        r = self._ret
        if r == "raise":
            raise RuntimeError("drop failed")
        return {"n": k, "none": None, "np": np.int64(k), "junk": "x"}[r]


class TP:
    """A packer: counts like store.Packer; packs at once or only notes the submit."""
    def __init__(self, log, inline):
        self.log, self.inline = log, inline
        self.packed = self.recompressed = self.errors = 0

    def submit(self, entry, codec):
        self.log.append(("submit", entry.tag, codec))
        if self.inline:
            entry.pack(codec)
            self.packed += 1


class S:
    """Settings, any values (Settings is a dataclass; the caps read four of its fields)."""
    def __init__(self, max_steps, memory_limit_mb, raw_steps, compression):
        self.max_steps, self.memory_limit_mb, self.raw_steps, self.compression = (max_steps, memory_limit_mb,
                                                                                  raw_steps, compression)


_STEPS = {}


def _step(seq, n):
    if (seq, n) not in _STEPS:
        rng = np.random.default_rng(seq * 7919 + n)
        idx = np.arange(n, dtype=np.uint32)
        _STEPS[seq, n] = (idx, rng.integers(0, 2 ** 32, (n, 1), dtype=np.uint32))
    idx, vals = _STEPS[seq, n]
    return Step(seq, "Stroke", "SCULPT", "obj", 0.0, DELTA, [ChannelDelta(MASK_KEY, idx.copy(), vals.copy())])


class World:
    def __init__(self, spec, side):
        self.log, self.keep, self.side = [], [], side
        self.hs = [self._history(h) for h in spec["hs"]]
        self.settings = S(*spec["settings"])
        self.packer = None
        self.memo = side.CapsMemo()
        self.ids = {}

    def _entry(self, e):
        kind, tag, a, b = e
        if kind == "real":
            x = TE(_step(a, b))
            x.log, x.tag = self.log, tag
        else:
            x = FE(self.log, tag, a, b, a)
        self.keep.append(x)             # no id is reused while the world lives (_sig holds ids)
        return x

    def _history(self, h):
        uid, entries, cursor, ret = h
        x = TH(self.log, uid, [self._entry(e) for e in entries], cursor, ret)
        self.keep.append(x)
        self.keep.append(x._entries)
        return x

    def names(self):
        """id -> name of every history, entries list and entry made so far (for _sig and the memo)."""
        out = {}
        for x in self.keep:
            out[id(x)] = (type(x).__name__, getattr(x, "tag", getattr(x, "_uid", None)))
        return out

    def state(self):
        out = []
        for h in self.hs:
            es = []
            for e in h._entries:
                if isinstance(e, TE):
                    es.append((e.tag, Entry.step.__get__(e) is None, e.blob))
                else:
                    es.append((e.tag, e._step is None, repr(e._size)))
            out.append((h._uid, h._cursor, es))
        return out


def _norm(x, names):
    """A result with histories by uid and ids by name."""
    if isinstance(x, tuple):
        return tuple(_norm(v, names) for v in x)
    if isinstance(x, list):
        return [_norm(v, names) for v in x]
    if isinstance(x, dict):
        return {k: _norm(v, names) for k, v in x.items()}
    if isinstance(x, TH):
        return ("history", x._uid)
    if type(x) is int and x in names:
        return names[x]
    return x


def _call(w, op):
    kind = op[0]
    if kind == "tiers":
        return store_fn(w, "enforce_tiers")(w.hs, w.settings, w.packer)
    if kind == "caps":
        stats = {} if op[1] else None
        r = store_fn(w, "enforce_caps")(w.hs, w.settings, stats)
        return r, stats
    if kind == "total":
        return store_fn(w, "total_bytes")(w.hs)
    if kind == "enforce":
        _k, use_memo, changed, use_stats, on_caps = op
        stats = {} if use_stats else None
        ch = w.hs[changed % len(w.hs)] if changed is not None and w.hs else None
        seen = []
        cb = (lambda drops, st: seen.append((list(drops), dict(st)))) if on_caps else None
        r = store_fn(w, "enforce")(w.hs, w.settings, w.packer, stats, w.memo if use_memo else None, ch, cb)
        return r, stats, seen
    if kind == "stroke":                # as runtime._enforce: a new step, then a pass told which history got it
        _call(w, ("push", op[1], op[2]))
        return _call(w, ("enforce", True, op[1], True, True))
    if kind == "sig":
        return store_fn(w, "_sig")(w.hs[op[1] % len(w.hs)])
    if kind == "pkey":
        return store_fn(w, "_packer_key")(w.packer)
    if kind == "packer":
        w.packer = None if op[1] is None else TP(w.log, op[1])
        return None
    if kind == "push":
        h = w.hs[op[1] % len(w.hs)]
        h._entries.append(w._entry(op[2]))
        h._cursor = len(h._entries)
        return None
    if kind == "cursor":
        h = w.hs[op[1] % len(w.hs)]
        h._cursor = op[2]
        return None
    if kind == "set":
        setattr(w.settings, op[1], op[2])
        return None
    if kind == "new":
        w.hs.append(w._history(op[1]))
        return None
    if kind == "pack":
        h = w.hs[op[1] % len(w.hs)]
        if h._entries:
            e = h._entries[op[2] % len(h._entries)]
            e.pack("zlib")
            if w.packer is not None:
                w.packer.packed += 1
        return None
    if kind == "memo_clear":
        w.memo.clear()
        return None
    raise AssertionError(op)


def store_fn(w, name):
    return getattr(w.side, name)


def run_both(spec, ops):
    ws = [World(spec, side) for side in SIDES]
    for i, op in enumerate(ops):
        outs = []
        for w in ws:
            del w.log[:]
            try:
                r = _call(w, op)
            except Exception as e:      # noqa: BLE001 - compared below
                r = e
            names = w.names()
            outs.append((equiv._exc(r) or _norm(r, names), list(w.log), w.state(),
                         _norm(w.memo.state, names)))
        (rr, rl, rs, rm), (er, el, es, em) = outs
        at = f"op {i} {op!r}"
        assert equiv._diff(rr, er) is None, f"{at}: result: reference {rr!r}, engine {er!r}"
        assert rl == el, f"{at}: calls differ: reference {rl}, engine {el}"
        assert equiv._diff(rs, es) is None, f"{at}: entries differ at {equiv._diff(rs, es)}"
        assert equiv._diff(rm, em) is None, f"{at}: memo state: reference {rm!r}, engine {em!r}"


# ------------------------------------------------------------------------------ the tests
def test_store_names_the_engine():
    """store.py's caps, tiers and packer are the engine's (24.5a, 24.5b); it holds no function or loop."""
    tree = ast.parse(open(store.__file__, encoding="utf-8").read())
    for node in tree.body:
        assert not isinstance(node, (ast.FunctionDef, ast.For, ast.While, ast.If)), \
            f"store.py still has {type(node).__name__} at line {node.lineno}"
    names = {"enforce_tiers": "enforce_tiers", "enforce_caps": "enforce_caps", "total_bytes": "total_bytes",
             "enforce": "enforce", "CapsMemo": "CapsMemo", "_sig": "caps_sig", "_packer_key": "packer_key",
             "_can_gain": "store_can_gain", "Packer": "Packer"}
    for py, eng in names.items():
        assert getattr(store, py) is getattr(store._N, eng), py
    assert store.EVICT_TO == ref.EVICT_TO


def test_can_gain_corpus_and_hostile():
    """_can_gain over the corpus's steps (each cut at every length up to 40 and with header bytes flipped), as bytes,
    bytearray and memoryview, and hostile input: other types, indexes that aren't ints, unhashable codes."""
    cases = []
    for k, b in enumerate(C.steps()):
        if k % 3:
            continue
        cases += [b[:n] for n in range(0, 40)] + [b]
        for n in range(4, 16):
            for v in (0, 4, 5, 8, 0xFF):
                x = bytearray(b)
                x[n] = v
                cases.append(bytes(x))
    assert len(cases) > 1000, len(cases)
    for c in cases:
        for form in (c, bytearray(c), memoryview(c)):
            equiv.check(ref._can_gain, store._can_gain, form)
    head = b"HSS3" + bytes([0, 0, 0, 0, 2, 0])
    hostile = [None, 7, 1.5, "HSS3" + "x" * 20, [0] * 20, list(head) + [0, 0, 0, 0, 0], (1, 2),
               list(b"HSS3") + [0, 0, 0, 0, 2, 0, 0, 0, 0, [1], 0], list(b"HSS3") + [0, 0, 0, 0, "a", 0] + [0] * 8,
               list(b"HSS3") + [0, 0, 0, 0, 2.0, 0] + [0] * 8, list(b"HSS3") + [0, 0, 0, 0, 0, 0, 0, 0, None, 0],
               memoryview(np.zeros(20, np.int32)), memoryview(np.frombuffer(head + bytes(10), np.uint8)),
               np.frombuffer(head + bytes(10), np.uint8), {i: 0 for i in range(20)}]
    for x in hostile:
        equiv.check(ref._can_gain, store._can_gain, x)


def _spec_strategy(hostile):
    """Plain values (so steps are packed, dropped and the memo's pass runs), or now and then hostile ones."""
    from hypothesis import strategies as st

    def mostly(plain, bad):
        return st.one_of(plain, plain, plain, st.sampled_from(bad)) if hostile else plain
    size = mostly(st.integers(0, 1_500_000), [-5, np.int64(900_000), 2.5e5, 1 << 70, -1 << 70, "x", None,
                                               ValueError("no size")])
    seq = mostly(st.integers(0, 40), [-3, None, "s", 1.5, np.int32(3), KeyError, (1,)])
    counter = iter(range(10 ** 9))
    tag = st.builds(lambda: next(counter))
    real = st.tuples(st.just("real"), tag, st.integers(1, 40), st.sampled_from([4, 2_000, 60_000]))
    fake = st.tuples(st.just("fake"), tag, size, seq)
    entry = st.one_of(real, fake, fake)
    cursor = mostly(st.integers(0, 8), [-2, 12, np.int64(3), 2.0, 1 << 70, None, "2"])
    ret = mostly(st.just("n"), ["none", "np", "junk", "raise"])
    hist = st.tuples(st.sampled_from(["A", "B", "C", "D", "A"]), st.lists(entry, max_size=7), cursor, ret)
    num = mostly(st.integers(0, 8), [-3, 1 << 70, -1 << 70, 2.7, None, "3", np.int64(2)])
    mb = mostly(st.integers(0, 3), [-1, 1 << 70, -1 << 70, 0.5, None, "1", np.int32(1), 1e300, 1 << 1100])
    codec = mostly(st.sampled_from(["zlib", "none"]), ["lz4", "bogus", None])
    settings = st.tuples(num, mb, num, codec)
    spec = st.fixed_dictionaries({"hs": st.lists(hist, max_size=4), "settings": settings})
    hi = st.integers(0, 6)
    op = st.one_of(
        st.tuples(st.just("tiers")), st.tuples(st.just("caps"), st.booleans()), st.tuples(st.just("total")),
        st.tuples(st.just("enforce"), st.booleans(), st.one_of(st.none(), hi), st.booleans(), st.booleans()),
        st.tuples(st.just("stroke"), hi, entry), st.tuples(st.just("stroke"), hi, entry),
        st.tuples(st.just("stroke"), hi, entry),
        st.tuples(st.just("sig"), hi), st.tuples(st.just("pkey")),
        st.tuples(st.just("packer"), st.sampled_from([None, True, False])),
        st.tuples(st.just("push"), hi, entry), st.tuples(st.just("cursor"), hi, cursor),
        st.tuples(st.just("set"), st.sampled_from(["max_steps", "raw_steps"]), num),
        st.tuples(st.just("set"), st.just("memory_limit_mb"), mb),
        st.tuples(st.just("set"), st.just("compression"), codec),
        st.tuples(st.just("new"), hist), st.tuples(st.just("pack"), hi, hi), st.tuples(st.just("memo_clear")))
    return spec, op


def test_hypothesis_worlds():
    """Random histories, cursors, cap settings and calls: the same as the reference after every call; with plain
    values, then with hostile ones (sizes, seqs, cursors and settings of other types, drop_oldest's answers)."""
    from hypothesis import HealthCheck, given, settings, strategies as st
    for hostile in (False, True):
        spec, op = _spec_strategy(hostile)

        @settings(max_examples=250, deadline=None, suppress_health_check=list(HealthCheck))
        @given(spec, st.lists(op, max_size=14))
        def prop(sp, ops):
            run_both(sp, ops)
        prop()


def test_stuck_over_the_limit_memo():
    """The case the memo is for (CapsMemo): over the limit with every cursor at 0, then strokes on one history; real
    entries big enough to go past 1 MB, so the memo's pass runs and must match the reference's, call for call."""
    big = [("real", 1000 + i, i + 1, 200_000) for i in range(6)]
    spec = {"hs": [("A", big, 0, "n"), ("B", [("real", 2000, 30, 60_000)], 1, "n"), ("C", [], 0, "n")],
            "settings": (40, 1, 2, "zlib")}
    ops = [("enforce", True, 1, True, True)]
    for i in range(12):
        ops += [("push", 1 + i % 2, ("real", 3000 + i, 50 + i, 200_000)), ("enforce", True, 1 + i % 2, True, True)]
        if i == 5:
            ops += [("cursor", 0, 3), ("packer", True)]
        if i == 8:
            ops += [("set", "memory_limit_mb", 2), ("pack", 0, 0)]
    run_both(spec, ops)
    # then random runs of strokes as in tests/test_core_store.py's memo test, with duck-typed entries (fast)
    for seed in range(24):
        rng = np.random.default_rng(seed)
        tag = iter(range(10_000, 10 ** 6))

        def fake():
            return ("fake", next(tag), int(rng.integers(50_000, 700_000)), int(rng.integers(0, 60)))
        spec = {"hs": [("A", [fake() for _ in range(8)], 0, "n"), ("B", [fake()], 1, "n"),
                       ("C", [fake(), fake()], 2, "n")], "settings": (12, 1, 2, "zlib")}
        ops = [("enforce", True, 1, True, False)]
        for _ in range(60):
            r = rng.random()
            if r < 0.75:
                ops.append(("stroke", int(rng.integers(1, 4)) if rng.random() < 0.95 else 0, fake()))
            elif r < 0.8:
                ops.append(("cursor", int(rng.integers(0, 4)), int(rng.integers(0, 6))))
            elif r < 0.85:
                ops.append(("new", (f"N{len(ops)}", [fake()], 1, "n")))
            elif r < 0.9:
                ops.append(("packer", bool(rng.integers(0, 2))))
            elif r < 0.95:
                ops.append(("pack", int(rng.integers(0, 4)), int(rng.integers(0, 6))))
            else:
                ops.append(("set", "memory_limit_mb", int(rng.integers(1, 3))))
        run_both(spec, ops)


def main():
    for t in (test_store_names_the_engine, test_can_gain_corpus_and_hostile, test_stuck_over_the_limit_memo,
              test_hypothesis_worlds):
        R.run(t)
    R.finish()


if __name__ == "__main__":
    main()
