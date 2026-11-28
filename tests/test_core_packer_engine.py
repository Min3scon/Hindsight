"""#155 (24.5b): the packer is a Rust thread (native/src/packer.rs) that holds no Python object: a step is handed over
as bytes and pinned arrays made on the main thread (Packer.submit), a packed step to recompress as its pinned blob, and
the results are taken in on the main thread, at the next call into the packer (submit, queue_recompress, pending,
drain, stop, take_in, its counters) and on Blender's tick (provisional owner point 6, dev/RUST_PLAN.md). Checked
against the Python packer (tests/reference/store.py) with a fake clock: random submits, duplicate submits, recompress
offers (some damaged), activity and drains, then the user goes idle and the packer stops; the same entries packed and
recompressed in the same order, with the same bytes, counters and errors. Stopping packs every step handed over; a
step packed or replaced meanwhile is left alone; the thread packs while the main thread holds the GIL.
Run: <bpy python> tests/test_core_packer_engine.py"""
import os
import random
import sys
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tests.runner as R  # noqa: E402
from tests import corpus as C  # noqa: E402
from tests.reference import store as ref  # noqa: E402
from hindsight.core import step as S, store  # noqa: E402
from hindsight.core.native import ENGINE as _N  # noqa: E402
from hindsight.core.types import Entry  # noqa: E402

TIMES = {"QUIET_S": 0.3, "IDLE_S": 0.02, "USER_IDLE_S": 0.5, "DUTY": 1.0}
CODECS = ("auto", "zlib", "none")
_CORPUS = []


def _ref_encodes(b):
    try:
        ref.parts_to_bytes(S.step_from_bytes(b), "auto")
        return True
    except TypeError:   # records holding the engine's MISSING, which the reference's Python JSON can't write
        return False


def corpus():
    """A sample of the corpus's steps (blobs), small ones the reference's encoder takes, so a run takes seconds."""
    if not _CORPUS:
        blobs = [bytes(b) for b in C.steps() if len(b) < 200_000]
        random.Random(155).shuffle(blobs)
        _CORPUS.extend([b for b in blobs if _ref_encodes(b)][:60])
    return _CORPUS


def damaged(blob):
    """The blob with a byte of its body flipped: _can_gain still says yes, recompress raises."""
    b = bytearray(blob)
    b[len(b) * 2 // 3] ^= 0x5A
    return bytes(b)


class _Clock:
    """The reference's time module with monotonic() on the test's clock."""

    def __init__(self, real):
        self.real, self.now = real, 1000.0
        self.sleep, self.perf_counter = real.sleep, real.perf_counter

    def monotonic(self):
        return self.now


class RefSide:
    """The Python packer, its clock the test's, its encode and recompress calls logged."""
    name = "reference"

    def __enter__(self):
        self.saved = {k: getattr(ref, k) for k in (*TIMES, "time", "parts_to_bytes", "recompress")}
        for k, v in TIMES.items():
            setattr(ref, k, v)
        self.clock = ref.time = _Clock(self.saved["time"])
        self.log = []
        enc, rec = self.saved["parts_to_bytes"], self.saved["recompress"]

        def parts_to_bytes(parts, codec):
            out = enc(parts, codec)
            self.log.append(("pack", zlib.crc32(out)))
            return out

        def recompress(b):
            self.log.append(("recompress", zlib.crc32(b)))
            return rec(b)
        ref.parts_to_bytes, ref.recompress = parts_to_bytes, recompress
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(ref, k, v)

    def packer(self):
        return ref.Packer()

    def tick(self, dt):
        self.clock.now += dt

    def calls(self):
        out, self.log = self.log, []
        return out


class EngineSide:
    name = "engine"

    def __enter__(self):
        self.saved = {k: getattr(store, k) for k in TIMES}
        for k, v in TIMES.items():
            setattr(store, k, v)
        self.now, self.log = 1000.0, []
        _N.packer_hook(clock=self.now, log=True)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(store, k, v)
        _N.packer_hook(clock=None, log=False)

    def packer(self):
        return store.Packer()

    def tick(self, dt):
        self.now += dt
        self.log += _N.packer_hook(clock=self.now, log=True)

    def calls(self):
        out, self.log = self.log + list(_N.packer_hook(clock=self.now, log=True)), []
        return out


def _wait(cond, timeout=20.0):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def plan_of(rng):
    """Random calls: ("submit", step, codec), ("again", step) (still in flight), ("offer", [blobs]), ("activity",),
    ("drain",). Steps and blobs are corpus indices; an offer's blob may be damaged."""
    n = len(corpus())
    plan, sent = [("activity",)], []
    for _ in range(rng.randint(5, 25)):
        r = rng.random()
        if r < 0.45:
            i = rng.randrange(n)
            plan.append(("submit", i, rng.choice(CODECS)))
            sent.append(i)
        elif r < 0.55 and sent:
            plan.append(("again", rng.choice(sent)))
        elif r < 0.75:
            plan.append(("offer", [(rng.randrange(n), rng.random() < 0.2) for _ in range(rng.randint(0, 6))]))
        elif r < 0.85:
            plan.append(("activity",))
        else:
            plan.append(("drain",))
    return plan


def counters(p):
    return (p.packed, p.recompressed, p.errors, p.last_error, p.pending())


def state(entries):
    return [(e.is_packed(), None if e.blob is None else bytes(e.blob)) for e in entries]


def drive(side, plan):
    """Run a plan on one side; return what can be seen after every drain and at the end, and the thread's calls."""
    blobs = corpus()
    seen = []
    with side() as sd:
        p = sd.packer()
        raw, offered = {}, []
        try:
            for op in plan:
                if op[0] == "submit":
                    e = raw.setdefault(len(raw), Entry(S.step_from_bytes(blobs[op[1]])))
                    p.submit(e, op[2])
                elif op[0] == "again":
                    p.submit(list(raw.values())[-1], "auto")
                elif op[0] == "offer":
                    es = [Entry(blob=damaged(blobs[i]) if bad else blobs[i], meta={}) for i, bad in op[1]]
                    offered += es
                    p.queue_recompress(es)
                elif op[0] == "activity":
                    p.note_activity()
                else:
                    p.drain()
                    seen.append(("drain", counters(p), state(raw.values()), state(offered)))
            p.drain()
            seen.append(("drained", counters(p), state(raw.values()), state(offered)))
            sd.tick(10.0)                                  # the user went idle: every offer is recompressed
            assert _wait(lambda: len(p._strong) == 0), (side.name, len(p._strong))
        finally:
            p.stop()
        seen.append(("stopped", counters(p), state(raw.values()), state(offered)))
        return seen, sd.calls()


def test_same_as_the_python_packer():
    rng = random.Random(24_5)
    recompressed = errors = 0
    for k in range(12):
        plan = plan_of(rng)
        want, want_calls = drive(RefSide, plan)
        got, got_calls = drive(EngineSide, plan)
        for a, b in zip(want, got):
            assert a == b, (k, a[0], a[1], b[1])
        assert len(want) == len(got) and want_calls == got_calls, (k, want_calls, got_calls)
        recompressed, errors = recompressed + want[-1][1][1], errors + want[-1][1][2]
    assert recompressed and errors, ("nothing was recompressed or nothing failed", recompressed, errors)


def test_stopping_packs_every_step_handed_over():
    """Steps still waiting when the packer stops are all packed before stop returns (quitting doesn't drain first)."""
    blobs = corpus()[:20]
    out = []
    for side in (RefSide, EngineSide):
        with side() as sd:
            p = sd.packer()
            p.note_activity()
            es = [Entry(S.step_from_bytes(b)) for b in blobs]
            for e in es:
                p.submit(e, "auto")
            t0 = time.monotonic()
            p.stop()
            assert time.monotonic() - t0 < 10.0, side.name
            assert not p._alive if side is EngineSide else not p._thread.is_alive()
            out.append((p.packed, p.errors, state(es)))
            e = Entry(S.step_from_bytes(blobs[0]))
            p.submit(e, "zlib")                           # after stop, packed right away
            assert e.is_packed(), side.name
    assert out[0] == out[1] and out[0][0] == len(blobs), (out[0][:2], out[1][:2])


def test_a_step_packed_or_replaced_meanwhile_is_left_alone():
    blobs = corpus()
    out = []
    for side in (RefSide, EngineSide):
        with side() as sd:
            p = sd.packer()
            try:
                p.note_activity()
                es = [Entry(S.step_from_bytes(b)) for b in blobs[:6]]
                for e in es:
                    p.submit(e, "auto")
                es[1].pack("zlib")                        # packed on the main thread (enforce without a packer)
                es[3].step = S.step_from_bytes(blobs[10])  # replaced
                p.drain()
                out.append((counters(p), state(es), [e.step is None for e in es]))
            finally:
                p.stop()
    assert out[0] == out[1], (out[0][0], out[1][0])


def test_results_are_taken_in_on_the_main_thread():
    """The thread packs, the entry stays as it was until the main thread calls the packer (here pending())."""
    with EngineSide() as sd:
        p = sd.packer()
        try:
            p.note_activity()
            e = Entry(S.step_from_bytes(corpus()[0]))
            p.submit(e, "auto")
            sd.calls()
            sd.tick(1.0)                                   # past QUIET_S: the thread packs it
            assert _wait(lambda: any(c[0] == "pack" for c in sd.calls())), "the thread never packed"
            assert not e.is_packed(), "an entry changed off the main thread"
            assert p.pending() == 0 and e.is_packed() and p.packed == 1
        finally:
            p.stop()


def test_the_packer_never_takes_the_gil():
    """The main thread holds the GIL and never lets it go (a switch interval of 1,000 s): the engine's packer still
    packs the step; the Python packer's thread can't."""
    for side, wait in ((RefSide, 1.0), (EngineSide, 10.0)):
        with side() as sd:
            p = sd.packer()
            try:
                p.note_activity()
                e = Entry(S.step_from_bytes(corpus()[0]))
                old = sys.getswitchinterval()
                sys.setswitchinterval(1000.0)
                try:
                    p.submit(e, "auto")
                    sd.tick(1.0)
                    t0 = time.monotonic()
                    while p.pending() and time.monotonic() - t0 < wait:
                        pass
                    left = p.pending()
                finally:
                    sys.setswitchinterval(old)
                p.drain()
                assert e.is_packed(), side.name
                assert left == (1 if side is RefSide else 0), (side.name, left)
            finally:
                p.stop()


def test_store_is_a_facade():
    """store.Packer is the engine's; store.py runs no thread of its own."""
    p = store.Packer()
    try:
        assert type(p) is _N.Packer, type(p)
    finally:
        p.stop()
    src = open(store.__file__, encoding="utf-8").read()
    assert "import threading" not in src and "import queue" not in src, "store.py still has the Python packer"


if __name__ == "__main__":
    for t in (test_same_as_the_python_packer, test_stopping_packs_every_step_handed_over,
              test_a_step_packed_or_replaced_meanwhile_is_left_alone, test_results_are_taken_in_on_the_main_thread,
              test_the_packer_never_takes_the_gil, test_store_is_a_facade):
        R.run(t)
    R.finish()
