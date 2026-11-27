# SPDX-License-Identifier: GPL-3.0-or-later
"""Save / load glue: sidecar folder, embedded Text datablock and the crash journal.

Every save stores the history inside the .blend (Text ``.hindsight_history``, bytes ID property
``hindsight_data_bin``; files saved by 1.0.0-beta.2 and earlier hold base64 text in ``hindsight_data``, still
read); there is no other place to save it (#48, #51). A history folder ``<stem>_hindsight`` left by V1
(its Save history setting, SIDECAR or BOTH) is still read until the next save.

Crash Recovery (``SETTINGS.crash_recovery``):
  LAST_SAVED   loading a file restores only what was saved
  INTERRUPTED  steps recorded after the last save of an interrupted session are recovered
               from the journal (the mesh stays at the saved cursor; scrub forward to see them)
"""
from __future__ import annotations

import contextlib
import binascii
import hashlib
import os
import queue
import shutil
import tempfile
import threading
import uuid

import bpy

from ..core import persist
from ..core.journal import Journal
from ..core.registry import Registry
from ..core.types import SETTINGS
from . import guard, idmap, log, runtime

TEXT_NAME = ".hindsight_history"
DATA_PROP = "hindsight_data"            # base64 text: earlier builds (read; never written since R061)
DATA_BIN_PROP = "hindsight_data_bin"    # R061: the registry's bytes as they are (no base64)
SIDECAR_PROP = "sidecar"
MISSING_TEXT = "History folder missing. Keep '{folder}' next to the .blend to see this file's history"
NEWER_TEXT = ("This history was saved by a newer Hindsight (format {found}). Update Hindsight to see it. "
              "It is left untouched until then")
UNREADABLE_TEXT = ("The saved history could not be read, so it was not loaded. Your .blend is fine. "
                   "The unreadable copy was kept as '{name}'")
UNREADABLE_EMBED_TEXT = ("The history stored in this .blend could not be read, so it was not loaded. "
                         "It is kept in the file in case it can be recovered")
DAMAGED_TEXT = "{lost} damaged step{s} of '{obj}' could not be loaded. The {kept} steps before them are fine"
DAMAGED_PART_TEXT = ("{lost} step{s} of '{obj}' could not be kept because part of its history is damaged. "
                     "The {kept} steps next to where it was saved are fine")
DAMAGED_ALL_TEXT = ("The history of '{obj}' is damaged before the step it was saved at, so none of it could be "
                    "kept ({lost} step{s}). '{obj}' itself is as you saved it")
LEFT_OUT_TEXT = ("{n} step{s} of '{obj}' from the interrupted session could not be brought back. "
                 "They were made after going back past the last save. '{obj}' is as you saved it")
UNREADABLE_PROP = "hindsight_data_unreadable"
SAVE_ID_PROP = "save_id"         # which save of the history this .blend goes with (older builds ignore it)
KEPT_LAST_SAVE_TEXT = ("The history could not be stored, so the .blend keeps the history of the save before "
                       "this one: objects changed since that save may open without their history. Free some "
                       "memory and save again to keep your history")
NOT_STORED_TEXT = ("The history could not be stored in the .blend. Free some memory and save again to keep "
                   "your history")
_SAVE_NOTES = (KEPT_LAST_SAVE_TEXT, NOT_STORED_TEXT)      # what a save says; the next save replaces it
OTHER_SAVE_TEXT = ("Steps from an interrupted session were found, but they belong to another save of this "
                   "file. They were not brought back")
STALE_SIDECAR_TEXT = "The history folder is from another save of this file"

# Set while loading a file whose history this version must not overwrite (newer format); "sidecar" also
# when the history folder it points at is missing: saving keeps pointing at that folder.
_PROTECT = {"sidecar": False, "embed": False}

_ROOT_PACKAGE = (__package__ or "hindsight").rsplit(".bl", 1)[0]

_J = {"journal": None, "path": None, "sid": uuid.uuid4().hex[:12]}    # live journal of the session file
# the history couldn't be stored in the .blend, which keeps the last save's: on_save_post keeps the journal
_FALLBACK = {"kept": False}
_FROM_TEXT = [False]             # the history loaded last was read from the .blend's Text


# ----------------------------------------------------------------------------- journal
def journal_dir() -> str:
    try:
        d = bpy.utils.extension_path_user(_ROOT_PACKAGE, path="journal", create=True)
        if not d:
            raise ValueError("no extension user path")
    except Exception:
        d = os.path.join(tempfile.gettempdir(), "hindsight_journal")
    os.makedirs(d, exist_ok=True)
    return d


def _session_key(blend_path: str) -> str:
    return os.path.abspath(blend_path) if blend_path else f"untitled-{os.getpid()}"


def journal_path(blend_path: str) -> str:
    """Journal file of a .blend path ("" = the unsaved session of this process)."""
    h = hashlib.sha1(_session_key(blend_path).encode("utf-8")).hexdigest()[:16]
    return os.path.join(journal_dir(), h + ".hsj")


def current_journal():
    """The live Journal (or None if none has been opened yet), with every record handed to the helper in it."""
    drain_journal_writer()
    return _J["journal"]


# R079: the records of big steps (a topology change at 1M vertices is about 120 MB to encode and write) are
# encoded and written on a helper thread, in order: once one is waiting, every later record waits behind it.
# Whatever reads, empties, switches or closes the journal waits for the helper first. A crash can lose only the
# newest records still waiting, so the journal recovery reads is always a prefix of what was written.
BIG_RECORD_DEFAULT = 4 << 20     # a step held with this many bytes or more is journaled by the helper
BIG_RECORD = [BIG_RECORD_DEFAULT]
_JW = {"q": None, "thread": None, "error": None}
_JLOCK = threading.Lock()        # one writer at a time into the journal file


def _writer_loop(q) -> None:
    while True:
        item = q.get()
        try:
            if item is None:
                return
            j, rtype, header, fn = item
            payload = fn() if fn is not None else b""
            with _JLOCK:
                if not j.closed:
                    j.append(rtype, header, payload)
                    j.flush(fsync=False)
        except Exception as e:
            _JW["error"] = e
        finally:
            q.task_done()


def _writer_queue():
    q = _JW["q"]
    t = _JW["thread"]
    if q is None or t is None or not t.is_alive():
        q = _JW["q"] = queue.Queue()
        t = _JW["thread"] = threading.Thread(target=_writer_loop, args=(q,), name="hindsight-journal", daemon=True)
        t.start()
    return q


def drain_journal_writer() -> None:
    """Wait until every record handed to the helper is in the journal (main thread)."""
    q = _JW["q"]
    if q is not None:
        q.join()
    e, _JW["error"] = _JW["error"], None
    if e is not None:
        log.error_once(("journal_writer", str(e)), f"crash-recovery journal error: {e}", e)


def _stop_journal_writer() -> None:
    q, t = _JW["q"], _JW["thread"]
    if q is not None and t is not None and t.is_alive():
        drain_journal_writer()
        q.put(None)
        t.join(timeout=10.0)
    _JW["q"] = _JW["thread"] = None


def _close_journal(remove_untitled: bool = False) -> None:
    drain_journal_writer()
    j = _J["journal"]
    path = _J["path"]
    _J["journal"] = None
    _J["path"] = None
    if j is not None:
        try:
            j.close()
        except Exception as e:
            guard.fail("saving", e, "journal close error")
        if remove_untitled and path == journal_path(""):
            with contextlib.suppress(OSError):
                os.remove(path)


def _open_journal(blend_path: str, keep: bool = False, discard: bool = False) -> Journal:
    """(Re)open the journal for blend_path, keeping the current one if it is the same file. keep: the
    records are read right after (crash recovery), so opening reads them already (R065). discard: the
    journal starts again right after (no recovery), so what is in it is let go unread (R067)."""
    path = journal_path(blend_path)
    j = _J["journal"]
    if j is not None and not j.closed and _J["path"] == path:
        if keep or discard:
            drain_journal_writer()
        return j
    _close_journal(remove_untitled=True)
    j = Journal(path, keep=keep, discard=discard)
    _J["journal"] = j
    _J["path"] = path
    return j


def _carry_journal(blend_path: str) -> None:
    """#56: a Save As whose history stayed with the last save. The journal since that save goes with the new
    .blend, so a crash after it brings those steps back; the old .blend's journal is left as it was."""
    j, old, new = _J["journal"], _J["path"], journal_path(blend_path)
    if j is None or j.closed or old == new:
        return
    drain_journal_writer()
    j.flush(fsync=True)
    shutil.copyfile(old, new)
    _open_journal(blend_path)


def _current_blend() -> str:
    try:
        return bpy.data.filepath or ""
    except Exception:
        return ""


def _on_journal(rtype, header, payload_fn=None) -> None:
    # Every journal record is something the user just did (a stroke, a jump, a change to a history):
    # the packer's idle recompression keeps off the CPU while they work.
    p = runtime.packer
    if p is not None:
        note = getattr(p, "note_activity", None)
        if note is not None:
            note()
    j = _J["journal"]
    if j is None or j.closed:
        j = _open_journal(_current_blend())
    header = dict(header, s=_J["sid"])
    q = _JW["q"]
    if (q is not None and q.unfinished_tasks) or getattr(payload_fn, "size", 0) >= BIG_RECORD[0]:
        _writer_queue().put((j, rtype, header, payload_fn))       # R079: encoded and written by the helper
        return
    payload = payload_fn() if payload_fn is not None else b""
    with _JLOCK:
        j.append(rtype, header, payload)
        j.flush(fsync=False)


def _new_session() -> None:
    """A new id for the records this Blender writes from now on (a file was opened). The journal is
    keyed by the file's path, so the same file open in two Blenders shares one: replay keeps to the
    records of the session that saved or opened it last (core.persist.replay)."""
    _J["sid"] = uuid.uuid4().hex[:12]


def _mark_saved(j: Journal, blend_path: str, save_id=None) -> None:
    """Start the journal again at a save point: SAVE names the save of the history it follows
    (``save_id``, as the saved history holds it) and the session writing it."""
    drain_journal_writer()
    j.append("SAVE", {"blend": os.path.abspath(blend_path) if blend_path else "", "save_id": save_id or None,
                      "s": _J["sid"]})
    j.flush(fsync=True)


# ----------------------------------------------------------------------------- helpers
def _handler_path(args) -> str:
    for a in args:
        if isinstance(a, str) and a:
            return a
    return _current_blend()


def _same_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _drain_packer() -> None:
    p = runtime.packer
    if p is not None:
        with contextlib.suppress(Exception):
            p.drain(timeout=30.0)


def _recovery() -> str:
    return str(getattr(SETTINGS, "crash_recovery", "LAST_SAVED") or "LAST_SAVED").upper()


def _codec() -> str:
    return getattr(SETTINGS, "compression", "auto") or "auto"


# ----------------------------------------------------------------------------- handlers
def has_data(reg=None) -> bool:
    """Something to keep: a step, an object switched on or paused, or Record everything switched off by choice (#61)."""
    reg = reg or runtime.reg()
    return (any(h.entries for h in reg.histories.values())
            or (bool(getattr(reg, "everything", ())) and not getattr(reg, "file_default", False))
            or bool(getattr(reg, "disabled", ())) or getattr(reg, "everything_chosen", False))


def _writes_anything(path: str) -> bool:
    """D29: a file Hindsight never recorded gets nothing from it when saved (no Text, no folder, no
    journal). A file that already has Hindsight data (its Text) is kept up to date, also when it
    became empty. A history folder alone doesn't count: after Remove Hindsight data the folder can be
    kept as a backup without the file using it again."""
    return has_data() or bpy.data.texts.get(TEXT_NAME) is not None


_UNSAVED: list = []      # (datablock, id) left out of one save, put back after it (see _default_ids_off)


def _default_ids_off() -> list:
    """D29: in a file where nothing is recorded and only the default switched everything on (#10),
    the ids it gave out are left out of the save; _default_ids_back puts them back after it."""
    if not getattr(runtime.reg(), "file_default", False):
        return []
    out = []
    for c in idmap.COLLECTIONS:
        for idb in getattr(bpy.data, c, ()):
            u = idb.get(idmap.UID_PROP) if idb.library is None else None
            if u is not None:
                out.append((idb, u))
                del idb[idmap.UID_PROP]
    return out


@bpy.app.handlers.persistent
@guard.guarded("saving")
def _default_ids_back(*_a) -> None:
    for idb, u in _UNSAVED:
        idb[idmap.UID_PROP] = u
    _UNSAVED.clear()


def drop_default_settings() -> int:
    """Blender stores a scene's Hindsight settings as soon as anything reads them (the sidebar, the
    recorder). If none of them was changed, take them off again before saving, so a file only keeps
    settings someone actually set."""
    n = 0
    for sc in bpy.data.scenes:
        if sc.library is not None:
            continue
        get = getattr(sc, "bl_system_properties_get", None)
        stored = get() if get is not None else sc
        if stored is None or "hindsight" not in stored.keys():
            continue
        p = sc.hindsight
        from .cleanup import _movie_fcurves          # layered actions (Blender 5) and older ones
        keyed = bool(_movie_fcurves(sc))
        default = not keyed and all(
            (len(getattr(p, prop.identifier)) == 0) if prop.type == "COLLECTION"
            else getattr(p, prop.identifier) == getattr(prop, "default", None)
            for prop in p.bl_rna.properties if prop.identifier != "rna_type" and prop.type != "POINTER")
        if default:
            sc.property_unset("hindsight")
            n += 1
    return n


def _bridge_paused(reg) -> int:
    """Changes made to an object while its recording is paused become one step before the file is
    saved, the step Turn On would make (the history stays paused). Without it the file holds a mesh
    that isn't the history's state: opened again, that mesh is taken as the state at the cursor, and
    going back plays the steps onto a mesh they weren't made on."""
    from . import datarec
    n = 0
    thr = SETTINGS.truncation_threshold
    for h in list(reg.histories.values()):
        if h.enabled or not h.entries:
            continue
        for m in list(h.members):
            ob = runtime.find_object(m)
            if ob is None or ob.type != "MESH" or ob.mode == "EDIT":
                continue
            h.enabled = True
            SETTINGS.truncation_threshold = 0     # never ask (and never put the mesh back) while saving
            try:
                if datarec.is_tracked(ob):
                    n += len(datarec.catch_up(ob, "Changes While Off") or ())
                elif m in runtime._STATES and runtime.commit(ob, label="Changes While Off", full=True):
                    n += 1
            finally:
                h.enabled = False
                SETTINGS.truncation_threshold = thr
    return n


@bpy.app.handlers.persistent
@guard.guarded("saving")
def on_save_pre(*args) -> None:
    runtime.multires.flush()          # a Multires visit that has just ended is a step before the save
    runtime.end_paused(now=True)      # D153: and a Dyntopo visit that paused recording
    runtime.drop_unused_uids()
    drop_default_settings()
    _FALLBACK["kept"] = False
    if not _writes_anything(_handler_path(args)):
        _UNSAVED[:] = _default_ids_off()
        return
    reg = runtime.reg()
    try:
        _bridge_paused(reg)
    except Exception as e:
        guard.fail("saving", e, "could not record changes made while recording was paused")
    # Every save is a new state of the history: its id goes into the saved history, the .blend and the
    # SAVE record that starts the crash journal again, so recovery never replays a journal onto
    # another save of the file (core.persist.journal_belongs)
    reg.save_id = uuid.uuid4().hex
    try:
        reg.saved = runtime.saved_prints()          # D46
    except Exception as e:
        log.error_once(("saved_prints", str(e)), f"could not note the recorded objects for saving: {e}", e)
    text = None
    building = False
    try:
        _drain_packer()
        text = bpy.data.texts.get(TEXT_NAME)
        if text is None:
            text = bpy.data.texts.new(TEXT_NAME)
        text.use_fake_user = True
        if not _PROTECT["embed"]:          # else: history from a newer Hindsight, kept exactly as it was
            # The parts are the step blobs themselves (no copy); anything that can go wrong in
            # putting them together happens here, before the stored copy is touched.
            building = True
            parts = persist.registry_parts(reg, _codec())
            # Blender's copy of the last saved history goes before the new one is built: otherwise the old copy,
            # the new bytes and Blender's new copy are all held at once. A failure reads it back from disk (#9).
            for key in (DATA_PROP, DATA_BIN_PROP, SAVE_ID_PROP):
                if key in text:
                    del text[key]
            # R061: the bytes themselves (base64 cost time and a third more). Both keys are gone by now, which
            # matters: Blender cuts bytes at the first zero when it replaces an existing bytes property.
            data = persist.parts_to_bytes(parts)
            del parts
            text[DATA_BIN_PROP] = data
            del data
            text[SAVE_ID_PROP] = reg.save_id
        # #48: the history always goes in the .blend. A folder from a newer Hindsight, or one that
        # is missing, stays pointed at, so its history is still found there.
        text[SIDECAR_PROP] = _PROTECT["sidecar"]
    except Exception as e:
        guard.fail("saving", e, "could not store undo history in the .blend")
        _FALLBACK["kept"] = True
        try:
            if building:
                for key in (DATA_BIN_PROP, SAVE_ID_PROP):      # this save's, as far as it got
                    if key in text:
                        del text[key]
            _keep_last_saved(text)                            # #9: read back from the .blend on disk
            had = text is not None and (DATA_PROP in text or DATA_BIN_PROP in text or text.get(SIDECAR_PROP))
            _drop_save_notes()
            _note("ERROR", KEPT_LAST_SAVE_TEXT if had else NOT_STORED_TEXT)
        except Exception as e2:
            guard.fail("saving", e2, "could not keep the last save's history in the .blend")


def _keep_last_saved(text) -> None:
    """The .blend keeps the history its last save stored: the copy still on the Text, else the one in the
    session's .blend on disk (read only now, when a save has already failed)."""
    if any(k in text for k in (DATA_PROP, DATA_BIN_PROP)):
        return
    src = bpy.data.filepath
    if not src or not os.path.isfile(src):
        return                          # never saved: the history stays in the session until a save works
    # Blender won't load the open file itself (4.2 crashes): a hard link under another name, else a copy
    link = os.path.join(os.path.dirname(src), f".{uuid.uuid4().hex[:12]}.hindsight.blend")
    known = set(bpy.data.libraries)
    try:
        try:
            os.link(src, link)
        except OSError:
            link = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.blend")
            shutil.copyfile(src, link)
        with bpy.data.libraries.load(link) as (stored, into):
            into.texts = [TEXT_NAME] if TEXT_NAME in stored.texts else []
        for old in into.texts:
            try:
                for key in (SIDECAR_PROP, DATA_PROP, DATA_BIN_PROP, SAVE_ID_PROP):
                    if key in old:
                        text[key] = old[key]
            finally:
                bpy.data.texts.remove(old)
    finally:
        for lib in [lib for lib in bpy.data.libraries if lib not in known]:
            bpy.data.libraries.remove(lib)          # 4.2 keeps the library it appended from
        with contextlib.suppress(OSError):
            os.remove(link)


def _drop_copy(text) -> None:
    try:
        for key in (DATA_PROP, DATA_BIN_PROP):
            if key in text:
                del text[key]
    except Exception as e:
        guard.fail("saving", e, "could not let go of Blender's copy of the history")


@bpy.app.handlers.persistent
@guard.guarded("saving")
def on_save_post_fail(*args) -> None:
    # #56: Blender couldn't write the file, so the .blend on disk is as it was and the history in memory goes
    # with the next save; Blender's copy put back for this one doesn't stay
    text = bpy.data.texts.get(TEXT_NAME)
    if text is not None and not _PROTECT["embed"]:
        _drop_copy(text)


@bpy.app.handlers.persistent
@guard.guarded("saving")
def on_save_post(*args) -> None:
    _default_ids_back()
    path = _handler_path(args)
    if not path or not _writes_anything(path):
        return
    text = bpy.data.texts.get(TEXT_NAME)
    if text is not None and not _PROTECT["embed"]:
        # #9: the history is written; Blender's copy goes again, for Save, Save As and Save Copy alike (a newer
        # Hindsight's copy stays: nothing would put it back)
        _drop_copy(text)
    kept = _FALLBACK["kept"]
    _FALLBACK["kept"] = False
    current = _current_blend()
    if current and not _same_path(path, current):
        return   # "Save Copy": the session file itself did not change
    _folder_message(path)
    if kept:
        # The .blend still holds the save before this one, and the journal goes with that save: keep it
        # (on a Save As, at the new path)
        try:
            _carry_journal(path)
        except Exception as e:
            guard.fail("saving", e, "journal error on save")
        return
    try:
        j = _open_journal(path)
        drain_journal_writer()
        j.truncate(0)            # everything before this point is in the saved file
        _mark_saved(j, path, getattr(runtime.reg(), "save_id", None))
    except Exception as e:
        guard.fail("saving", e, "journal error on save")
    _drop_save_notes()                 # the history is stored: an earlier save's failure no longer holds


def _drop_save_notes() -> None:
    runtime.load_notes[:] = [n for n in runtime.load_notes if n[1] not in _SAVE_NOTES]


def _folder_message(path) -> None:
    """The sidebar message while the .blend points at a history folder that isn't next to it (#8 review)."""
    missing = _PROTECT["sidecar"] and persist.sidecar_missing(path, True)
    runtime.missing_message[0] = (MISSING_TEXT.format(folder=os.path.basename(persist.sidecar_dir(path)))
                                  if missing else None)


@bpy.app.handlers.persistent
@guard.guarded("loading")
def on_load_pre(*args) -> None:
    _close_journal(remove_untitled=True)
    runtime.missing_message[0] = None
    runtime.load_notes.clear()
    _PROTECT["sidecar"] = _PROTECT["embed"] = False


def _note(sev, text):
    runtime.load_notes.append((sev, text))
    (runtime.log.warn if sev != "INFO" else runtime.log.info)(text)


def _load_registry(path: str, text):
    """The saved registry for this file, or None. Never raises; problems become sidebar notes."""
    embedded = text is not None and (DATA_PROP in text or DATA_BIN_PROP in text)
    read = []

    def from_text():
        # R061: base64 text when it is there (an earlier build wrote it, so it is the newer copy when both
        # are: such a build never touches the bytes), else the bytes
        key = DATA_PROP if DATA_PROP in text else DATA_BIN_PROP
        try:
            v = text[key]
            # a2b_base64 reads the stored text in place (b64decode would first copy it to bytes)
            raw = binascii.a2b_base64(v) if key == DATA_PROP else v
            del v
            read.append(persist.registry_from_bytes(raw))
            return read[-1]
        except persist.NewerFormatError as e:
            _PROTECT["embed"] = True
            _note("ERROR", NEWER_TEXT.format(found=e.found))
        except Exception as e:
            runtime.log.error_once(("embed_unreadable", str(e)), f"embedded history unreadable: {e}", e)
            with contextlib.suppress(Exception):
                v = text[key]
                if UNREADABLE_PROP in text:
                    # Blender cuts bytes at the first zero when it replaces a bytes property: a new one only
                    del text[UNREADABLE_PROP]
                text[UNREADABLE_PROP] = v           # keep the data, out of the way
                del v, text[key]
            _note("ERROR", UNREADABLE_EMBED_TEXT)
        return None

    def from_sidecar():
        # only when the file says its history is in the folder (every build writes that on save):
        # a folder kept after Remove Hindsight data isn't loaded again
        if not path or text is None or not text.get(SIDECAR_PROP):
            return None
        try:
            return persist.load_sidecar(path)
        except persist.NewerFormatError as e:
            _PROTECT["sidecar"] = True
            _note("ERROR", NEWER_TEXT.format(found=e.found))
        except Exception as e:
            runtime.log.error_once(("sidecar_unreadable", str(e)), f"history file unreadable: {e}", e)
            try:
                moved = persist.set_aside(persist.sidecar_file(path))
            except OSError:
                moved = None
            _note("ERROR", UNREADABLE_TEXT.format(name=os.path.basename(moved or persist.HSR_NAME)))
        return None

    want = text.get(SAVE_ID_PROP) if text is not None else None
    # a V1 folder first, only while the .blend still points at it; then the .blend
    reg = from_sidecar()
    if reg is None and embedded:
        reg = from_text()
    elif reg is not None and want and getattr(reg, "save_id", None) != want:
        # the folder wasn't written by the last save of this .blend (writing it failed, or the
        # .blend is an older or newer save of it): the history stored in the .blend, if it is
        # the right one, goes first
        other = from_text() if embedded else None
        if other is not None and getattr(other, "save_id", None) == want:
            if getattr(reg, "save_id", None) is None:
                # a V1 folder back after this build saved the .blend without it (#120 review): the .blend's
                # histories are the newer ones, the folder's go on for every object the .blend has none for
                _take_folder_histories(other, reg)
            reg = other
        elif getattr(reg, "save_id", None) is not None:
            _note("WARNING", STALE_SIDECAR_TEXT)
    _FROM_TEXT[0] = reg is not None and any(r is reg for r in read)
    if reg is not None:
        _report_load(reg)
    return reg


def _take_folder_histories(reg, folder) -> None:
    """Into `reg` (the .blend's), the folder's histories of objects `reg` has no history for, nor turned off."""
    have = set(reg.by_object) | reg.disabled
    taken = {h.uid for h in folder.all() if have.isdisjoint(h.members)}
    for d in (folder.load_report or {}).get("damaged", []):
        if d.get("uid") in taken:                     # reported with the .blend's own (_report_load)
            reg.load_report["damaged"].append(d)
    for h in folder.all():
        if h.uid in taken:
            reg.histories[h.uid] = h
            for m in h.members:
                reg.by_object[m] = h.uid
                if m in folder.saved:
                    reg.saved[m] = folder.saved[m]
    reg.everything |= folder.everything - have
    # shortcut: step numbers aren't made unique, so the global timeline may take a folder step and a .blend
    # step as one operation; renumber the packed steps if that matters
    reg.next_seq = max(reg.next_seq, folder.next_seq)


def _report_load(reg) -> None:
    rep = getattr(reg, "load_report", None) or {}
    for d in rep.get("damaged", []):
        obj = None
        for m in d.get("members", []):
            ob = runtime.find_object(m)
            if ob is not None:
                obj = ob.name
                break
        lost = int(d.get("lost", 0))
        kept = int(d.get("kept", 0))
        if not d.get("before_saved"):
            text = DAMAGED_TEXT
        else:
            text = DAMAGED_PART_TEXT if kept else DAMAGED_ALL_TEXT
        _note("ERROR", text.format(lost=lost, s="" if lost == 1 else "s", obj=obj or "an object",
                                   kept=kept))
    if rep.get("migrated"):
        runtime.log.info(f"loaded history format {rep.get('format')} ({rep.get('writer')}); "
                         f"it is saved as format {persist.FORMAT_VERSION} from now on")


def _report_replay(reg) -> None:
    """Sidebar notes for steps crash recovery had to leave out (persist.replay says which)."""
    for uid, n in (getattr(reg, "replay_report", None) or {}).get("stopped", {}).items():
        h = reg.histories.get(uid)
        obj = None
        for m in (h.members if h is not None else []):
            ob = runtime.find_object(m)
            if ob is not None:
                obj = ob.name
                break
        _note("WARNING", LEFT_OUT_TEXT.format(n=n, s="" if n == 1 else "s", obj=obj or "an object"))


def _since_save(recs) -> list:
    """The records after the last SAVE (all of them without one), resync markers left out."""
    start = 0
    for i, r in enumerate(recs):
        if r[0] == "SAVE":
            start = i + 1
    return [r for r in recs[start:] if not (r[0] == "META" and not r[1].get("hist") and r[1].get("resync"))]


@bpy.app.handlers.persistent
@guard.guarded("loading")
def on_load_post(*args) -> None:
    _new_session()
    runtime.set_registry(Registry())
    runtime.missing_message[0] = None
    path = _current_blend()
    text = bpy.data.texts.get(TEXT_NAME)
    reg = _load_registry(path, text)

    # The folder's history wasn't read: none came from the .blend, or the .blend's was saved by this build while
    # the folder was missing (a beta 2 file saved both ways holds the folder's own copy, and no save id). Saving
    # keeps pointing at the folder until it is back (#8 review).
    if (path and text is not None and text.get(SIDECAR_PROP) and (reg is None or SAVE_ID_PROP in text)
            and persist.sidecar_missing(path, True)):
        _PROTECT["sidecar"] = True
    _folder_message(path)

    recover = reg is not None and bool(path) and _recovery() == "INTERRUPTED"
    # #57: a saved history that isn't in what was opened (Blender's autosave holds no copy of it, the folder is
    # missing) leaves the journal of that save alone, for when the .blend itself is opened; replay skips this
    # session's records
    held_elsewhere = reg is None and text is not None and bool(text.get(SAVE_ID_PROP))
    j = None
    # D29: a file without history gets no journal until something is recorded (opened on the first
    # step); one with history, or with a journal left by an interrupted session, gets it now
    if reg is not None or (not held_elsewhere and os.path.isfile(journal_path(path))):
        try:
            # without recovery the journal is emptied below in any case (not kept): R067 lets it go now
            j = _open_journal(path, keep=recover, discard=not recover)
        except Exception as e:
            guard.fail("loading", e, "could not open the journal")
    else:
        _close_journal(remove_untitled=True)

    kept = False          # the journal goes on (recovered from it); otherwise it starts again here
    if reg is not None:
        if recover and j is not None:
            try:
                drain_journal_writer()
                recs = j.read()
                if any(r[0] == "SAVE" for r in recs) and persist.journal_belongs(reg, recs):
                    persist.replay(reg, recs)
                    # This session goes on from the recovered state and owns the journal from here:
                    # a second recovery starts again at this record.
                    j.append("META", {"resync": True, "s": _J["sid"]})
                    j.flush(fsync=False)
                    kept = True
                    _report_replay(reg)
                elif _since_save(recs):
                    _note("WARNING", OTHER_SAVE_TEXT)
            except Exception as e:
                guard.fail("loading", e, "crash recovery failed")
                if not kept:
                    # replay may have changed part of the history before it failed: start again
                    # from the file alone
                    notes = list(runtime.load_notes)
                    reg = _load_registry(path, text) or Registry()
                    runtime.load_notes[:] = notes

    if j is not None and not kept:
        # A fresh session (Last saved, nothing to recover, or a journal of another save). When the
        # journal was recovered from it is kept with the records since the last real save: the .blend
        # on disk still matches that save, so a second interruption must recover them again. Either
        # way this comes before the check for changes made without Hindsight, whose records the
        # next recovery needs (D82, D83).
        try:
            drain_journal_writer()
            j.truncate(0)
            _mark_saved(j, path, getattr(reg, "save_id", None) if reg is not None else None)
        except Exception as e:
            guard.fail("loading", e, "journal error on load")

    if reg is not None:
        with contextlib.suppress(Exception):
            p = getattr(bpy.context.scene, "hindsight", None)
            if p is not None:
                if hasattr(p, "use_global"):
                    reg.use_global = bool(p.use_global)
                if hasattr(p, "preserve_modes"):
                    reg.preserve_modes = bool(p.preserve_modes)
        runtime.set_registry(reg)
        if _FROM_TEXT[0]:
            # #9: Hindsight holds the history now, so Blender's copy of it goes (a save builds it again). Only
            # after recovery, which reads it again if it fails. A copy that wasn't read (newer, unreadable) stays.
            try:
                for key in (DATA_PROP, DATA_BIN_PROP):
                    if key in text:
                        del text[key]
            except Exception as e:
                guard.fail("loading", e, "could not let go of Blender's copy of the history")
        try:
            runtime.restamp_overrides(getattr(reg, "saved", None))        # D109
        except Exception as e:
            guard.fail("loading", e, "could not find recorded library overrides again")
        try:
            runtime.prime_states()
        except Exception as e:
            guard.fail("loading", e, "could not read meshes after loading")
        try:
            from . import datarec
            datarec.prime()
        except Exception as e:
            guard.fail("loading", e, "could not read recorded data after loading")
        try:
            runtime.check_outside_changes(getattr(reg, "saved", None))       # D46
        except Exception as e:
            guard.fail("loading", e, "could not compare the file with its history")
        if runtime.packer is not None:
            # steps from older files and steps saved in the quick form get re-packed when idle
            runtime.packer.queue_recompress(e for h in reg.all() for e in h.entries)
    try:
        from . import datarec
        if reg is not None or text is None:     # a history that didn't load may hold "switched off" (#61)
            datarec.record_file_default()
    except Exception as e:
        guard.fail("loading", e, "could not switch on Everything in this file")

    runtime.tag_redraw_all()
    problems = ([runtime.missing_message[0]] if runtime.missing_message[0] else []) +         [t for sev, t in runtime.load_notes if sev == "ERROR"]
    if problems:
        more = f" ({len(problems) - 1} more in the Undo History tab)" if len(problems) > 1 else ""
        with contextlib.suppress(Exception):
            from .selfcheck import flash_status
            flash_status("Hindsight: " + problems[0] + more, 15.0)


# ----------------------------------------------------------------------------- register
_HANDLERS = (
    ("save_pre", on_save_pre), ("save_post", on_save_post), ("save_post_fail", on_save_post_fail),
    ("load_pre", on_load_pre), ("load_post", on_load_post), ("save_post_fail", _default_ids_back),
)


def register() -> None:
    with contextlib.suppress(Exception):
        from .selfcheck import addon_version
        persist.WRITER = f"Hindsight {addon_version()}"
    for name, fn in _HANDLERS:
        hl = getattr(bpy.app.handlers, name)
        if fn not in hl:
            hl.append(fn)
    runtime.journal_hook = _on_journal


def unregister() -> None:
    _stop_journal_writer()
    for name, fn in _HANDLERS:
        hl = getattr(bpy.app.handlers, name)
        while fn in hl:
            hl.remove(fn)
    if runtime.journal_hook is _on_journal:
        runtime.journal_hook = None
    _close_journal(remove_untitled=True)
