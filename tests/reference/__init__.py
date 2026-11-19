"""Reference implementations: the Python code the Rust engine (native/) replaced, kept as bit-exact
oracles for tests. Nothing under hindsight/ may import this package (tests/test_core_reference.py
fails if it does).

`pure()` runs a block with the Python reference in place of the engine's delta functions.
"""
import contextlib


@contextlib.contextmanager
def pure():
    """Run a block with hindsight.core.delta's functions replaced by the reference (numpy) ones and
    native.lib None (History.goto without apply_steps): Python all the way down."""
    from hindsight.core import delta, native
    from . import delta as ref
    names = ("diff", "apply", "pack_delta", "unpack_delta", "unpack_at", "packed_size")
    saved = {n: getattr(delta, n) for n in names}
    lib = native.lib
    for n in names:
        setattr(delta, n, getattr(ref, n))
    native.lib = None
    try:
        yield
    finally:
        for n, f in saved.items():
            setattr(delta, n, f)
        native.lib = lib
