import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Embedded history (Save Mode Embed and Both): the stored bytes are exactly the history file (R061:
earlier builds wrote its base64 as text), saving again replaces them, the packer is told when the user
works. #48: Save history Both writes no history folder."""
import shutil
import tempfile

import bpy
import tests.harness as H
from hindsight.bl import persistence, runtime
from hindsight.core import persist
from hindsight.core.types import SETTINGS

TMP = tempfile.mkdtemp(prefix="hindsight_saveload_")
NAME = "Sculpty"


def _record(ob, seeds):
    for s in seeds:
        H.fake_stroke(ob, s, n=80)
        assert H.commit(ob) is not None


def test_embed_bytes_are_the_history_file():
    H.fresh()
    SETTINGS.save_mode = "BOTH"
    ob = H.make_mesh(1500, name=NAME)
    H.enter(ob, "SCULPT")
    _record(ob, range(20, 26))
    H.enter(ob, "OBJECT")
    path = os.path.join(TMP, "both.blend")
    for round_ in range(2):                       # the second save replaces the stored bytes
        bpy.ops.wm.save_as_mainfile(filepath=path)
        text = bpy.data.texts[persistence.TEXT_NAME]
        stored = text[persistence.DATA_BIN_PROP]
        assert isinstance(stored, bytes)
        assert persistence.DATA_PROP not in text, "the base64 text of earlier builds is written again"
        want = persist.registry_to_bytes(runtime.reg(), SETTINGS.compression)
        assert stored == want, round_
        assert not os.path.isdir(persist.sidecar_dir(path)), "a history folder was written"
        H.enter(ob, "SCULPT")
        _record(ob, [40 + round_])
        H.enter(ob, "OBJECT")
    SETTINGS.save_mode = "EMBED"
    bpy.ops.wm.save_as_mainfile(filepath=path)
    shutil.rmtree(persist.sidecar_dir(path), ignore_errors=True)
    bpy.ops.wm.open_mainfile(filepath=path)
    h = runtime.history_of(bpy.data.objects[NAME])
    assert h is not None and len(h.entries) == 8, None if h is None else len(h.entries)


def test_strokes_tell_the_packer():
    H.fresh()
    p = runtime.packer
    assert p is not None
    before = p._last_activity
    ob = H.make_mesh(800, name=NAME)
    H.enter(ob, "SCULPT")
    _record(ob, [3])
    assert p._last_activity > before, "a recorded step didn't hold back idle recompression"


for f in [test_embed_bytes_are_the_history_file, test_strokes_tell_the_packer]:
    H.run(f)
shutil.rmtree(TMP, ignore_errors=True)
H.finish()
