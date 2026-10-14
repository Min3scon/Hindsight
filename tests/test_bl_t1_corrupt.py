"""Tier 1 gate, damaged history files (gate item FAULT.CORRUPT), headless.

For every Tier 1 row: record the row's operations, save with a sidecar, flip one byte inside one
step of one history, reopen.
  - Damage before the step the history was saved at: a clear message in the sidebar, that history is
    dropped (D27: nothing before the damage can be reached exactly any more), the object is exactly as
    saved, and every other history still plays back exactly.
  - Damage after it (saved after scrubbing back): the steps before the damage are kept and play back
    exactly; the message says how many were lost.
Never a crash, never a state that wasn't recorded.

Run: blender -b --factory-startup --python tests/test_bl_t1_corrupt.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import json
import struct
import tempfile

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from hindsight.bl import datarec, idmap, persistence, runtime
from hindsight.core import persist
from hindsight.core.types import SETTINGS

C = bpy.context
ROWS = ("obj.transforms", "obj.parenting", "obj.constraints", "obj.collections", "obj.addremove", "obj.editsteps")
TMP = tempfile.mkdtemp(prefix="hs_t1_corrupt_")
U32, U64 = struct.Struct("<I"), struct.Struct("<Q")


def damage(path, hist_uid, index):
    """Flip one byte in the middle of step `index` of history `hist_uid` in the sidecar file."""
    f = persist.sidecar_file(path)
    blob = bytearray(open(f, "rb").read())
    (jlen,) = U32.unpack_from(blob, 4)
    doc = json.loads(bytes(blob[8:8 + jlen]))
    pos = 8 + jlen
    for hd in doc["histories"]:
        for i in range(int(hd["n_entries"])):
            (n,) = U64.unpack_from(blob, pos)
            if hd["uid"] == hist_uid and i == index:
                blob[pos + 8 + n // 2] ^= 0xFF
                open(f, "wb").write(bytes(blob))
                return
            pos += 8 + n
    raise AssertionError("step not found")


def target_history(r):
    """The history with the most steps (an object's, or the scene's for the add and delete row)."""
    hs = [h for h in runtime.reg().histories.values() if h.entries]
    return max(hs, key=lambda h: (len(h.entries), h.uid))


def save(path):
    SETTINGS.save_mode = "SIDECAR"
    T.select()
    bpy.ops.wm.save_as_mainfile(filepath=path)


def reopen(path):
    bpy.ops.wm.open_mainfile(filepath=path)
    datarec.FORCE_SCOPE[0] = "EVERYTHING"


def damaged_note():
    return [t for sev, t in runtime.load_notes if sev == "ERROR"]


@covers(ROWS, "FAULT.CORRUPT")
def test_damage_before_the_saved_step():
    for row in ROWS:
        r = L.Run(T.sequences()[row])
        h = target_history(r)
        hid, n = h.uid, len(h.entries)
        path = os.path.join(TMP, f"before_{row.replace('.', '_')}.blend")
        save(path)
        saved = L.snap()
        damage(path, hid, n // 2)
        reopen(path)
        notes = damaged_note()
        assert notes and "none of it could be kept" in notes[0], notes
        h2 = runtime.reg().histories.get(hid)
        assert h2 is not None and len(h2.entries) == 0 and h2.cursor == 0, (row, h2 and len(h2.entries))
        now = L.snap()
        assert now == saved, f"{row}: the file isn't as saved: {O.diff(saved, now)[:3]}"
        # every other history still plays back exactly
        r.names = {u: n_ for u, n_ in r.names.items() if runtime.reg().history_for(u) is not None
                   and runtime.reg().history_for(u).uid != hid}
        T.select()
        L.check_object_histories(r)


@covers(ROWS, "FAULT.CORRUPT")
def test_damage_after_the_saved_step():
    for row in ROWS:
        r = L.Run(T.sequences()[row])
        h = target_history(r)
        hid, n = h.uid, len(h.entries)
        if n < 3:
            continue
        runtime._goto_history(h, 1, True)               # saved after scrubbing back to step 1
        path = os.path.join(TMP, f"after_{row.replace('.', '_')}.blend")
        save(path)
        damage(path, hid, 2)
        reopen(path)
        notes = damaged_note()
        assert notes and "could not be loaded" in notes[0], notes
        h2 = runtime.reg().histories[hid]
        assert len(h2.entries) == 2 and h2.cursor == 1, (row, len(h2.entries), h2.cursor)
        # positions 0..2 of that history are the recorded ones
        member = h2.members[0]
        ob = idmap.find(member)
        if isinstance(ob, bpy.types.Object):
            want = {}
            for i, ln in enumerate(r.lens):
                then = r.names_at[i].get(member)
                if then is not None and ln.get(member, 0) <= 2:
                    want.setdefault(ln.get(member, 0), L._own(r.states[i][then], r.renames))
            T.select()
            for pos in (0, 2, 1, 0, 2):
                runtime._goto_history(h2, pos, True)
                got = L._own(O.object_state(idmap.find(member), derived=False), r.renames)
                assert got == want[pos], f"{row} position {pos}: {O.diff(want[pos], got)[:3]}"


for fn in (test_damage_before_the_saved_step, test_damage_after_the_saved_step):
    H.run(fn)
H.finish()
