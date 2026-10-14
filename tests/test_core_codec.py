"""Step encoding: adversarial round trips and damaged input.

Round trips: random and awkward data (NaN payloads, -0, denormals, infinities, extreme integers,
an empty step, one element, every element, huge index gaps, smooth and noisy runs) through every
codec, both encoder settings, the strong form and background recompression. Each decoded step must
move old to new and back bit for bit.

Damaged input: for every codec, 600 damaged blobs (bit flips, cuts, extensions, header edits), half
of them with the checksum patched so the inner decoder sees the damage too. Decoding must either
raise one of the expected errors or return a step; nothing else may escape, and nothing may hang.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import struct
import time
import traceback
import zlib

import numpy as np

from hindsight.core import codec, delta, step as stepmod
from hindsight.core.types import ADD, CHANNEL_TYPES, DELTA, XOR, ChannelDelta, Step, bits_dtype, make_key

stepmod.VERIFY = True
RNG = np.random.default_rng(1234)
CODECS = [c for c in codec.available()]
WEIRD32 = np.array([0x00000000, 0x80000000, 0x7F800000, 0xFF800000, 0x00000001, 0x80000001, 0x007FFFFF,
                    0x7F7FFFFF, 0xFF7FFFFF, 0x7FC00000, 0x7FC00001, 0xFFC12345, 0x7F800001, 0x3F800000,
                    0xBF800000, 0xFFFFFFFF, 0x7FFFFFFF], dtype=np.uint32)


def all_keys():
    return [make_key(f"a_{t.lower()}", "POINT", t) for t in CHANNEL_TYPES]


def bits_of(key, n):
    t = key.rsplit("|", 1)[1]
    dt, w, _ = CHANNEL_TYPES[t]
    bd = bits_dtype(dt)
    if bd == np.uint8:
        if dt == np.bool_:
            return RNG.integers(0, 2, (n, w)).astype(np.uint8)
        return RNG.integers(0, 256, (n, w)).astype(np.uint8)
    return RNG.integers(0, 2 ** 32, (n, w), dtype=np.uint64).astype(np.uint32)


def change(old, kind):
    """A new state from an old one: smooth, noisy, weird, or a sign flip of the bits."""
    new = old.copy()
    k = old.shape[0]
    if old.size == 0:
        return new
    if old.dtype == np.uint8:
        new ^= RNG.integers(0, 2, new.shape).astype(np.uint8) if old.max() <= 1 else \
            RNG.integers(0, 256, new.shape).astype(np.uint8)
        return new
    if kind == "smooth":
        f = old.view(np.float32).copy()
        f[~np.isfinite(f)] = 0
        f += (np.sin(np.arange(k) / 7.0)[:, None] * 1e-3).astype(np.float32)
        return f.view(np.uint32)
    if kind == "weird":
        return RNG.choice(WEIRD32, new.shape)
    if kind == "signflip":
        return new ^ np.uint32(0x80000000)
    return RNG.integers(0, 2 ** 32, new.shape, dtype=np.uint64).astype(np.uint32)


def index_sets(n):
    yield np.zeros(0, np.uint32)
    yield np.array([0], np.uint32)
    yield np.array([n - 1], np.uint32)
    yield np.arange(n, dtype=np.uint32)
    yield np.sort(RNG.choice(n, n // 3, replace=False)).astype(np.uint32)
    yield np.concatenate([np.arange(0, 5), np.arange(n - 5, n)]).astype(np.uint32)
    runs = np.concatenate([np.arange(s, min(n, s + 17)) for s in range(3, n, 61)])
    yield np.unique(runs).astype(np.uint32)


def deltas_for(key, n=700):
    w = CHANNEL_TYPES[key.rsplit("|", 1)[1]][1]
    base = bits_of(key, n)
    for idx in index_sets(n):
        for kind in ("smooth", "noisy", "weird", "signflip"):
            old = base[idx]
            new = change(old, kind)
            keep = (old != new).any(axis=1) if idx.size else np.zeros(0, bool)
            i2 = idx[keep]
            o2, n2 = old[keep], new[keep]
            for op in (ADD, XOR):
                vals = (n2 - o2) if op == ADD else (o2 ^ n2)
                for exists in ((True, True), (False, True), (True, False)):
                    yield ChannelDelta(key, i2.copy(), vals.reshape(-1, w), exists, op), o2, n2


def same_delta(a, b):
    return (a.key == b.key and a.op == b.op and tuple(a.exists) == tuple(b.exists)
            and np.array_equal(np.asarray(a.idx, np.int64), np.asarray(b.idx, np.int64))
            and a.vals.dtype == b.vals.dtype and a.vals.reshape(-1).tobytes() == b.vals.reshape(-1).tobytes())


def moves(d, old, new):
    if d.idx.size == 0:
        return True
    v = d.vals.reshape(old.shape)
    fwd = (old ^ v) if d.op == XOR else (old + v)
    back = (new ^ v) if d.op == XOR else (new - v)
    return np.array_equal(fwd, new) and np.array_equal(back, old)


def test_round_trips():
    count = 0
    for key in all_keys():
        for d, old, new in deltas_for(key):
            for effort in (delta.QUICK, delta.NORMAL):
                rec = delta.pack_delta(d, effort)
                d2 = delta.unpack_delta(rec)
                assert same_delta(d, d2), (key, effort, d.idx.size)
                assert moves(d2, old, new), (key, effort)
                assert delta.packed_size(rec) == len(rec)
                count += 1
    print(f"PASS test_round_trips ({count} records)")


def test_steps_through_every_codec():
    count = 0
    keys = all_keys()
    for trial in range(40):
        ds = []
        for key in RNG.choice(keys, 3, replace=False):
            cands = list(deltas_for(key, 200))
            ds.append(cands[RNG.integers(len(cands))][0])
        # one key per step
        seen, uniq = set(), []
        for d in ds:
            if d.key not in seen:
                seen.add(d.key)
                uniq.append(d)
        st = Step(trial + 1, "Stroke ✓", "SCULPT", "obj", 0.25 * trial, DELTA, uniq,
                  extra={"note": "ünïcode", "n": trial})
        for name in CODECS:
            for effort in (delta.QUICK, delta.NORMAL):
                for strong in (False, True):
                    b = stepmod.step_to_bytes(st, name, effort, strong)
                    assert stepmod.same_step(st, stepmod.step_from_bytes(b)), (name, effort, strong)
                    r = stepmod.recompress(b)
                    if r is not None:
                        assert len(r) < len(b) and stepmod.same_step(st, stepmod.step_from_bytes(r))
                    count += 1
    empty = Step(99, "", "", "", 0.0, DELTA, [])
    for name in CODECS:
        assert stepmod.same_step(empty, stepmod.step_from_bytes(stepmod.step_to_bytes(empty, name)))
    print(f"PASS test_steps_through_every_codec ({count} blobs, codecs {CODECS})")


def test_encoder_refuses_unsorted_or_repeated_indices():
    key = make_key("p", "POINT", "FLOAT")
    for idx in ([3, 3, 4], [5, 2, 9], [0, 0]):
        d = ChannelDelta(key, np.array(idx, np.uint32), np.ones((len(idx), 1), np.uint32), (True, True), ADD)
        try:
            delta.pack_delta(d)
        except ValueError:
            continue
        raise AssertionError(f"accepted indices {idx}")
    print("PASS test_encoder_refuses_unsorted_or_repeated_indices")


def test_prediction_pays_off():
    """A smooth stroke-like change must come out much smaller with prediction than stored as is,
    and the chosen orders must be above zero (catches a broken order choice, not just broken data)."""
    k = 4000
    old = (np.stack([np.cos(np.arange(k) / 300.0), np.sin(np.arange(k) / 300.0), np.zeros(k)], 1) * 1.3)
    old = old.astype(np.float32)
    bump = np.sin(np.linspace(0, np.pi, k))[:, None] ** 2 * np.array([2e-3, -1e-3, 3e-3])
    new = (old + bump).astype(np.float32)
    d = delta.diff("position|POINT|FLOAT_VECTOR", old.reshape(-1), new.reshape(-1))
    raw = len(codec.compress(delta.pack_delta(d, delta.QUICK), "zlib"))
    best = delta.pack_delta(d, delta.NORMAL)
    hdr = delta._HDR.unpack_from(best, 0)
    tags = best[delta._HDR.size + hdr[5] + hdr[7]:][:3]
    assert min(tags) >= 1, list(tags)
    packed = len(codec.compress(best, "zlib"))
    assert packed < 0.7 * raw, (packed, raw)
    print(f"PASS test_prediction_pays_off ({raw} -> {packed} bytes, orders {list(tags)})")


def test_same_change_matches_undo_and_redo():
    key = "position|POINT|FLOAT_VECTOR"
    a = RNG.standard_normal(300).astype(np.float32)
    b = a.copy()
    b[10:40] += np.float32(0.25)
    rec = delta.diff(key, a, b)                       # recorded step a -> b
    undo = delta.diff(key, b, a)                      # Blender undo seen afterwards: b -> a
    redo = delta.diff(key, a, b)
    assert delta.same_change(undo, rec, b, inverse=True)
    assert not delta.same_change(undo, rec, b, inverse=False)
    assert delta.same_change(redo, rec, a, inverse=False)
    assert not delta.same_change(redo, rec, a, inverse=True)
    old_style = ChannelDelta(key, rec.idx, delta.as_xor(rec, a), rec.exists, XOR)   # a format 2 step
    assert delta.same_change(undo, old_style, b, inverse=True)
    assert delta.same_change(redo, old_style, a, inverse=False)
    other = b.copy()
    other[12] += np.float32(1.0)
    assert not delta.same_change(delta.diff(key, other, a), rec, other, inverse=True)
    print("PASS test_same_change_matches_undo_and_redo")


def test_recompress_refuses_a_bad_result():
    st = Step(3, "Stroke", "SCULPT", "obj", 1.0, DELTA, [next(x for x in deltas_for(all_keys()[4], 300)
                                                             if x[0].idx.size > 20)[0]])
    blob = stepmod.step_to_bytes(st, "zlib", delta.QUICK)
    real = stepmod.step_to_bytes
    verify = stepmod.VERIFY

    def broken(step, codec_name="auto", effort=delta.NORMAL, strong=False):
        s2 = Step(step.seq + 1, step.label, step.mode, step.target, step.time, step.kind, step.deltas)
        return real(s2, codec_name, effort, strong)        # a different step: must be noticed
    stepmod.VERIFY = False
    stepmod.step_to_bytes = broken
    try:
        assert stepmod.recompress(blob) is None
    finally:
        stepmod.step_to_bytes = real
        stepmod.VERIFY = verify
    assert stepmod.recompress(blob) is not None
    print("PASS test_recompress_refuses_a_bad_result")


def test_apply_refuses_indices_past_the_end():
    key = "position|POINT|FLOAT_VECTOR"
    arr = np.arange(30, dtype=np.float32)               # 10 vertices
    before = arr.copy()
    for idx in ([8, 9, 10], [10, 11, 12], [2, 5, 40]):   # contiguous and scattered, past the end
        d = ChannelDelta(key, np.array(idx, np.uint32), np.ones((3, 3), np.uint32), (True, True), ADD)
        try:
            delta.apply(arr, d)
        except ValueError:
            pass
        else:
            raise AssertionError(f"applied indices {idx} to 10 elements")
        assert arr.tobytes() == before.tobytes(), "array changed by a refused delta"
    print("PASS test_apply_refuses_indices_past_the_end")


def _rewrite_v3(blob, body_fn=None, raw_len_fn=None):
    """Rebuild a format 3 blob stored with codec "none": change its body or its stated body length,
    then fix the checksum, so only the inner checks can notice."""
    mv = memoryview(blob)
    (hl,) = struct.unpack_from("<H", mv, 8)
    head = bytearray(mv[10:10 + hl])
    payload = bytes(mv[10 + hl:])
    assert payload[0] == codec.NONE
    body = payload[1:]
    if body_fn:
        body = body_fn(body)
    fields = list(stepmod._FIXED.unpack_from(head, 0))
    fields[6] = raw_len_fn(len(body)) if raw_len_fn else len(body)
    head[:stepmod._FIXED.size] = stepmod._FIXED.pack(*fields)
    rest = struct.pack("<H", hl) + bytes(head) + bytes((codec.NONE,)) + body
    return stepmod.STEP_MAGIC + struct.pack("<I", zlib.crc32(rest)) + rest


def test_inner_checks_without_the_checksum():
    key = all_keys()[4]
    d = next(x for x in deltas_for(key, 300) if x[0].idx.size > 20)[0]
    st = Step(5, "Stroke", "SCULPT", "obj", 1.0, DELTA, [d])
    blob = stepmod.step_to_bytes(st, "none")
    assert stepmod.same_step(st, stepmod.step_from_bytes(_rewrite_v3(blob)))     # the rewrite itself is fine
    cases = {
        "bytes after the last record": _rewrite_v3(blob, body_fn=lambda b: b + b"\x00" * 16),
        "stated body length too long": _rewrite_v3(blob, raw_len_fn=lambda n: n + 16),
        "stated body length too short": _rewrite_v3(blob, raw_len_fn=lambda n: n - 16),
    }
    for what, bad in cases.items():
        try:
            stepmod.step_from_bytes(bad)
        except stepmod.CorruptStep:
            continue
        raise AssertionError(f"accepted a step with {what}")
    print("PASS test_inner_checks_without_the_checksum")


EXPECTED = (stepmod.CorruptStep, delta.CorruptDelta, codec.CorruptBlob, ValueError)


def _try(blob, stats):
    t = time.perf_counter()
    try:
        stepmod.step_from_bytes(blob)
        stats["decoded"] += 1
    except EXPECTED:
        stats["refused"] += 1
    dt = time.perf_counter() - t
    assert dt < 2.0, f"decoding damaged data took {dt:.1f} s"


def _patch_crc(b):
    b = bytearray(b)
    if bytes(b[:4]) == stepmod.STEP_MAGIC and len(b) >= 8:
        b[4:8] = struct.pack("<I", zlib.crc32(bytes(b[8:])))
    return bytes(b)


def _damaged(blob, rng):
    b = bytearray(blob)
    kind = rng.integers(7)
    if kind == 0:                                    # flip bits
        for _ in range(int(rng.integers(1, 6))):
            i = int(rng.integers(len(b)))
            b[i] ^= 1 << int(rng.integers(8))
    elif kind == 1:                                  # cut
        b = b[:int(rng.integers(len(b)))]
    elif kind == 2:                                  # extend
        b += rng.integers(0, 256, int(rng.integers(1, 64))).astype(np.uint8).tobytes()
    elif kind == 3:                                  # random bytes over a stretch
        i = int(rng.integers(len(b)))
        n = int(rng.integers(1, 32))
        b[i:i + n] = rng.integers(0, 256, len(b[i:i + n])).astype(np.uint8).tobytes()
    elif kind == 4:                                  # huge numbers in the header area
        i = int(rng.integers(8, min(len(b), 80)))
        b[i:i + 4] = b"\xff\xff\xff\x7f"
    elif kind == 5:                                  # zero a stretch
        i = int(rng.integers(len(b)))
        b[i:i + 16] = bytes(len(b[i:i + 16]))
    else:                                            # swap two halves
        i = int(rng.integers(1, len(b)))
        b = b[i:] + b[:i]
    return bytes(b)


def test_damaged_input_fails_cleanly():
    rng = np.random.default_rng(99)
    st_parts = []
    for key in all_keys()[:5]:
        d = next(x for x in deltas_for(key, 300) if x[0].idx.size > 20)[0]
        st_parts.append(d)
    st = Step(7, "Stroke", "SCULPT", "obj", 1.0, DELTA, st_parts[:4])
    for name in CODECS:
        stats = {"decoded": 0, "refused": 0}
        good = [stepmod.step_to_bytes(st, name, e) for e in (delta.QUICK, delta.NORMAL)]
        for i in range(600):
            src = good[i % 2]
            bad = _damaged(src, rng)
            if i % 2:
                bad = _patch_crc(bad)                # get past the checksum: test the decoder itself
                _try(bad, stats)
            elif bad != src:                         # checksum not patched: damage is always refused
                plain = {"decoded": 0, "refused": 0}
                _try(bad, plain)
                assert plain["refused"] == 1, (name, i)
                stats["refused"] += 1
        print(f"  {name}: {stats}")
    # the delta records alone, with no checksum in front of them
    stats = {"decoded": 0, "refused": 0}
    recs = [delta.pack_delta(d, e) for d in st_parts for e in (delta.QUICK, delta.NORMAL)]
    for i in range(600):
        bad = _damaged(recs[i % len(recs)], rng)
        t = time.perf_counter()
        try:
            delta.unpack_delta(bad)
            stats["decoded"] += 1
        except (delta.CorruptDelta, ValueError):
            stats["refused"] += 1
        assert time.perf_counter() - t < 2.0
    print(f"  records: {stats}")
    print("PASS test_damaged_input_fails_cleanly")


def main():
    ok = True
    for t in (test_round_trips, test_steps_through_every_codec, test_encoder_refuses_unsorted_or_repeated_indices,
              test_prediction_pays_off, test_same_change_matches_undo_and_redo, test_recompress_refuses_a_bad_result,
              test_apply_refuses_indices_past_the_end, test_inner_checks_without_the_checksum,
              test_damaged_input_fails_cleanly):
        try:
            t()
        except Exception:
            ok = False
            print("FAIL", t.__name__)
            traceback.print_exc()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
