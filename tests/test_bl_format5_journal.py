import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""#128 (11.9): the crash journal around format 5 (dev/FORMAT5_JOURNAL.md, "What the build ticket does with this").

A file without its parts (Blender's autosave, `quit.blend`) opens with no history and a note, and leaves the journal
of its path byte for byte as it was, so the .blend opened afterwards brings every step back. A save whose step parts
did not all come back keeps the journal. A journal of another save is not replayed, and replayed steps are saved as
the journal held them. Saves that meet another add-on's save handlers keep every step (#127's review).
Crash recovery: Unsaved steps.

Run: blender -b --factory-startup --python tests/test_bl_format5_journal.py (or tests/run_tests.py).
"""
import tempfile

import bpy

import hindsight
import tests.harness as H
from hindsight.bl import persistence, prefs, runtime
from hindsight.core import persist
from hindsight.core.types import SETTINGS

TMP = tempfile.mkdtemp(prefix="hs_f5_journal_")


def _strokes(seeds):
    ob = bpy.data.objects["J"]
    H.enter(ob, "SCULPT")
    for s in seeds:
        H.fake_stroke(ob, s, n=40)
        assert H.commit(ob) is not None
    after = H.snapshot(ob)
    H.enter(ob, "OBJECT")
    return after


def _saved_a(name):
    """A format 5 file saved with two strokes (save A), then a stroke more, only in its crash journal. Returns the
    path and the mesh after that stroke."""
    H.fresh()
    SETTINGS.crash_recovery = "INTERRUPTED"
    H.make_mesh(600, name="J")
    _strokes(range(2))
    path = os.path.join(TMP, name + ".blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return path, _strokes([9])


def _open(path):
    bpy.ops.wm.open_mainfile(filepath=path)
    prefs.FORCE["record_scope"] = "EVERYTHING"


def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _journal(path):
    persistence.drain_journal_writer()
    return _bytes(persistence.journal_path(path))


def _autosave(path):
    """What Blender's autosave holds of the open session: Blender writes it with no Python handler run, so the Text
    has the stub without its parts. It is put at `path`, as Recover Auto Save opens it under the .blend's own path."""
    auto = os.path.join(TMP, "autosave.blend")
    hl = bpy.app.handlers
    gone = [(lst, fn) for lst in (hl.save_pre, hl.save_post) for fn in (persistence.on_save_pre, persistence.on_save_post)
            if fn in lst]
    for lst, fn in gone:
        lst.remove(fn)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=auto, copy=True)
    finally:
        for lst, fn in gone:
            lst.append(fn)
    stored = H.stored_text(auto)
    assert stored.get(persistence.DATA_PROP) == persist.STUB and persistence.PARTS_PROP not in stored, list(stored)
    os.replace(auto, path)


def _back(path, after, n, cursor):
    h = runtime.history_of(bpy.data.objects["J"])
    assert h is not None and (len(h.entries), h.cursor) == (n, cursor), None if h is None else (len(h.entries), h.cursor)
    ob = bpy.data.objects["J"]
    H.enter(ob, "SCULPT")
    runtime.goto(bpy.context, n)
    H.assert_same(H.snapshot(ob), after, "the steps since the save, brought back by the journal")
    H.enter(ob, "OBJECT")


def test_file_without_its_parts_leaves_the_journal_and_the_blend_brings_every_step_back():
    """An autosave opened at the .blend's path: no history, the note, and the journal of that path byte for byte as
    it was, also after a stroke in it (journaled as an unsaved file's). The .blend opened afterwards brings back
    every step, the one since its save included."""
    path, after = _saved_a("no_parts")
    blend, journal = _bytes(path), _journal(path)
    _autosave(path)
    _open(path)
    assert runtime.history_of(bpy.data.objects["J"]) is None
    assert persistence.NO_PARTS_TEXT in [t for _s, t in runtime.load_notes], runtime.load_notes
    assert _journal(path) == journal, "opening a file without its parts changed the journal of its path"
    _strokes([20])
    assert _journal(path) == journal, "a stroke in a file without its parts went into the .blend's journal"
    with open(path, "wb") as fh:
        fh.write(blend)
    _open(path)                                      # the .blend itself; the autosave's session ends without saving
    _back(path, after, 3, 2)


def test_file_without_its_parts_saved_over_the_blend_starts_its_journal_again():
    """Saving the session of a file without its parts over the .blend starts the journal of that path again, its
    SAVE naming the index part's save id; a stroke after that save comes back after a crash, also when Hindsight was
    turned off and on in between."""
    path, _after = _saved_a("no_parts_saved")
    _autosave(path)
    _open(path)
    _strokes([30])
    bpy.ops.wm.save_mainfile(filepath=path)
    recs = persistence.current_journal().read()
    sid = H.stored_registry(path).save_id
    assert sid and [(t, h.get("save_id")) for t, h, _p in recs] == [("SAVE", sid)], recs
    hindsight.unregister()
    hindsight.register()
    after = _strokes([31])
    _open(path)                                      # the session ends without saving
    _back(path, after, 2, 1)


def test_save_whose_step_parts_did_not_all_come_back_keeps_the_journal():
    """A save handler after Hindsight's deletes a step part of Blender's copy: the journal of the last good save
    stays byte for byte, also after a stroke (the history is cut, so it is journaled as an unsaved file's). Save A
    put back (as Blender's .blend1 keeps it) is the save the journal names, and every step comes back."""
    path, after = _saved_a("partial")
    save_a, journal = _bytes(path), _journal(path)

    def drop(*_a):
        g = bpy.data.texts[persistence.TEXT_NAME][persistence.PARTS_PROP]
        uid = next(k for k in g.keys() if k not in ("index", "index_copy"))
        del g[uid]["0"]
    bpy.app.handlers.save_pre.append(drop)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path)
    finally:
        bpy.app.handlers.save_pre.remove(drop)
    assert _journal(path) == journal, "a save whose step parts did not all come back started the journal again"
    _strokes([40])
    assert _journal(path) == journal, "a stroke after that save went into the journal of the last good save"
    with open(path, "wb") as fh:
        fh.write(save_a)
    _open(path)
    _back(path, after, 3, 2)


def test_journal_of_save_a_beside_the_file_of_save_b_replays_nothing():
    """A crash after Blender put save B in place, before save_post started the journal again: the journal still
    names save A. Nothing is replayed, and every step is the file's (save B holds them all)."""
    path, after = _saved_a("a_beside_b")
    journal = _journal(path)
    bpy.ops.wm.save_as_mainfile(filepath=path)
    persistence._close_journal()
    with open(persistence.journal_path(path), "wb") as fh:
        fh.write(journal)
    _open(path)
    h = runtime.history_of(bpy.data.objects["J"])
    assert h is not None and (len(h.entries), h.cursor) == (3, 3), None if h is None else (len(h.entries), h.cursor)
    H.assert_same(H.snapshot(bpy.data.objects["J"]), after, "save B's mesh")


def test_replayed_steps_are_saved_as_the_journal_held_them():
    """A stroke brought back by the journal is saved into the open step part byte for byte as the journal held it."""
    path, _after = _saved_a("replayed")
    held = [bytes(p) for t, _h, p in persistence.current_journal().read() if t == "STEP"]
    assert len(held) == 1, len(held)
    _open(path)
    uid = runtime.history_of(bpy.data.objects["J"]).uid
    bpy.ops.wm.save_mainfile(filepath=path)
    assert list(H.stored_text(path)[persistence.PARTS_PROP][uid]) == ["0"], "the steps are not in one step part"
    steps = H.saved_steps(H.stored_registry(path))[uid][3]
    assert len(steps) == 3 and steps[2][0] == held[0], "the replayed stroke isn't saved as the journal held it"


def _count():
    h = runtime.history_of(bpy.data.objects["J"])
    return None if h is None else len(h.entries)


def _first_in_save_post(fn, path):
    """Saves `path` with `fn` as a save_post handler run before Hindsight's (another add-on's)."""
    bpy.app.handlers.save_post.insert(0, fn)
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path)
    finally:
        if fn in bpy.app.handlers.save_post:
            bpy.app.handlers.save_post.remove(fn)


def test_save_from_another_handler_during_a_save_cuts_no_step():
    """#127 review 2: another add-on's save_post handler, run before Hindsight's, saves a copy while Blender's copy
    of the step parts is the only one: the session cuts no step, and both files hold every step."""
    path, _after = _saved_a("nested")
    _open(path)                          # the saved steps are read out of their step part
    _strokes([50])                       # at the saved step: it takes the place of the replayed one
    assert _count() == 3, f"{_count()} steps before saving"
    copy = os.path.join(TMP, "nested_copy.blend")

    ran = []

    def nested(*_a):
        if not ran:
            ran.append(1)
            bpy.ops.wm.save_as_mainfile(filepath=copy, copy=True)
    _first_in_save_post(nested, path)
    assert ran and os.path.isfile(copy), "the nested save didn't run"
    assert _count() == 3, f"{_count()} steps in the session"
    for p in (copy, path):
        _open(p)
        assert _count() == 3, f"{_count()} steps in {os.path.basename(p)}"


def test_step_parts_lost_after_the_file_was_written_are_read_back_from_it():
    """#127 review 3: a save_post handler run before Hindsight's deletes the step parts Blender has just written:
    they are read back from that file, so no step is cut, and the journal starts again with its save id."""
    path, _after = _saved_a("lost_after")
    _open(path)

    def drop(*_a):
        del bpy.data.texts[persistence.TEXT_NAME][persistence.PARTS_PROP]
    _first_in_save_post(drop, path)
    assert _count() == 3, f"{_count()} steps in the session"
    recs = persistence.current_journal().read()
    sid = H.stored_registry(path).save_id
    assert sid and [(t, h.get("save_id")) for t, h, _p in recs] == [("SAVE", sid)], recs
    assert persistence.PARTS_PROP not in bpy.data.texts[persistence.TEXT_NAME], "the history is held twice"
    _open(path)
    assert _count() == 3, f"{_count()} steps after reopening"


for fn in (test_file_without_its_parts_leaves_the_journal_and_the_blend_brings_every_step_back,
           test_file_without_its_parts_saved_over_the_blend_starts_its_journal_again,
           test_save_whose_step_parts_did_not_all_come_back_keeps_the_journal,
           test_journal_of_save_a_beside_the_file_of_save_b_replays_nothing,
           test_replayed_steps_are_saved_as_the_journal_held_them,
           test_save_from_another_handler_during_a_save_cuts_no_step,
           test_step_parts_lost_after_the_file_was_written_are_read_back_from_it):
    H.run(fn)
H.finish()
