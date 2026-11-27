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


for t in (test_same_results_and_bytes_pass, test_a_different_value_fails, test_different_bytes_written_fail,
          test_a_different_exception_fails, test_each_side_gets_its_own_copy):
    H.run(t)
H.finish()
