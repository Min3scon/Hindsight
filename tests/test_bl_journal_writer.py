import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R079: the crash journal's records of big steps (a topology change at 1M vertices is about 120 MB to encode and
write, 344 ms of the 532 ms the step cost) are encoded and written on a helper thread. Records keep their order
(any record behind a pending one waits for it), and everything that reads, empties or switches the journal waits
for the helper first, so recovery sees exactly what it saw before. The threshold is lowered here so every step
goes through the helper. Since 24.2b (#153) the helper is the engine's writer thread: the engine counts what it
encodes and can wait before each record (its test hooks), where these tests once patched core.step.parts_to_bytes."""
import contextlib
import shutil
import tempfile
import threading

import bpy

import tests.harness as H
from hindsight.bl import persistence, runtime
from hindsight.core import journal as J
from hindsight.core import persist
from hindsight.core import step as stepmod
from hindsight.core.native import ENGINE as _N
from hindsight.core.types import SETTINGS

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hindsight_jwriter_")
MAIN = threading.get_ident()


def setup(big=0):
    H.fresh()
    SETTINGS.crash_recovery = "INTERRUPTED"
    persistence.BIG_RECORD[0] = big


def start(tag, n):
    ob = H.make_mesh(1500, name="W")
    H.enter(ob, "SCULPT")
    after = {}
    for s in range(n):
        H.fake_stroke(ob, 10 + s, n=80)
        assert H.commit(ob) is not None
        h = runtime.history_of(ob)
        after[h.entries[h.cursor - 1].seq] = H.snapshot(ob)
    return ob, after, os.path.join(TMP, f"{tag}.blend")


@contextlib.contextmanager
def encode_threads():
    """The thread id of every step encoding in Python while inside the block, then how many the engine's writer
    thread encoded (its last item, "writer N")."""
    threads = []
    real = stepmod.parts_to_bytes

    def spy(*a, **k):
        threads.append(threading.get_ident())
        return real(*a, **k)
    stepmod.parts_to_bytes = spy
    _N.journal_calls()
    try:
        yield threads
    finally:
        stepmod.parts_to_bytes = real
        threads.append(f"writer {_N.journal_calls()['encoded']}")
        _N.journal_fault(None)


def test_big_records_are_encoded_off_the_main_thread_in_order():
    setup(big=0)
    ob, _after, path = start("order", 1)
    H.enter(ob, "OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    ob = bpy.data.objects["W"]
    H.enter(ob, "SCULPT")
    with encode_threads() as threads:
        for s in range(6):
            H.fake_stroke(ob, 40 + s, n=80)
            assert H.commit(ob) is not None
            if s == 2:
                runtime.goto(C, 2)                     # a CURSOR record between steps
                runtime.goto(C, len(runtime.history_of(ob).entries))
        persistence.drain_journal_writer()
    assert threads == ["writer 6"], f"big records were encoded on the main thread: {threads}"
    recs = J.Journal(persistence.journal_path(path)).read()
    steps = [r for r in recs if r[0] == "STEP"]
    seqs = [stepmod.read_header(r[2])["meta"]["seq"] for r in steps]
    assert seqs == sorted(seqs) and len(seqs) == 6, seqs
    kinds = [r[0] for r in recs]
    assert kinds.index("CURSOR") > kinds.index("STEP"), kinds


def test_recovery_after_records_written_by_the_helper():
    setup(big=0)
    ob, after, path = start("recover", 3)
    H.enter(ob, "OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    file_state = H.snapshot(bpy.data.objects["W"])
    ob = bpy.data.objects["W"]
    H.enter(ob, "SCULPT")
    for s in range(8):
        H.fake_stroke(ob, 70 + s, n=80)
        assert H.commit(ob) is not None
        h = runtime.history_of(ob)
        after[h.entries[h.cursor - 1].seq] = H.snapshot(ob)
    live = [e.seq for e in runtime.history_of(ob).entries]
    H.enter(ob, "OBJECT")
    bpy.ops.wm.open_mainfile(filepath=path)              # the session ends without saving
    ob = bpy.data.objects["W"]
    h = runtime.history_of(ob)
    assert [e.seq for e in h.entries] == live and h.cursor == 3, ([e.seq for e in h.entries], h.cursor)
    H.enter(ob, "SCULPT")
    H.assert_same(H.snapshot(ob), file_state, "the file's mesh")
    for i in (11, 0, 5, 3, 11):
        runtime.goto(C, i)
        if i:
            H.assert_same(H.snapshot(ob), after[h.entries[i - 1].seq], f"position {i}")


def test_a_save_waits_for_the_helper():
    setup(big=0)
    ob, _after, path = start("save", 4)
    H.enter(ob, "OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    recs = J.Journal(persistence.journal_path(path)).read()
    assert recs and recs[0][0] == "SAVE" and not [r for r in recs if r[0] == "STEP"], [r[0] for r in recs]


def test_crash_in_the_middle_of_a_save_loses_no_step():
    """dev/FORMAT5_JOURNAL.md (PR #103's point 9): big records still on the helper thread when a save starts, then a
    crash while the save builds the parts (the last save's .blend and the journal are what is left on disk):
    opening the file recovers every step."""
    setup(big=0)
    ob, _after, path = start("crash_in_save", 2)
    H.enter(ob, "OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    ob = bpy.data.objects["W"]
    H.enter(ob, "SCULPT")
    _N.journal_writer_delay(0.3)    # the helper is still writing them when the save starts
    try:
        for s in range(3):
            H.fake_stroke(ob, 90 + s, n=80)
            assert H.commit(ob) is not None
        live, state = [e.seq for e in runtime.history_of(ob).entries], H.snapshot(ob)
        H.enter(ob, "OBJECT")
        jp, left, real_build = persistence.journal_path(path), {}, persist.registry_to_format5

        def crash(*a, **k):
            for key, f in (("journal", jp), ("blend", path)):
                with open(f, "rb") as fh:
                    left[key] = fh.read()
            return real_build(*a, **k)
        persist.registry_to_format5 = crash
        try:
            bpy.ops.wm.save_as_mainfile(filepath=path)
        finally:
            persist.registry_to_format5 = real_build
    finally:
        _N.journal_writer_delay(0)
    H.fresh()                       # let go of the file and its journal, then put back what the crash left
    SETTINGS.crash_recovery = "INTERRUPTED"
    for key, f in (("journal", jp), ("blend", path)):
        with open(f, "wb") as fh:
            fh.write(left[key])
    bpy.ops.wm.open_mainfile(filepath=path)
    ob = bpy.data.objects["W"]
    h = runtime.history_of(ob)
    assert [e.seq for e in h.entries] == live and h.cursor == 2, ([e.seq for e in h.entries], h.cursor)
    H.enter(ob, "SCULPT")
    runtime.goto(C, len(live))
    H.assert_same(H.snapshot(ob), state, "every step after the last save")


def test_small_records_stay_on_the_main_thread():
    setup(big=1 << 40)
    ob, _after, path = start("small", 1)
    H.enter(ob, "OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    ob = bpy.data.objects["W"]
    H.enter(ob, "SCULPT")
    with encode_threads() as threads:
        H.fake_stroke(ob, 99, n=80)
        assert H.commit(ob) is not None
    assert threads[:-1] and all(t == MAIN for t in threads[:-1]) and threads[-1] == "writer 0", threads


if __name__ == "__main__":
    try:
        for t in (test_big_records_are_encoded_off_the_main_thread_in_order,
                  test_recovery_after_records_written_by_the_helper, test_a_save_waits_for_the_helper,
                  test_small_records_stay_on_the_main_thread, test_crash_in_the_middle_of_a_save_loses_no_step):
            H.run(t)
    finally:
        persistence.BIG_RECORD[0] = persistence.BIG_RECORD_DEFAULT
        shutil.rmtree(TMP, ignore_errors=True)
    H.finish()
