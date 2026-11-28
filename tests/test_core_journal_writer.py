"""#130 (24.2), second part (#153): the crash journal's writer thread runs in the engine (native/src/journal.rs). A
record handed to it writes the bytes R079's Python writer (tests/reference/journal_writer.py) wrote, in order and
around the records the main thread writes when none is waiting (as _on_journal chooses), its payload what
runtime.step_payload hands over; a step edited or let go after the hand-off doesn't change it; a write error is
reported at the next drain, once, and the records around it stay; stopping the writer writes every record handed
over. And the writer never takes the GIL (owner point 1, dev/RUST_DESIGN.md rule 3): it writes while the main thread
holds the GIL. Run: <bpy python> tests/test_core_journal_writer.py"""
import errno
import gc
import os
import random
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tests.harness as H  # noqa: E402
from tests import corpus as C, equiv  # noqa: E402
from tests.reference import journal as RJ, journal_writer as RW  # noqa: E402
from tests.test_core_saveload_storage import _Engine, _Ref  # noqa: E402
from tests.test_core_topopack import edit, handed_step  # noqa: E402
from hindsight.bl import runtime  # noqa: E402
from hindsight.core import journal as J, step as S  # noqa: E402
from hindsight.core.native import ENGINE as _N  # noqa: E402
from hindsight.core.types import JOURNAL_CODEC, Entry  # noqa: E402


class RefSide:
    """R079's Python writer and the reference Journal."""
    J = RJ
    pending = staticmethod(RW.pending)
    drain = staticmethod(RW.drain_journal_writer)
    stop = staticmethod(RW._stop_journal_writer)

    @staticmethod
    def put(j, rtype, header, fn):
        RW.put(j, rtype, header, fn)


class EngineSide:
    """The engine's writer and Journal, handed what _on_journal hands it."""
    J = J
    pending = staticmethod(J.writer_pending)
    drain = staticmethod(J.writer_drain)
    stop = staticmethod(J.writer_stop)

    @staticmethod
    def put(j, rtype, header, fn):
        j.append_later(rtype, header, *(fn.later if fn is not None else ()))


def payload(spec):
    """runtime.step_payload of a step ("step", its bytes: encoded at the hand-off from its parts) or of a packed
    entry ("blob", its bytes), or None."""
    if spec is None:
        return None
    kind, b = spec
    return runtime.step_payload(Entry(S.step_from_bytes(b)) if kind == "step" else Entry(blob=b))


def hand(side, j, rtype, header, fn, big):
    """_on_journal: to the writer if a record is waiting or the step is big, else written here."""
    if side.pending() or big:
        side.put(j, rtype, header, fn)
    else:
        j.append(rtype, header, fn() if fn is not None else b"")
        j.flush(fsync=False)


def drive(side, path, plan):
    j = side.J.Journal(path)
    out = []
    try:
        for op in plan:
            if op == "drain":
                side.drain()
                out.append(j.size())
            else:
                rtype, header, spec, big = op
                hand(side, j, rtype, header, payload(spec), big)
        side.drain()
    finally:
        j.close()
    return out


def plan_of(rng, steps):
    plan = []
    for i, b in enumerate(steps):
        r = rng.random()
        if r < 0.15:
            plan.append(("CURSOR", {"hist": f"h{i % 3}", "cursor": i, "s": "abc"}, None, rng.random() < 0.3))
        elif r < 0.2:
            plan.append("drain")
        elif r < 0.25:
            plan.append(("META", {"hist": "h0", "members": ["a", "b"], "enabled": True, "f": 0.1 * i}, None, False))
        plan.append(("STEP", {"hist": f"h{i % 3}", "at": i, "s": "abc"}, (rng.choice(("step", "blob")), b),
                     rng.random() < 0.6))
    return plan


def test_records_in_order_as_the_python_writer():
    steps = list(C.steps())
    assert len(steps) > 100, len(steps)
    for seed in (153, 1530, 15300):
        plan = plan_of(random.Random(seed), steps)
        equiv.check(lambda p, pl: drive(RefSide, p, pl), lambda p, pl: drive(EngineSide, p, pl), equiv.Out("j.hsj"),
                    plan)


def test_a_step_edited_or_let_go_after_the_hand_off_writes_as_handed():
    want = S.step_to_bytes(handed_step(), JOURNAL_CODEC, 0)
    d = tempfile.mkdtemp(prefix="hs_jwriter_")
    try:
        j = J.Journal(os.path.join(d, "j.hsj"))
        st = handed_step()
        _N.journal_writer_delay(0.3)         # the writer is still waiting while the step changes
        EngineSide.put(j, "STEP", {"hist": "h", "at": 1}, runtime.step_payload(Entry(st)))
        edit(st)
        st.deltas.clear()
        st.after = None
        st.extra.clear()
        del st                               # its arrays live on in what the writer pinned
        gc.collect()
        assert J.writer_pending() == 1, "the writer did not wait"
        J.writer_drain()
        assert [r[2] for r in j.read()] == [want], "the record is not the step as it was handed over"
        j.close()
    finally:
        _N.journal_writer_delay(0)
        shutil.rmtree(d, ignore_errors=True)


def test_a_write_error_is_reported_at_the_next_drain():
    """A full disk under the writer (the faults of test_core_saveload_storage.py): the next drain reports it, once;
    the records before and after the one cut back stay."""
    for side, faults in ((RefSide, _Ref()), (EngineSide, _Engine())):
        d = tempfile.mkdtemp(prefix="hs_jwriter_")
        try:
            path = os.path.join(d, "j.hsj")
            j = side.J.Journal(path)
            j.append("SAVE", {"blend": "a"})
            j.append("CURSOR", {"hist": "h", "cursor": 1})
            j.flush(fsync=False)
            faults.fail_write(j)
            side.put(j, "STEP", {"hist": "h", "at": 1}, payload(("blob", b"x" * 5000)))
            side.put(j, "CURSOR", {"hist": "h", "cursor": 2}, None)          # space again
            try:
                side.drain()
            except OSError as e:
                assert e.errno == errno.ENOSPC, (side.__name__, e)
            else:
                raise AssertionError(f"{side.__name__}: the full disk was not reported")
            side.drain()                     # reported once
            j.close()
            recs = side.J.Journal(path).read()
            assert [(r[0], r[1].get("cursor")) for r in recs] == [("SAVE", None), ("CURSOR", 1), ("CURSOR", 2)], \
                (side.__name__, recs)
        finally:
            faults.done()
            shutil.rmtree(d, ignore_errors=True)


def test_stopping_writes_every_record_handed_over():
    """Turning Hindsight off (and so quitting) stops the writer: what was handed over is in the journal first."""
    for side in (RefSide, EngineSide):
        d = tempfile.mkdtemp(prefix="hs_jwriter_")
        try:
            path = os.path.join(d, "j.hsj")
            j = side.J.Journal(path)
            _N.journal_writer_delay(0.1)
            for i in range(3):
                side.put(j, "STEP", {"hist": "h", "at": i}, payload(("step", S.step_to_bytes(handed_step()))))
            side.stop()
            _N.journal_writer_delay(0)
            assert side.pending() == 0, side.__name__
            assert [r[1]["at"] for r in j.read()] == [0, 1, 2], side.__name__
            j.close()
        finally:
            _N.journal_writer_delay(0)
            shutil.rmtree(d, ignore_errors=True)


def test_the_writer_never_takes_the_gil():
    """The main thread holds the GIL and never lets it go (a switch interval of 1,000 s): the engine's writer still
    writes the record; R079's Python thread can't."""
    want = S.step_to_bytes(handed_step(), JOURNAL_CODEC, 0)
    for side, wait in ((RefSide, 1.0), (EngineSide, 10.0)):
        d = tempfile.mkdtemp(prefix="hs_jwriter_")
        try:
            j = side.J.Journal(os.path.join(d, "j.hsj"))
            side.put(j, "CURSOR", {"hist": "h", "cursor": 0}, None)
            side.drain()                     # the thread is running
            old = sys.getswitchinterval()
            sys.setswitchinterval(1000.0)
            try:
                side.put(j, "STEP", {"hist": "h", "at": 1}, runtime.step_payload(Entry(handed_step())))
                t0 = time.monotonic()
                while side.pending() and time.monotonic() - t0 < wait:
                    pass
                left = side.pending()
            finally:
                sys.setswitchinterval(old)
            side.drain()
            assert [r[2] for r in j.read()][-1] == want, side.__name__
            j.close()
            assert left == (1 if side is RefSide else 0), (side.__name__, left)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    for t in (test_records_in_order_as_the_python_writer,
              test_a_step_edited_or_let_go_after_the_hand_off_writes_as_handed,
              test_a_write_error_is_reported_at_the_next_drain, test_stopping_writes_every_record_handed_over,
              test_the_writer_never_takes_the_gil):
        H.run(t)
    H.finish()
