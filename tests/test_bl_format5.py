import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""#64 (11.1, 64b): a .blend whose history is in format 5's Text layout (dev/FORMAT5_LAYOUT.md) opens with every
step bit-exact, reading the step parts one at a time and deleting Blender's copy of each as it is read; scrubbing
and the crash journal work after opening.

Until the save side of 64b writes format 5, a save_pre handler here stands in for it: after Hindsight's own
save_pre it swaps the stored history for the stub, `sidecar` False and the `hindsight_parts` group built by
core.persist.registry_to_format5, which is what that save will write.

Run: blender -b --factory-startup --python tests/test_bl_format5.py (or tests/run_tests.py).
"""
import tempfile

import bpy

import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.bl import persistence, prefs, runtime
from hindsight.core import persist
from hindsight.core.native import ENGINE
from hindsight.core.types import SETTINGS

TMP = tempfile.mkdtemp(prefix="hs_format5_")
ROWS = ("obj.transforms", "obj.editsteps")
PARTS = "hindsight_parts"     # dev/FORMAT5_LAYOUT.md
SAVED = {}          # {history uid: [step bytes]} as the last stand-in save wrote them


@bpy.app.handlers.persistent
def _as_format5(*_args):
    text = bpy.data.texts.get(persistence.TEXT_NAME)
    if text is None:
        return
    for key in (persistence.DATA_PROP, persistence.DATA_BIN_PROP, persistence.SAVE_ID_PROP):
        if key in text:
            del text[key]
    index, parts = persist.registry_to_format5(runtime.reg(), SETTINGS.compression)
    text[persistence.DATA_PROP] = persist.STUB
    text[persistence.SIDECAR_PROP] = False
    group = {"index": index, "index_copy": index}
    group.update({uid: {str(i): p for i, p in enumerate(ps)} for uid, ps in parts.items()})
    text[PARTS] = group
    SAVED.clear()
    for uid, ps in parts.items():
        SAVED[uid] = []
        for p in ps:
            _first, offs, lens, _crcs, _metas = ENGINE.part_read(p)
            SAVED[uid] += [p[o:o + n] for o, n in zip(offs, lens)]


def _save(path):
    T.select()
    bpy.app.handlers.save_pre.append(_as_format5)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path)
    finally:
        bpy.app.handlers.save_pre.remove(_as_format5)
    assert SAVED, "the stand-in save wrote no step parts"


def _open(path):
    """Opens `path`, checking that every step part is gone from Blender's copy as soon as it is read."""
    real = persist.registry_from_format5
    taken = []

    def spy(index, index_copy, take):
        def t(uid, i):
            b = take(uid, i)
            group = bpy.data.texts[persistence.TEXT_NAME][PARTS]
            assert str(i) not in group.get(uid, {}), f"Blender still holds step part {i} of {uid} after reading it"
            taken.append((uid, i))
            return b
        return real(index, index_copy, t)
    persist.registry_from_format5 = spy
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
    finally:
        persist.registry_from_format5 = real
    prefs.FORCE["record_scope"] = "EVERYTHING"
    return taken


def _check_opened(taken):
    text = bpy.data.texts[persistence.TEXT_NAME]
    assert PARTS not in text, "Blender's copy of the parts is kept after opening"
    assert text[persistence.DATA_PROP] == persist.STUB and not text.get(persistence.SIDECAR_PROP)
    assert taken and len(taken) == len(set(taken)), taken
    reg = runtime.reg()
    assert reg.load_report["format"] == 5, reg.load_report
    assert reg.load_report["damaged"] == [], reg.load_report
    for uid, want in SAVED.items():
        assert [e.blob for e in reg.histories[uid].entries] == want, f"{uid}: a step isn't bit-exact"


def test_rows_reopen_from_format5():
    """Real operators (transforms: data steps; Edit Mode steps), saved in format 5, reopened: every step
    bit-exact, every position of every history exactly as recorded (the scrub and the cursor work)."""
    for row in ROWS:
        r = L.Run(T.sequences()[row])
        path = os.path.join(TMP, f"{row.replace('.', '_')}.blend")
        _save(path)
        _check_opened(_open(path))
        L.recheck(r)
        L.check_object_histories(r)


def test_sculpt_steps_and_crash_journal_after_format5():
    """Quick and packed sculpt strokes reopen bit-exact; the crash journal started by the save names the index
    part's save id, so a stroke after the save comes back after a crash (Crash recovery: Unsaved steps)."""
    old = SETTINGS.crash_recovery
    SETTINGS.crash_recovery = "INTERRUPTED"
    try:
        H.fresh()
        ob = H.make_mesh(1200, name="F5")
        H.enter(ob, "SCULPT")
        for s in range(4):
            H.fake_stroke(ob, s, n=60)
            assert H.commit(ob) is not None
        if runtime.packer is not None:
            runtime.packer.drain(timeout=30.0)
        H.fake_stroke(ob, 9, n=60)
        assert H.commit(ob) is not None         # the newest stroke stays raw: the save encodes it
        H.enter(ob, "OBJECT")
        path = os.path.join(TMP, "sculpt.blend")
        _save(path)
        sid = runtime.reg().save_id
        _check_opened(_open(path))
        ob = bpy.data.objects["F5"]
        h = runtime.history_of(ob)
        assert runtime.reg().save_id == sid and len(h.entries) == 5 and h.cursor == 5
        H.enter(ob, "SCULPT")
        H.fake_stroke(ob, 20, n=60)
        assert H.commit(ob) is not None
        after = H.snapshot(ob)
        H.enter(ob, "OBJECT")
        bpy.ops.wm.open_mainfile(filepath=path)            # the session ends without saving
        ob = bpy.data.objects["F5"]
        h = runtime.history_of(ob)
        assert h is not None and len(h.entries) == 6, None if h is None else len(h.entries)
        H.enter(ob, "SCULPT")
        runtime.goto(bpy.context, 6)
        H.assert_same(H.snapshot(ob), after, "the stroke after the save, brought back by the journal")
        H.enter(ob, "OBJECT")
    finally:
        SETTINGS.crash_recovery = old


for fn in (test_rows_reopen_from_format5, test_sculpt_steps_and_crash_journal_after_format5):
    H.run(fn)
H.finish()
