import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Plan item 0 (owner, 2026-11-22): people using beta 2 keep their history when they update.

The files in tests/fixtures/beta2 were saved by the real 1.0.0-beta.2 code (tests/fixtures/beta2/make_beta2_files.py)
in each of its save modes (a history folder, its default; inside the .blend; both), and one was left by a crash with
two strokes only in beta 2's crash journal. Each case is copied to a new place first, as if moved to another computer
with its folder. This build must give back every step beta 2 recorded, exactly, at every position both ways (the
sculpt history with its topology step, and the moves recorded with Record everything); bring back the unsaved strokes
when "Unsaved steps" is chosen, and not when it isn't; and after one save with the history inside the .blend, the
file alone (its folder gone) must still hold all of it.
"""
import base64
import json
import shutil
import tempfile

import bpy
import numpy as np

import tests.harness as H
from hindsight.bl import persistence, runtime
from hindsight.core.types import SETTINGS

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "beta2")
TMP = tempfile.mkdtemp(prefix="hindsight_beta2_")


def place(case):
    """(the case's .blend at a new place, what beta 2 recorded); its crash journal goes where this build looks."""
    dst = os.path.join(TMP, f"{case}_{len(os.listdir(TMP))}")
    shutil.copytree(os.path.join(FIX, case), dst)
    blend = os.path.join(dst, "scene.blend")
    hsj = os.path.join(dst, "scene.hsj")
    if os.path.exists(hsj):
        os.replace(hsj, persistence.journal_path(blend))
    with open(os.path.join(dst, "expect.json"), encoding="utf-8") as fh:
        return blend, json.load(fh)


def positions(ob) -> str:
    a = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", a)
    return base64.b64encode(a.tobytes()).decode()


def check(want_s, want_o, what, opened_at=None):
    """Every position of S's and O's histories, both ways, bit for bit. S opens at `opened_at` (default: its last
    position); O at its last."""
    s = bpy.data.objects["S"]
    h = runtime.history_of(s)
    at = len(want_s) - 1 if opened_at is None else opened_at
    assert h is not None, f"{what}: S has no history"
    assert len(h.entries) == len(want_s) - 1 and h.cursor == at, (what, len(h.entries), h.cursor)
    assert positions(s) == want_s[at], f"{what}: S as opened"
    for p in list(range(len(want_s) - 1, -1, -1)) + list(range(len(want_s))):
        runtime._goto_history(h, p, True)
        assert positions(s) == want_s[p], f"{what}: S at position {p}"
    o = bpy.data.objects["O"]
    ho = runtime.history_of(o)
    assert ho is not None and len(ho.entries) == len(want_o) - 1, (what, None if ho is None else len(ho.entries))
    for p in list(range(len(want_o) - 1, -1, -1)) + list(range(len(want_o))):
        runtime._goto_history(ho, p, True)
        assert list(o.location) == want_o[p], (what, p, list(o.location), want_o[p])


def open_file(blend):
    H.fresh()
    bpy.ops.wm.open_mainfile(filepath=blend)


def into_the_blend_and_alone(blend, want_s, want_o, what):
    """Saved once by this build with the history inside the .blend, then opened with its folder gone."""
    SETTINGS.save_mode = "EMBED"
    try:
        bpy.ops.wm.save_mainfile(filepath=blend)
    finally:
        SETTINGS.save_mode = "SIDECAR"
    shutil.rmtree(os.path.splitext(blend)[0] + "_hindsight", ignore_errors=True)
    open_file(blend)
    check(want_s, want_o, what + ", saved inside the .blend and opened without its folder")


def upgrade(case):
    blend, want = place(case)
    SETTINGS.crash_recovery = "LAST_SAVED"
    open_file(blend)
    check(want["S"], want["O"], case)
    into_the_blend_and_alone(blend, want["S"], want["O"], case)


def test_history_folder_file():
    upgrade("folder")


def test_history_inside_the_blend():
    upgrade("embed")


def test_saved_both_ways():
    upgrade("both")


def test_unsaved_strokes_after_a_crash():
    blend, want = place("crash")
    SETTINGS.crash_recovery = "INTERRUPTED"
    try:
        open_file(blend)
        # the unsaved strokes come back as steps after the cursor: the file holds the mesh as it was saved
        check(want["S"] + want["S_unsaved"], want["O"], "crash, Unsaved steps", opened_at=len(want["S"]) - 1)
    finally:
        SETTINGS.crash_recovery = "LAST_SAVED"
    into_the_blend_and_alone(blend, want["S"] + want["S_unsaved"], want["O"], "crash")


def test_last_save_after_a_crash():
    blend, want = place("crash")
    SETTINGS.crash_recovery = "LAST_SAVED"
    open_file(blend)
    check(want["S"], want["O"], "crash, Last save")


def test_moved_without_its_folder():
    """Beta 2's default kept the history in a folder: a file moved without it opens, with no history and no error."""
    blend, want = place("folder")
    shutil.rmtree(os.path.splitext(blend)[0] + "_hindsight")
    open_file(blend)
    h = runtime.history_of(bpy.data.objects["S"])
    assert h is None or not h.entries, len(h.entries)
    assert positions(bpy.data.objects["S"]) == want["S"][-1]


if __name__ == "__main__":
    try:
        for t in (test_history_folder_file, test_history_inside_the_blend, test_saved_both_ways,
                  test_unsaved_strokes_after_a_crash, test_last_save_after_a_crash, test_moved_without_its_folder):
            H.run(t)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    H.finish()
