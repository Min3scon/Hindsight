"""Reference implementations: the Python code the Rust engine (native/) replaced, kept as bit-exact
oracles for tests. Nothing under hindsight/ may import this package (tests/test_core_reference.py
fails if it does).

`pure()` runs a block with the engine's per-function delta paths off (hindsight.core.native.lib is
None), so a reference step encode or decode is Python all the way down.
"""
import contextlib


@contextlib.contextmanager
def pure():
    from hindsight.core import native
    saved = native.lib
    native.lib = None
    try:
        yield
    finally:
        native.lib = saved
