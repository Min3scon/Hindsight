"""Data records (adapters other than the mesh recorder): storage and playback, no bpy.

Round trips through every codec with awkward values (NaN, -0.0, infinities, denormals, long and
nested lists, unicode paths, None, MISSING); playback of mixed histories (mesh deltas and data
records, several datablocks) at every position in random order; damaged record sections; memory
accounting; file format 4.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import math
import random
import struct
import traceback
import zlib

import numpy as np

from hindsight.core import codec, delta, persist, step as stepmod
from hindsight.core.history import History, MeshState
from hindsight.core.registry import Registry
from hindsight.core.types import (ADD, DATA, DELTA, MISSING, POSITION_KEY, DataChange, Entry, Step)

stepmod.VERIFY = True
WEIRD = [0.0, -0.0, math.inf, -math.inf, 5e-324, -5e-324, 1.1754943508222875e-38, 3.4028234663852886e+38,
         0.1, 1 / 3, -2.5, 1e300]


def same_value(a, b):
    if a is MISSING or b is MISSING:
        return a is b
    if isinstance(a, float) or isinstance(b, float):
        if not (isinstance(a, float) and isinstance(b, float)):
            return False
        return struct.pack("<d", a) == struct.pack("<d", b) or (math.isnan(a) and math.isnan(b))
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        return (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b)
                and all(same_value(x, y) for x, y in zip(a, b)))
    return type(a) is type(b) and a == b


def records_equal(ra, rb):
    if len(ra) != len(rb):
        return False
    for a, b in zip(ra, rb):
        if (a.adapter, a.target) != (b.adapter, b.target) or list(a.changes) != list(b.changes):
            return False
        for k in a.changes:
            if not all(same_value(x, y) for x, y in zip(a.changes[k], b.changes[k])):
                return False
    return True


def weird_changes(rng, n=20):
    vals = WEIRD + [math.nan, None, True, False, 0, -1, 2 ** 53, "", "ü✓", [1.5, -0.0, [math.nan, "x"]],
                    list(np.float32(rng.standard_normal(16)).astype(float)), MISSING]
    out = {}
    for i in range(n):
        o = vals[int(rng.integers(len(vals)))]
        new = vals[int(rng.integers(len(vals)))]
        out[f'path[{i}].ünï "q"'] = (o, new)
    return out


def test_round_trip_every_codec():
    rng = np.random.default_rng(3)
    for name in codec.available():
        for trial in range(30):
            recs = [DataChange("xform", "obj01", weird_changes(rng)), DataChange("rna", "mat02", weird_changes(rng, 3))]
            st = Step(trial + 1, "Move", "OBJECT", "obj01", 1.0, DATA, records=recs)
            for effort in (delta.QUICK, delta.NORMAL):
                b = stepmod.step_to_bytes(st, name, effort)
                assert b[10] == 4, "records make it a version 4 step"
                back = stepmod.step_from_bytes(b)
                assert back.kind == DATA and records_equal(st.records, back.records), (name, trial)
                r = stepmod.recompress(b)
                if r is not None:
                    assert records_equal(st.records, stepmod.step_from_bytes(r).records)
        # records on a mesh step too
        k = 50
        d = delta.diff(POSITION_KEY, np.zeros(k * 3, np.float32), np.ones(k * 3, np.float32))
        st = Step(99, "Stroke", "SCULPT", "obj01", 1.0, DELTA, [d],
                  records=[DataChange("xform", "obj01", {"location": ([0.0, 0.0, 0.0], [1.0, 2.0, -0.0])})])
        back = stepmod.step_from_bytes(stepmod.step_to_bytes(st, name))
        assert records_equal(st.records, back.records) and len(back.deltas) == 1
    # a step without records keeps the version 3 layout
    plain = Step(5, "Stroke", "SCULPT", "o", 0.0, DELTA, [])
    assert stepmod.step_to_bytes(plain, "zlib")[10] == 3
    print("PASS test_round_trip_every_codec")


def test_playback_every_position():
    """Two datablocks (an object with mesh steps and data records, a material with data only),
    random walk, every position checked against the recorded values."""
    rng = np.random.default_rng(11)
    rnd = random.Random(5)
    n = 200
    pos = rng.standard_normal(n * 3).astype(np.float32)
    obj_vals, mat_vals = {}, {}
    hist_o, hist_m = History("Ho", ["obj"]), History("Hm", ["mat"])
    exp_o = [(pos.copy(), dict(obj_vals))]
    exp_m = [dict(mat_vals)]
    seq = 0
    paths = ["location", "rotation_euler", "scale", "parent", "constraints[0].influence", "name"]
    for i in range(120):
        seq += 1
        choice = rnd.random()
        if choice < 0.3:                                       # mesh stroke on the object
            new = pos.copy()
            j = rng.choice(n * 3, 30, replace=False)
            new[j] += rng.standard_normal(30).astype(np.float32)
            d = delta.diff(POSITION_KEY, pos, new)
            st = Step(seq, "Stroke", "SCULPT", "obj", 0.0, DELTA, [d])
            pos = new
            hist_o.push(st)
            exp_o.append((pos.copy(), dict(obj_vals)))
        elif choice < 0.7:                                     # data change on the object
            ch = {}
            for p in rnd.sample(paths, rnd.randint(1, 3)):
                old = obj_vals.get(p, MISSING)
                new = MISSING if (old is not MISSING and rnd.random() < 0.15) else \
                    [float(x) for x in rng.standard_normal(3)] if p != "name" else f"n{i}"
                if new is MISSING:
                    obj_vals.pop(p, None)
                else:
                    obj_vals[p] = new
                ch[p] = (old, new)
            hist_o.push(Step(seq, "Move", "OBJECT", "obj", 0.0, DATA, records=[DataChange("xform", "obj", ch)]))
            exp_o.append((pos.copy(), dict(obj_vals)))
        else:                                                  # data change on the material
            p = rnd.choice(["roughness", "base_color", "metallic"])
            old = mat_vals.get(p, MISSING)
            new = rnd.choice([0.25, -0.0, math.inf, [0.1, 0.2, 0.3, 1.0]])
            mat_vals[p] = new
            hist_m.push(Step(seq, "Edit", "OBJECT", "mat", 0.0, DATA, records=[DataChange("rna", "mat", {p: (old, new)})]))
            exp_m.append(dict(mat_vals))
    for packed in (False, True):
        if packed:
            for h in (hist_o, hist_m):
                for e in h.entries:
                    e.pack("zlib")
        so = MeshState({"POINT": n}, {POSITION_KEY: pos.copy()},
                       {("xform", k): v for k, v in obj_vals.items()})
        sm = MeshState({}, {}, {("rna", k): v for k, v in mat_vals.items()})
        for h, st, exp, obj in ((hist_o, so, exp_o, "obj"), (hist_m, sm, exp_m, "mat")):
            h.cursor = len(h.entries)
            order = list(range(len(h.entries) + 1))
            rnd.shuffle(order)
            for p in order:
                changed = h.goto(p, {obj: st})
                if obj == "obj":
                    want_pos, want_vals = exp[p]
                    assert st.arrays[POSITION_KEY].tobytes() == want_pos.tobytes(), (packed, p)
                else:
                    want_vals = exp[p]
                adapter = "xform" if obj == "obj" else "rna"
                got = {k[1]: v for k, v in st.values.items() if k[0] == adapter}
                assert set(got) == set(want_vals) and all(same_value(got[k], want_vals[k]) for k in got), \
                    (packed, obj, p)
                for key in changed.get(obj, ()):
                    assert isinstance(key, str) or key[0] == "@"
    print("PASS test_playback_every_position")


def test_reverse_order_within_a_step():
    """Two records on the same path in one step: going back must undo them in reverse order."""
    h = History("H", ["o"])
    recs = [DataChange("a", "o", {"x": (1.0, 2.0)}), DataChange("a", "o", {"x": (2.0, 3.0)})]
    h.push(Step(1, "s", "OBJECT", "o", 0.0, DATA, records=recs))
    st = MeshState({}, {}, {("a", "x"): 1.0})
    h.cursor = 0
    h.goto(1, {"o": st})
    assert st.values[("a", "x")] == 3.0
    h.goto(0, {"o": st})
    assert st.values[("a", "x")] == 1.0
    print("PASS test_reverse_order_within_a_step")


def _patch_records(blob, fn):
    """Rewrite the records JSON of a codec-"none" step and fix lengths and checksum."""
    mv = memoryview(blob)
    (hl,) = struct.unpack_from("<H", mv, 8)
    head = bytearray(mv[10:10 + hl])
    body = bytes(mv[10 + hl + 1:])
    (rec_n,) = struct.unpack_from("<I", head, hl - 4)
    recs = body[len(body) - rec_n:]
    new = fn(recs)
    body = body[:len(body) - rec_n] + new
    head[hl - 4:hl] = struct.pack("<I", len(new))
    fields = list(stepmod._FIXED.unpack_from(head, 0))
    fields[6] = len(body)
    head[:stepmod._FIXED.size] = stepmod._FIXED.pack(*fields)
    rest = struct.pack("<H", hl) + bytes(head) + bytes((codec.NONE,)) + body
    return stepmod.STEP_MAGIC + struct.pack("<I", zlib.crc32(rest)) + rest


def test_damaged_records_refused():
    st = Step(1, "Move", "OBJECT", "o", 0.0, DATA, records=[DataChange("xform", "o", {"location": ([0.0], [1.0])})])
    blob = stepmod.step_to_bytes(st, "none")
    assert records_equal(st.records, stepmod.step_from_bytes(_patch_records(blob, lambda r: r)).records)
    bad = [b"not json", b"[1,2,3]", b'[["xform"]]', b'[[1, "o", []]]', b'[["a","o",[["p",{"x":1},2]]]]',
           b"[" * 100000 + b"]" * 100000, b'[["a","o",[["p",1]]]]']
    for b in bad:
        try:
            stepmod.step_from_bytes(_patch_records(blob, lambda r, b=b: b))
        except stepmod.CorruptStep:
            continue
        raise AssertionError(f"accepted records {b[:40]!r}")
    # flipping any byte without fixing the checksum is refused
    for i in range(len(blob)):
        b = bytearray(blob)
        b[i] ^= 0x10
        try:
            stepmod.step_from_bytes(bytes(b))
        except ValueError:
            continue
        raise AssertionError(f"accepted a flipped byte at {i}")
    print("PASS test_damaged_records_refused")


def test_memory_and_file_format():
    recs = [DataChange("xform", "o", {"matrix": ([0.0] * 16, [1.0] * 16)})]
    st = Step(1, "Move", "OBJECT", "o", 0.0, DATA, records=recs)
    assert st.nbytes() > Step(1, "Move", "OBJECT", "o").nbytes() + 16 * 16
    reg = Registry()
    h = reg.create("o")
    h.push(st)
    h.push(Step(2, "Move", "OBJECT", "o", 0.0, DATA, records=[DataChange("xform", "o", {"name": ("a", "b")})]))
    h.entries[0].pack()
    blob = persist.registry_to_bytes(reg)
    reg2 = persist.registry_from_bytes(blob)
    assert reg2.load_report["format"] == 4
    h2 = reg2.history_for("o")
    assert [e.load().records[0].changes for e in h2.entries][1] == {"name": ("a", "b")}
    assert records_equal(h2.entries[0].load().records, recs)
    print("PASS test_memory_and_file_format")


def main():
    ok = True
    for t in (test_round_trip_every_codec, test_playback_every_position, test_reverse_order_within_a_step,
              test_damaged_records_refused, test_memory_and_file_format):
        try:
            t()
        except Exception:
            ok = False
            print("FAIL", t.__name__)
            traceback.print_exc()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
