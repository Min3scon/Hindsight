"""Tier 0 gate, save and reload (gate items RELOAD.*), headless.

Each sculpting row's recorded run is saved with its history in a sidecar folder, embedded in the
.blend, or both; reopened; and checked again exactly like right after recording (every object's
history at every position, the global timeline). A renamed object keeps its history, also after a
reload.

Run: blender -b --factory-startup --python tests/test_bl_t0_reload.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile

import bpy

import tests.harness as H
from tests import t0_gate_lib as L
from tests import t0_ops as T0
from tests.gate import covers
from hindsight.bl import persistence, runtime
from hindsight.core import persist
from hindsight.core.types import SETTINGS

C = bpy.context
ROWS = ("sculpt.positions", "sculpt.masks", "sculpt.colors", "sculpt.topology", "sculpt.editvisit")
TMP = tempfile.mkdtemp(prefix="hs_t0_reload_")


def save_and_reopen(path, mode):
    SETTINGS.save_mode = mode
    bpy.ops.wm.save_as_mainfile(filepath=path)
    side = persist.sidecar_dir(path)
    assert os.path.isdir(side) == (mode in ("SIDECAR", "BOTH")), (mode, os.path.isdir(side))
    bpy.ops.wm.open_mainfile(filepath=path)
    text = bpy.data.texts.get(persistence.TEXT_NAME)
    assert text is not None and ("hindsight_data" in text) == (mode in ("EMBED", "BOTH"))


def recheck(r):
    L.check_histories(r)
    L.check_global(r)


def reload_all_rows(mode):
    for row in ROWS:
        r = L.Run(T0.sequences()[row])
        path = os.path.join(TMP, f"{row.replace('.', '_')}_{mode.lower()}.blend")
        save_and_reopen(path, mode)
        if mode == "BOTH":
            # either copy alone is enough: the sidecar is preferred, the embedded copy is the backup
            shutil.rmtree(persist.sidecar_dir(path))
            bpy.ops.wm.open_mainfile(filepath=path)
        recheck(r)
    SETTINGS.save_mode = "SIDECAR"


@covers(ROWS, "RELOAD.SIDECAR")
def test_reload_sidecar():
    reload_all_rows("SIDECAR")


@covers(ROWS, "RELOAD.EMBED")
def test_reload_embed():
    reload_all_rows("EMBED")


@covers(ROWS, "RELOAD.BOTH")
def test_reload_both():
    reload_all_rows("BOTH")


@covers(ROWS, "RELOAD.RENAME")
def test_renamed_object_keeps_its_history():
    """Renaming (F2) isn't a sculpt step; the history stays with the object, before and after a
    reload, and every position is still exact (the checks look objects up by their new name)."""
    for row in ROWS:
        r = L.Run(T0.sequences()[row])
        T0.enter("Clay", "OBJECT")
        n = {name: len(runtime.history_of(bpy.data.objects[name]).entries)
             for name in r.states[-1] if runtime.history_of(bpy.data.objects[name])}
        for name in list(r.states[-1]):
            bpy.data.objects[name].name = name + " renamed"
        L.tick()
        got = {name: len(runtime.history_of(bpy.data.objects[name + " renamed"]).entries) for name in n}
        assert got == n, (row, n, got)
        r.states = [{k + " renamed": v for k, v in st.items()} for st in r.states]
        r.lens = [{k + " renamed": v for k, v in ln.items()} for ln in r.lens]
        recheck(r)
        save_and_reopen(os.path.join(TMP, f"{row.replace('.', '_')}_renamed.blend"), "SIDECAR")
        recheck(r)


for fn in (test_reload_sidecar, test_reload_embed, test_reload_both, test_renamed_object_keeps_its_history):
    H.run(fn)
H.finish()
