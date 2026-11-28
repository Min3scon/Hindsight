import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""#68 (11.5): older builds keep a format 5 history untouched, and this build gets it back (dev/FORMAT5_LAYOUT.md,
"Older builds open it without the history and save it back untouched"; dev/FORMAT5_V1.md, "A format 5 file that V1
saved again").

tests/fixtures/older_builds (make_older_build_files.py) holds a format 5 .blend this build saved (v2/), and that file
as each older build left it, run as it was by its own code: V1 (beta 2) and the main-branch build (beta 3), in each of
their save modes. There it opened without its history, with only the "newer Hindsight" note and no error; the build
then made a stroke on S, a history of its own on a new object N and an unrecorded move of O, and saved over the file.
Here:
- the saved file holds this build's parts and stub byte for byte; `sidecar` is True where the build wrote its folder;
- opened, S's and O's histories hold every step this build saved, bit-exact, and play back bit for bit at every
  position both ways; what the older build changed is one more step on each (D107 for S's mesh, from the backup copy
  its save left; D46 for O's location); N gets the older build's history from its folder, and none in the embedded
  mode, where V1 drops it;
- the next save writes `sidecar` False, says once that the folder is no longer used and leaves it byte for byte;
  opened alone, the file holds all of it.

Run: blender -b --factory-startup --python tests/test_bl_older_builds.py (or tests/run_tests.py).
"""
import json
import shutil
import tempfile

import bpy

import tests.harness as H
from tests.test_bl_beta2_upgrade import check, positions, sha
from hindsight.bl import persistence, prefs, runtime
from hindsight.core import persist

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "older_builds")
TMP = tempfile.mkdtemp(prefix="hindsight_older_builds_")
V2_BLEND = os.path.join(FIX, "v2", "scene.blend")
with open(os.path.join(FIX, "expect.json"), encoding="utf-8") as _fh:
    EXPECT = json.load(_fh)
V2 = EXPECT["v2"]


def place(case):
    """The case's .blend at a new place, with the backup copy the older build's save left beside it (Save
    Versions): this build's file, byte for byte."""
    dst = os.path.join(TMP, f"{case}_{len(os.listdir(TMP))}")
    shutil.copytree(os.path.join(FIX, case), dst)
    blend = os.path.join(dst, "scene.blend")
    shutil.copyfile(V2_BLEND, blend + "1")
    return blend


def open_file(blend):
    H.fresh()
    prefs.FORCE["record_scope"] = "EVERYTHING"      # as O was recorded: D46 compares its values at open
    bpy.ops.wm.open_mainfile(filepath=blend)


def test_older_builds_open_it_without_history_and_save_it_untouched():
    parts = H.stored_text(V2_BLEND)
    assert persistence.PARTS_PROP in parts and parts[persistence.DATA_PROP] == persist.STUB, list(parts)
    for case, got in EXPECT["cases"].items():
        assert got["histories"] == [], f"{case}: the older build loaded a history: {got['histories']}"
        assert got["notes"] == [["ERROR", persistence.NEWER_TEXT.format(found=5)]], (case, got["notes"])
        assert not got["traceback"], f"{case}: the older build printed an error"
        stored = H.stored_text(os.path.join(FIX, case, "scene.blend"))
        for key in (persistence.PARTS_PROP, persistence.DATA_PROP):
            assert stored[key] == parts[key], f"{case}: the older build changed {key}"
        assert bool(stored[persistence.SIDECAR_PROP]) == (got["mode"] != "EMBED"), case


def round_trip(case):
    got = EXPECT["cases"][case]
    folder_mode = got["mode"] != "EMBED"
    want_s, want_o = V2["S"] + [got["S"]], V2["O"] + [got["O"]]
    v2 = H.saved_steps(H.stored_registry(V2_BLEND))
    blend = place(case)
    folder = persist.sidecar_file(blend)
    had = sha(folder) if folder_mode else None

    def check_all(what):
        check(want_s, want_o, what)
        held = H.saved_steps(runtime.reg())
        for uid in v2:
            if uid != "settings":
                assert held[uid][3][:len(v2[uid][3])] == v2[uid][3], f"{what}: {uid}'s saved steps aren't bit-exact"
        n = bpy.data.objects["N"]
        h = runtime.history_of(n)
        if not folder_mode:
            assert h is None, f"{what}: N has a history V1 never saved"
            return
        assert h is not None and len(h.entries) == len(got["N"]) - 1, what
        for p in list(range(len(got["N"]) - 1, -1, -1)) + list(range(len(got["N"]))):
            runtime._goto_history(h, p, True)
            assert positions(n) == got["N"][p], f"{what}: N at position {p}"

    open_file(blend)
    opened = [runtime.OUTSIDE_BRIDGED_TEXT.format(name="S")]
    assert [t for _s, t in runtime.load_notes] == opened, (case, runtime.load_notes)
    check_all(case + ", opened")

    bpy.ops.wm.save_mainfile(filepath=blend)
    stored = H.stored_text(blend)
    assert stored[persistence.DATA_PROP] == persist.STUB and not stored[persistence.SIDECAR_PROP], case
    notes = [t for _s, t in runtime.load_notes]
    moved = [persistence.MOVED_IN_TEXT.format(folder="scene_hindsight")] if had else []
    assert notes == opened + moved, (case, notes)
    if had:
        assert sha(folder) == had, f"{case}: the history folder changed"
        shutil.rmtree(persist.sidecar_dir(blend))
    os.remove(blend + "1")
    open_file(blend)
    assert not runtime.load_notes and runtime.missing_message[0] is None, (case, runtime.load_notes)
    check_all(case + ", saved once and opened alone")


def make_test(case):
    def t():
        round_trip(case)
    t.__name__ = f"test_{case}_round_trip"
    return t


TESTS = [test_older_builds_open_it_without_history_and_save_it_untouched] + [make_test(c) for c in EXPECT["cases"]]


if __name__ == "__main__":
    try:
        for t in TESTS:
            H.run(t)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    H.finish()
