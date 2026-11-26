import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Plan item 0 (owner, 2026-11-22): people using beta 2 keep their history when they update.

The files in tests/fixtures/beta2 were saved by the real 1.0.0-beta.2 code (tests/fixtures/beta2/make_beta2_files.py)
in each of its save modes (a history folder, its default; inside the .blend; both), and one was left by a crash with
two strokes only in beta 2's crash journal. Each case is copied to a new place first, as if moved to another computer
with its folder. This build must give back every step beta 2 recorded, exactly, at every position both ways (the
sculpt history with its topology step, and the moves recorded with Record everything); bring back the unsaved strokes
when "Unsaved steps" is chosen, and not when it isn't; and after one save with the default preferences, the
file alone (its folder gone) must still hold all of it, and the folder is never read again (#50).
"""
import base64
import json
import shutil
import struct
import tempfile

import bpy
import numpy as np

import tests.harness as H
from hindsight.bl import persistence, runtime
from hindsight.core import persist
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


def quiet(what):
    """Nothing to tell the user: no sidebar note, no missing-folder message, no folder taken as a newer one's."""
    assert not runtime.load_notes and runtime.missing_message[0] is None, (what, runtime.load_notes,
                                                                          runtime.missing_message[0])
    assert not persistence._PROTECT["sidecar"], f"{what}: the folder was read"


def into_the_blend_and_alone(blend, want_s, want_o, what):
    """Saved once by this build with the default preferences, then opened with its folder gone (#50)."""
    bpy.ops.wm.save_mainfile(filepath=blend)
    shutil.rmtree(os.path.splitext(blend)[0] + "_hindsight", ignore_errors=True)
    open_file(blend)
    check(want_s, want_o, what + ", saved once and opened without its folder")
    quiet(what + ", saved once and opened without its folder")


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
    """Beta 2's default kept the history in a folder: a file moved without it opens, with no history and no error,
    and the user is told to keep the folder next to the .blend."""
    blend, want = place("folder")
    shutil.rmtree(os.path.splitext(blend)[0] + "_hindsight")
    open_file(blend)
    h = runtime.history_of(bpy.data.objects["S"])
    assert h is None or not h.entries, len(h.entries)
    assert positions(bpy.data.objects["S"]) == want["S"][-1]
    assert runtime.missing_message[0] == persistence.MISSING_TEXT.format(folder="scene_hindsight"), \
        runtime.missing_message[0]
    assert not runtime.load_notes, runtime.load_notes


def test_saved_without_its_folder():
    """#8 review: a beta 2 folder file opened and saved without its folder keeps pointing at the folder, and the
    user is still told to keep it next to the .blend, however often it is saved; once the folder is back, so is
    its history."""
    blend, want = place("folder")
    folder = os.path.splitext(blend)[0] + "_hindsight"
    os.replace(folder, folder + "_away")
    missing = persistence.MISSING_TEXT.format(folder="scene_hindsight")
    SETTINGS.crash_recovery = "LAST_SAVED"
    for what in ("saved", "saved again"):
        open_file(blend)
        bpy.ops.wm.save_mainfile()
        assert runtime.missing_message[0] == missing, (what, runtime.missing_message[0])
    open_file(blend)
    assert runtime.missing_message[0] == missing, ("opened", runtime.missing_message[0])
    os.replace(folder + "_away", folder)
    open_file(blend)
    check(want["S"], want["O"], "saved without its folder, then the folder put back")


def test_steps_made_without_its_folder():
    """#120 review, problem 1: steps recorded on S while the folder was away, and saved, are still there once the
    folder is back, and after the next save without it. O, not changed meanwhile, gets its folder history back."""
    blend, want = place("folder")
    folder = os.path.splitext(blend)[0] + "_hindsight"
    os.replace(folder, folder + "_away")
    SETTINGS.crash_recovery = "LAST_SAVED"
    open_file(blend)
    s = bpy.data.objects["S"]
    H.enter(s, "SCULPT")
    new = []
    for seed in (11, 12):
        H.fake_stroke(s, seed, n=80)
        assert H.commit(s) is not None, f"no step for seed {seed}"
        new.append(positions(s))
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.wm.save_mainfile()
    os.replace(folder + "_away", folder)
    for what in ("the folder put back", "saved and opened without its folder"):
        open_file(blend)
        check([want["S"][-1]] + new, want["O"], f"steps made without its folder, {what}")
        bpy.ops.wm.save_mainfile()
        shutil.rmtree(folder, ignore_errors=True)


def test_folder_not_read_after_the_save():
    """#50: once a save has put the history in the .blend, beta 2's folder is never read again. A damaged
    folder, or one a newer Hindsight changed, left beside the file changes nothing and shows no message."""
    for case in ("folder", "both"):
        blend, want = place(case)
        SETTINGS.crash_recovery = "LAST_SAVED"
        open_file(blend)
        bpy.ops.wm.save_mainfile()
        hsr = persist.sidecar_file(blend)
        with open(hsr, "rb") as fh:
            blob = fh.read()
        (jlen,) = struct.unpack_from("<I", blob, 4)
        doc = json.loads(blob[8:8 + jlen])
        doc["format"] = doc["version"] = persist.FORMAT_VERSION + 1
        js = json.dumps(doc).encode()
        for how, data in (("damaged", b"\0not a history" * 64),
                          ("changed by a newer Hindsight", blob[:4] + struct.pack("<I", len(js)) + js
                           + blob[8 + jlen:])):
            what = f"{case}, folder {how}"
            with open(hsr, "wb") as fh:
                fh.write(data)
            for _ in range(2):             # opened, then saved and opened again
                open_file(blend)
                check(want["S"], want["O"], what)
                quiet(what)
                bpy.ops.wm.save_mainfile()
            with open(hsr, "rb") as fh:
                assert fh.read() == data, f"{what}: the folder was set aside or written"


def _out_of_memory_save():
    real = persist.parts_to_bytes

    def boom(parts):
        raise MemoryError("test: out of memory building the stored history")
    persist.parts_to_bytes = boom
    try:
        bpy.ops.wm.save_mainfile()
    finally:
        persist.parts_to_bytes = real


def test_history_folder_file_saved_out_of_memory():
    """#48 (option A): the history can't be stored in the .blend at the first save of a beta 2 folder file
    (out of memory). No folder is written: the .blend keeps pointing at beta 2's folder, left as it was."""
    blend, want = place("folder")
    hsr = persist.sidecar_file(blend)
    with open(hsr, "rb") as fh:
        before = fh.read()
    SETTINGS.crash_recovery = "LAST_SAVED"
    open_file(blend)
    _out_of_memory_save()
    assert persistence.KEPT_LAST_SAVE_TEXT in [t for _s, t in runtime.load_notes], \
        ("the user isn't told the history wasn't stored", runtime.load_notes)
    with open(hsr, "rb") as fh:
        assert fh.read() == before, "beta 2's folder was written"
    open_file(blend)
    check(want["S"], want["O"], "folder file saved out of memory")


def test_a_newer_hindsights_folder_stays_pointed_at():
    """A history folder saved by a newer Hindsight is never written, and saving keeps the .blend pointing at
    it, so that Hindsight still finds it (every other save stops pointing at a folder, #48)."""
    blend, want = place("both")
    hsr = persist.sidecar_file(blend)
    with open(hsr, "rb") as fh:
        blob = fh.read()
    (jlen,) = struct.unpack_from("<I", blob, 4)
    doc = json.loads(blob[8:8 + jlen])
    doc["format"] = doc["version"] = persist.FORMAT_VERSION + 1
    js = json.dumps(doc).encode()
    newer = persist.REG_MAGIC + struct.pack("<I", len(js)) + js + blob[8 + jlen:]
    with open(hsr, "wb") as fh:
        fh.write(newer)
    SETTINGS.crash_recovery = "LAST_SAVED"
    for what in ("opened", "saved and opened again"):
        open_file(blend)
        assert persistence._PROTECT["sidecar"], f"{what}: the folder isn't taken for a newer Hindsight's"
        check(want["S"], want["O"], f"newer folder, {what}")      # the history comes from the .blend
        bpy.ops.wm.save_mainfile()
        assert bpy.data.texts[persistence.TEXT_NAME].get(persistence.SIDECAR_PROP), \
            f"{what}: the .blend no longer points at the newer Hindsight's folder"
        with open(hsr, "rb") as fh:
            assert fh.read() == newer, f"{what}: the newer Hindsight's folder was written"


if __name__ == "__main__":
    try:
        for t in (test_history_folder_file, test_history_inside_the_blend, test_saved_both_ways,
                  test_unsaved_strokes_after_a_crash, test_last_save_after_a_crash, test_moved_without_its_folder,
                  test_saved_without_its_folder, test_steps_made_without_its_folder, test_folder_not_read_after_the_save, test_history_folder_file_saved_out_of_memory,
                  test_a_newer_hindsights_folder_stays_pointed_at):
            H.run(t)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    H.finish()
