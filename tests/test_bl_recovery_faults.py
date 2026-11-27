"""Faults while saving and while recording, in Blender: what comes back must match the file.

The owner's rule: a history that comes back must match the meshes in the file at every position, or be
left out with a note. It may never show shapes that never existed, and a failure while saving may
never destroy history that was valid before.

Each test records strokes and keeps the mesh after (and before) every step by its number. The oracle
(check) opens every position of every history that came back and compares the mesh with the one the
session had there, and the position the history opens at with the file.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import contextlib
import random
import shutil
import tempfile

import bpy
import tests.harness as H
from hindsight.bl import runtime, persistence, joinsep
from hindsight.core import persist
from hindsight.core.journal import Journal, _records, _scan
from hindsight.core.types import SETTINGS

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hindsight_recovery_faults_")


def setup(recovery="INTERRUPTED"):
    H.fresh()
    SETTINGS.crash_recovery = recovery
    SETTINGS.max_steps = 10000
    runtime.missing_message[0] = None


class Book:
    """The mesh after and before every step, by step number."""

    def __init__(self):
        self.after = {}
        self.before = {}

    def stroke(self, ob, seed):
        if ob.mode != "SCULPT":
            H.enter(ob, "SCULPT")
        before = H.snapshot(ob)
        H.fake_stroke(ob, seed, n=40)
        step = H.commit(ob)
        runtime.history_of(ob)
        if step is None:
            return None
        self.after[step.seq] = H.snapshot(ob)
        self.before[step.seq] = before
        return step


def make(names=("A", "B"), n=400):
    obs = {}
    for i, nm in enumerate(names):
        obs[nm] = H.make_mesh(n, name=nm, location=(3 * i, 0, 0))
    C.view_layer.update()
    return obs


def to_object_mode():
    ob = C.view_layer.objects.active
    if ob is not None and ob.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def save(path):
    to_object_mode()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return {o.name: H.snapshot(o) for o in bpy.data.objects if o.type == "MESH"}


def interrupt(path, cut=None):
    """The session ends without saving (its journal as it was, or cut to its first `cut` records);
    the file is opened again."""
    to_object_mode()
    persistence._close_journal()
    if cut is not None:
        jp = persistence.journal_path(path)
        cut_journal(jp, cut)
    bpy.ops.wm.open_mainfile(filepath=path)


def cut_journal(jp, n):
    """Cut the journal after its first `n` records (all of them if it has fewer): one scan finds
    where each record ends, then the file is cut at that offset."""
    j = Journal(jp)
    recs = j.read()
    j.close()
    ends = []
    with open(jp, "rb") as fh:
        for rec in _records(_scan(fh, os.path.getsize(jp), False), []):
            if len(ends) == n:
                break
            ends.append(rec[1])
    size = ends[-1] if ends else 0
    with open(jp, "r+b") as fh:
        fh.truncate(size)
    return recs[:n]


def notes():
    return [t for _sev, t in runtime.load_notes]


def blender_copy():
    """The history keys Blender holds in memory on the Text (none once a save is over, #55/#56)."""
    text = bpy.data.texts.get(persistence.TEXT_NAME)
    return [k for k in (persistence.DATA_PROP, persistence.DATA_BIN_PROP) if text is not None and k in text]


def check(book, file_state, where=""):
    """Every history that came back: the mesh at each position is the session's, and the position it
    opens at is the file's. Objects are compared by name with the file."""
    to_object_mode()
    for name, snap in file_state.items():
        ob = bpy.data.objects.get(name)
        assert ob is not None, f"{where}: {name} missing after loading"
        H.assert_same(H.snapshot(ob), snap, f"{where}: {name} as loaded")
    seen = set()
    for name in sorted(file_state):
        ob = bpy.data.objects[name]
        h = runtime.history_of(ob)
        if h is None or not h.entries or h.uid in seen:
            continue
        seen.add(h.uid)
        if len(h.members) > 1:
            continue                        # merged histories: checked by their own tests
        cursor = h.cursor
        for i in list(range(len(h.entries) + 1)) + [cursor]:
            runtime._goto_history(h, i, True)
            ob = bpy.data.objects[name]
            if i > 0:
                want = book.after.get(h.entries[i - 1].seq)
            else:
                want = book.before.get(h.entries[0].seq)
            if want is not None:
                H.assert_same(H.snapshot(ob), want, f"{where}: {name} at position {i} of {len(h.entries)}")
        H.assert_same(H.snapshot(bpy.data.objects[name]), file_state[name], f"{where}: {name} back at {cursor}")


# ------------------------------------------------------------------------------------------- saving
def _save_fails_and_keeps_the_last_save(fails, tag, fake=None, save_as=False):
    """#48 (option A): building the history for the .blend fails in `fails` (out of memory; or `fake`
    stands in for it). No history folder is written: the .blend keeps the history of the save before
    (put back if it was taken out to save memory) with the meshes of this one, its crash journal stays
    with it (on a Save As, at the new path), and the user is told to save again. Opening it must not
    play that history onto the meshes that changed since."""
    setup()
    obs = make(("A", "B"))
    book = Book()
    for s in range(3):
        book.stroke(obs["A"], s)
        book.stroke(obs["B"], 20 + s)
    path = os.path.join(TMP, f"kept_{tag}.blend")
    save(path)
    stored = bpy.data.texts[persistence.TEXT_NAME][persistence.SAVE_ID_PROP]
    book.stroke(obs["A"], 10)            # B is unchanged since that save
    real = getattr(persist, fails)

    def boom(*a, **k):
        raise MemoryError("test: out of memory building the stored history")
    setattr(persist, fails, fake or boom)
    if save_as:
        path = os.path.join(TMP, f"kept_{tag}_as.blend")
    try:
        file_state = save(path)
    finally:
        setattr(persist, fails, real)
    assert not os.path.exists(persist.sidecar_dir(path)), f"{tag}: a history folder was written"
    assert not blender_copy(), f"{tag}: Blender's copy of the history is left behind (#56)"
    text = H.stored_text(path)
    assert persistence.DATA_BIN_PROP in text and text[persistence.SAVE_ID_PROP] == stored, \
        f"{tag}: the .blend doesn't keep the history of the save before"
    kept = getattr(persistence, "KEPT_LAST_SAVE_TEXT", None)
    assert kept in notes(), f"{tag}: the user isn't told"
    # A changed since the save before, and opens below without the history it had then (unless a .blend1
    # bridges it): the note must not promise more
    assert "changed since" in kept, f"{tag}: the note says the .blend keeps more than it does"
    assert not [lib for lib in bpy.data.libraries if lib.filepath.endswith(".hindsight.blend")], \
        "reading the last save back leaves no library behind"
    assert not [f for f in os.listdir(TMP) if f.endswith(".hindsight.blend")], "nor the link it was read through"
    book.stroke(obs["B"], 30)            # recorded after the failed save, then interrupted
    # INTERRUPTED first: opening with LAST_SAVED starts the journal again
    for rec in ("INTERRUPTED", "LAST_SAVED"):
        SETTINGS.crash_recovery = rec
        interrupt(path)
        ha = runtime.history_of(bpy.data.objects["A"])
        # A's history is from the save before: left out, or (D107) bridged by Blender's backup copy,
        # which holds A as that save had it, with one step to A as it is now
        assert ha is None or not ha.entries or ha.entries[-1].load().label == runtime.OUTSIDE_LABEL, \
            f"{tag} {rec}: A's history from the save before is never played onto this mesh"
        hb = runtime.history_of(bpy.data.objects["B"])
        assert hb is not None and len(hb.entries) >= 3, (tag, rec, hb)
        if rec == "INTERRUPTED":
            # the .blend kept the save before, so the crash journal since that save stays with it
            assert len(hb.entries) == 4, f"{tag}: B's stroke after the failed save was not brought back"
        check(book, file_state, f"{tag} {rec}")


def test_embed_save_out_of_memory_keeps_the_history():
    """The last save's copy is taken out of the .blend before the new one is built (so three copies are
    never held at once); building then runs out of memory. Before #48 the history went to a folder."""
    _save_fails_and_keeps_the_last_save("parts_to_bytes", "bytes")


def test_embed_save_blender_fails_to_write():
    """#56: Blender fails to write the .blend (its temporary file can't be made). The .blend on disk stays as
    it was, Blender's copy of the history isn't left behind in memory, and the next save stores every step."""
    setup()
    obs = make(("A",))
    book = Book()
    for s in range(3):
        book.stroke(obs["A"], s)
    path = os.path.join(TMP, "embed_write_fails.blend")
    save(path)
    with open(path, "rb") as fh:
        before = fh.read()
    book.stroke(obs["A"], 10)
    os.mkdir(path + "@")                 # Blender writes "<file>@" first, then renames it: a folder there stops it
    try:
        try:
            save(path)
        except RuntimeError:
            pass
        else:
            raise AssertionError("the save was meant to fail")
    finally:
        os.rmdir(path + "@")
    with open(path, "rb") as fh:
        assert fh.read() == before, "the .blend on disk is as it was"
    assert not blender_copy(), "Blender's copy of the history is not left behind"
    h = runtime.history_of(bpy.data.objects["A"])
    assert h is not None and len(h.entries) == 4 and h.cursor == 4, h
    file_state = save(path)
    SETTINGS.crash_recovery = "LAST_SAVED"
    interrupt(path)
    h = runtime.history_of(bpy.data.objects["A"])
    assert h is not None and len(h.entries) == 4 and h.cursor == 4, h
    check(book, file_state, "saved after the failed save")


def test_embed_save_fails_before_the_old_copy_is_touched():
    """Building the history fails before the last save's copy is taken out of the .blend."""
    _save_fails_and_keeps_the_last_save("registry_parts", "parts")


def test_embed_save_fails_as_blender_stores_it():
    """The new history is built, but Blender can't take its copy of it (stood in for by a value Blender
    refuses): the last save's copy, taken out of the .blend to save memory, must still go back."""
    _save_fails_and_keeps_the_last_save("parts_to_bytes", "store", fake=lambda parts: object())


def test_embed_save_as_out_of_memory_takes_the_journal():
    """A Save As that can't store the history: the new .blend keeps the last save's history, so the crash
    journal since that save must go with it, or a crash loses every step since."""
    _save_fails_and_keeps_the_last_save("parts_to_bytes", "save_as", save_as=True)


def test_save_failure_note_goes_when_a_save_works():
    """#8 review: the note that a save couldn't store the history is shown once however many saves fail,
    and goes as soon as a save stores it (it isn't left until the file is opened again)."""
    setup()
    obs = make(("A",))
    Book().stroke(obs["A"], 0)
    path = os.path.join(TMP, "note_goes.blend")
    for name in ("NOT_STORED_TEXT", "KEPT_LAST_SAVE_TEXT"):     # first save, then a save with one before it
        text = getattr(persistence, name)
        with _no_room_in_blend():
            save(path)
            save(path)
        assert notes().count(text) == 1, (name, notes())
        save(path)
        assert text not in notes(), f"{name}: still shown after a save that stored the history"


def test_first_save_out_of_memory_keeps_the_history():
    """#48 (option A): the first save can't store the history in the .blend (out of memory, no copy in
    it yet). No history folder is written either: the history stays in the session, the user is told
    to free memory and save again, and the next save that works stores it in the .blend."""
    setup()
    obs = make(("A",))
    book = Book()
    for s in range(3):
        book.stroke(obs["A"], s)
    path = os.path.join(TMP, "first_oom.blend")
    with _no_room_in_blend():
        save(path)
    assert not os.path.exists(persist.sidecar_dir(path)), "a history folder was written"
    assert getattr(persistence, "NOT_STORED_TEXT", None) in notes(), "the user isn't told to save again"
    h = runtime.history_of(bpy.data.objects["A"])
    assert h is not None and len(h.entries) == 3, "the history doesn't stay in the session"
    file_state = save(path)
    SETTINGS.crash_recovery = "LAST_SAVED"
    interrupt(path)
    h = runtime.history_of(bpy.data.objects["A"])
    assert h is not None and len(h.entries) == 3, h
    check(book, file_state, "first save out of memory, then saved again")


@contextlib.contextmanager
def _no_room_in_blend():
    """Building the history to store in the .blend runs out of memory."""
    real = persist.parts_to_bytes

    def boom(parts):
        raise MemoryError("test: out of memory building the stored history")
    persist.parts_to_bytes = boom
    try:
        yield
    finally:
        persist.parts_to_bytes = real


def test_save_with_recording_paused_after_changes():
    """Strokes made while an object's recording is paused aren't steps. Saved like that and opened
    again, the file's mesh became the history's last state: turning recording on found nothing to
    record, and going back played the steps onto a mesh they weren't made on."""
    setup("LAST_SAVED")
    obs = make(("A",))
    book = Book()
    for s in range(3):
        book.stroke(obs["A"], s)
    ob = obs["A"]
    runtime.turn_off(C, ob)
    H.fake_stroke(ob, 99, n=40)
    assert H.commit(ob) is None
    path = os.path.join(TMP, "paused.blend")
    file_state = save(path)
    interrupt(path)
    ob = bpy.data.objects["A"]
    h = runtime.history_of(ob)
    H.enter(ob, "SCULPT")
    runtime.turn_on(C, ob)
    h = runtime.history_of(ob)
    assert h is not None
    for e in h.entries[3:]:
        book.after[e.seq] = file_state["A"]
    check(book, file_state, "paused")


def test_blend1_renamed_back():
    """After a crash the user puts back the previous save (the .blend1 Blender keeps), with the journal
    of the newer save still there. Its records are positions in the newer history: replaying them onto
    the older one gave shapes that never existed. They belong to another save and are left alone.
    #48: the history is in the .blend (and its .blend1)."""
    setup()
    obs = make(("A",))
    book = Book()
    for s in range(3):
        book.stroke(obs["A"], s)
    path = os.path.join(TMP, "backup.blend")
    old_state = save(path)
    for s in range(3):
        book.stroke(obs["A"], 10 + s)
    runtime.goto(C, 2)
    book.stroke(obs["A"], 20)
    save(path)
    book.stroke(obs["A"], 30)
    book.stroke(obs["A"], 31)
    to_object_mode()
    persistence._close_journal()
    shutil.copyfile(path + "1", path)                   # the .blend1 put back
    bpy.ops.wm.open_mainfile(filepath=path)
    h = runtime.history_of(bpy.data.objects["A"])
    assert h is not None and len(h.entries) == 3 and h.cursor == 3, h
    assert persistence.OTHER_SAVE_TEXT in notes(), notes()
    check(book, old_state, "backup")


# ------------------------------------------------------------------------------------- interrupted
def _journal_len(path):
    persistence.current_journal().flush(fsync=False)
    return len(Journal(persistence.journal_path(path)).read())


def _cuts(path, n0, n1):
    """Open the file again with the session's journal cut after each of its records n0..n1."""
    to_object_mode()
    persistence._close_journal()
    jp = persistence.journal_path(path)
    with open(jp, "rb") as fh:
        full = fh.read()
    for cut in range(n0, n1 + 1):
        with open(jp, "wb") as fh:
            fh.write(full)
        persistence._close_journal()
        interrupt(path, cut=cut)
        yield cut


def test_crash_in_the_middle_of_a_global_jump():
    """A jump on the global timeline moves several histories one after another and writes a CURSOR
    record for each. Cut after any of them: the file still holds the meshes of the save, and every
    history comes back with the saved state where it opens."""
    setup()
    obs = make(("A", "B", "C"))
    book = Book()
    for s in range(3):
        for nm in ("A", "B", "C"):
            book.stroke(obs[nm], s * 3 + ord(nm))
    path = os.path.join(TMP, "jump.blend")
    file_state = save(path)
    book.stroke(obs["B"], 99)
    runtime.set_global(C, True)
    n0 = _journal_len(path)
    runtime.goto(C, 4)
    n1 = _journal_len(path)
    assert n1 - n0 >= 2, (n0, n1)
    for cut in _cuts(path, n0 - 1, n1):
        check(book, file_state, f"cut {cut}")
        assert not notes(), notes()


def test_crash_in_the_middle_of_a_join():
    """A join after the save writes RESET, the merged history step by step, and the join. Cut after
    any record: A and B are as in the file, each points at a history that lists it, and the merged
    history (when it comes back) opens at the file's state."""
    setup()
    obs = make(("A", "B"), n=600)
    book = Book()
    book.stroke(obs["A"], 1)
    book.stroke(obs["B"], 2)
    book.stroke(obs["A"], 3)
    path = os.path.join(TMP, "join_cut.blend")
    file_state = save(path)
    book.stroke(obs["B"], 4)
    to_object_mode()
    n0 = _journal_len(path)
    A, B = bpy.data.objects["A"], bpy.data.objects["B"]
    for o in C.selected_objects:
        o.select_set(False)
    A.select_set(True)
    B.select_set(True)
    C.view_layer.objects.active = A
    assert joinsep.join_with_history(C) is True
    n1 = _journal_len(path)
    assert n1 - n0 >= 5, (n0, n1)
    for cut in _cuts(path, n0, n1):
        A, B = bpy.data.objects["A"], bpy.data.objects["B"]
        H.assert_same(H.snapshot(A), file_state["A"], f"cut {cut}: A")
        H.assert_same(H.snapshot(B), file_state["B"], f"cut {cut}: B")
        for ob in (A, B):
            h = runtime.history_of(ob)
            assert h is None or runtime.obj_uid(ob) in h.members, f"cut {cut}: {ob.name} not in its history"
        h = runtime.history_of(A)
        if h is not None and len(h.members) > 1 and h.entries:
            cursor = h.cursor
            for i in range(len(h.entries) + 1):
                runtime._goto_history(h, i, True)
            runtime._goto_history(h, cursor, True)
            H.assert_same(H.snapshot(bpy.data.objects["A"]), file_state["A"], f"cut {cut}: A back")
            H.assert_same(H.snapshot(bpy.data.objects["B"]), file_state["B"], f"cut {cut}: B back")
        else:
            check(book, file_state, f"cut {cut}")


# --------------------------------------------------------------------------------- random sessions
def run_session(seed, n_actions=24):
    """Strokes, scrubbing (back past the saved position too), new branches, the step cap, Delete
    earlier steps, Clear, recording off and on, saves, and an interruption at a random record, twice."""
    rng = random.Random(seed)
    setup()
    names = ("A", "B")
    obs = make(names, n=300)
    book = Book()
    for nm in names:
        book.stroke(obs[nm], rng.randrange(10 ** 6))
    path = os.path.join(TMP, f"random_{seed}.blend")
    file_state = save(path)
    for round_ in range(2):
        for _ in range(n_actions):
            nm = rng.choice(names)
            ob = bpy.data.objects[nm]
            h = runtime.history_of(ob)
            a = rng.random()
            if a < 0.45:
                if h is None or h.enabled:
                    book.stroke(ob, rng.randrange(10 ** 6))
            elif a < 0.65 and h is not None and h.enabled:
                H.enter(ob, "SCULPT")
                runtime.goto(C, rng.randint(0, len(h.entries)))
            elif a < 0.7:
                SETTINGS.max_steps = rng.choice((3, 4, 10000))
                if h is not None and h.enabled:
                    book.stroke(ob, rng.randrange(10 ** 6))
                SETTINGS.max_steps = 10000
            elif a < 0.75 and h is not None:
                H.enter(ob, "SCULPT")
                runtime.delete_past(C, ob)
            elif a < 0.78 and h is not None:
                H.enter(ob, "SCULPT")
                runtime.reset_history(C, ob)
            elif a < 0.85 and h is not None and h.enabled:
                H.enter(ob, "SCULPT")
                runtime.turn_off(C, ob)
                H.fake_stroke(ob, rng.randrange(10 ** 6), n=40)
                runtime.turn_on(C, ob)
                h = runtime.history_of(ob)
                if h.entries and h.cursor == len(h.entries):
                    e = h.entries[-1]
                    if e.seq not in book.after:
                        book.after[e.seq] = H.snapshot(ob)
            elif a < 0.92:
                file_state = save(path)
        cut = None
        n = _journal_len(path)
        if rng.random() < 0.7:
            cut = rng.randint(1, n)
        interrupt(path, cut=cut)
        check(book, file_state, f"seed {seed} round {round_} cut {cut}/{n}")
        obs = {nm: bpy.data.objects[nm] for nm in names}


# ------------------------------------------------------------------------------------- damaged files
def _damage(data: bytes, kind: str) -> bytes:
    n = len(data)
    if kind == "flip":                       # one bit in the middle of the step data
        k = n * 2 // 3
        return data[:k] + bytes([data[k] ^ 0x10]) + data[k + 1:]
    if kind == "cut":                        # the end of the file lost
        return data[: n // 2]
    if kind == "zeros":                      # a tail of zeros, as after a power cut on some disks
        k = n * 3 // 4
        return data[:k] + bytes(n - k)
    raise ValueError(kind)


def _resave_without_hindsight(path, change):
    """Change the stored history inside the .blend and save it, with Hindsight off (its save handler
    would write the history again)."""
    import hindsight
    hindsight.unregister()
    H._registered[0] = False
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
        change()
        bpy.ops.wm.save_mainfile()
    finally:
        hindsight.register()
        H._registered[0] = True


def _as_v1_folder(path):
    """The history stored in a saved .blend moved into a history folder next to it, as V1 saved it with
    Save history next to the .blend (its default): the folder holds the same bytes, the .blend points there."""
    def change():
        t = bpy.data.texts[persistence.TEXT_NAME]
        data = bytes(t[persistence.DATA_BIN_PROP])
        del t[persistence.DATA_BIN_PROP]
        t[persistence.SIDECAR_PROP] = True
        os.makedirs(persist.sidecar_dir(path), exist_ok=True)
        with open(persist.sidecar_file(path), "wb") as fh:
            fh.write(data)
    _resave_without_hindsight(path, change)


def test_damaged_saved_history():
    """The saved history damaged on disk (a bit flipped, the end cut off, a tail of zeros), in the
    history folder and inside the .blend. Opening the file never fails and never plays damaged steps:
    what comes back matches the file at every position, and anything that didn't come back is told."""
    for store in ("folder", "blend"):          # a V1 history folder, the history inside the .blend
        for kind in ("flip", "cut", "zeros"):
            setup("LAST_SAVED")
            obs = make(("A",))
            book = Book()
            for s in range(4):
                book.stroke(obs["A"], 20 + s)
            path = os.path.join(TMP, f"damaged_{store}_{kind}.blend")
            file_state = save(path)
            if store == "folder":
                _as_v1_folder(path)         # #48: no save writes a folder; V1's still opens
                f = persist.sidecar_file(path)
                with open(f, "rb") as fh:
                    data = fh.read()
                with open(f, "wb") as fh:
                    fh.write(_damage(data, kind))
            else:
                def change():
                    t = bpy.data.texts[persistence.TEXT_NAME]
                    raw = t[persistence.DATA_BIN_PROP]
                    del t[persistence.DATA_BIN_PROP]      # Blender cuts bytes at a zero when it replaces them
                    t[persistence.DATA_BIN_PROP] = _damage(raw, kind)
                _resave_without_hindsight(path, change)
            setup("LAST_SAVED")
            interrupt(path)
            where = f"{store} {kind}"
            h = runtime.history_of(bpy.data.objects["A"])
            got = 0 if h is None else len(h.entries)
            check(book, file_state, where)
            if got < 4:
                assert notes(), f"{where}: {got} of 4 steps came back and nothing says so"
            # saving again keeps the file usable and never writes the damaged steps back as good ones
            file_state = save(path)
            setup("LAST_SAVED")
            interrupt(path)
            check(book, file_state, where + " after saving again")


REGRESSION_SEEDS = []


def test_random_sessions():
    n = int(os.environ.get("HS_BL_FAULT_SEEDS", "6"))
    for seed in REGRESSION_SEEDS + list(range(n)):
        run_session(seed)


if __name__ == "__main__":
    try:
        for name, fn in list(globals().items()):
            if name.startswith("test_") and callable(fn):
                H.run(fn)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    H.finish()
