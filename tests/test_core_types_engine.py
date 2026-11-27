"""#147 (24.3b): the shapes every step is made of (Step, ChannelDelta, Snapshot, DataChange, Slice, Entry, the key
helpers, MISSING) are engine classes, and step_from_bytes and same_step run in the engine. hindsight/core/types.py
and step.py against their references (tests/reference/types.py, tests/reference/step_build.py) bit for bit through
tests/equiv.py: random steps, every step of the corpus (corpus.FILES and corpus.MADE), hostile input and wrong types
(results, exception class and message); Entry.nbytes over the corpus; copying and pickling. Settings stays a
dataclass. Run: <bpy python> tests/test_core_types_engine.py"""
import copy
import dataclasses
import os
import pickle
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

import tests.harness as H  # noqa: E402
from tests import corpus as C, equiv  # noqa: E402
from tests.reference import step_build as RS, types as RT  # noqa: E402
from hindsight.core import native, step as S, types as T  # noqa: E402

N = native.ENGINE
RNG = random.Random(147)
CLASSES = ("Step", "ChannelDelta", "Snapshot", "DataChange", "Slice", "Entry")
NAMES = {"Step": ("seq", "label", "mode", "target", "time", "kind", "deltas", "before", "after", "extra", "records"),
         "ChannelDelta": ("key", "idx", "vals", "exists", "op"), "Snapshot": ("counts", "arrays"),
         "DataChange": ("adapter", "target", "changes"), "Slice": ("part", "off", "n"),
         "Entry": ("meta", "step", "_bc", "_nbytes")}


def plain(o, seen=None):
    """Reference and engine objects in one form: (class name, attributes), MISSING as a string; the rest as it is."""
    seen = set() if seen is None else seen
    if o is T.MISSING or o is RT.MISSING:
        return "<MISSING>"
    if id(o) in seen:
        return "<cycle>"
    name = type(o).__name__
    if name in NAMES and type(o).__module__ in ("hindsight_native", RT.__name__, T.__name__):
        seen.add(id(o))
        return (name,) + tuple((n, plain(getattr(o, n), seen)) for n in NAMES[name])
    if isinstance(o, (list, tuple)):
        return type(o)(plain(x, seen) for x in o)
    if isinstance(o, dict):
        return {k: plain(v, seen) for k, v in o.items()}
    return o


def same(ref, eng, *args, **kw):
    return equiv.check(ref, eng, *args, key=plain, **kw)


def both(name, fn):
    """fn(module) run on the reference's types and on the engine's."""
    same(lambda *a: fn(RT, *a), lambda *a: fn(T, *a))


def rand_step(rng, kind=None):
    nv = rng.randint(0, 40)
    deltas = []
    for _ in range(rng.randint(0, 3)):
        k = rng.randint(0, nv)
        w = rng.choice((1, 3, 4))
        dt = rng.choice((np.uint8, np.uint16, np.uint32))
        deltas.append(T.ChannelDelta(f"c{rng.randint(0, 9)}|POINT|FLOAT", np.sort(np.random.default_rng(
            rng.randint(0, 9999)).choice(max(nv, 1), k, replace=False).astype(np.uint32)) if k <= max(nv, 1) else
            np.arange(k, dtype=np.uint32), np.frombuffer(rng.randbytes(k * w * np.dtype(dt).itemsize), dt).reshape(k, w),
            (rng.random() < .9, rng.random() < .9), rng.choice((T.ADD, T.XOR))))
    snap = (lambda: T.Snapshot({"POINT": nv, "FACE": 1}, {T.POSITION_KEY: np.frombuffer(rng.randbytes(nv * 12),
            np.float32), T.FACE_OFFSETS_KEY: np.arange(2, dtype=np.int32)}))
    kind = kind or rng.choice((T.DELTA, T.TOPO, T.JOIN, T.DATA))
    extra = {"a": rng.random(), "b": [1, "x", None]} if rng.random() < .5 else {}
    if kind == T.JOIN:
        extra[S.SNAPSHOTS_KEY] = {"u1": snap(), "u2": snap()}
    recs = [T.DataChange("xform", f"u{i}", {"loc": ([rng.random()], [rng.random()]), "c": (T.MISSING, 1)})
            for i in range(rng.randint(0, 2))]
    return T.Step(rng.randint(0, 2 ** 40), rng.choice(("Stroke", "", "é")), "SCULPT", "u1", rng.random() * 1e6, kind,
                  deltas, snap() if kind in (T.TOPO, T.JOIN) else None, snap() if kind == T.TOPO else None, extra, recs)


# --------------------------------------------------------------------------- the move itself
def test_names_are_the_engines():
    """types.py's classes and helpers and step.py's step_from_bytes and same_step are the engine's own."""
    for n in CLASSES + ("make_key", "split_key", "key_dtype", "key_width", "bits_dtype", "MISSING"):
        assert getattr(T, n) is getattr(N, n), f"types.{n} is not the engine's"
    for n in ("step_from_bytes", "same_step", "snapshot_from_bytes"):
        assert getattr(S, n) is getattr(N, n), f"step.{n} is not the engine's"
    src = open(T.__file__, encoding="utf-8").read()
    assert src.count("\nclass ") == 1 and "\ndef " not in src, "types.py still holds Python classes or functions"


def test_settings_is_a_dataclass():
    assert dataclasses.is_dataclass(T.Settings) and isinstance(T.SETTINGS, T.Settings)
    assert [f.name for f in dataclasses.fields(T.SETTINGS)] == [f.name for f in dataclasses.fields(RT.Settings)]


# --------------------------------------------------------------------------- classes
ARRS = (np.arange(3, dtype=np.uint32), np.arange(6, dtype=np.uint32).reshape(3, 2))
BUILDS = [
    ("ChannelDelta", ("k|POINT|FLOAT",) + ARRS, {}), ("ChannelDelta", ("k",) + ARRS + ((False, True), 1), {}),
    ("ChannelDelta", (), {"key": "k", "idx": ARRS[0], "vals": ARRS[1], "op": 1}), ("ChannelDelta", ("k",), {}),
    ("ChannelDelta", ("k", 1, 2, 3, 4, 5), {}), ("ChannelDelta", ("k", 1, 2), {"key": 1}),
    ("ChannelDelta", ("k", 1, 2), {"bad": 1}), ("ChannelDelta", ([1], "x", None), {}),
    ("Snapshot", ({"POINT": 3}, {"a": ARRS[0]}), {}), ("Snapshot", (), {}), ("Snapshot", (1, 2, 3), {}),
    ("Snapshot", ({}, {"a": [1]}), {}), ("Snapshot", (None, None), {}),
    ("DataChange", ("rna", "u", {"p": (1.5, T.MISSING), "q": ([1, [2, "ab"]], "xy")}), {}),
    ("DataChange", ("a", "b", {"p": (1, 2, 3)}), {}), ("DataChange", ("a", "b", {"p": 5}), {}),
    ("DataChange", ("a", "b", {"p": (1,)}), {}), ("DataChange", ("a", "b", [1]), {}), ("DataChange", ("a",), {}),
    ("Step", (1, "L", "SCULPT", "u"), {}), ("Step", (1, "L", "SCULPT"), {}), ("Step", tuple(range(12)), {}),
    ("Step", (1, "L", "M", "u"), {"time": 2.5, "kind": "TOPO", "extra": {"x": 1}}),
    ("Step", (1, "L", "M", "u"), {"deltas": [5]}), ("Step", (1, "L", "M", "u"), {"seq": 2}),
    ("Step", ("s", 2, 3, 4, None, None, None, None, None, None, None), {}),
    ("Slice", (None, 3, "4"), {}), ("Slice", (None, "x", 1), {}), ("Slice", (None, 1.9, 2), {}), ("Slice", (), {}),
]


def _use(o):
    """What can be read from an object: its repr, nbytes, meta and copy (or the exception each gives)."""
    out = [repr(o) if type(o).__name__ not in ("Slice", "Entry") else None]    # theirs shows an address
    for m in ("nbytes", "meta", "copy"):
        f = getattr(o, m, None)
        if f is not None:
            try:
                out.append(f())
            except Exception as e:      # compared by class and message
                out.append((type(e).__name__, str(e)))
    return out


def test_constructors_and_methods():
    for name, args, kw in BUILDS:
        same(lambda a, k: _use(getattr(RT, name)(*a, **k)), lambda a, k: _use(getattr(T, name)(*a, **k)), args, kw)
    s = rand_step(RNG, T.TOPO)
    assert T.Step(1, "a", "b", "c").deltas is not T.Step(1, "a", "b", "c").deltas    # a new list each time
    same(lambda: _use(s), lambda: _use(s))


def test_eq_hash_copy_pickle():
    for mod in (RT, T):
        a, b = mod.DataChange("a", "b", {"p": (1, 2)}), mod.DataChange("a", "b", {"p": (1, 2)})
        assert a == b and not (a != b) and a != mod.DataChange("a", "c", {}) and (a == 1) is False
    for t in ("DataChange", "Step", "ChannelDelta", "Snapshot"):
        for x, y in ((1, 1), (1, 2), (np.zeros(3), np.zeros(3)), (np.zeros(1), np.ones(1))):
            def eq(mod):
                c = getattr(mod, t)
                n = len(NAMES[t]) if t != "Step" else 4
                return (c(*([x] * n)) == c(*([y] * n)), c(*([x] * n)) == c(*([x] * n)))
            same(lambda: eq(RT), lambda: eq(T))
        same(lambda: hash(getattr(RT, t)(*range(len(NAMES[t]) if t != "Step" else 4))),
             lambda: hash(getattr(T, t)(*range(len(NAMES[t]) if t != "Step" else 4))))
        same(lambda: getattr(RT, t)(*range(4 if t == "Step" else len(NAMES[t]))) < 1,
             lambda: getattr(T, t)(*range(4 if t == "Step" else len(NAMES[t]))) < 1)
    s = rand_step(RNG, T.JOIN)
    for f in (copy.copy, copy.deepcopy, lambda o: pickle.loads(pickle.dumps(o))):
        c = f(s)
        assert type(c) is T.Step and plain(c) is not None and S.same_step(c, s) and equiv._diff(plain(c), plain(s)) is None
    c = copy.copy(s)
    assert c.deltas is s.deltas and copy.deepcopy(s).deltas is not s.deltas
    assert copy.copy(T.MISSING) is T.MISSING and pickle.loads(pickle.dumps(T.MISSING)) is T.MISSING
    assert repr(T.MISSING) == "MISSING"
    e = T.Entry(s)
    e.pack()
    for f in (copy.copy, copy.deepcopy, lambda o: pickle.loads(pickle.dumps(o))):
        c = f(e)
        assert type(c) is T.Entry and c.blob_crc() == e.blob_crc() and c.step is None
        assert equiv._diff(plain(c.meta), plain(e.meta)) is None


def test_gc_sees_what_each_holds():
    """The cycle collector reaches what an object holds, as through the dataclass: the held-bytes walk of
    test_core_format5_once (gc.get_referents) finds it, and a cycle through the object is freed."""
    import gc, weakref

    class W:
        pass

    def reach(o, x):
        todo, seen = [o], set()
        for _ in range(3):              # the object, its __dict__ or slots, their values
            nxt = []
            for y in todo:
                if y is x:
                    return True
                if id(y) not in seen and not isinstance(y, type):
                    seen.add(id(y))
                    nxt += gc.get_referents(y)
            todo = nxt
        return any(y is x for y in todo)

    def held_and_freed(mod, t):
        w = W()
        n = {"Step": 4, "Entry": 3}.get(t, len(NAMES.get(t, "abc")))
        o = getattr(mod, t)(*({"Entry": [None, None, w], "Slice": [w, 0, 1]}.get(t) or [w] * n))
        found = reach(o, w)
        w.o, r = o, weakref.ref(w)
        del o, w
        gc.collect()
        return found, r() is None

    for t in ("ChannelDelta", "Snapshot", "DataChange", "Step", "Slice", "Entry"):
        same(lambda: held_and_freed(RT, t), lambda: held_and_freed(T, t))
        assert held_and_freed(RT, t) == (True, True), t


def test_key_helpers():
    for k in ("position|POINT|FLOAT_VECTOR", "a|b|c|INT8", "|FACE|INT", "a|b", "", "x|y|NOPE", 5, None, b"a|b|c"):
        for fn in ("split_key", "key_dtype", "key_width"):
            same(getattr(RT, fn), getattr(T, fn), k)
    for d in (np.float32, np.uint8, "<i2", np.float64, "x", None, np.bool_):
        same(RT.bits_dtype, T.bits_dtype, d)
    for a in (("a", "b", "c"), (1, None, 2.5), ("é", "", "x")):
        same(RT.make_key, T.make_key, *a)


class Part:
    def __init__(self, b):
        self.bytes = b


def test_slice_and_entry():
    p = Part(b"0123456789")
    for args in ((p, 2, 5), (p, 0, 0), (p, 8, 5), (Part(None), 1, 2), (p, -3, 2)):
        def use(mod, *a):
            sl = mod.Slice(*a)
            out = [len(sl) if sl.n >= 0 else None, sl.copy(), bytes(sl.view())]
            for i in (0, 1, -1, 4, 9, "x", slice(1, 3), slice(None, None, -1), slice(-2, None)):
                try:
                    out.append(sl[i])
                except Exception as e:
                    out.append((type(e).__name__, str(e)))
            return out
        same(lambda *a: use(RT, *a), lambda *a: use(T, *a), *args)
    blob = rand_step(RNG, T.DELTA)

    def entry(mod, *a, **k):
        e = mod.Entry(*a, **k)
        out = [e.meta, e.is_packed(), e.blob_crc(), e.nbytes(), e._nbytes]
        for f in ("seq", "target", "kind", "blob"):
            try:
                out.append(getattr(e, f))
            except Exception as x:
                out.append((type(x).__name__, str(x)))
        return out
    b = blob_bytes = T.Entry(blob).to_bytes()
    for a, k in (((), {}), ((None, b), {}), ((None, b, {"seq": 3}, 7), {}), ((blob,), {}), ((None, "x"), {}),
                 ((None, b), {"meta": {"kind": "TOPO", "seq": 1, "target": "t"}}), ((None, None, 5), {}),
                 ((1, 2, 3, 4, 5), {}), ((), {"x": 1})):
        same(lambda *x, **y: entry(RT, *x, **y), lambda *x, **y: entry(T, *x, **y), *a, **k)
    for mod in (RT, T):
        e = mod.Entry(None, blob_bytes)
        assert S.same_step(e.load(), blob) and e.load() is not e.load() and e.to_bytes() == blob_bytes
        e.blob = b"abc"
        assert e.blob_crc() == (b"abc", native.crc32(b"abc")) and e.nbytes() == 131
        e.blob = None
        assert e.blob_crc() == (None, None) and e._bc is mod.Entry()._bc
        e = mod.Entry(blob)
        assert not e.is_packed() and e.load() is blob and e.to_bytes(effort=0)
        e.pack()
        assert e.is_packed() and e.step is None and S.same_step(e.load(), blob)
        e._bc = (mod.Slice(Part(blob_bytes), 0, len(blob_bytes)), 7)
        assert e.blob == blob_bytes and e.blob_crc() == (blob_bytes, 7) and e.nbytes() == len(blob_bytes) + 128

    class Sub(T.Entry):
        __slots__ = ()

        def load(self):
            return "mine"
    assert Sub(blob).load() == "mine" and isinstance(Sub(), T.Entry)


# --------------------------------------------------------------------------- step_from_bytes, same_step
def _pairs(a, b):
    yield a, a
    yield a, b
    yield a, copy.deepcopy(a)
    for f, v in (("seq", -1), ("time", None), ("label", 0), ("extra", {"z": 1}), ("records", []), ("deltas", []),
                 ("before", None), ("after", a.before), ("kind", "X")):
        c = copy.copy(a)
        setattr(c, f, v)
        yield a, c
    if a.deltas:
        c = copy.deepcopy(a)
        c.deltas[0].vals = c.deltas[0].vals.astype(np.uint64)
        yield a, c
        c = copy.deepcopy(a)
        c.deltas[0].exists = [c.deltas[0].exists[0], not c.deltas[0].exists[1]]
        yield a, c


def test_corpus_steps():
    n, prev = 0, None
    for b in C.steps():
        s = same(RS.step_from_bytes, S.step_from_bytes, b)
        e = T.Entry(None, b)
        assert RT.Entry(None, b).nbytes() == e.nbytes() == len(b) + 128
        e.step = S.step_from_bytes(b)
        assert RT.Entry(s).nbytes() == e.nbytes(), f"step {s.seq}: nbytes"
        for x, y in _pairs(S.step_from_bytes(b), prev if prev is not None else s):
            same(RS.same_step, S.same_step, x, y)
        if n % 7 == 0:
            for d in (b[:len(b) // 2], b[:40], bytes([b[0] ^ 1]) + b[1:], b[:-1] + bytes([b[-1] ^ 0x40])):
                same(RS.step_from_bytes, S.step_from_bytes, d)
        prev, n = s, n + 1
    assert n > 50, n
    print(f"  test_corpus_steps ({n} steps)")


def test_random_and_hostile_steps():
    for i in range(400):
        s = rand_step(RNG)
        b = S.step_to_bytes(s, "auto", i % 2)
        x = same(RS.step_from_bytes, S.step_from_bytes, b)
        for p, q in _pairs(x, s):
            same(RS.same_step, S.same_step, p, q)
    for b in (b"", b"HSS3", None, 5, "HSS3", bytearray(b"HSS1\0\0\0\0"), memoryview(b"xx")):
        same(RS.step_from_bytes, S.step_from_bytes, b)
    s = rand_step(RNG, T.TOPO)
    odd = [(s, None), (None, s), (s, 5), (T.Step(1, 2, 3, 4, extra=None), T.Step(1, 2, 3, 4, extra={})),
           (T.Step(1, 2, 3, 4, deltas=[1]), T.Step(1, 2, 3, 4, deltas=[2])),
           (T.Step(1, 2, 3, 4, kind="TOPO", before=T.Snapshot({}, {"a": [1]})), T.Step(1, 2, 3, 4, before=5)),
           (T.Step(1, 2, 3, 4, extra={"_snapshots": {"a": 1}}), T.Step(1, 2, 3, 4, extra={"_snapshots": {"a": 1}})),
           (T.Step(1, 2, 3, 4, time="x"), T.Step(1, 2, 3, 4, time="x")), (T.Step(1, 2, 3, 4, records=[1]),
                                                                          T.Step(1, 2, 3, 4))]
    for a, b in odd:
        same(RS.same_step, S.same_step, a, b)


for t in (test_names_are_the_engines, test_settings_is_a_dataclass, test_constructors_and_methods,
          test_eq_hash_copy_pickle, test_gc_sees_what_each_holds, test_key_helpers, test_slice_and_entry, test_corpus_steps,
          test_random_and_hostile_steps):
    H.run(t)
H.finish()
