import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""#69 (11.6): a .blend whose format 5 history is damaged in one part opens with every other step bit-exact, tells
the user how many steps of which object were lost, stays byte for byte the same when it is closed without saving,
and its next save leaves a history that reopens with nothing more lost (dev/FORMAT5_DAMAGE.md). The damage is
written into the saved file: bytes of a step part changed as the save hands it to Blender.

Run: blender -b --factory-startup --python tests/test_bl_format5_damage.py (or tests/run_tests.py).
"""
import hashlib
import tempfile

import bpy

import tests.harness as H
from tests import t1_ops as T
from hindsight.bl import log, persistence, prefs, runtime
from hindsight.core import persist
from hindsight.core.native import ENGINE

TMP = tempfile.mkdtemp(prefix="hs_format5_damage_")


def _two_histories(back=0):
    """F5A and F5B, three strokes each; F5A then undoes `back` steps. {name: [snapshot after each step]}."""
    H.fresh()
    shapes = {}
    for name in ("F5A", "F5B"):
        ob = H.make_mesh(600, name=name)
        H.enter(ob, "SCULPT")
        shapes[name] = [H.snapshot(ob)]
        for s in range(3):
            H.fake_stroke(ob, s, n=40)
            assert H.commit(ob) is not None
            shapes[name].append(H.snapshot(ob))
        H.enter(ob, "OBJECT")
    if back:
        ob = bpy.data.objects["F5A"]
        bpy.context.view_layer.objects.active = ob
        runtime.goto(bpy.context, 3 - back)
    return shapes


def _save_damaged(path, damage):
    """Saves `path` (format 5) with F5A's step part made `damage(part, offsets)`; {name: [step bytes]} as saved."""
    real, saved = persist.registry_to_format5, {}

    def build(*a, **k):
        index, parts = real(*a, **k)
        for name in ("F5A", "F5B"):
            uid = runtime.history_of(bpy.data.objects[name]).uid
            assert len(parts[uid]) == 1, "one step part each"
            _first, offs, lens, _crcs, _metas = ENGINE.part_read(parts[uid][0])
            saved[name] = [parts[uid][0][o:o + n] for o, n in zip(offs, lens)]
            if name == "F5A":
                parts[uid][0] = damage(parts[uid][0], offs)
        return index, parts
    T.select()
    persist.registry_to_format5 = build
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path)
    finally:
        persist.registry_to_format5 = real
    return saved


def _flip(part, at):
    b = bytearray(part)
    b[at] ^= 0x40
    return bytes(b)


def _open(path):
    log._LINES.clear()
    bpy.ops.wm.open_mainfile(filepath=path)
    prefs.FORCE["record_scope"] = "EVERYTHING"
    return [t for _sev, t in runtime.load_notes]


def _steps(name):
    return [e.blob for e in runtime.history_of(bpy.data.objects[name]).entries]


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _scrubs_to(name, shapes, cursors):
    ob = bpy.data.objects[name]
    bpy.context.view_layer.objects.active = ob
    for c in cursors:
        runtime.goto(bpy.context, c)
        H.assert_same(H.snapshot(ob), shapes[c], f"{name} at step {c}")


def test_damaged_step_loses_only_it_and_says_so():
    """F5A's third step (after its cursor) damaged: F5A keeps its first two steps, F5B all three; the note says
    one step of F5A was lost. Opened and closed without saving, the file is byte for byte the same; its next save
    reopens with no note and nothing more lost."""
    shapes = _two_histories(back=1)
    path = os.path.join(TMP, "damaged_step.blend")
    saved = _save_damaged(path, lambda p, offs: _flip(p, offs[2] + 20))
    before = _sha(path)
    notes = _open(path)
    want = persistence.DAMAGED_TEXT.format(lost=1, s="", obj="F5A", kept=2)
    assert notes == [want], notes
    assert _steps("F5A") == saved["F5A"][:2] and _steps("F5B") == saved["F5B"], "a step isn't bit-exact"
    assert runtime.history_of(bpy.data.objects["F5A"]).cursor == 2
    _scrubs_to("F5A", shapes["F5A"], (0, 2))
    _scrubs_to("F5B", shapes["F5B"], (0, 3))
    bpy.ops.wm.read_homefile(app_template="")
    assert _sha(path) == before, "opening the damaged file changed it"
    _open(path)
    T.select()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    assert _open(path) == [], runtime.load_notes
    assert _steps("F5A") == saved["F5A"][:2] and _steps("F5B") == saved["F5B"], "the save after lost steps"
    _scrubs_to("F5A", shapes["F5A"], (0, 2))


def test_damaged_step_table_loses_nothing():
    """F5A's step table damaged: every step is found by its prefix, with no note (a line in the log); the next
    save writes the step part again, and it reopens with no line."""
    shapes = _two_histories()
    path = os.path.join(TMP, "damaged_table.blend")
    saved = _save_damaged(path, lambda p, offs: _flip(p, 21))
    assert _open(path) == [], runtime.load_notes
    assert any("no step lost" in line for line in log._LINES), list(log._LINES)[-5:]
    assert _steps("F5A") == saved["F5A"] and _steps("F5B") == saved["F5B"], "a step isn't bit-exact"
    _scrubs_to("F5A", shapes["F5A"], (0, 3))
    T.select()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    assert _open(path) == [] and not any("no step lost" in line for line in log._LINES), list(log._LINES)[-5:]
    assert _steps("F5A") == saved["F5A"] and _steps("F5B") == saved["F5B"]


def test_damaged_stub_loses_nothing():
    """The stub damaged: V2 doesn't read it, so nothing is lost and there is no note; the next save writes it
    again, for older builds."""
    _two_histories()
    path = os.path.join(TMP, "damaged_stub.blend")
    real = persist.STUB
    persist.STUB = real[:-5] + "xxxx="
    try:
        saved = _save_damaged(path, lambda p, offs: p)
    finally:
        persist.STUB = real
    assert _open(path) == [], runtime.load_notes
    assert _steps("F5A") == saved["F5A"] and _steps("F5B") == saved["F5B"], "a step isn't bit-exact"
    T.select()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    _open(path)
    assert bpy.data.texts[persistence.TEXT_NAME][persistence.DATA_PROP] == real
    assert _steps("F5A") == saved["F5A"] and _steps("F5B") == saved["F5B"]


for fn in (test_damaged_step_loses_only_it_and_says_so, test_damaged_step_table_loses_nothing,
           test_damaged_stub_loses_nothing):
    H.run(fn)
H.finish()
