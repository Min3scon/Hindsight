import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Tests for hindsight.core.history (restore engine). Plain script, no bpy."""
import time
import traceback

import numpy as np

from hindsight.core.types import (
    POSITION_KEY, MASK_KEY, FACE_SET_KEY, HIDE_VERT_KEY,
    EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY,
    ChannelDelta, Entry, Snapshot, Step, DELTA, TOPO, JOIN,
    bits_dtype, key_dtype, key_width, split_key,
)
from hindsight.core.history import MeshState, History

COL_KEY = "Col|CORNER|BYTE_COLOR"
UID = "objA"
N_STEPS = 300
TOPO_AT = 150          # entry index of the TOPO step
MASK_CREATE = (20, 120, 200)
MASK_DELETE = (100,)


# ---------------------------------------------------------------- helpers
def bits2d(arr, key):
    return arr.view(bits_dtype(arr.dtype)).reshape(-1, key_width(key))


def make_delta(key, old, new, exists=(True, True)):
    """Vectorised XOR delta between two flat native arrays (or None)."""
    x = bits2d(old, key) ^ bits2d(new, key)
    idx = np.flatnonzero(x.any(axis=1)).astype(np.uint32)
    if len(idx) == 0 and exists[0] == exists[1]:
        return None
    return ChannelDelta(key, idx, np.ascontiguousarray(x[idx]), tuple(exists))


def rand_values(rng, key, n):
    """n*width random native values; floats use raw random bits (NaN, -0.0, inf included)."""
    dt = np.dtype(key_dtype(key))
    w = key_width(key)
    if dt == np.bool_:
        return rng.integers(0, 2, n * w).astype(np.bool_)
    if dt == np.float32:
        v = rng.integers(0, 2 ** 32, n * w, dtype=np.uint64).astype(np.uint32).view(np.float32)
        v[: max(1, len(v) // 7)] = -0.0
        return v
    return rng.integers(-2 ** 31, 2 ** 31, n * w, dtype=np.int64).astype(np.int32)


def make_mesh(rng, nv, nf):
    ne = nv * 2
    nl = nf * 4
    counts = {"POINT": nv, "EDGE": ne, "FACE": nf, "CORNER": nl}
    arrays = {
        POSITION_KEY: rng.standard_normal(nv * 3).astype(np.float32),
        FACE_SET_KEY: rng.integers(0, 10, nf).astype(np.int32),
        HIDE_VERT_KEY: np.zeros(nv, np.bool_),
        COL_KEY: rng.random(nl * 4).astype(np.float32),
    }
    arrays.update(topo_arrays(rng, counts))   # baseline holds topology too (like a Snapshot)
    return counts, arrays


def topo_arrays(rng, counts):
    nv, ne, nf, nl = counts["POINT"], counts["EDGE"], counts["FACE"], counts["CORNER"]
    return {
        EDGE_VERTS_KEY: rng.integers(0, nv, ne * 2).astype(np.int32),
        CORNER_VERT_KEY: rng.integers(0, nv, nl).astype(np.int32),
        CORNER_EDGE_KEY: rng.integers(0, ne, nl).astype(np.int32),
        FACE_OFFSETS_KEY: (np.arange(nf + 1) * 4).astype(np.int32),
    }


def domain_count(counts, key):
    return counts[split_key(key)[1]]


def build(seed=1, target=UID, nv=5000, nf=1500):
    """Record N_STEPS random steps. Returns (history, start_state, gt, steps).

    gt[i] = (counts, arrays) after entries[:i]; arrays are shared copy-on-write between states.
    """
    rng = np.random.default_rng(seed)
    counts, arrays = make_mesh(rng, nv, nf)
    gt = [(dict(counts), dict(arrays))]
    steps = []
    h = History(target)
    for i in range(N_STEPS):
        counts, cur = gt[-1]
        cur = dict(cur)
        if i == TOPO_AT:
            before = Snapshot(dict(counts), {k: v.copy() for k, v in cur.items()})
            counts2, arrays2 = make_mesh(rng, nv - 1000, nf - 300)   # vertex count changes, mask dropped
            after = Snapshot(dict(counts2), {k: v.copy() for k, v in arrays2.items()})
            step = Step(seq=i, label="Remesh", mode="SCULPT", target=target, time=float(i),
                        kind=TOPO, before=before, after=after)
            new_state = (dict(after.counts), {k: v.copy() for k, v in after.arrays.items()})
        else:
            deltas = []
            if i in MASK_CREATE:
                assert MASK_KEY not in cur
                old = np.zeros(domain_count(counts, MASK_KEY), np.float32)
                new = old.copy()
                sel = rng.choice(len(new), rng.integers(1, len(new)), replace=False)
                new[sel] = rng.random(len(sel)).astype(np.float32)
                deltas.append(make_delta(MASK_KEY, old, new, (False, True)))
                cur[MASK_KEY] = new
            elif i in MASK_DELETE:
                old = cur.pop(MASK_KEY)
                deltas.append(make_delta(MASK_KEY, old, np.zeros_like(old), (True, False)))
            keys = [POSITION_KEY, FACE_SET_KEY, HIDE_VERT_KEY, COL_KEY]
            if MASK_KEY in cur and i not in MASK_CREATE:
                keys.append(MASK_KEY)
            for key in rng.choice(keys, rng.integers(1, 3), replace=False):
                key = str(key)
                old = cur[key]
                ne = domain_count(counts, key)
                k = int(rng.choice([1, 10, rng.integers(1, ne + 1)]))
                sel = np.sort(rng.choice(ne, k, replace=False))
                new = old.copy()
                w = key_width(key)
                new.reshape(-1, w)[sel] = rand_values(rng, key, k).reshape(-1, w)
                d = make_delta(key, old, new)
                if d is not None:
                    deltas.append(d)
                    cur[key] = new
            step = Step(seq=i, label="Stroke", mode="SCULPT", target=target, time=float(i),
                        kind=DELTA, deltas=deltas)
            new_state = (dict(counts), cur)
        h.push(step)
        steps.append(step)
        gt.append(new_state)
    state = MeshState(dict(gt[-1][0]), {k: v.copy() for k, v in gt[-1][1].items()})
    return h, state, gt, steps


def assert_state(state, ref, where=""):
    counts, arrays = ref
    assert state.counts == counts, (where, state.counts, counts)
    assert set(state.arrays) == set(arrays), (where, sorted(state.arrays), sorted(arrays))
    for k, v in arrays.items():
        a = state.arrays[k]
        assert a.dtype == v.dtype, (where, k, a.dtype, v.dtype)
        assert a.shape == v.shape, (where, k, a.shape, v.shape)
        assert np.array_equal(a.view(np.uint8), v.view(np.uint8)), (where, k)
    assert not (state.removed & set(state.arrays)), (where, state.removed)


def expected_changed(steps, a, b):
    lo, hi = min(a, b), max(a, b)
    keys = set()
    for s in steps[lo:hi]:
        if s.kind == DELTA:
            keys.update(d.key for d in s.deltas)
        else:
            keys.add("__TOPO__")
    return keys


# ------------------------------------------------------------------ tests
def test_random_goto_exact():
    h, st, gt, steps = build(seed=7)
    states = {UID: st}
    assert len(h) == N_STEPS and h.cursor == N_STEPS and h.future_count() == 0
    assert_state(st, gt[N_STEPS], "start")
    rng = np.random.default_rng(123)
    crossed_topo = [0, 0]
    t0 = time.perf_counter()
    for it in range(500):
        r = it % 5
        if r == 0:
            pos = int(rng.integers(0, N_STEPS + 1))
        elif r == 1:
            pos = h.cursor + int(rng.choice([-1, 1]))
        elif r == 2:
            pos = h.cursor + int(rng.choice([-100, 100]))
        elif r == 3:
            pos = 0 if h.cursor > N_STEPS // 2 else N_STEPS     # jump of ~300
        else:
            pos = int(rng.integers(-20, N_STEPS + 20))          # out of range -> clamped
        before = h.cursor
        clamped = max(0, min(pos, N_STEPS))
        if min(before, clamped) <= TOPO_AT < max(before, clamped):
            crossed_topo[clamped < before] += 1
        changed = h.goto(pos, states)
        assert h.cursor == clamped
        assert_state(states[UID], gt[clamped], f"it={it} {before}->{clamped}")
        exp = expected_changed(steps, before, clamped)
        assert changed.get(UID, set()) == exp, (it, before, clamped, changed, exp)
        if before == clamped:
            assert changed == {}
    dt = time.perf_counter() - t0
    assert crossed_topo[0] > 5 and crossed_topo[1] > 5, crossed_topo
    # the TOPO step's snapshots must not have been mutated by later deltas
    tstep = steps[TOPO_AT]
    h.goto(TOPO_AT + 1, states)
    assert_state(states[UID], (tstep.after.counts, tstep.after.arrays), "topo after intact")
    h.goto(TOPO_AT, states)
    assert_state(states[UID], (tstep.before.counts, tstep.before.arrays), "topo before intact")
    print(f"  500 gotos in {dt:.2f}s")


def test_full_jumps():
    h, st, gt, steps = build(seed=3)
    states = {UID: st}
    for _ in range(3):
        t0 = time.perf_counter()
        h.goto(0, states)
        assert_state(st if states[UID] is st else states[UID], gt[0], "to 0")
        h.goto(N_STEPS, states)
        assert_state(states[UID], gt[N_STEPS], "to end")
        assert time.perf_counter() - t0 < 5.0


def test_topo_vertex_count():
    h, st, gt, steps = build(seed=5)
    states = {UID: st}
    h.goto(TOPO_AT + 1, states)
    assert states[UID].counts["POINT"] == 4000
    assert len(states[UID].arrays[POSITION_KEY]) == 4000 * 3
    ch = h.goto(TOPO_AT, states)
    assert "__TOPO__" in ch[UID]
    assert states[UID].counts["POINT"] == 5000
    assert len(states[UID].arrays[POSITION_KEY]) == 5000 * 3
    # the mask existed before the TOPO step but not after -> crossing forward flags it removed
    assert MASK_KEY in states[UID].arrays
    h.goto(TOPO_AT + 1, states)
    assert MASK_KEY in states[UID].removed and MASK_KEY not in states[UID].arrays
    assert EDGE_VERTS_KEY in states[UID].arrays
    assert_state(states[UID], (steps[TOPO_AT].after.counts, steps[TOPO_AT].after.arrays))


def test_removed_tracking():
    h, st, gt, steps = build(seed=11)
    states = {UID: st}
    c = MASK_CREATE[0]
    h.goto(c + 1, states)
    assert MASK_KEY in st.arrays and MASK_KEY not in st.removed
    ch = h.goto(c, states)
    assert MASK_KEY in ch[UID]
    assert MASK_KEY not in st.arrays and MASK_KEY in st.removed
    assert_state(st, gt[c])
    h.goto(c + 1, states)
    assert MASK_KEY in st.arrays and MASK_KEY not in st.removed
    assert_state(st, gt[c + 1])
    # deletion step crossed forward -> removed; backward -> recreated exactly
    dpos = MASK_DELETE[0]
    h.goto(dpos + 1, states)
    assert MASK_KEY not in st.arrays and MASK_KEY in st.removed
    h.goto(dpos, states)
    assert MASK_KEY not in st.removed
    assert_state(st, gt[dpos])
    # jump over creation AND deletion: ends without mask
    h.goto(0, states)
    h.goto(dpos + 5, states)
    assert MASK_KEY not in st.arrays and MASK_KEY in st.removed
    assert_state(st, gt[dpos + 5])
    # direct apply_delta on a missing key creates zeros of the right size
    ms = MeshState({"POINT": 10, "EDGE": 0, "FACE": 0, "CORNER": 0}, {})
    new = np.arange(10, dtype=np.float32)
    d = make_delta(MASK_KEY, np.zeros(10, np.float32), new, (False, True))
    ms.apply_delta(d, True)
    assert np.array_equal(ms.arrays[MASK_KEY], new) and not ms.removed
    ms.apply_delta(d, False)
    assert MASK_KEY not in ms.arrays and ms.removed == {MASK_KEY}
    # bool channel with a missing key
    hv = np.zeros(10, np.bool_); hv[[1, 3]] = True
    ms.apply_delta(make_delta(HIDE_VERT_KEY, np.zeros(10, np.bool_), hv, (False, True)), True)
    assert ms.arrays[HIDE_VERT_KEY].dtype == np.bool_ and np.array_equal(ms.arrays[HIDE_VERT_KEY], hv)
    snap = ms.to_snapshot()
    ms.arrays[HIDE_VERT_KEY][0] = True
    assert not snap.arrays[HIDE_VERT_KEY][0]


def test_branching():
    h, st, gt, steps = build(seed=13)
    states = {UID: st}
    h.goto(100, states)
    h.morph_target = 250
    rng = np.random.default_rng(9)
    old = st.arrays[POSITION_KEY]
    new = old.copy()
    new[:30] = rng.random(30).astype(np.float32)
    d = make_delta(POSITION_KEY, old, new)
    branch = Step(seq=1000, label="Branch", mode="SCULPT", target=UID, kind=DELTA, deltas=[d])
    st.apply_delta(d, True)   # what the recorder does: baseline already holds the new state
    gt_branch = (dict(st.counts), {k: v.copy() for k, v in st.arrays.items()})
    dropped = h.push(branch)
    assert len(dropped) == N_STEPS - 100
    assert all(isinstance(e, Entry) for e in dropped)
    assert [e.seq for e in dropped] == list(range(100, N_STEPS))
    assert len(h) == 101 and h.cursor == 101 and h.future_count() == 0
    assert h.morph_target is None
    h.goto(0, states)
    assert_state(st, gt[0])
    h.goto(101, states)
    assert_state(st, gt_branch)
    h.goto(37, states)
    assert_state(st, gt[37])
    # truncate_future
    dropped = h.truncate_future()
    assert len(dropped) == 64 and len(h) == 37 and h.cursor == 37


def test_drop_oldest_delete_past():
    h, st, gt, steps = build(seed=17)
    states = {UID: st}
    h.goto(200, states)
    h.morph_target = 210
    assert h.drop_oldest(30) == 30
    assert h.cursor == 170 and len(h) == N_STEPS - 30 and h.morph_target == 180
    rng = np.random.default_rng(4)
    for _ in range(60):
        p = int(rng.integers(0, len(h) + 1))
        h.goto(p, states)
        assert_state(st, gt[p + 30], f"after drop p={p}")
    h.goto(170, states)
    removed = h.delete_past()
    assert removed == 170 and h.cursor == 0 and len(h) == N_STEPS - 200
    assert h.morph_target == 10
    for _ in range(60):
        p = int(rng.integers(0, len(h) + 1))
        h.goto(p, states)
        assert_state(st, gt[p + 200], f"after delete_past p={p}")
    h.goto(0, states)
    h.goto(-5, states)
    assert h.cursor == 0
    assert_state(st, gt[200])
    # morph target dropped
    h.morph_target = 3
    h.drop_oldest(5)
    assert h.morph_target is None and h.cursor == 0
    # n clamped to len
    n = len(h)
    assert h.drop_oldest(10 ** 6) == n and len(h) == 0 and h.cursor == 0
    assert h.drop_oldest(3) == 0


def test_reset_and_morph_target():
    h, st, gt, steps = build(seed=19)
    states = {UID: st}
    h.goto(50, states)
    h.morph_target = 50
    rng = np.random.default_rng(2)
    old = st.arrays[FACE_SET_KEY]
    new = old.copy(); new[:5] += 1
    d = make_delta(FACE_SET_KEY, old, new)
    h.push(Step(seq=999, label="x", mode="SCULPT", target=UID, deltas=[d]))
    assert h.morph_target == 50          # still a valid position
    h.morph_target = 51
    h.goto(10, states)
    h.push(Step(seq=1001, label="y", mode="SCULPT", target=UID, deltas=[]))
    assert h.morph_target is None        # beyond the branch point
    h.reset()
    assert len(h) == 0 and h.cursor == 0 and h.morph_target is None and h.future_count() == 0
    assert h.goto(5, states) == {} and h.cursor == 0
    assert h.uid == UID and h.members == [UID] and h.enabled is True
    h2 = History("H", members=["a", "b"])
    assert h2.members == ["a", "b"]


class _Boom(Entry):
    __slots__ = ()

    def load(self):
        raise AssertionError("skipped entry must not be loaded")


def test_targets_skipped_and_interleaved():
    rng = np.random.default_rng(21)
    ca, aa = make_mesh(rng, 800, 200)
    cb, ab = make_mesh(rng, 300, 90)
    gt = {"A": [(dict(ca), dict(aa))], "B": [(dict(cb), dict(ab))]}
    h = History("A", members=["A", "B"])
    seq = 0
    for i in range(120):
        tgt = "A" if rng.random() < 0.5 else "B"
        for u in ("A", "B"):
            gt[u].append(gt[u][-1])
        counts, cur = gt[tgt][-1]
        cur = dict(cur)
        key = str(rng.choice([POSITION_KEY, FACE_SET_KEY, HIDE_VERT_KEY, COL_KEY]))
        n = domain_count(counts, key)
        sel = np.sort(rng.choice(n, int(rng.integers(1, n)), replace=False))
        new = cur[key].copy()
        w = key_width(key)
        new.reshape(-1, w)[sel] = rand_values(rng, key, len(sel)).reshape(-1, w)
        d = make_delta(key, cur[key], new)
        cur[key] = new
        gt[tgt][-1] = (counts, cur)
        h.push(Step(seq=seq, label="s", mode="SCULPT", target=tgt, deltas=[d] if d else []))
        seq += 1
    pa, pb = h.positions_of("A"), h.positions_of("B")
    assert sorted(pa + pb) == list(range(len(h))) and pa and pb
    assert h.positions_of("zzz") == []
    states = {u: MeshState(dict(gt[u][-1][0]), {k: v.copy() for k, v in gt[u][-1][1].items()})
              for u in ("A", "B")}
    for _ in range(80):
        p = int(rng.integers(0, len(h) + 1))
        ch = h.goto(p, states)
        assert set(ch) <= {"A", "B"}
        for u in ("A", "B"):
            assert_state(states[u], gt[u][p], f"{u} p={p}")
    # only A present: B steps are skipped, cursor still moves
    h.goto(len(h), states)
    only_a = {"A": states["A"]}
    b_before = {k: v.copy() for k, v in states["B"].arrays.items()}
    for _ in range(40):
        p = int(rng.integers(0, len(h) + 1))
        ch = h.goto(p, only_a)
        assert h.cursor == p and set(ch) <= {"A"}
        assert_state(only_a["A"], gt["A"][p], f"onlyA p={p}")
    for k, v in b_before.items():
        assert np.array_equal(states["B"].arrays[k].view(np.uint8), v.view(np.uint8))
    # a skipped entry is never unpacked
    h3 = History("A")
    h3.entries.append(_Boom(blob=b"garbage", meta={"seq": 0, "label": "x", "mode": "SCULPT",
                                                  "target": "GONE", "time": 0.0,
                                                  "kind": DELTA, "extra": {}}))
    assert h3.goto(1, {"A": states["A"]}) == {} and h3.cursor == 1
    assert h3.goto(0, {}) == {} and h3.cursor == 0


def test_join_hook():
    rng = np.random.default_rng(23)
    c1, a1 = make_mesh(rng, 100, 30)
    c2, a2 = make_mesh(rng, 160, 50)
    before = Snapshot(c1, {k: v.copy() for k, v in a1.items()})
    after = Snapshot(c2, {k: v.copy() for k, v in a2.items()})
    step = Step(seq=0, label="Join", mode="OBJECT", target="T", kind=JOIN, before=before, after=after,
                extra={"members": [{"uid": "M"}]})
    h = History("T", members=["T", "M"])
    h.push(step)
    st = MeshState(dict(c2), {k: v.copy() for k, v in a2.items()})
    # without hook: treated as TOPO
    ch = h.goto(0, {"T": st})
    assert ch == {"T": {"__TOPO__"}} and st.counts == c1
    assert_state(st, (c1, a1))
    ch = h.goto(1, {"T": st})
    assert ch == {"T": {"__TOPO__"}}
    assert_state(st, (c2, a2))
    calls = []

    def hook(s, forward, states):
        calls.append((s.seq, forward))
        states["T"].set_snapshot(s.after if forward else s.before)
        return {"M": {"__TOPO__"}}

    ch = h.goto(0, {"T": st}, join_hook=hook)
    assert calls == [(0, False)] and ch == {"T": {"__TOPO__"}, "M": {"__TOPO__"}}
    assert_state(st, (c1, a1))
    h.goto(1, {"T": st}, join_hook=hook)
    assert calls[-1] == (0, True)
    assert_state(st, (c2, a2))


def test_member_brought_back_mid_jump():
    """Regression (format 3 work): a join crossed backwards adds its member to `states` in the
    middle of a jump; the member's own earlier steps must still be applied, packed or not."""
    from hindsight.core import delta as D
    rng = np.random.default_rng(41)
    cb, ab = make_mesh(rng, 120, 40)
    b_states = [{k: v.copy() for k, v in ab.items()}]
    entries = []
    seq = 0
    for i in range(6):                                 # B's own steps, then A's, interleaved
        for target in ("B", "A"):
            seq += 1
            if target == "B":
                cur = b_states[-1]
                new = {k: v.copy() for k, v in cur.items()}
                p = new[POSITION_KEY]
                j = rng.choice(p.size, 40, replace=False)
                p[j] += rng.standard_normal(40).astype(np.float32)
                d = D.diff(POSITION_KEY, cur[POSITION_KEY], p)
                b_states.append(new)
                entries.append(Step(seq, "B", "SCULPT", "B", 0.0, DELTA, [d]))
            else:
                entries.append(Step(seq, "A", "SCULPT", "A", 0.0, DELTA, []))
    ca, aa = make_mesh(rng, 80, 20)
    join = Step(seq + 1, "Join", "OBJECT", "A", 0.0, JOIN, [], Snapshot(ca, aa), Snapshot(ca, aa),
                extra={"members": [{"uid": "B"}]})
    final_b = b_states[-1]

    def hook(s, forward, states):
        if not forward:
            states["B"] = MeshState(dict(cb), {k: v.copy() for k, v in final_b.items()})
        else:
            states.pop("B", None)
        return {"A": {"__TOPO__"}}

    for packed in (False, True):
        h = History("A", members=["A", "B"])
        for st in entries + [join]:
            h.push(st)
        if packed:
            for e in h.entries:
                e.pack("zlib")
        states = {"A": MeshState(dict(ca), {k: v.copy() for k, v in aa.items()})}
        h.goto(0, states, join_hook=hook)
        assert "B" in states
        assert_state(states["B"], (cb, b_states[0]))


TESTS = [test_random_goto_exact, test_full_jumps, test_topo_vertex_count, test_removed_tracking,
         test_branching, test_drop_oldest_delete_past, test_reset_and_morph_target,
         test_targets_skipped_and_interleaved, test_join_hook, test_member_brought_back_mid_jump]


if __name__ == "__main__":
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    sys.stdout.flush()
    sys.exit(1 if failed else 0)
