import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""The crash journal's step blobs are lz4 now (types.JOURNAL_CODEC; they were zstd). Recovery reads
journals of both kinds, and a journal that has both: zstd records written before an upgrade, lz4 ones
after it (the same file is appended to). Pure Python, no bpy."""
import shutil
import struct
import tempfile
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import test_core_journal as tj                                           # noqa: E402
from hindsight.core import codec, types                                  # noqa: E402
from hindsight.core.journal import Journal                               # noqa: E402
from hindsight.core.persist import replay                                # noqa: E402
from hindsight.core.registry import Registry                             # noqa: E402
from hindsight.core.types import Entry                                   # noqa: E402


def blob_codec(blob):
    hl = struct.unpack_from("<H", blob, 8)[0]
    return codec.codec_of(bytes(blob[10 + hl:10 + hl + 1]))


def test_the_journal_codec_is_lz4():
    assert types.JOURNAL_CODEC == "lz4"
    st = tj._delta_step(1, "objA", np.random.default_rng(1))
    e = Entry(st)
    assert blob_codec(e.to_bytes(types.JOURNAL_CODEC, 0)) == "lz4"
    assert blob_codec(e.to_bytes("zstd", 0)) == "zstd"
    print("PASS test_the_journal_codec_is_lz4")


def _journal_with(codecs, d):
    """A journal file with one STEP record per codec name, in order; -> (path, steps)."""
    path = os.path.join(d, "s.hsj")
    rng = np.random.default_rng(5)
    steps = [tj._delta_step(i + 1, "objA", rng) for i in range(len(codecs))]
    j = Journal(path)
    j.append("META", {"hist": "H1", "members": ["objA"], "enabled": True, "morph_target": None, "objects": {}})
    for i, (st, c) in enumerate(zip(steps, codecs)):
        j.append("STEP", {"hist": "H1", "at": i}, Entry(st).to_bytes(c, 0))
    j.flush()
    j.close()
    return path, steps


def _recover(path):
    reg = Registry()
    n = replay(reg, Journal(path).read())
    return reg, n


def test_old_new_and_mixed_journals_replay():
    d = tempfile.mkdtemp(prefix="hs_jc_")
    try:
        for k, (name, codecs) in enumerate((("old zstd journal", ["zstd"] * 4), ("new lz4 journal", ["lz4"] * 4),
                                            ("mixed, zstd then lz4 after an upgrade",
                                             ["zstd", "zstd", "lz4", "lz4", "zlib", "none"]))):
            sub = os.path.join(d, f"j{k}")
            os.makedirs(sub)
            path, steps = _journal_with(codecs, sub)
            reg, n = _recover(path)
            h = reg.histories["H1"]
            assert len(h.entries) == len(steps), (name, len(h.entries))
            assert all(tj._steps_equal(e.load(), s) for e, s in zip(h.entries, steps)), name
            seen = [blob_codec(r[2]) for r in Journal(path).read() if r[0] == "STEP"]
            assert seen == codecs, (name, seen)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS test_old_new_and_mixed_journals_replay")


def test_a_journal_appended_to_after_an_upgrade():
    """The same file: zstd records, the process ends, a newer build reopens and appends lz4 records."""
    d = tempfile.mkdtemp(prefix="hs_jc_")
    try:
        path, steps = _journal_with(["zstd", "zstd"], d)
        rng = np.random.default_rng(9)
        more = [tj._delta_step(3, "objA", rng), tj._delta_step(4, "objA", rng)]
        j = Journal(path)
        for i, st in enumerate(more):
            j.append("STEP", {"hist": "H1", "at": 2 + i}, Entry(st).to_bytes(types.JOURNAL_CODEC, 0))
        j.flush()
        j.close()
        reg, n = _recover(path)
        h = reg.histories["H1"]
        assert [blob_codec(r[2]) for r in Journal(path).read() if r[0] == "STEP"] == ["zstd", "zstd", "lz4", "lz4"]
        assert len(h.entries) == 4
        assert all(tj._steps_equal(e.load(), s) for e, s in zip(h.entries, steps + more))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("PASS test_a_journal_appended_to_after_an_upgrade")


def test_journal_size_growth():
    """What lz4 costs in bytes against zstd, for the test strokes (random values compress badly)."""
    rng = np.random.default_rng(2)
    z = l = 0
    for i in range(40):
        st = tj._delta_step(i + 1, "objA", rng)
        z += len(Entry(st).to_bytes("zstd", 0))
        l += len(Entry(st).to_bytes("lz4", 0))
    assert l < z * 1.6, (z, l)
    print(f"PASS test_journal_size_growth (40 test strokes: zstd {z} bytes, lz4 {l} bytes, x{l / z:.2f})")


def main():
    tests = [test_the_journal_codec_is_lz4, test_old_new_and_mixed_journals_replay,
             test_a_journal_appended_to_after_an_upgrade, test_journal_size_growth]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
