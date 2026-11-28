"""Reference implementations: the Python code the Rust engine (native/) replaced, kept as bit-exact
oracles for tests. Nothing under hindsight/ may import this package (tests/test_core_reference.py
fails if it does).

`pure()` runs a block with the Python reference in place of the engine's delta functions.
"""
import contextlib


@contextlib.contextmanager
def pure():
    """Run a block with hindsight.core.delta's functions replaced by the reference (numpy) ones.
    (History: reference.history.History crosses steps one at a time in Python.)"""
    from hindsight.core import delta
    from . import delta as ref
    names = ("diff", "apply", "pack_delta", "unpack_delta", "unpack_at")   # packed_size is only in the reference
    saved = {n: getattr(delta, n) for n in names}
    for n in names:
        setattr(delta, n, getattr(ref, n))
    try:
        yield
    finally:
        for n, f in saved.items():
            setattr(delta, n, f)
