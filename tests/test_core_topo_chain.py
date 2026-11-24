import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""LIMITS-A 1.6, the TOPO chain: each TOPO step holds the whole mesh before and after, and its `before`
is the previous TOPO step's `after`.

In memory: History.push makes the new step hold the older step's arrays where they are bit for bit the
same (one copy of the mesh per link of the chain, not two), and nothing ever writes into them.
Packed: a body over 1 MB is compressed with a window as large as itself plus long distance matching,
so the second mesh of a step costs only its differences from the first (the default window of zstd
level 3, 2 MB, never saw the first one). Same format, any reader of zstd frames reads it. Pure Python."""
import traceback

import numpy as np

from hindsight.core import codec, delta, step as stepmod
from hindsight.core.history import History, MeshState
from hindsight.core.persist import registry_from_bytes, registry_to_bytes
from hindsight.core.registry import Registry
from hindsight.core.types import CORNER_EDGE_KEY, CORNER_VERT_KEY, DATA, DELTA, EDGE_VERTS_KEY, FACE_OFFSETS_KEY, POSITION_KEY, TOPO, DataChange, Entry, Snapshot, Step

RNG = np.random.default_rng(11)


def mesh(nv, nf):
    cv = RNG.integers(0, nv, nf * 4).astype(np.int32)
    return {POSITION_KEY: RNG.random(nv * 3).astype(np.float32), EDGE_VERTS_KEY: RNG.integers(0, nv, nf * 2).astype(np.int32),
            CORNER_VERT_KEY: cv, CORNER_EDGE_KEY: RNG.integers(0, nf * 2, nf * 4).astype(np.int32),
            FACE_OFFSETS_KEY: (np.arange(nf + 1) * 4).astype(np.int32)}


def counts(a):
    return {"POINT": a[POSITION_KEY].size // 3, "EDGE": a[EDGE_VERTS_KEY].size // 2, "FACE": a[FACE_OFFSETS_KEY].size - 1,
            "CORNER": a[CORNER_VERT_KEY].size}


def edit(a, grow=40):
    """A local change: a patch of vertices moves, a few vertices, faces and corners are appended."""
    b = {k: v.copy() for k, v in a.items()}
    p = b[POSITION_KEY].reshape(-1, 3)
    s = int(RNG.integers(0, p.shape[0] - 60))
    p[s:s + 50] += 0.01
    nv = p.shape[0]
    b[POSITION_KEY] = np.concatenate([b[POSITION_KEY], RNG.random(grow * 3).astype(np.float32)])
    b[CORNER_VERT_KEY] = np.concatenate([b[CORNER_VERT_KEY], (nv + RNG.integers(0, grow, grow * 4)).astype(np.int32)])
    b[CORNER_EDGE_KEY] = np.concatenate([b[CORNER_EDGE_KEY], RNG.integers(0, 50, grow * 4).astype(np.int32)])
    b[FACE_OFFSETS_KEY] = np.concatenate([b[FACE_OFFSETS_KEY], b[FACE_OFFSETS_KEY][-1] + (np.arange(1, grow + 1) * 4).astype(np.int32)])
    b[EDGE_VERTS_KEY] = np.concatenate([b[EDGE_VERTS_KEY], (nv + RNG.integers(0, grow, 2 * grow)).astype(np.int32)])
    return b


def topo(seq, a, b, target="o"):
    return Step(seq, "Dyntopo", "SCULPT", target, float(seq), TOPO, before=Snapshot(counts(a), a), after=Snapshot(counts(b), b))


def pos_delta(arr, seq, target="o"):
    new = arr.copy()
    new[:30] += 0.5
    d = delta.diff(POSITION_KEY, arr, new)
    return Step(seq, "Stroke", "SCULPT", target, float(seq), DELTA, [d]), new


def build(share=True, n=6):
    """A history that mixes TOPO, DELTA and DATA steps; -> (history, start arrays, end arrays).
    With share False every step owns its arrays (what History.push did before)."""
    h = History("o")
    cur = mesh(2000, 1500)
    start = {k: v.copy() for k, v in cur.items()}
    seq = 0
    for i in range(n):
        nxt = edit(cur)
        seq += 1
        st = topo(seq, {k: v.copy() for k, v in cur.items()}, nxt)
        h.push(st) if share else h.entries.append(Entry(st))
        h.cursor = len(h.entries)
        cur = {k: v.copy() for k, v in nxt.items()}
        if i % 2 == 1:
            seq += 1
            ds, moved = pos_delta(cur[POSITION_KEY], seq)
            h.push(ds) if share else h.entries.append(Entry(ds))
            h.cursor = len(h.entries)
            cur[POSITION_KEY] = moved
        if i == 2:
            seq += 1
            dc = Step(seq, "Move", "OBJECT", "o", float(seq), DATA,
                      records=[DataChange("xform", "o", {"location": ([0.0, 0, 0], [float(i), 0, 0])})])
            h.push(dc) if share else h.entries.append(Entry(dc))
            h.cursor = len(h.entries)
    return h, start, cur


def state_of(arrs):
    return MeshState(counts(arrs), {k: v.copy() for k, v in arrs.items()})


def key(st):
    return (st.counts, sorted((k, a.dtype.str, a.tobytes()) for k, a in st.arrays.items()), sorted(st.values.items(), key=repr))


def unique_bytes(h):
    seen = {}
    for e in h.entries:
        s = e.step
        if s is None:
            continue
        for sn in (s.before, s.after):
            if sn is not None:
                for a in sn.arrays.values():
                    seen[id(a)] = a.nbytes
    return sum(seen.values())


def test_the_chain_shares_arrays_and_halves_memory():
    RNG.bit_generator.state = np.random.default_rng(11).bit_generator.state
    hs, _s, _e = build(True)
    RNG.bit_generator.state = np.random.default_rng(11).bit_generator.state
    hn, _s, _e = build(False)
    shared = unique_bytes(hs)
    plain = unique_bytes(hn)
    assert shared < plain * 0.85, (shared, plain)        # a DELTA between two TOPO steps ends the sharing
    topos = [e.step for e in hs.entries if e.step.kind == TOPO]
    # consecutive TOPO steps with nothing between them share every array whose bits are equal
    for e0, e1 in zip(hs.entries, hs.entries[1:]):
        if e0.step.kind == TOPO and e1.step.kind == TOPO:
            for k in e1.step.before.arrays:
                assert e1.step.before.arrays[k] is e0.step.after.arrays[k], k
    print(f"PASS test_the_chain_shares_arrays_and_halves_memory ({len(topos)} TOPO steps: {plain/1e6:.2f} MB held, "
          f"{shared/1e6:.2f} MB with sharing)")


def test_goto_is_exact_with_shared_arrays_and_nothing_is_written_into_them():
    RNG.bit_generator.state = np.random.default_rng(12).bit_generator.state
    hs, start, end = build(True, 8)
    RNG.bit_generator.state = np.random.default_rng(12).bit_generator.state
    hn, _s, _e = build(False, 8)
    n = len(hs.entries)
    assert n == len(hn.entries)
    before = [(id(a), a.tobytes()) for e in hs.entries if e.step.kind == TOPO
              for sn in (e.step.before, e.step.after) for a in sn.arrays.values()]
    ss, sn_ = state_of(end), state_of(end)
    for pos in [0, n, 3, 1, n - 1, 0, 5, n, 2, 4, 0, n]:
        cs, cn = hs.goto(pos, {"o": ss}), hn.goto(pos, {"o": sn_})
        assert cs == cn, (pos, cs, cn)
        assert key(ss) == key(sn_), pos
    after = [(id(a), a.tobytes()) for e in hs.entries if e.step.kind == TOPO
             for sn in (e.step.before, e.step.after) for a in sn.arrays.values()]
    assert before == after, "a jump wrote into a step's arrays"
    print(f"PASS test_goto_is_exact_with_shared_arrays_and_nothing_is_written_into_them ({n} steps, 12 jumps)")


def test_caps_truncate_save_and_reload():
    RNG.bit_generator.state = np.random.default_rng(13).bit_generator.state
    h, start, end = build(True, 6)
    n = len(h.entries)
    # save and reload: every entry packed, then jumped across
    reg = Registry()
    reg.histories["o"] = h
    reg.by_object["o"] = "o"
    reg2 = registry_from_bytes(registry_to_bytes(reg))
    h2 = reg2.histories["o"]
    a, b = state_of(end), state_of(end)
    for pos in [0, n, 2, n - 2, 1, n]:
        h.goto(pos, {"o": a})
        h2.goto(pos, {"o": b})
        assert key(a) == key(b), pos
    # drop the oldest steps: the next step still holds its `before` (it is its own reference)
    keep = {k: v.copy() for k, v in h.entries[1].step.before.arrays.items()} if h.entries[1].step.kind == TOPO else None
    h.goto(n, {"o": a})
    h.drop_oldest(1)
    if keep is not None:
        for k, v in h.entries[0].step.before.arrays.items():
            assert np.array_equal(v, keep[k]), k
    # truncate: a new step after going back
    h.goto(2, {"o": a})
    nxt = edit(a.arrays)
    h.push(topo(99, {k: v.copy() for k, v in a.arrays.items()}, nxt))
    assert len(h.entries) == 3 and h.cursor == 3
    print("PASS test_caps_truncate_save_and_reload")


def test_a_big_body_compresses_its_two_meshes_as_one():
    """A TOPO step of 100k-vertex size: the blob is about one mesh, not two, and reads back exactly,
    with the engine and with python's zstd (a reader that knows nothing of this build)."""
    nv, nf = 100_000, 100_000
    a = mesh(nv, nf)
    b = edit(a, grow=1000)
    st = topo(1, a, b)
    blob = stepmod.step_to_bytes(st, "zstd")
    only_after = len(codec.compress(b"".join(x.tobytes() for x in b.values()), "zstd"))
    assert len(blob) < only_after * 1.25, (len(blob), only_after)        # before costs almost nothing
    back = stepmod.step_from_bytes(blob)
    for k in a:
        assert back.before.arrays[k].tobytes() == a[k].tobytes() and back.after.arrays[k].tobytes() == b[k].tobytes(), k
    try:
        import zstandard
    except ImportError:
        zstandard = None
    if zstandard is not None:
        import struct
        hl = struct.unpack_from("<H", blob, 8)[0]
        payload = blob[10 + hl + 1:]
        raw = zstandard.ZstdDecompressor().decompress(payload)
        assert len(raw) > 10_000_000
    small = stepmod.step_to_bytes(topo(2, mesh(300, 200), edit(mesh(300, 200))), "zstd")
    assert stepmod.step_from_bytes(small).kind == TOPO
    print(f"PASS test_a_big_body_compresses_its_two_meshes_as_one (blob {len(blob)/1e6:.2f} MB, one mesh alone "
          f"{only_after/1e6:.2f} MB, raw {2*sum(x.nbytes for x in a.values())/1e6:.1f} MB)")


def test_strong_and_recompress_keep_the_gain():
    nv, nf = 40_000, 40_000
    a = mesh(nv, nf)
    st = topo(1, a, edit(a, grow=400))
    quick = stepmod.step_to_bytes(st, "zstd", delta.QUICK)
    strong = stepmod.recompress(quick)
    assert strong is not None and len(strong) <= len(quick)
    assert stepmod.same_step(st, stepmod.step_from_bytes(strong))
    print(f"PASS test_strong_and_recompress_keep_the_gain (quick {len(quick)/1e6:.2f} MB, strong {len(strong)/1e6:.2f} MB)")


def main():
    tests = [test_the_chain_shares_arrays_and_halves_memory, test_goto_is_exact_with_shared_arrays_and_nothing_is_written_into_them,
             test_caps_truncate_save_and_reload, test_a_big_body_compresses_its_two_meshes_as_one,
             test_strong_and_recompress_keep_the_gain]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
