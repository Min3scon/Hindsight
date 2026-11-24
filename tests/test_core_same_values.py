import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R
"""R070: the engine's same_values, the data recorder's "nothing changed" check (datarec._same_values).

  rule        True only for strictly equal values: the same exact type at every level, floats by their
              bits, dicts in the same key order; False for anything else (always safe: the caller then
              compares the exact, slow way)
  reference   equals a Python reference of that rule (written here) on random pairs: equal copies,
              copies with one change, values of other types
  marshal     every pair marshal (version 2, what the recorder compared before) calls equal is True;
              every True pair is equal in marshal version 0, which writes no interning flag (version 2
              can differ only by a string being interned on one side, which the recorder's exact diff
              then finds equal)
  cases       -0.0 and 0.0, 1, 1.0 and True, NaN bits, subclasses, sets and bytes, nesting, cycles"""
import marshal
import random
import struct

from hindsight.core.native import ENGINE

SAME = ENGINE.same_values
F64 = struct.Struct("<d")


def ref(a, b, depth=0) -> bool:
    """The rule, in Python."""
    if a is b:
        return True
    if type(a) is not type(b) or depth > 64:
        return False
    t = type(a)
    if t is float:
        return F64.pack(a) == F64.pack(b)
    if t in (int, str):
        return a == b
    if t in (list, tuple):
        return len(a) == len(b) and all(ref(x, y, depth + 1) for x, y in zip(a, b))
    if t is dict:
        return (len(a) == len(b) and all(ref(ka, kb, depth + 1) and ref(va, vb, depth + 1)
                                         for (ka, va), (kb, vb) in zip(a.items(), b.items())))
    return False


def nan(payload):
    return struct.unpack("<d", struct.pack("<Q", 0x7FF8000000000000 | payload))[0]


SPECIAL_FLOATS = [0.0, -0.0, 1.0, -1.5, 1e-310, float("inf"), float("-inf"), nan(0), nan(1), nan(12345), 0.1, 2.0 ** 60]
STRINGS = ["", "a", "location", "rna", "Cube.001", "ü", "日本", "\ud800x", "a" * 70]


def gen(rng: random.Random, depth=0):
    """A random JSON-like value as the recorder holds them (and tuples, big ints)."""
    k = rng.randrange(12 if depth < 4 else 7)
    if k == 0:
        return None
    if k == 1:
        return rng.random() < 0.5
    if k == 2:
        return rng.choice([0, 1, -1, 7, 2 ** 31, 2 ** 63, -(2 ** 70), rng.randrange(-1000, 1000)])
    if k in (3, 4):
        return rng.choice(SPECIAL_FLOATS) if rng.random() < 0.3 else rng.uniform(-10, 10)
    if k in (5, 6):
        return rng.choice(STRINGS) + (str(rng.randrange(100)) if rng.random() < 0.3 else "")
    if k in (7, 8):
        return [gen(rng, depth + 1) for _ in range(rng.randrange(5))]
    if k == 9:
        return tuple(gen(rng, depth + 1) for _ in range(rng.randrange(4)))
    d = {}
    for _ in range(rng.randrange(5)):
        key = rng.choice(STRINGS) if rng.random() < 0.8 else rng.randrange(10)
        d[key] = gen(rng, depth + 1)
    return d


def copy(v):
    """An equal value made of new objects (strings rebuilt, so not interned)."""
    t = type(v)
    if t is float:
        return F64.unpack(F64.pack(v))[0]
    if t is str:
        return "".join(list(v)) if len(v) != 1 else (v + "x")[:1]
    if t is int:
        return int(str(v))
    if t is list:
        return [copy(x) for x in v]
    if t is tuple:
        return tuple(copy(x) for x in v)
    if t is dict:
        return {copy(k): copy(x) for k, x in v.items()}
    return v


def mutate(rng: random.Random, v):
    """A copy of v with one change somewhere (perhaps one marshal or the rule can't see: then the
    test only checks that native and reference agree)."""
    t = type(v)
    if t is list and v and rng.random() < 0.7:
        i = rng.randrange(len(v))
        return v[:i] + [mutate(rng, v[i])] + v[i + 1:]
    if t is tuple and v and rng.random() < 0.7:
        i = rng.randrange(len(v))
        return v[:i] + (mutate(rng, v[i]),) + v[i + 1:]
    if t is dict and v and rng.random() < 0.7:
        items = list(v.items())
        i = rng.randrange(len(items))
        if rng.random() < 0.2 and len(items) > 1:
            items[0], items[-1] = items[-1], items[0]            # same keys, another order
        else:
            items[i] = (items[i][0], mutate(rng, items[i][1]))
        return dict(items)
    options = [
        lambda: None, lambda: True, lambda: False, lambda: 0, lambda: 1, lambda: 0.0, lambda: -0.0, lambda: 1.0,
        lambda: nan(1), lambda: "", lambda: [], lambda: (), lambda: {},
        lambda: (v + 1) if t is int else (v * -1.0 if t is float else gen(rng)),
        lambda: float(v) if t is int else (int(v) if t is float and v == v and abs(v) < 1e18 else gen(rng)),
        lambda: (not v) if t is bool else gen(rng),
        lambda: list(v) if t is tuple else (tuple(v) if t is list else gen(rng)),
        lambda: (v + "x") if t is str else gen(rng),
    ]
    return rng.choice(options)()


def check_pair(a, b):
    got = SAME(a, b)
    want = ref(a, b)
    assert got is want, (got, want, a, b)
    assert SAME(b, a) is got, ("not symmetric", a, b)
    try:
        m2 = marshal.dumps(a, 2) == marshal.dumps(b, 2)
        m0 = marshal.dumps(a, 0) == marshal.dumps(b, 0)
    except ValueError:
        return got                                   # (no such values are generated)
    if m2:
        assert got, ("marshal-equal but not the same", a, b)
    if got:
        assert m0, ("same but different in marshal", a, b)
    return got


def test_random_pairs():
    rng = random.Random(70)
    n_same = n_diff = 0
    for i in range(6000):
        a = gen(rng)
        b = copy(a) if i % 2 == 0 else mutate(rng, copy(a))
        if check_pair(a, b):
            n_same += 1
        else:
            n_diff += 1
        # a recorder-like dict of values: (adapter, path) keys, most values the same object
        if i % 10 == 0:
            keys = [("rna", f"p{j}") for j in range(rng.randrange(1, 40))]
            old = {k: gen(rng) for k in keys}
            new = {k: (v if rng.random() < 0.4 else copy(v)) for k, v in old.items()}
            assert check_pair(old, new)
            k = rng.choice(keys)
            new[k] = mutate(rng, new[k])
            check_pair(old, new)
    assert n_same > 2500 and n_diff > 1500, (n_same, n_diff)
    print(f"  test_random_pairs ({n_same} same, {n_diff} different, plus recorder-like dicts)")


def test_hypothesis():
    try:
        from hypothesis import given, settings, strategies as hs, HealthCheck
    except ImportError:
        raise R.Skip("hypothesis not installed (test_random_pairs checks the same rule)")
    # The Blender program runs with denormals flushed to zero (its CPU mode, as test_bl_t1_prop notes); hypothesis then
    # refuses to make subnormal floats. Where they exist (the bpy module) they are drawn; test_cases has some from their bits.
    sub = float.fromhex('0x1p-1060') * float.fromhex('0x1p-10') > 0.0   # False when flushed to zero
    leaf = (hs.none() | hs.booleans() | hs.integers() | hs.floats(allow_nan=True, allow_infinity=True, allow_subnormal=sub)
            | hs.text(max_size=8))
    value = hs.recursive(leaf, lambda ch: hs.lists(ch, max_size=4) | hs.tuples(ch, ch)
                         | hs.dictionaries(hs.text(max_size=4) | hs.integers(-3, 3), ch, max_size=4), max_leaves=30)

    @settings(max_examples=800, deadline=None, suppress_health_check=list(HealthCheck))
    @given(value, value, hs.integers(0, 2 ** 31))
    def prop(a, b, seed):
        check_pair(a, b)
        check_pair(a, copy(a))
        assert SAME(a, copy(a)), a
        check_pair(a, mutate(random.Random(seed), copy(a)))
    prop()
    print("  test_hypothesis (800 examples of three pairs each)")


def test_cases():
    class MyInt(int):
        pass

    class MyStr(str):
        pass

    class MyList(list):
        pass

    class MyFloat(float):
        pass

    S = SAME
    # numbers: types are exact, floats by bits
    assert S(1, 1) and S(2 ** 100, 2 ** 100) and S(-(2 ** 64) + 1, int("-18446744073709551615"))
    assert not S(1, 1.0) and not S(1, True) and not S(0, False) and not S(1.0, True) and not S(True, False)
    assert S(True, True) and S(None, None) and not S(None, 0) and not S(None, False) and not S(None, [])
    assert S(0.0, F64.unpack(F64.pack(0.0))[0]) and not S(0.0, -0.0) and not S([0.0], [-0.0])
    assert S(nan(7), nan(7)) and not S(nan(7), nan(8)) and not S(nan(0), -nan(0)) and not S(float("nan"), 0.0)
    assert S(float("inf"), float("inf")) and not S(float("inf"), float("-inf"))
    # subnormals, made from their bits (so also where the process flushes them to zero in arithmetic): by bits
    sub1, sub2 = (struct.unpack("<d", struct.pack("<Q", b))[0] for b in (1, 2))
    msub = struct.unpack("<d", struct.pack("<Q", (1 << 63) | 1))[0]
    assert S(sub1, sub1) and S([sub2], [struct.unpack("<d", struct.pack("<Q", 2))[0]])
    assert not S(sub1, sub2) and not S(sub1, 0.0) and not S(sub1, msub) and not S({"a": sub1}, {"a": 0.0})
    assert S(0.1 + 0.2, 0.30000000000000004) and not S(0.1 + 0.2, 0.3)
    # subclasses and other types are another type: False even when equal
    assert not S(MyInt(3), 3) and not S(MyInt(3), MyInt(3)) and not S(MyStr("a"), MyStr("a")) and not S(MyStr("a"), "a")
    assert not S(MyList([1]), MyList([1])) and not S(MyFloat(1.0), MyFloat(1.0))
    b1, b2 = bytes(bytearray(b"abc")), bytes(bytearray(b"abc"))
    assert b1 is not b2 and not S(b1, b2) and not S(bytearray(b"x"), bytearray(b"x"))
    assert not S({1, 2}, {1, 2}) and not S(frozenset([1]), frozenset([1])) and not S(complex(0, 1), complex(0, 1))
    x = object()
    assert S(x, x) and not S(object(), object()), "the same object is the same value"
    # str by value
    s1, loc = "".join(["loc", "ation"]), "location"
    assert s1 is not loc and S(s1, loc) and not S("a", "b") and not S("a", "a ")
    assert S("\ud800", "\ud800") and S("日本", "".join(["日", "本"]))
    # containers: same container type, element by element, dicts in the same key order
    assert S([], []) and S((), ()) and S({}, {}) and not S([], ()) and not S([1], (1,)) and not S({}, [])
    assert S([1, [2.5, {"a": [None, True]}]], [1, [2.5, {"a": [None, True]}]])
    assert not S([1, [2.5, {"a": [None, True]}]], [1, [2.5, {"a": [None, 1]}]])
    assert not S([1, [2.5, {"a": [None, True]}]], [1, [2.5, {"b": [None, True]}]])
    assert not S([1, 2], [1, 2, 3]) and not S([1, 2, 3], [1, 2])
    assert S({"a": 1, "b": 2}, {"a": 1, "b": 2}) and not S({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert not S({"a": 1}, {"a": 1, "b": 2}) and not S({"a": 1}, {"a": 1.0}) and not S({1: 0}, {1.0: 0})
    assert not S({1: 0}, {True: 0})
    assert S({("rna", "location"): [0.0, 1.0]}, {("rna", "location"): [0.0, 1.0]})
    assert not S({("rna", "location"): [0.0, 1.0]}, {["rna", "location"][0] + "x": [0.0, 1.0]})
    # deep nesting: equal up to 64 levels, False (never an error) deeper; a cycle ends there too
    deep_a, deep_b = 1, 1
    for _ in range(60):
        deep_a, deep_b = [deep_a], [deep_b]
    assert S(deep_a, deep_b)
    for _ in range(20):
        deep_a, deep_b = [deep_a], [deep_b]
    assert not S(deep_a, deep_b) and S(deep_a, deep_a)
    c1, c2 = [], []
    c1.append(c1)
    c2.append(c2)
    assert not S(c1, c2) and S(c1, c1)
    d1, d2 = {}, {}
    d1["x"] = d1
    d2["x"] = d2
    assert not S(d1, d2)


def main():
    tests = [test_cases, test_random_pairs, test_hypothesis]
    for t in tests:
        R.run(t)
    R.finish()


if __name__ == "__main__":
    main()
