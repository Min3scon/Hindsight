import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Save, load and background storage work (pure Python, no bpy): the embedded text built piece by
piece, the crash journal on a full or failing disk, and the packer's idle recompression (what it
skips, and that it waits while the user works)."""
import base64
import errno
import shutil
import tempfile
import time
import tracemalloc
import traceback

import numpy as np

from hindsight.core import delta, journal as J, persist, step as stepmod, store
from hindsight.core.types import DELTA, POSITION_KEY, Entry, Step


# ---------------------------------------------------------------------------- embedded text
def test_b64_text_is_exactly_b64encode():
    rng = np.random.default_rng(3)
    cases = [[], [b""], [b"a"], [b"ab"], [b"abc"], [b"a", b"b"], [b"ab", b"", b"c", b"defg"],
             [bytes(persist._B64_CHUNK)], [bytes(persist._B64_CHUNK + 1), b"x"],
             [b"q" * (persist._B64_CHUNK - 1), b"zz", b"w"]]
    for _ in range(200):
        n = int(rng.integers(0, 12))
        cases.append([rng.bytes(int(rng.choice([0, 1, 2, 3, 4, 5, 8, 100, 9001, 200_003])))
                      for _ in range(n)])
    cases.append([bytearray(b"xyz12"), memoryview(b"hello world")])
    for parts in cases:
        want = base64.b64encode(b"".join(bytes(p) for p in parts)).decode("ascii")
        got = persist.parts_to_b64(parts)
        assert type(got) is str and got == want, ([len(p) for p in parts], len(got), len(want))


def test_b64_text_of_a_registry_reads_back():
    from hindsight.core.history import History
    from hindsight.core.registry import Registry
    reg = Registry()
    h = History("H", ["obj"])
    for i in range(30):
        h.push(_step(i + 1, 50 + i))
    reg.histories = {"H": h}
    reg.by_object = {"obj": "H"}
    text = persist.parts_to_b64(persist.registry_parts(reg))
    assert text == base64.b64encode(persist.registry_to_bytes(reg)).decode("ascii")
    back = persist.registry_from_bytes(base64.b64decode(text))
    got = back.histories["H"].entries
    assert len(got) == 30 and all(stepmod.same_step(a.load(), b.load())
                                   for a, b in zip(got, h.entries))


def test_b64_text_peak_memory():
    """The text is built without the joined history or a second encoded copy: the peak is about the
    encoded size twice (the buffer and the text), not the history plus the encoded size twice."""
    rng = np.random.default_rng(5)
    parts = [rng.bytes(9000) for _ in range(1500)]          # 13.5 MB, like a 1,500-step history
    enc = 4 * ((sum(map(len, parts)) + 2) // 3)
    tracemalloc.start()
    s = persist.parts_to_b64(parts)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(s) == enc
    assert peak < 2 * enc + (2 << 20), (peak, enc)


# ---------------------------------------------------------------------------- journal
def _tmp():
    return tempfile.mkdtemp(prefix="hs_saveload_")


def test_journal_fsync_failure_is_reported():
    d = _tmp()
    real = os.fsync
    try:
        j = J.Journal(os.path.join(d, "j.hsj"))
        j.append("SAVE", {"blend": "a"})
        calls = []

        def broken(fd):
            calls.append(fd)
            raise OSError(errno.EIO, "I/O error")
        os.fsync = broken
        j.flush(fsync=False)                  # a stroke's flush never asks the disk
        assert not calls
        try:
            j.flush(fsync=True)
        except OSError as e:
            assert e.errno == errno.EIO
        else:
            raise AssertionError("a failed fsync was reported as durable")

        def unsupported(fd):
            raise OSError(errno.EINVAL, "not supported")
        os.fsync = unsupported               # a file system with nothing to sync is not a failure
        j.flush(fsync=True)
        os.fsync = real
        j.close()
        assert [r[0] for r in J.Journal(os.path.join(d, "j.hsj")).read()] == ["SAVE"]
    finally:
        os.fsync = real
        shutil.rmtree(d, ignore_errors=True)


class _FailingFile:
    """Passes writes through until `fail_after` bytes, then writes part of the next one and fails
    like a full disk."""

    def __init__(self, fh, fail_after):
        self._fh = fh
        self.left = fail_after

    def write(self, b):
        b = bytes(b)
        if len(b) > self.left:
            self._fh.write(b[:self.left // 2])
            self._fh.flush()
            self.left = 0
            raise OSError(errno.ENOSPC, "No space left on device")
        self.left -= len(b)
        return self._fh.write(b)

    def __getattr__(self, name):
        return getattr(self._fh, name)


def test_journal_full_disk_keeps_records_before_and_after():
    """A record cut off by a full disk must not hide the records written after it (the reader stops
    at the first broken record); the records before it stay."""
    d = _tmp()
    try:
        path = os.path.join(d, "j.hsj")
        j = J.Journal(path)
        j.append("SAVE", {"blend": "a"})
        j.append("CURSOR", {"hist": "h", "cursor": 1})
        j.flush(fsync=False)
        j._fh = _FailingFile(j._fh, 10)
        try:
            j.append("STEP", {"hist": "h", "at": 1}, b"x" * 5000)
            j.flush(fsync=False)
        except OSError:
            pass
        else:
            raise AssertionError("the full disk was not reported")
        j.append("CURSOR", {"hist": "h", "cursor": 2})      # space again
        j.flush(fsync=False)
        assert [r[0] for r in j.read()] == ["SAVE", "CURSOR", "CURSOR"]
        j.close()
        recs = J.Journal(path).read()
        assert [(r[0], r[1].get("cursor")) for r in recs] == [("SAVE", None), ("CURSOR", 1), ("CURSOR", 2)]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_journal_failed_flush_is_reported_and_cut_back():
    d = _tmp()
    try:
        path = os.path.join(d, "j.hsj")
        j = J.Journal(path)
        j.append("SAVE", {"blend": "a"})
        j.flush(fsync=False)

        class Buffered:
            def __init__(self, fh):
                self._fh = fh
                self.buf = b""

            def write(self, b):
                self.buf += bytes(b)
                return len(b)

            def flush(self):
                self._fh.write(self.buf[:7])
                self._fh.flush()
                raise OSError(errno.ENOSPC, "No space left on device")

            def close(self):
                self._fh.close()
                raise OSError(errno.ENOSPC, "No space left on device")

            def __getattr__(self, name):
                return getattr(self._fh, name)
        j._fh = Buffered(j._fh)
        j.append("CURSOR", {"hist": "h", "cursor": 5})
        try:
            j.flush(fsync=False)
        except OSError:
            pass
        else:
            raise AssertionError("a failed flush was not reported")
        j.append("CURSOR", {"hist": "h", "cursor": 6})
        j.flush(fsync=True)
        j.close()
        recs = J.Journal(path).read()
        assert [(r[0], r[1].get("cursor")) for r in recs] == [("SAVE", None), ("CURSOR", 6)], recs
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------- packer
def _step(seq, n=3000):
    """A stroke on a 20,000-vertex mesh moving n vertices a little (a real diff, as recorded)."""
    rng = np.random.default_rng(seq)
    old = rng.normal(0, 1, 60000).astype(np.float32)
    new = old.copy()
    s = int(rng.integers(0, 20000 - n))
    new.reshape(-1, 3)[s:s + n] += np.cumsum(rng.normal(0, 1e-4, (n, 3)), 0).astype(np.float32)
    return Step(seq, "Draw", "SCULPT", "obj", float(seq), DELTA, [delta.diff(POSITION_KEY, old, new)])


def test_can_gain_matches_recompress():
    """_can_gain says False only where step.recompress returns None without trying."""
    s = _step(1)
    normal = stepmod.step_to_bytes(s)
    quick = stepmod.step_to_bytes(s, "auto", 0)
    strong = stepmod.recompress(normal)
    assert strong is not None
    blobs = {"normal": normal, "quick": quick, "strong": strong,
             "quick_strong": stepmod.recompress(quick), "none": stepmod.step_to_bytes(s, "none"),
             "zlib": stepmod.step_to_bytes(s, "zlib")}
    for name, b in blobs.items():
        if b is None:
            continue
        if not store._can_gain(b):
            assert stepmod.recompress(b) is None, name
    assert store._can_gain(normal) and store._can_gain(quick) and not store._can_gain(strong)
    assert store._can_gain(b"HSS1" + bytes(20))        # formats 1 and 2: re-packed in full
    assert store._can_gain(b"")


def test_packer_skips_steps_with_nothing_to_gain():
    """A file saved after recompression: nothing is queued again, so the packer doesn't spend an
    idle second per step reading and checking them."""
    s = _step(2)
    strong = stepmod.recompress(stepmod.step_to_bytes(s))
    p = store.Packer()
    try:
        p.queue_recompress([Entry(blob=strong, meta=s.meta()) for _ in range(50)]
                           + [Entry(blob=stepmod.step_to_bytes(_step(3)), meta={})])
        assert len(p._strong) == 1, len(p._strong)
    finally:
        p.stop()


def _wait(cond, timeout):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_packer_waits_while_the_user_works():
    saved = store.IDLE_S, store.USER_IDLE_S
    store.IDLE_S, store.USER_IDLE_S = 0.05, 0.6
    p = store.Packer()
    try:
        s = _step(4)
        e = Entry(blob=stepmod.step_to_bytes(s), meta=s.meta())
        before = e.blob
        p.note_activity()
        p.queue_recompress([e])
        end = time.monotonic() + 1.5
        while time.monotonic() < end:          # a stroke every 0.1 s
            p.note_activity()
            time.sleep(0.1)
        assert p.recompressed == 0 and e.blob is before, "recompressed while strokes were coming in"
        assert _wait(lambda: p.recompressed == 1, 5.0), "never recompressed once the user stopped"
        assert stepmod.same_step(stepmod.step_from_bytes(e.blob), s)
        assert len(e.blob) < len(before)
    finally:
        p.stop()
        store.IDLE_S, store.USER_IDLE_S = saved


def test_packer_without_activity_recompresses_as_before():
    saved = store.IDLE_S
    store.IDLE_S = 0.05
    p = store.Packer()
    try:
        s = _step(5)
        e = Entry(blob=stepmod.step_to_bytes(s), meta=s.meta())
        want = stepmod.recompress(e.blob)
        p.queue_recompress([e])
        assert _wait(lambda: p.recompressed == 1, 5.0)
        assert e.blob == want                  # the same bytes the strong setting always gave
    finally:
        p.stop()
        store.IDLE_S = saved


class _CountingZlib:
    """persist's zlib with crc32 calls counted (and their sizes)."""

    def __init__(self, real):
        self._real, self.sizes = real, []

    def crc32(self, b, *a):
        self.sizes.append(len(b))
        return self._real.crc32(b, *a)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_save_reuses_known_step_checksums():
    """R046: a packed step's CRC-32 is known from when its bytes were made (packed, recompressed,
    loaded), so saving doesn't read every packed step again; the file is the same and reads back."""
    from hindsight.core.history import History
    from hindsight.core.registry import Registry
    reg = Registry()
    h = History("H", ["obj"])
    for i in range(40):
        h.push(_step(i + 1, 200 + i))
    for e in h.entries[:36]:
        e.pack("zstd")                       # 36 packed, 4 raw (as the raw tier keeps the newest)
    reg.histories = {"H": h}
    reg.by_object = {"obj": "H"}
    reg.next_seq = 41
    real = persist.zlib
    persist.zlib = cz = _CountingZlib(real)
    try:
        data = persist.registry_to_bytes(reg)
    finally:
        persist.zlib = real
    assert len(cz.sizes) <= 4 + 2, len(cz.sizes)          # the raw steps and the header, not all 40
    back = persist.registry_from_bytes(data)
    got = back.histories["H"].entries
    assert len(got) == 40 and all(stepmod.same_step(a.load(), b.load()) for a, b in zip(got, h.entries))
    assert not back.load_report["damaged"]
    # loaded steps carry the CRC read from the file: saving them again checks nothing twice
    persist.zlib = cz = _CountingZlib(real)
    try:
        again = persist.registry_to_bytes(back)
    finally:
        persist.zlib = real
    assert again == data and len(cz.sizes) <= 2, len(cz.sizes)
    # a step whose bytes are replaced (the packer's recompression) gets the new bytes' CRC
    e = back.histories["H"].entries[3]
    new = stepmod.recompress(e.blob) or bytes(e.blob)
    e.blob = new
    back2 = persist.registry_from_bytes(persist.registry_to_bytes(back))
    assert not back2.load_report["damaged"] and back2.histories["H"].entries[3].blob == new


TESTS = [test_b64_text_is_exactly_b64encode, test_b64_text_of_a_registry_reads_back, test_b64_text_peak_memory,
         test_journal_fsync_failure_is_reported, test_journal_full_disk_keeps_records_before_and_after,
         test_journal_failed_flush_is_reported_and_cut_back, test_can_gain_matches_recompress,
         test_packer_skips_steps_with_nothing_to_gain, test_packer_waits_while_the_user_works,
         test_packer_without_activity_recompresses_as_before, test_save_reuses_known_step_checksums]


def main():
    failed = 0
    for t in TESTS:
        try:
            t()
            print("PASS %s" % t.__name__)
        except Exception:
            failed += 1
            print("FAIL %s" % t.__name__)
            traceback.print_exc()
    sys.stdout.flush()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
