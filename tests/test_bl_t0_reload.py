"""Tier 0 gate, save and reload (gate items RELOAD.*), headless.

Each sculpting row's recorded run is saved (the history goes in the .blend, #48); reopened; and checked again exactly like right after recording (every object's
history at every position, the global timeline). A renamed object keeps its history, also after a
reload.

Run: blender -b --factory-startup --python tests/test_bl_t0_reload.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tempfile

import bpy

import tests.harness as H
from tests import t0_gate_lib as L
from tests import t0_ops as T0
from hindsight.bl import persistence, runtime
from hindsight.core import persist

C = bpy.context
ROWS = ("sculpt.positions", "sculpt.masks", "sculpt.colors", "sculpt.topology", "sculpt.editvisit")
TMP = tempfile.mkdtemp(prefix="hs_t0_reload_")


def save_and_reopen(path):
    bpy.ops.wm.save_as_mainfile(filepath=path)
    assert not os.path.isdir(persist.sidecar_dir(path)), "#48: a save writes no history folder"
    bpy.ops.wm.open_mainfile(filepath=path)
    text = bpy.data.texts.get(persistence.TEXT_NAME)
    assert text is not None and persistence.PARTS_PROP not in text     # Blender let go of the parts as they were read
    stored = H.stored_text(path)                  # the history is in the .blend, in format 5 (#64)
    assert persistence.PARTS_PROP in stored and stored[persistence.DATA_PROP] == persist.STUB, list(stored)


def recheck(r):
    L.check_histories(r)
    L.check_global(r)


def test_reload():
    for row in ROWS:
        r = L.Run(T0.sequences()[row])
        save_and_reopen(os.path.join(TMP, f"{row.replace('.', '_')}.blend"))
        recheck(r)


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
        save_and_reopen(os.path.join(TMP, f"{row.replace('.', '_')}_renamed.blend"))
        recheck(r)


for fn in (test_reload, test_renamed_object_keeps_its_history):
    H.run(fn)
H.finish()
