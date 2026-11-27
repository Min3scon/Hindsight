"""#131 (24.3), first part: the JSON of a step's extra and data records and the header dict of read_header run in the
engine. hindsight/core/step.py against its reference (tests/reference/step_json.py), bit for bit through
tests/equiv.py: random values (hypothesis and random float bits), every step of the corpus (corpus.FILES and
corpus.MADE), and hostile values and bytes (results, exception class and message). Until the engine code exists
(#146) step.py is still the reference's code, so these pass; #146 adds the test that step.py runs no JSON of its own.
Run: <bpy python> tests/test_core_step_json.py"""
import os
import random
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

import tests.harness as H  # noqa: E402
import tests.runner as R  # noqa: E402
from tests import corpus as C, equiv  # noqa: E402
from tests.reference import step_json as ref, types as RT  # noqa: E402
from hindsight.core import step  # noqa: E402
from hindsight.core.types import MISSING, DataChange  # noqa: E402

RNG = random.Random(131)


class Odd:
    """A value json can't encode. It has an attribute: equiv.check compares an object with nothing set on it by ==."""

    def __init__(self):
        self.tag = "odd"


EDGE_FLOATS = [0.0, -0.0, 1.0, -1.0, 0.1, 1e16, 1e15, 9999999999999998.0, 1e-4, 1e-5, 1e22, 1e-7, 5e-324,
               2.2250738585072014e-308, 1.7976931348623157e308, 123.456, 1 / 3, float("nan"), float("inf"),
               float("-inf"), 2.0 ** 53, 2.0 ** 63, 0.30000000000000004, 100.0, 1e100, 3.4028234663852886e38]


def on_ref(f):
    """f given the reference's own objects (equiv.like): DataChange and MISSING of tests/reference/types.py."""
    return lambda *a: f(*equiv.like(a, RT))


def same(fn, *args):
    # no wrapper where there is nothing to convert: its frame would move where a RecursionError hits (3.11)
    f = on_ref(getattr(ref, fn)) if equiv.holds_shapes(args) else getattr(ref, fn)
    return equiv.check(f, getattr(step, fn), *args, key=equiv.plain)


def same_deep(fn, v):
    """same() for a value too deep for equiv.check's deepcopy: both sides get the one object (neither changes it)."""
    out = []
    # the reference's own objects taken first: a wrapper's frame would move where a RecursionError hits (3.11)
    for f, x in ((getattr(ref, fn), equiv.like(v, RT)), (getattr(step, fn), v)):
        try:
            out.append(("result", f(x)))
        except Exception as e:                  # noqa: BLE001 - the exception is what is compared
            out.append(equiv._exc(e))
    assert out[0] == out[1], (fn, out)


def rand_str(n=8):
    pool = [0x22, 0x5C, 0x2F, 0x7F, 0x80, 0xFF, 0x100, 0x7FF, 0x800, 0xFFFF, 0x10000, 0x1F600, 0x10FFFF, 0xD800, 0xDFFF]
    out = []
    for _ in range(RNG.randint(0, n)):
        r = RNG.random()
        out.append(chr(RNG.randint(0, 0x1F) if r < .1 else RNG.choice(pool) if r < .25 else RNG.randint(0x20, 0x7E)
                       if r < .8 else RNG.randint(0x80, 0x10FFFF)))
    return "".join(out)


def rand_float():
    return struct.unpack("<d", struct.pack("<Q", RNG.getrandbits(64)))[0] if RNG.random() < .7 else \
        RNG.choice(EDGE_FLOATS)


def rand_value(depth=0, records=False):
    r = RNG.random()
    if depth > 3 or r < .45:
        return RNG.choice([None, True, False, RNG.randint(-2 ** 70, 2 ** 70), RNG.randint(-9, 9), rand_float(),
                           rand_str()] + ([MISSING] if records else [np.float32(rand_float()), np.int64(-7),
                                                                     np.bool_(True), np.float64(0.1)]))
    if r < .65:
        return [rand_value(depth + 1, records) for _ in range(RNG.randint(0, 4))]
    if r < .75:
        return tuple(rand_value(depth + 1, records) for _ in range(RNG.randint(0, 3)))
    if r < .8 and not records:
        return RNG.choice([np.arange(RNG.randint(0, 5), dtype=RNG.choice([np.float32, np.int32, np.uint8])),
                           {1, 2, 3}, frozenset({"a"})])
    keys = [rand_str(4), RNG.randint(-5, 5), rand_float(), True, None] if not records else [rand_str(4), 7, 1.5, None]
    return {RNG.choice(keys) if RNG.random() < .3 else rand_str(4): rand_value(depth + 1, records)
            for _ in range(RNG.randint(0, 4))}


def rand_records():
    return [DataChange(rand_str(5), rand_str(5), {rand_str(6): (rand_value(records=True), rand_value(records=True))
                                                  for _ in range(RNG.randint(0, 4))}) for _ in range(RNG.randint(0, 4))]


def test_extra_json_is_the_reference_for_random_values():
    for _ in range(3000):
        v = {rand_str(5): rand_value() for _ in range(RNG.randint(0, 5))}
        same("_dumps", v)
        same("_dumps", rand_value())
    for o in (np.float32(0.1), np.int8(-3), np.bool_(False), np.zeros((2, 2), np.float32), {3, 1}, frozenset(),
              Odd(), b"x", 1j, (1, 2)):
        same("_json_default", o)


def test_float_and_string_bits():
    fs = EDGE_FLOATS + [rand_float() for _ in range(50000)]
    for i in range(0, len(fs), 500):
        same("_dumps", fs[i:i + 500])
        same("_dumps", {repr(f): f for f in fs[i:i + 50]})
        same("_dumps", {f: 1 for f in fs[i:i + 50]})
    for _ in range(5000):
        s = rand_str(20)
        same("_dumps", [s, {s: s}])


def test_hypothesis_values():
    try:
        from hypothesis import given, settings, strategies as hs, HealthCheck
    except ImportError:
        raise R.Skip("hypothesis not installed")
    leaf = hs.none() | hs.booleans() | hs.integers() | hs.floats(allow_nan=True) | hs.text()
    vals = hs.recursive(leaf, lambda c: hs.lists(c) | hs.dictionaries(hs.text(), c) | hs.tuples(c, c), max_leaves=30)

    @settings(max_examples=400, deadline=None, suppress_health_check=list(HealthCheck))
    @given(vals, vals, hs.text())
    def run(v, w, k):
        same("_dumps", {k: v, "w": w})
        recs = [DataChange(k, "t", {"p": (v, w), k: (MISSING, v)})]
        b = equiv.check(on_ref(ref.records_to_bytes), step.records_to_bytes, recs)
        same("records_from_bytes", b)
    run()


def test_records_json_round_trip():
    for _ in range(3000):
        recs = rand_records()
        b = same("records_to_bytes", recs)
        same("records_from_bytes", b)
        same("records_from_bytes", bytearray(b))
    same("records_to_bytes", [])
    same("records_from_bytes", b"[]")


class Sub(dict):
    def items(self):
        return [("x", 1)]


class Lst(list):
    def __iter__(self):
        return iter([9])


class Rec:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def nest(n, inner=1):
    v = inner
    for _ in range(n):
        v = [v]
    return v


def test_hostile_values():
    loop = [1]
    loop.append(loop)
    dloop = {}
    dloop["a"] = dloop
    bad = [Odd(), b"x", 1j, loop, dloop, {(1, 2): 1}, {b"k": 1}, Sub(a=2), Lst([1, 2]), nest(150), nest(199),
           nest(201), {"a": np.float32(1.5)}, [np.arange(3)], {np.int64(1): 2}, "\ud800", {"\udfff": 1},
           [float("nan")], 2 ** 5000, [True, 1, 1.0], {1: 1, "1": 2}, {False: 1, None: 2, 0.5: 3}]
    for v in bad:
        same("_dumps", v)
    same_deep("_dumps", nest(5000))
    same_deep("records_to_bytes", [DataChange("a", "t", {"p": (nest(150), nest(5000))})])
    recs = [[DataChange("a", "t", {"p": (v, 1)})] for v in bad] + [
        [DataChange("a", "t", {"p": (np.float32(1), 1)})], [DataChange(1, None, {"p": (1, 2)})],
        [DataChange("a", "t", {1: (1, 2), (1,): (3, 4)})], [DataChange("a", "t", {"p": (1, 2, 3)})],
        [DataChange("a", "t", {"p": [1]})], [DataChange("a", "t", {"p": 5})], [DataChange("a", "t", [("p", (1, 2))])],
        [DataChange("a", "t", Sub())], [Rec(adapter="a", target="t")], [Rec(adapter="a")], [1], 5, None,
        [DataChange("a", "t", {"p": ({1: 2, "1": 3}, [MISSING, (MISSING,)])})],
        [DataChange("a", "t", {"p": (Lst([1]), {"d": {"$d": 1}})}), DataChange("b", "t", {"p": (Odd(), 1)})],
        [DataChange("a", "t", {"p": ([1, 2], "x" * 3)}), DataChange("b", "t", 7)]]
    for r in recs:
        same("records_to_bytes", r)


def bytes_cases():
    good = step.records_to_bytes(rand_records() + [DataChange("a", "b", {"p": ([1.5, "é", None], {"k": MISSING})})])
    yield from (good[:i] for i in range(0, len(good), max(1, len(good) // 40)))
    for i in range(0, len(good), max(1, len(good) // 60)):
        b = bytearray(good)
        b[i] ^= RNG.choice([1, 0x20, 0x80, 0xFF])
        yield bytes(b)
    yield from (s.encode("utf-8", "surrogatepass") for s in (
        "[]", " [] ", "\ufeff[]", "[[\"a\",\"b\",[]]]", "{}", "{\"abc\":1}", "5", "\"abc\"", "null", "[1]", "[[1,2,3]]",
        "[[\"a\",\"b\",\"c\"]]", "[[\"a\",\"b\",[[1,2]]]]", "[[\"a\",\"b\",[[1,2,3,4]]]]", "[[\"a\",\"b\",[[\"p\",{\"x\":1},1]]]]",
        "[[\"a\",\"b\",[[\"p\",{\"$d\":1},1]]]]", "[[\"a\",\"b\",[[\"p\",{\"$d\":{\"k\":{\"$\":\"missing\"}}},{\"$\":\"missing\",\"y\":1}]]]]",
        "[[\"a\",\"b\",[[1.5,NaN,Infinity],[null,-Infinity,-0.0],[[1,2],1e400,123456789012345678901234567890]]]]",
        "[[\"a\",\"b\",[[\"p\",1,2],[\"p\",3,4]]]]", "[[\"a\",\"b\",[[\"p\",{\"a\":1,\"a\":2},[]]]]]",
        "[[\"\\ud800\",\"b\",[]]]", "[[\"\\ud83d\\ude00\\u00e9\\n\\/\",\"b\",[]]]", "[[\"a\x01\",\"b\",[]]]", "[1,]", "[01]",
        "[1.]", "[.5]", "[-]", "[1e]", "[\"\\x\"]", "[tru]", "[nan]", "[1] x", "", "   ", "[\"\\u12\"]",
        "[[\"a\",\"b\",[[\"p\"," + "[" * 150 + "]" * 150 + ",1]]]]",
        "[[\"a\",\"b\",[[\"p\"," + "[" * 250 + "]" * 250 + ",1]]]]", "[" * 5000 + "]" * 5000,
        "[[\"a\",\"b\",[[\"p\"," + "{\"$d\":" * 150 + "1" + "}" * 150 + ",1]]]]"))
    yield b"\xff\xfe[]"
    yield b"[[\"a\xc3\",\"b\",[]]]"


def test_hostile_bytes():
    n = 0
    for b in bytes_cases():
        same("records_from_bytes", b)
        same("_extra", b)
        n += 1
    for b in (b"", b"{}", b"[[\"a\", 1]]", b"[1]", b"[[1,2,3]]", b"\"ab\"", b"{\"a\":[1,{\"b\":null}]}", b"1",
              b"{\"_snapshots\":{}}", b"[\"ab\"]"):
        same("_extra", b)
    print(f"  test_hostile_bytes ({n} byte strings)")


def test_corpus_steps():
    n = 0
    for b in C.steps():
        same("read_header", b)
        r = ref.read_header(b)
        if r.get("v") == 3 and r["records"]:
            same("records_from_bytes", step.records_to_bytes(step.step_from_bytes(b).records))
        n += 1
    assert n > 50, n
    for i, b in enumerate(C.steps()):
        if i % 10 == 0:
            for d in (b[:20], b[:4] + b"\0" * 8, b"HSS1\5\0\0\0[1,]", b"HSS1\2\0\0\0{}", b"HSS3"):
                same("read_header", d)
    print(f"  test_corpus_steps ({n} steps)")


def test_json_kept_out_of_python():
    """step.py holds no JSON of its own: its names are the engine's (or call it)."""
    src = open(step.__file__, encoding="utf-8").read()
    assert "json." not in src and "import json" not in src, "step.py still runs Python JSON"


for t in (test_extra_json_is_the_reference_for_random_values, test_float_and_string_bits, test_hypothesis_values,
          test_records_json_round_trip, test_hostile_values, test_hostile_bytes, test_corpus_steps,
          test_json_kept_out_of_python):
    H.run(t)
H.finish()
