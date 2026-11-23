import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""#48: whatever Save history says, a save puts the history inside the .blend and writes no history
folder, so the .blend alone, moved to another folder, opens with its whole history."""
import shutil
import tempfile

import bpy
import tests.harness as H
from hindsight.bl import runtime
from hindsight.core import persist
from hindsight.core.types import SETTINGS

TMP = tempfile.mkdtemp(prefix="hindsight_blendonly_")
NAME = "Sculpty"


def _check(mode, tag=None):
    H.fresh()
    SETTINGS.save_mode = mode
    ob = H.make_mesh(1200, name=NAME)
    H.enter(ob, "SCULPT")
    for s in range(5):
        H.fake_stroke(ob, 30 + s, n=60)
        assert H.commit(ob) is not None
    H.enter(ob, "OBJECT")
    here = os.path.join(TMP, tag or mode, "here")
    os.makedirs(here)
    path = os.path.join(here, "piece.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    assert not os.path.exists(persist.sidecar_dir(path)), f"{mode}: a history folder was written"
    there = os.path.join(TMP, tag or mode, "elsewhere")
    os.makedirs(there)
    moved = shutil.copy2(path, os.path.join(there, "piece.blend"))
    H.fresh()
    bpy.ops.wm.open_mainfile(filepath=moved)
    h = runtime.history_of(bpy.data.objects[NAME])
    assert h is not None and len(h.entries) == 5, f"{mode}: {None if h is None else len(h.entries)} steps"


def test_default_preference():
    assert SETTINGS.save_mode == "SIDECAR"     # the default today: the history folder
    _check(SETTINGS.save_mode, "default")


def test_next_to_the_blend():
    _check("SIDECAR")


def test_inside():
    _check("EMBED")


def test_both():
    _check("BOTH")


for f in [test_default_preference, test_next_to_the_blend, test_inside, test_both]:
    H.run(f)
shutil.rmtree(TMP, ignore_errors=True)
H.finish()
