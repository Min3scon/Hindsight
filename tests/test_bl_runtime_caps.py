"""The memory limit when nothing more may be dropped (review finding S4-3).

A history scrubbed back keeps its steps at and after its cursor (D64). When those alone are over
the memory limit, every new stroke elsewhere used to bring a pass over every step of every history.
Run: <bpy python> tests/test_bl_runtime_caps.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy  # noqa: E402
import tests.harness as H  # noqa: E402
from hindsight.bl import runtime  # noqa: E402
from hindsight.core.types import SETTINGS, Entry  # noqa: E402

C = bpy.context


class _count_reads:
    """Count Entry.nbytes calls on the given entries."""
    def __init__(self, entries):
        self.ids = {id(e) for e in entries}
        self.n = 0

    def __enter__(self):
        self.real = Entry.nbytes
        me = self

        def nbytes(e):
            if id(e) in me.ids:
                me.n += 1
            return me.real(e)
        Entry.nbytes = nbytes
        return self

    def __exit__(self, *a):
        Entry.nbytes = self.real


def _stuck_scene():
    H.fresh()
    SETTINGS.memory_limit_mb = 4096
    a = H.make_mesh(30000, name="A")
    H.enter(a, "SCULPT")
    for i in range(12):
        H.fake_stroke(a, i + 1, n=20000)
        assert runtime.commit(a) is not None
    ha = runtime.history_of(a)
    ha.cursor = 0          # scrubbed back to the start (as a jump leaves it): none of A's steps may go
    H.enter(a, "OBJECT")
    b = H.make_mesh(3000, name="B", location=(4, 0, 0))
    H.enter(b, "SCULPT")
    from hindsight.core import store
    assert store.total_bytes(runtime.reg().all()) > (2 << 20)
    SETTINGS.memory_limit_mb = 1
    return a, ha, b


def test_stroke_while_stuck_reads_only_its_history():
    a, ha, b = _stuck_scene()
    try:
        for i in range(6):
            H.fake_stroke(b, 100 + i)
            with _count_reads(ha.entries) as c:
                runtime.commit(b)
            hb = runtime.history_of(b)
            assert len(hb.entries) == 0, "the limit still holds: B's new step goes"
            assert len(ha.entries) == 12 and ha.cursor == 0, "A's steps are never dropped"
            if i:                                    # the first pass finds out it is stuck
                assert c.n == 0, f"stroke {i}: A's {len(ha.entries)} steps were read {c.n} times"
        assert runtime.MEMORY["evicted"] == 6
    finally:
        SETTINGS.memory_limit_mb = 4096


def test_cap_holds_when_something_may_go_again():
    """Not weaker: a cursor moved forward or a new history brings back the whole pass."""
    a, ha, b = _stuck_scene()
    try:
        for i in range(3):
            H.fake_stroke(b, 200 + i)
            runtime.commit(b)
        ha.cursor = 5                                # A scrubbed forward: its first 5 steps may go
        first = ha.entries[5].seq
        H.fake_stroke(b, 300)
        runtime.commit(b)
        assert ha.cursor == 0 and ha.entries[0].seq == first, "A's past went first, as the whole pass does"
        H.enter(b, "OBJECT")                         # (adding a mesh in Sculpt Mode crashes headless)
        c = H.make_mesh(3000, name="Cnew", location=(8, 0, 0))
        H.enter(c, "SCULPT")
        H.fake_stroke(c, 400)
        assert runtime.commit(c) is not None
        hc = runtime.history_of(c)
        assert len(hc.entries) == 0, "a new history's step goes too (A is still over the limit)"
        assert len(ha.entries) == 7
    finally:
        SETTINGS.memory_limit_mb = 4096


for f in [test_stroke_while_stuck_reads_only_its_history, test_cap_holds_when_something_may_go_again]:
    H.run(f)
H.finish()
