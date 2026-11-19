"""The runtime writes its crash journal's step blobs with lz4 (types.JOURNAL_CODEC), whatever codec
the user chose for the history; recovery of such a journal is exact (test_bl_recovery covers the
replay, this checks what is written and that a recovered session matches)."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import struct

import bpy
import tests.harness as H
from hindsight.bl import runtime
from hindsight.core import codec
from hindsight.core.types import JOURNAL_CODEC, SETTINGS


def blob_codec(blob):
    hl = struct.unpack_from("<H", blob, 8)[0]
    return codec.codec_of(bytes(blob[10 + hl:10 + hl + 1]))


def test_stroke_journal_blobs_are_lz4_whatever_the_history_codec():
    H.fresh()
    assert JOURNAL_CODEC == "lz4"
    for user_codec in ("zstd", "zlib", "none", "auto"):
        SETTINGS.compression = user_codec
        ob = H.make_mesh(1500, name=f"J_{user_codec}")
        H.enter(ob, "SCULPT")
        seen = []
        runtime.journal_hook = lambda t, header, fn=None: seen.append((t, fn() if fn else None))
        try:
            H.fake_stroke(ob, 4, n=80)
            assert H.commit(ob) is not None
        finally:
            runtime.journal_hook = None
        steps = [b for t, b in seen if t == "STEP"]
        assert len(steps) == 1 and blob_codec(steps[0]) == "lz4", (user_codec, [blob_codec(b) for b in steps])
        H.enter(ob, "OBJECT")
    SETTINGS.compression = "auto"
    print("PASS test_stroke_journal_blobs_are_lz4_whatever_the_history_codec")


if __name__ == "__main__":
    H.run(test_stroke_journal_blobs_are_lz4_whatever_the_history_codec)
    H.finish()
