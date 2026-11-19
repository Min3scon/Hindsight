import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R056: a jump's moves (globaltl.transition) collapse runs of stroke steps to one move per history, and
everything else happens in the same order as before. R055: global_position read from position_index (one look-up per history) is the walk over every
operation it replaces, for random timelines: several histories, operations spanning histories (extra
op), operations whose steps land out of order in a history, and every kind of cursor set (all applied,
none, the global state, histories moved one by one to any index). Plain script, no bpy."""
import random
import time
import traceback

from hindsight.core import globaltl
from hindsight.core.history import History
from hindsight.core.registry import Registry
from hindsight.core.types import Entry


def walk(reg, visible, groups):
    """global_position as it was before R055 (every group checked in order)."""
    cur = {h.uid: h.cursor for h in globaltl._visible_histories(reg, visible)}
    n = 0
    for g in groups:
        if all(i < cur[hid] for _seq, hid, i in g):
            n += 1
        else:
            break
    return n


def timeline(rng):
    reg = Registry()
    hs = [History(f"h{k}", [f"o{k}"]) for k in range(rng.randint(1, 6))]
    seq = 1
    for _ in range(rng.randint(0, 120)):
        r = rng.random()
        if r < 0.15 and len(hs) > 1:                      # one operation on several histories
            op = seq
            for h in rng.sample(hs, rng.randint(2, len(hs))):
                h.entries.append(Entry(blob=b"x", meta={"seq": seq, "kind": "DATA", "target": h.members[0],
                                                       "extra": {"op": op}}))
                seq += 1
        elif r < 0.2 and seq > 3:                         # a step joining an earlier operation (out of order)
            h = rng.choice(hs)
            h.entries.append(Entry(blob=b"x", meta={"seq": seq, "kind": "DATA", "target": h.members[0],
                                                   "extra": {"op": rng.randint(1, seq - 1)}}))
            seq += 1
        else:
            h = rng.choice(hs)
            h.entries.append(Entry(blob=b"x", meta={"seq": seq, "kind": "DELTA", "target": h.members[0]}))
            seq += 1
    for h in hs:
        h.cursor = len(h.entries)
        reg.histories[h.uid] = h
    return reg, hs


def test_index_equals_the_walk():
    rng = random.Random(55)
    checked = 0
    for _ in range(400):
        reg, hs = timeline(rng)
        visible = None if rng.random() < 0.7 else {h.members[0] for h in hs if rng.random() < 0.6}
        groups = globaltl.global_groups(reg, visible)
        index = globaltl.position_index(groups)
        cursor_sets = [lambda: None,
                       lambda: [setattr(h, "cursor", 0) for h in hs],
                       lambda: [setattr(h, "cursor", rng.randint(0, len(h.entries))) for h in hs]]
        for g in sorted({0, len(groups), rng.randint(0, len(groups)), rng.randint(0, len(groups))}):
            cursor_sets.append(lambda g=g: [setattr(h, "cursor", c) for h, c in (
                (reg.histories[hid], c) for hid, c in globaltl.plan_global_goto(reg, g, visible, groups).items())])
        for setter in cursor_sets:
            setter()
            want = walk(reg, visible, groups)
            got = globaltl.global_position(reg, visible, groups, index)
            assert got == want, (got, want, [(h.uid, h.cursor, len(h.entries)) for h in hs])
            assert globaltl.global_position(reg, visible, groups) == want
            checked += 1
    print(f"PASS test_index_equals_the_walk ({checked} cursor sets)")


def test_cost_does_not_grow_with_the_steps():
    reg = Registry()
    hs = [History(f"h{k}", [f"o{k}"]) for k in range(50)]
    seq = 1
    for _ in range(1000):
        for h in hs:
            h.entries.append(Entry(blob=b"x", meta={"seq": seq, "kind": "DELTA", "target": h.members[0]}))
            seq += 1
    for h in hs:
        h.cursor = len(h.entries)
        reg.histories[h.uid] = h
    groups = globaltl.global_groups(reg)
    index = globaltl.position_index(groups)
    t = time.perf_counter()
    for _ in range(20):
        a = globaltl.global_position(reg, None, groups, index)
    fast = (time.perf_counter() - t) / 20
    t = time.perf_counter()
    b = globaltl.global_position(reg, None, groups)
    slow = time.perf_counter() - t
    assert a == b == 50_000
    assert fast < slow / 20, (fast, slow)            # 50 look-ups against 50,000 groups walked
    print(f"PASS test_cost_does_not_grow_with_the_steps ({fast * 1e3:.3f} ms against {slow * 1e3:.2f} ms)")


def transition_strict(reg, g, visible=None, groups=None):
    """globaltl.transition as it was before R056: one move per run of one history's steps."""
    target = globaltl.plan_global_goto(reg, g, visible, groups)
    undo, redo = [], []
    for h in globaltl._visible_histories(reg, visible):
        t = target.get(h.uid, h.cursor)
        undo += [(h.entries[i].seq, h.uid, i) for i in range(t, h.cursor)]
        redo += [(h.entries[i].seq, h.uid, i) for i in range(h.cursor, t)]
    moves = []
    for _seq, hid, i in sorted(undo, reverse=True):
        if moves and moves[-1][0] == hid and moves[-1][2] == "u":
            moves[-1] = (hid, i, "u")
        else:
            moves.append((hid, i, "u"))
    for _seq, hid, i in sorted(redo):
        if moves and moves[-1][0] == hid and moves[-1][2] == "r":
            moves[-1] = (hid, i + 1, "r")
        else:
            moves.append((hid, i + 1, "r"))
    return [(hid, cur) for hid, cur, _ in moves]


def crossings(reg, moves):
    """Apply moves to the cursors; the steps crossed, in the order they are crossed."""
    cur = {u: h.cursor for u, h in reg.histories.items()}
    out = []
    for hid, c in moves:
        a = cur[hid]
        out += [(hid, i) for i in (range(a - 1, c - 1, -1) if c < a else range(a, c))]
        cur[hid] = c
    return out, cur


def mixed_timeline(rng):
    """Strokes in several histories, interleaved, with Object Mode operations (data records), operations
    on several histories at once, TOPO steps and a two-member history between them."""
    reg = Registry()
    hs = [History(f"h{k}", [f"o{k}"]) for k in range(rng.randint(1, 6))]
    if rng.random() < 0.3:
        hs[-1].members.append("joined")
    seq = 1
    for _ in range(rng.randint(0, 150)):
        h = rng.choice(hs)
        r = rng.random()
        if r < 0.6:
            meta = {"seq": seq, "kind": "DELTA", "mode": rng.choice(["SCULPT", "VERTEX_PAINT", "EDIT", "WEIGHT_PAINT"])}
        elif r < 0.7:
            meta = {"seq": seq, "kind": "DELTA", "mode": "OBJECT"}
        elif r < 0.8:
            meta = {"seq": seq, "kind": "TOPO", "mode": "SCULPT"}
        elif r < 0.9:
            meta = {"seq": seq, "kind": "DATA", "mode": "OBJECT"}
        else:
            meta = {"seq": seq, "kind": "DELTA", "mode": "SCULPT", "extra": {"op": max(1, seq - rng.randint(0, 3))}}
        meta["target"] = h.members[0]
        h.entries.append(Entry(blob=b"x", meta=meta))
        seq += 1
    for h in hs:
        h.cursor = len(h.entries)
        reg.histories[h.uid] = h
    return reg, hs


def test_collapsed_moves_keep_every_ordering_that_matters():
    rng = random.Random(56)
    jumps = 0
    fewer = 0
    for _ in range(500):
        reg, hs = mixed_timeline(rng)
        groups = globaltl.global_groups(reg)
        for g in (rng.randint(0, len(groups)), 0, len(groups), rng.randint(0, len(groups))):
            if rng.random() < 0.3:
                for h in hs:
                    h.cursor = rng.randint(0, len(h.entries))
            strict = transition_strict(reg, g, None, groups)
            new = globaltl.transition(reg, g, None, groups)
            a, cur_a = crossings(reg, strict)
            b, cur_b = crossings(reg, new)
            assert cur_a == cur_b, (cur_a, cur_b)                       # the same place for every history
            assert sorted(a) == sorted(b)                               # the same steps crossed
            strokes = {}
            for h in hs:
                one = len(h.members) == 1
                for i, e in enumerate(h.entries):
                    strokes[(h.uid, i)] = one and globaltl._stroke(e)
            # every step that isn't a stroke is crossed in the same order as before
            assert [x for x in a if not strokes[x]] == [x for x in b if not strokes[x]]
            # and every stroke step between the same two of them, after the same strokes of its own history
            def place(seq_):
                out, k = {}, 0
                for x in seq_:
                    if strokes[x]:
                        out[x] = k
                    else:
                        k += 1
                return out
            assert place(a) == place(b)
            for h in hs:
                assert [x for x in a if x[0] == h.uid] == [x for x in b if x[0] == h.uid]
            for hid, c in new:
                reg.histories[hid].cursor = c
            jumps += 1
            fewer += len(new) < len(strict)
    print(f"PASS test_collapsed_moves_keep_every_ordering_that_matters ({jumps} jumps, {fewer} with fewer moves)")


def test_interleaved_strokes_are_one_move_per_history():
    reg = Registry()
    hs = [History(f"h{k}", [f"o{k}"]) for k in range(3)]
    seq = 1
    for _ in range(200):
        for h in hs:
            h.entries.append(Entry(blob=b"x", meta={"seq": seq, "kind": "DELTA", "mode": "SCULPT", "target": h.members[0]}))
            seq += 1
    for h in hs:
        h.cursor = len(h.entries)
        reg.histories[h.uid] = h
    groups = globaltl.global_groups(reg)
    moves = globaltl.transition(reg, 0, None, groups)
    assert sorted(moves) == [("h0", 0), ("h1", 0), ("h2", 0)], moves
    assert len(transition_strict(reg, 0, None, groups)) == 600
    print("PASS test_interleaved_strokes_are_one_move_per_history (3 moves instead of 600)")


def test_plan_index_equals_the_walk():
    """R059: plan_global_goto through plan_index gives the walk's cursors (random timelines with operations
    across histories and out of order, every g), and transition the same moves."""
    rng = random.Random(59)
    n = 0
    for _ in range(300):
        reg, hs = mixed_timeline(rng) if rng.random() < 0.5 else timeline(rng)
        visible = None if rng.random() < 0.7 else {h.members[0] for h in hs if rng.random() < 0.6}
        groups = globaltl.global_groups(reg, visible)
        idx = globaltl.plan_index(groups)
        for g in range(-1, len(groups) + 2):
            assert globaltl.plan_global_goto(reg, g, visible, groups, idx) == globaltl.plan_global_goto(reg, g, visible, groups), g
            n += 1
        for h in hs:
            h.cursor = rng.randint(0, len(h.entries))
        g = rng.randint(0, len(groups))
        assert globaltl.transition(reg, g, visible, groups, idx) == globaltl.transition(reg, g, visible, groups)
    print(f"PASS test_plan_index_equals_the_walk ({n} positions)")


def main():
    failed = 0
    for t in (test_index_equals_the_walk, test_cost_does_not_grow_with_the_steps,
              test_collapsed_moves_keep_every_ordering_that_matters, test_interleaved_strokes_are_one_move_per_history,
              test_plan_index_equals_the_walk):
        try:
            t()
        except Exception:
            failed += 1
            print("FAIL %s" % t.__name__)
            traceback.print_exc()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
