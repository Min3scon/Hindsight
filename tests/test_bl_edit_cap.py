"""L6: the Edit Mode cap is a preference, "Edit Mode steps up to" (vertices, default 300,000). With
Record everything, Edit Mode is recorded per operation on meshes up to it and once per visit above
it; the note on a bigger mesh names the number. Measured on 5.3 (bench/scenarios/editmode.py, load 6):
an Edit Mode step costs 14 to 18 ms at 100k vertices and 60 to 83 ms at 300k."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bpy
import tests.harness as H
from tests import t1_gate_lib as L
from hindsight.bl import datarec, prefs, runtime
from hindsight.bl import warnings as W
from hindsight.core.types import SETTINGS, Settings

C = bpy.context


class _Prefs:
    edit_steps_max = 1234


def test_preference_and_setting():
    ann = prefs.HINDSIGHT_AP_prefs.__annotations__["edit_steps_max"].keywords
    assert ann["default"] == 300_000 == Settings().edit_steps_max, ann["default"]
    assert ann["min"] <= 10_000 and ann["max"] >= 2_000_000, (ann["min"], ann["max"])
    old = SETTINGS.edit_steps_max
    try:
        prefs.sync_settings(_Prefs())
        assert SETTINGS.edit_steps_max == 1234
    finally:
        SETTINGS.edit_steps_max = old
    print("PASS test_preference_and_setting")


def _edit_moves(a, n):
    """n Moves in one Edit Mode visit; returns how many steps the visit made."""
    h0 = runtime.history_of(a)
    before = len(h0.entries) if h0 else 0
    C.view_layer.objects.active = a
    bpy.ops.object.mode_set(mode="EDIT")
    runtime.check_mode_transition()
    L.tick_like("Toggle Edit Mode")
    bpy.ops.mesh.select_all(action="SELECT")
    for _ in range(n):
        bpy.ops.transform.translate(value=(0, 0, 0.1))
        L.tick_like("Move")
    bpy.ops.object.mode_set(mode="OBJECT")
    runtime.check_mode_transition()
    L.tick_like("Toggle Edit Mode")
    h = runtime.history_of(a)
    return (len(h.entries) if h else 0) - before


def test_the_setting_decides_per_operation_or_per_visit():
    L.fresh_scene()
    L.track_all()
    a = bpy.data.objects["A"]
    nv = len(a.data.vertices)
    old = SETTINGS.edit_steps_max
    try:
        SETTINGS.edit_steps_max = nv          # at the cap: per operation
        assert _edit_moves(a, 3) == 3
        SETTINGS.edit_steps_max = nv - 1      # over it: one step for the visit
        assert _edit_moves(a, 3) == 1
        txt = W.TXT_EV_EDIT_BIG.format(n=nv - 1)
        notes = [t for _lvl, t in W.everything_notes(C, a)]
        assert txt in notes, notes
    finally:
        SETTINGS.edit_steps_max = old
    print("PASS test_the_setting_decides_per_operation_or_per_visit")


if __name__ == "__main__":
    H.run(test_preference_and_setting)
    H.run(test_the_setting_decides_per_operation_or_per_visit)
    H.finish()
