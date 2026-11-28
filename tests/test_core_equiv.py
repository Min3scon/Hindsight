"""#129: the equivalence check every Rust move (#24) runs, reference against engine: it catches a different value,
different bytes written and a different exception, and passes when the two agree bit for bit.
Run: <bpy python> tests/test_core_equiv.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.argv = [sys.argv[0]]
import numpy as np  # noqa: E402

import tests.harness as H  # noqa: E402
from tests import equiv  # noqa: E402


def fails(ref, eng, *args, **kw):
    try:
        equiv.check(ref, eng, *args, **kw)
    except AssertionError as e:
        return str(e)
    raise AssertionError("equiv.check passed a difference")


def write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def test_same_results_and_bytes_pass():
    assert equiv.check(lambda a: a * 2, lambda a: a * 2, np.arange(4.0)).tolist() == [0, 2, 4, 6]
    assert equiv.check(write, write, equiv.Out("h.bin"), b"abc") is None
    err = equiv.check(int, int, "x")
    assert isinstance(err, ValueError), err


def test_a_different_value_fails():
    assert "result" in fails(lambda: {"a": [1, 2]}, lambda: {"a": [1, 3]})
    assert "result" in fails(lambda: 0.0, lambda: -0.0)                       # bit for bit, not ==
    assert "result" in fails(lambda: np.zeros(2, "f4"), lambda: np.zeros(2, "f8"))
    assert "result" in fails(lambda: float("nan"), lambda: 1.0)


def test_different_bytes_written_fail():
    assert "h.bin" in fails(write, lambda p, d: write(p, d + b"!"), equiv.Out("h.bin"), b"abc")
    assert "h.bin" in fails(write, lambda p, d: None, equiv.Out("h.bin"), b"abc")

    def in_place(a):
        a[0] = 1

    assert "argument 0" in fails(in_place, lambda a: None, np.zeros(3))


def test_a_different_exception_fails():
    def raises(cls, msg):
        def f():
            raise cls(msg)
        return f

    assert "exception" in fails(raises(ValueError, "bad header"), raises(ValueError, "bad header (crc)"))
    assert "exception" in fails(raises(ValueError, "bad"), raises(KeyError, "bad"))
    assert "exception" in fails(raises(ValueError, "bad"), lambda: None)
    assert "exception" in fails(lambda: None, raises(ValueError, "bad"))


def test_each_side_gets_its_own_copy():
    def grow(a):
        a.append(1)
        return len(a)

    assert equiv.check(grow, grow, []) == 1


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Slot:
    __slots__ = ("x", "y")


def test_objects_compare_both_sides_names():
    """An attribute only one side has is a difference, None against no attribute too; an object with nothing set on
    it is compared with ==."""
    assert "on one side only" in fails(lambda: Obj(a=1), lambda: Obj(a=1, extra=2))
    assert "on one side only" in fails(lambda: Obj(a=1, x=None), lambda: Obj(a=1))
    s1, s2 = Slot(), Slot()
    s1.x, s2.x, s2.y = 1, 1, 2
    assert "on one side only" in fails(lambda: s1, lambda: s2)
    assert "top" in fails(lambda: Obj(), lambda: object.__new__(Obj))   # nothing set: object() == is identity
    assert equiv.check(lambda: Obj(a=[1.5]), lambda: Obj(a=[1.5])).a == [1.5]


def test_registry_key_sees_the_load_report_and_runs_no_shipped_code():
    """equiv.registry (the key a move's registry reading is checked with) includes the load report, so a move that
    reports damage differently fails; and it never calls persist's own helpers."""
    from tests import corpus as C
    from hindsight.core import persist
    data = C.payload("beta2/folder/scene_hindsight/history.hsr", "registry")

    def changed(b):
        reg = persist.registry_from_bytes(b)
        reg.load_report = dict(reg.load_report, damaged=list(reg.load_report.get("damaged", [])) + ["x"])
        return reg

    saved, persist._meta_for_json = persist._meta_for_json, None      # the key must not need it
    try:
        assert equiv.check(persist.registry_from_bytes, lambda b: persist.registry_from_bytes(b), data,
                           key=equiv.registry).histories
        assert "load_report" in fails(persist.registry_from_bytes, changed, data, key=equiv.registry)
        for _n, b in C.damaged(data, "registry"):
            try:
                persist.registry_from_bytes(b)
            except ValueError:
                continue
            assert "load_report" in fails(persist.registry_from_bytes, changed, b, key=equiv.registry)
    finally:
        persist._meta_for_json = saved


for t in (test_same_results_and_bytes_pass, test_a_different_value_fails, test_different_bytes_written_fail,
          test_a_different_exception_fails, test_each_side_gets_its_own_copy,
          test_objects_compare_both_sides_names, test_registry_key_sees_the_load_report_and_runs_no_shipped_code):
    H.run(t)
H.finish()
