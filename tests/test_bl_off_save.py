import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""#124: Hindsight turned off after a save, then a save without it, keeps the history saved before:
the .blend opens with every step, as a Blender without Hindsight saving it again would."""
import shutil
import tempfile

import bpy
import hindsight
import tests.harness as H
from hindsight.bl import runtime, persistence
from hindsight.core.types import SETTINGS

TMP = tempfile.mkdtemp(prefix="hindsight_offsave_")


def _steps(ob, seeds):
    H.enter(ob, "SCULPT")
    for s in seeds:
        H.fake_stroke(ob, s, n=60)
        assert H.commit(ob) is not None
    H.enter(ob, "OBJECT")


def _off():
    hindsight.unregister()
    H._registered[0] = False


def _on():
    hindsight.register()
    H._registered[0] = True


def _count(name):
    h = runtime.history_of(bpy.data.objects[name])
    return None if h is None else len(h.entries)


def test_save_after_turning_off_keeps_history():
    H.fresh()
    ob = H.make_mesh(1200, name="OFF")
    _steps(ob, range(3))
    path = os.path.join(TMP, "off.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    _off()
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path)
    finally:
        _on()
    bpy.ops.wm.open_mainfile(filepath=path)
    assert _count("OFF") == 3, f"{_count('OFF')} steps"


def test_journal_of_the_last_save_is_not_replayed_onto_them():
    # the parts hold the steps recorded since the save, which the journal of that save holds too
    H.fresh()
    ob = H.make_mesh(1200, name="OFF")
    _steps(ob, range(3))
    path = os.path.join(TMP, "journal.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    _steps(ob, range(3, 5))
    _off()
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path)
    finally:
        _on()
    SETTINGS.crash_recovery = "INTERRUPTED"
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
        assert _count("OFF") == 5, f"{_count('OFF')} steps"
        # nothing was lost: the journal's steps since the last save are in the parts, so no warning says so
        assert persistence.OTHER_SAVE_TEXT not in [t for _s, t in runtime.load_notes], runtime.load_notes
    finally:
        SETTINGS.crash_recovery = "LAST_SAVED"


def test_turned_on_again_holds_the_history_once_and_saves_it():
    H.fresh()
    ob = H.make_mesh(1200, name="OFF")
    _steps(ob, range(3))
    path = os.path.join(TMP, "again.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    _off()
    _on()
    assert persistence.PARTS_PROP not in bpy.data.texts[persistence.TEXT_NAME], "the history is held twice"
    _steps(bpy.data.objects["OFF"], range(3, 4))
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    assert _count("OFF") == 4, f"{_count('OFF')} steps"


def test_turned_off_without_saving_still_recovers_from_the_journal():
    # the file on disk is the last save's: its journal is replayed as before, the held marker changes nothing
    H.fresh()
    ob = H.make_mesh(1200, name="OFF")
    _steps(ob, range(3))
    path = os.path.join(TMP, "nosave.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    _steps(ob, range(3, 5))
    _off()
    _on()
    SETTINGS.crash_recovery = "INTERRUPTED"
    try:
        bpy.ops.wm.open_mainfile(filepath=path)          # the session ended without a save
        assert _count("OFF") == 5, f"{_count('OFF')} steps"
        assert persistence.OTHER_SAVE_TEXT not in [t for _s, t in runtime.load_notes], runtime.load_notes
    finally:
        SETTINGS.crash_recovery = "LAST_SAVED"


def test_turned_on_after_opening_another_file_reads_its_parts():
    """#11 review 2: Hindsight off, another format 5 file opened (no load handler runs), Hindsight on: that file's
    parts are read, not deleted, and its next save keeps its own history, not the one opened before."""
    H.fresh()
    _steps(H.make_mesh(1200, name="ONE"), range(3))
    one = os.path.join(TMP, "one.blend")
    bpy.ops.wm.save_as_mainfile(filepath=one)
    H.fresh()
    _steps(H.make_mesh(1200, name="TWO"), range(2))
    two = os.path.join(TMP, "two.blend")
    bpy.ops.wm.save_as_mainfile(filepath=two)
    bpy.ops.wm.open_mainfile(filepath=one)
    _off()
    try:
        bpy.ops.wm.open_mainfile(filepath=two)
    finally:
        _on()
    assert _count("TWO") == 2, f"{_count('TWO')} steps"
    assert persistence.PARTS_PROP not in bpy.data.texts[persistence.TEXT_NAME], "the history is held twice"
    bpy.ops.wm.save_as_mainfile(filepath=two)
    bpy.ops.wm.open_mainfile(filepath=two)
    assert _count("TWO") == 2 and len(runtime.reg().histories) == 1, (_count("TWO"), runtime.reg().histories)


def test_steps_after_turning_on_again_come_back_after_a_crash():
    """#11 review 7 (#124's "not covered"): off, saved while off, on again, more steps, then a crash: the steps
    recorded after turning on are replayed onto the file saved while off, whose parts are the journal's marker's."""
    H.fresh()
    ob = H.make_mesh(1200, name="OFF")
    _steps(ob, range(3))
    path = os.path.join(TMP, "crash.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    _off()
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path)
    finally:
        _on()
    _steps(bpy.data.objects["OFF"], range(3, 5))
    SETTINGS.crash_recovery = "INTERRUPTED"
    try:
        bpy.ops.wm.open_mainfile(filepath=path)          # the session ended without a save
        assert _count("OFF") == 5, f"{_count('OFF')} steps"
        assert persistence.OTHER_SAVE_TEXT not in [t for _s, t in runtime.load_notes], runtime.load_notes
    finally:
        SETTINGS.crash_recovery = "LAST_SAVED"


H.run(test_save_after_turning_off_keeps_history)
H.run(test_turned_off_without_saving_still_recovers_from_the_journal)
H.run(test_journal_of_the_last_save_is_not_replayed_onto_them)
H.run(test_turned_on_again_holds_the_history_once_and_saves_it)
H.run(test_turned_on_after_opening_another_file_reads_its_parts)
H.run(test_steps_after_turning_on_again_come_back_after_a_crash)
shutil.rmtree(TMP, ignore_errors=True)
H.finish()
