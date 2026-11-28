"""#132 (24.4): the history and its mesh state run in the engine. hindsight/core/history.py (the engine's MeshState and
History) against its reference (tests/reference/history.py, which crosses one step at a time in Python), bit for bit
through tests/equiv.py: the corpus's histories jumped end to end and back, with damaged and unknown steps; random
histories under calls drawn by hypothesis (goto, push and the chain sharing, truncate, drop the oldest, delete the past,
reset, packing the topology, records, deltas and snapshots set directly); and hostile calls. Compared after every call:
its result, the history (cursor, entries, morph target) and every state (counts, arrays by dtype, shape and bits,
removed channels, recorded values, snapshots), or the exception's class and message. Each side gets its own step
shapes (equiv.like) and decodes the same bytes with its own code (rule 1, dev/RUST_PLAN.md).
Run: <bpy python> tests/test_core_history_engine.py"""
import copy
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tests.harness as H  # noqa: E402
from tests import corpus as C, equiv  # noqa: E402
from tests.reference import history as ref, topopack as rtopo, types as rtypes  # noqa: E402
from hindsight.core import delta, history, step as stepmod, topopack, types  # noqa: E402
from hindsight.core.types import (  # noqa: E402
    CORNER_VERT_KEY, DATA, DELTA, EDGE_VERTS_KEY, FACE_OFFSETS_KEY, FACE_SET_KEY, MASK_KEY, MISSING, POSITION_KEY,
    TOPO, DataChange, Snapshot, Step, key_dtype, key_width, split_key,
)

SIDES = {"ref": (ref, rtypes, rtopo), "eng": (history, types, topopack)}
COL = "Col|CORNER|BYTE_COLOR"
DOMAINS = ("POINT", "EDGE", "FACE", "CORNER")


# ------------------------------------------------------------------------------ inputs (engine shapes, one rng)
def _vals(rng, key, n):
    dt, m = np.dtype(key_dtype(key)), n * key_width(key)
    if dt == np.bool_:
        return rng.integers(0, 2, m).astype(np.bool_)
    return np.frombuffer(rng.bytes(m * dt.itemsize), dt).copy()


def _mesh(rng, nv):
    counts = {"POINT": nv, "EDGE": 2 * nv, "FACE": nv // 2, "CORNER": 2 * nv}
    arrays = {k: _vals(rng, k, counts[split_key(k)[1]]) for k in (POSITION_KEY, FACE_SET_KEY, COL)}
    arrays[EDGE_VERTS_KEY] = rng.integers(0, nv, 4 * nv).astype(np.int32)
    arrays[CORNER_VERT_KEY] = rng.integers(0, nv, 2 * nv).astype(np.int32)
    arrays[FACE_OFFSETS_KEY] = np.arange(0, 2 * nv + 1, 4, dtype=np.int32)[: nv // 2 + 1]
    return counts, arrays


def _stroke(rng, counts, cur, keys):
    out = []
    for key in keys:
        old = cur.get(key)
        n = counts[split_key(key)[1]]
        exists = (old is not None, True)
        old = np.zeros(n * key_width(key), key_dtype(key)) if old is None else old
        new = old.copy()
        sel = rng.choice(n, int(rng.integers(1, max(2, n // 4))), replace=False)
        w = key_width(key)
        new.reshape(-1, w)[sel] = _vals(rng, key, len(sel)).reshape(-1, w)
        d = delta.diff(key, old, new, exists)
        if d is not None:
            out.append(d)
            cur[key] = new
    return out


def make(seed, n=36, nv=60):
    """(start counts, start arrays, steps, packed?): strokes on objA (now and then objB, or with records and an extra),
    the mask appearing and going, a TOPO step, a DATA step with snapshots, a second TOPO step whose before is the
    first one's after (equal bits, other arrays: the chain sharing)."""
    rng = np.random.default_rng(seed)
    counts, cur = _mesh(rng, nv)
    start = (dict(counts), {k: v.copy() for k, v in cur.items()})
    steps = []
    for i in range(n):
        kw = dict(seq=i, label="Stroke", mode="SCULPT", target="objA", time=float(i))
        if i in (n // 2, n // 2 + 1):
            before = Snapshot(dict(counts), {k: v.copy() for k, v in cur.items()})
            if i == n // 2:
                counts, cur = _mesh(rng, nv - 10)
            st = Step(kind=TOPO, before=before, after=Snapshot(dict(counts), {k: v.copy() for k, v in cur.items()}),
                      **kw)
        elif i == n // 3:
            st = Step(kind=DATA, records=[DataChange("material", "objA", {"diffuse": (MISSING, [1.0, 0.5, -0.0]),
                                                                           "name": ("a", "b")})],
                      extra={"_snapshots": {"u1": Snapshot({"POINT": 1}, {POSITION_KEY: np.zeros(3, np.float32)})}},
                      **kw)
        else:
            keys = [POSITION_KEY] + ([MASK_KEY] if i == n // 4 else []) + ([COL] if i % 3 == 0 else [])
            if i == n // 4 + 3:
                old = cur.pop(MASK_KEY)
                ds = [delta.diff(MASK_KEY, old, np.zeros_like(old), (True, False))]
            else:
                ds = _stroke(rng, counts, cur, keys)
            if i % 5 == 4:
                kw["target"] = "objB"
            recs = [DataChange("object", kw["target"], {"loc": ([0.0, 1.0], [2.0, float(i)])})] if i % 7 == 3 else []
            st = Step(kind=DELTA, deltas=ds, records=recs, extra={"brush": "Draw"} if i % 4 == 1 else {}, **kw)
        steps.append(st)
    packed = [i % 3 != 2 and s.kind != TOPO or i == n - 1 for i, s in enumerate(steps)]
    return start, steps, packed


def blobs_of(steps, packed):
    return [stepmod.step_to_bytes(s, ("zstd", "lz4", "none")[i % 3]) if p else None
            for i, (s, p) in enumerate(zip(steps, packed))]


# ------------------------------------------------------------------------------ one side
def _entries(T, steps, blobs):
    out = []
    for s, b in zip(steps, blobs):
        out.append(T.Entry(step=equiv.like(s, T)) if b is None else T.Entry(blob=b, meta=copy.deepcopy(s.meta())))
    return out


def _state_digest(st):
    light = st.light
    return (dict(st.counts), [(k, light[k]) for k in sorted(light)], sorted(st.removed, key=repr),
            sorted(((repr(k), equiv.plain(v)) for k, v in st.values.items())),
            sorted((k, equiv.plain(v)) for k, v in st.snaps.items()), st.is_packed(), st.topo_hashes())


def digest(h, states):
    return (h.cursor, len(h.entries), h.morph_target, h.enabled, h.unknown, list(h.members), h.uid,
            [(e.is_packed(), e.target, e.seq) for e in h.entries],
            {u: _state_digest(st) for u, st in states.items()})


def _changed(c):
    return [(u, sorted(k, key=repr)) for u, k in c.items()]


def _entry_list(es):
    return [(e.is_packed(), e.target) for e in es]


def _join_hook(mode):
    def hook(step, forward, states):
        if mode == "raise":
            raise RuntimeError(f"hook {step.seq} {forward}")
        return {"dict": {"objB": {"k1", ("@", "a", "p")}}, "list": ["objB", "objC"], "none": None,
                "int": 7}[mode]
    return hook


def _op(H_, T, TP, h, states, extra, op, *a):
    if op == "goto":
        return _changed(h.goto(a[0], states, _join_hook(a[1]) if len(a) > 1 else None))
    if op == "push":
        s, b = extra[a[0] % len(extra)]
        e = T.Entry(step=equiv.like(s, T)) if b is None else T.Entry(blob=b, meta=copy.deepcopy(s.meta()))
        dropped = h.push(e if b is not None else e.step)
        last, prev = h.entries[-1], h.entries[-2] if len(h.entries) > 1 else None
        shared = []
        if last.step is not None and last.step.before is not None and prev is not None and prev.step is not None \
                and prev.step.after is not None:
            shared = sorted(k for k, v in last.step.before.arrays.items() if v is prev.step.after.arrays.get(k))
        return _entry_list(dropped), shared
    if op == "truncate":
        return _entry_list(h.truncate_future())
    if op == "drop":
        return h.drop_oldest(a[0])
    if op == "past":
        return h.delete_past()
    if op == "reset":
        return h.reset()
    if op == "info":
        return (h.reach(), h.held(), h.future_count(), len(h), repr(h), h.positions_of(a[0]))
    if op == "set":
        setattr(h, a[0], a[1])
        return None
    st = states[a[0]]
    if op == "pack":
        job = st.pack_job()
        if job is None:
            return None
        packed = TP.pack_all(job)
        if len(a) > 1:           # an array replaced meanwhile: the job is dropped
            st.light[sorted(job)[0]] = job[sorted(job)[0]].copy()
        return sorted(job), st.finish_pack(job, packed)
    if op == "arrays":
        return sorted(st.arrays)
    if op == "nbytes":
        return st.nbytes(), sorted(st.all_keys())
    if op == "snapshot":
        return equiv.plain(st.to_snapshot())
    if op == "setsnap":
        s = extra[a[1] % len(extra)][0]
        if s.after is None:
            return None
        st.set_snapshot(equiv.like(s.after if a[2] else s.before, T))
        return None
    if op == "delta":
        s = extra[a[1] % len(extra)][0]
        for d in s.deltas:
            st.apply_delta(equiv.like(d, T), a[2])
        return len(s.deltas)
    if op == "record":
        s = extra[a[1] % len(extra)][0]
        return [sorted(st.apply_record(equiv.like(r, T), a[2]), key=repr) for r in s.records]
    raise AssertionError(op)


def drive(side, start, steps, blobs, ops, members=("objA", "objB"), extra=(), unknown=0, cursor=None):
    H_, T, TP = SIDES[side]
    h = H_.History("h1", list(members))
    h.entries = _entries(T, steps, blobs)
    h.cursor = len(h.entries) if cursor is None else cursor
    h.unknown = unknown
    counts, arrays = start
    states = {u: H_.MeshState(counts, {k: v.copy() for k, v in arrays.items()}, {("object", "loc"): [0.0, 1.0]})
              for u in members}
    out = []
    for o in ops:
        try:
            r = _op(H_, T, TP, h, states, extra, *o)
        except Exception as e:      # noqa: BLE001 - compared
            r = ("raised", type(e).__qualname__, str(e))
        out.append((o[0], r, digest(h, states)))
    return out


def same(*a, **kw):
    equiv.check(lambda *x, **y: drive("ref", *x, **y), lambda *x, **y: drive("eng", *x, **y), *a, **kw)


def _walk(n):
    return [("goto", 0), ("goto", n), ("goto", n // 2), ("goto", n // 3 - 1), ("goto", n), ("goto", 1), ("goto", 0)]


# ------------------------------------------------------------------------------ tests
def test_history_holds_no_algorithm():
    """history.py names the engine's MeshState and History and keeps its three marks; nothing else."""
    src = open(history.__file__, encoding="utf-8").read()
    for word in ("np.", "def ", "class ", "for ", "while "):
        assert word not in src, f"history.py still does {word!r}"
    assert history.History is history._N.History and history.MeshState is history._N.MeshState
    assert (history.TOPO_MARK, history.DATA_MARK, history.SNAPSHOTS_KEY) == (ref.TOPO_MARK, ref.DATA_MARK,
                                                                             ref.SNAPSHOTS_KEY)


def test_random_histories_jumped():
    """Every position forward and back, raw and packed steps, from two seeds."""
    for seed in (1, 2):
        start, steps, packed = make(seed)
        blobs = blobs_of(steps, packed)
        n = len(steps)
        same(start, steps, blobs, _walk(n) + [("goto", i) for i in range(n, -1, -3)] + [("goto", n)], cursor=n)
        same(start, steps, blobs, [("goto", n), ("goto", 0)], members=("objA",), cursor=0)


def test_corpus_histories_jumped():
    """Each corpus registry's histories, from zeros, to the start, the end, the middle and back."""
    from hindsight.core import persist
    done = 0
    for path, kind, _f in C.FILES:
        if kind != "registry":
            continue
        reg = persist.registry_from_bytes(C.payload(path, kind))
        for h in reg.histories.values():
            steps, blobs, counts = [], [], dict.fromkeys(DOMAINS, 1)
            for e in h.entries:
                st = e.load()
                for d in st.deltas:
                    dom = split_key(d.key)[1]
                    counts[dom] = max(counts[dom], int(d.idx.max()) + 1 if len(d.idx) else 0)
                steps.append(st)
                blobs.append(e.to_bytes())
            n = len(steps)
            if n:
                same((counts, {}), steps, blobs, _walk(n), members=tuple(h.members), cursor=h.cursor)
                done += 1
    assert done >= 3, done


def test_damaged_and_unknown_steps():
    """A packed step cut short, flipped, or in an unknown encoding, inside a run of packed steps: the jump stops there
    with the reference's error; reach and held with unknown steps before and after the cursor."""
    start, steps, packed = make(3)
    n = len(steps)
    good = blobs_of(steps, [True if s.kind == DELTA else p for s, p in zip(steps, packed)])
    for at in (5, 7, n - 3):
        for how in ("cut", "flip", "tail", "unknown"):
            b = bytearray(good[at])
            if how == "cut":
                b = b[: len(b) // 2]
            elif how == "flip":
                b[len(b) // 2] ^= 0x40
            elif how == "tail":
                b = b[:-1]
            else:
                b[10] = 99
            blobs = list(good)
            blobs[at] = bytes(b)
            for cur in (0, n):
                same(start, steps, blobs, _walk(n) + [("info", "objA")], cursor=cur, unknown=int(how == "unknown"))


def test_hostile_calls():
    start, steps, packed = make(4, n=20)
    blobs = blobs_of(steps, packed)
    extra = list(zip(steps, blobs))
    ops = [("goto", "x"), ("goto", None), ("goto", 2.7), ("goto", True), ("goto", 10 ** 30), ("goto", -10 ** 30),
           ("goto", "3"), ("drop", "x"), ("drop", None), ("drop", 2.9), ("drop", -4), ("drop", 10 ** 30),
           ("goto", 20), ("drop", 2), ("info", 5), ("set", "morph_target", 9), ("drop", 3), ("set", "morph_target", 1),
           ("drop", 4), ("set", "cursor", "7"), ("info", "objA"), ("set", "cursor", 5), ("set", "morph_target", 8),
           ("truncate",), ("set", "entries", ()), ("truncate",), ("push", 1), ("set", "entries", []), ("push", 1),
           ("push", 2), ("goto", 0), ("goto", 2), ("past",), ("reset",), ("goto", 3)]
    same(start, steps, blobs, ops, extra=extra, cursor=10)
    for mode in ("raise", "dict", "list", "none", "int"):
        st = [Step(seq=1, label="Join", mode="OBJECT", target="objA", kind="JOIN",
                   before=steps[10].before, after=steps[10].after)] if steps[10].before is not None else steps[:1]
        same(start, st + steps[:4], [None] * 5, [("goto", 0, mode), ("goto", 5, mode), ("goto", 0, mode)], cursor=5)
    for bad in ([("goto", 2)], [("delta", "objC", 0, True)], [("delta", "objA", 3, False)], [("record", "objA", 3, 0)]):
        same(start, steps, blobs, bad, extra=extra, cursor=0, members=("objA",))
    # wrong kinds of state and argument
    for args in ((None, {}), ({}, {}, None), ({"POINT": "x"}, {}), ([("POINT", 3)], {}, [(("a", "p"), 1)]), (5,)):
        equiv.check(lambda *x: _make_state(ref, *x), lambda *x: _make_state(history, *x), *args)


def _make_state(mod, *args):
    T = rtypes if mod is ref else types
    st = mod.MeshState(*args)
    st.apply_delta(T.ChannelDelta(POSITION_KEY, np.zeros(0, np.uint32), np.zeros((0, 3), np.float32)), True)
    return _state_digest(st)


def test_hypothesis_calls():
    from hypothesis import HealthCheck, given, settings, strategies as hs
    start, steps, packed = make(5, n=30)
    blobs = blobs_of(steps, packed)
    extra = list(zip(steps, blobs))
    uid = hs.sampled_from(["objA", "objB"])
    op = hs.one_of(
        hs.tuples(hs.just("goto"), hs.integers(-3, 34)), hs.tuples(hs.just("push"), hs.integers(0, 29)),
        hs.tuples(hs.just("truncate")), hs.tuples(hs.just("drop"), hs.integers(-1, 8)), hs.tuples(hs.just("past")),
        hs.tuples(hs.just("reset")), hs.tuples(hs.just("info"), uid),
        hs.tuples(hs.just("set"), hs.just("morph_target"), hs.one_of(hs.none(), hs.integers(0, 30))),
        hs.tuples(hs.just("pack"), uid), hs.tuples(hs.just("pack"), uid, hs.just(1)), hs.tuples(hs.just("arrays"), uid),
        hs.tuples(hs.just("nbytes"), uid), hs.tuples(hs.just("snapshot"), uid),
        hs.tuples(hs.just("setsnap"), uid, hs.integers(0, 29), hs.booleans()),
        hs.tuples(hs.just("delta"), uid, hs.integers(0, 29), hs.booleans()),
        hs.tuples(hs.just("record"), uid, hs.integers(0, 29), hs.booleans()))

    @settings(max_examples=150, deadline=None, suppress_health_check=list(HealthCheck))
    @given(hs.lists(op, max_size=20), hs.integers(0, 30))
    def prop(ops, cursor):
        same(start, steps, blobs, ops, extra=extra, cursor=cursor)
    prop()


for t in (test_history_holds_no_algorithm, test_random_histories_jumped, test_corpus_histories_jumped,
          test_damaged_and_unknown_steps, test_hostile_calls, test_hypothesis_calls):
    H.run(t)
H.finish()
