# SPDX-License-Identifier: GPL-3.0-or-later
"""Save / load glue: sidecar folder, embedded Text datablock and the crash journal.

Save Mode (Add-on Preferences, ``SETTINGS.save_mode``):
  SIDECAR  history folder ``<stem>_hindsight`` beside the .blend (fastest saves)
  EMBED    stored inside the .blend (Text ``.hindsight_history``, ID property ``hindsight_data``)
  BOTH     both of the above

Crash Recovery (``SETTINGS.crash_recovery``):
  LAST_SAVED   loading a file restores only what was saved
  INTERRUPTED  steps recorded after the last save of an interrupted session are recovered
               from the journal (the mesh stays at the saved cursor; scrub forward to see them)
"""
from __future__ import annotations

import binascii
import hashlib
import os
import tempfile
import uuid

import bpy

from ..core import persist
from ..core.journal import Journal
from ..core.registry import Registry
from ..core.types import SETTINGS
from . import guard, log, runtime

TEXT_NAME = ".hindsight_history"
DATA_PROP = "hindsight_data"
SIDECAR_PROP = "sidecar"
MISSING_TEXT = "History folder missing. Keep '{folder}' next to the .blend to see this file's history"
NEWER_TEXT = ("This history was saved by a newer Hindsight (format {found}). Update Hindsight to see it. "
              "It is left untouched until then")
UNREADABLE_TEXT = ("The saved history could not be read, so it was not loaded. Your .blend is fine. "
                   "The unreadable copy was kept as '{name}'")
UNREADABLE_EMBED_TEXT = ("The history stored in this .blend could not be read, so it was not loaded. "
                         "It is kept in the file in case it can be recovered")
DAMAGED_TEXT = "{lost} damaged step{s} of '{obj}' could not be loaded. The {kept} steps before them are fine"
DAMAGED_ALL_TEXT = ("The history of '{obj}' is damaged before the step it was saved at, so none of it could be "
                    "kept ({lost} step{s}). '{obj}' itself is as you saved it")
LEFT_OUT_TEXT = ("{n} step{s} of '{obj}' from the interrupted session could not be brought back. "
                 "They were made after going back past the last save. '{obj}' is as you saved it")
UNREADABLE_PROP = "hindsight_data_unreadable"
SAVE_ID_PROP = "save_id"         # which save of the history this .blend goes with (older builds ignore it)
EMBED_FALLBACK_TEXT = ("The history could not be stored in the .blend. It is saved in the history folder "
                       "next to it instead")
SIDECAR_FAILED_TEXT = ("The history folder could not be written, so it still holds the last save before "
                       "this one. Save again to keep your history")
OTHER_SAVE_TEXT = ("Steps from an interrupted session were found, but they belong to another save of this "
                   "file. They were not brought back")
STALE_SIDECAR_TEXT = "The history folder is from another save of this file"

# Set while loading a file whose history this version must not overwrite (newer format).
_PROTECT = {"sidecar": False, "embed": False}

_ROOT_PACKAGE = (__package__ or "hindsight").rsplit(".bl", 1)[0]

_J = {"journal": None, "path": None, "sid": uuid.uuid4().hex[:12]}    # live journal of the session file
_FALLBACK = {"sidecar": False}   # the history couldn't be stored in the .blend: write the folder instead


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
    """The live Journal (or None if none has been opened yet)."""
    return _J["journal"]


def _close_journal(remove_untitled: bool = False) -> None:
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
            try:
                os.remove(path)
            except OSError:
                pass


def _open_journal(blend_path: str) -> Journal:
    """(Re)open the journal for blend_path, keeping the current one if it is the same file."""
    path = journal_path(blend_path)
    j = _J["journal"]
    if j is not None and not j.closed and _J["path"] == path:
        return j
    _close_journal(remove_untitled=True)
    j = Journal(path)
    _J["journal"] = j
    _J["path"] = path
    return j


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
    payload = payload_fn() if payload_fn is not None else b""
    j.append(rtype, dict(header, s=_J["sid"]), payload)
    j.flush(fsync=False)


def _new_session() -> None:
    """A new id for the records this Blender writes from now on (a file was opened). The journal is
    keyed by the file's path, so the same file open in two Blenders shares one: replay keeps to the
    records of the session that saved or opened it last (core.persist.replay)."""
    _J["sid"] = uuid.uuid4().hex[:12]


def _mark_saved(j: Journal, blend_path: str, save_id=None) -> None:
    """Start the journal again at a save point: SAVE names the save of the history it follows
    (``save_id``, as the saved history holds it) and the session writing it."""
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
        try:
            p.drain(timeout=30.0)
        except Exception:
            pass


def _mode() -> str:
    m = str(getattr(SETTINGS, "save_mode", "SIDECAR") or "SIDECAR").upper()
    return m if m in ("SIDECAR", "EMBED", "BOTH") else "SIDECAR"


def _recovery() -> str:
    return str(getattr(SETTINGS, "crash_recovery", "LAST_SAVED") or "LAST_SAVED").upper()


def _codec() -> str:
    return getattr(SETTINGS, "compression", "auto") or "auto"


# ----------------------------------------------------------------------------- handlers
def has_data(reg=None) -> bool:
    """Something to keep: a step, or an object switched on or paused."""
    reg = reg or runtime.reg()
    return (any(h.entries for h in reg.histories.values()) or bool(getattr(reg, "everything", ()))
            or bool(getattr(reg, "disabled", ())))


def _writes_anything(path: str) -> bool:
    """D29: a file Hindsight never recorded gets nothing from it when saved (no Text, no folder, no
    journal). A file that already has Hindsight data (its Text) is kept up to date, also when it
    became empty. A history folder alone doesn't count: after Remove Hindsight data the folder can be
    kept as a backup without the file using it again."""
    return has_data() or bpy.data.texts.get(TEXT_NAME) is not None


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
    runtime.drop_unused_uids()
    drop_default_settings()
    _FALLBACK["sidecar"] = False
    if not _writes_anything(_handler_path(args)):
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
    removed = False
    try:
        mode = _mode()
        _drain_packer()
        text = bpy.data.texts.get(TEXT_NAME)
        if text is None:
            text = bpy.data.texts.new(TEXT_NAME)
        text.use_fake_user = True
        text[SIDECAR_PROP] = mode in ("SIDECAR", "BOTH")
        if _PROTECT["embed"]:
            pass   # history from a newer Hindsight: keep it exactly as it was
        elif mode in ("EMBED", "BOTH"):
            # The parts are the step blobs themselves (no copy); anything that can go wrong in
            # putting them together happens here, before the stored copy is touched.
            parts = persist.registry_parts(reg, _codec())
            if DATA_PROP in text:
                # Blender's copy of the last saved history goes before the new one is built:
                # otherwise the old copy, the new text and Blender's new copy are all held at once.
                del text[DATA_PROP]
                removed = True
            data = persist.parts_to_b64(parts)    # the same text as b64encode(b"".join(parts))
            del parts
            text[DATA_PROP] = data
            del data
        elif DATA_PROP in text:
            del text[DATA_PROP]
        if not _PROTECT["embed"]:
            text[SAVE_ID_PROP] = reg.save_id
    except Exception as e:
        guard.fail("saving", e, "could not store undo history in the .blend")
        if removed and text is not None:
            # The copy from the last save is gone and the new one couldn't be made (out of memory,
            # most likely): the .blend would be saved with no history at all. The history folder
            # gets it instead, and the .blend says so.
            try:
                if DATA_PROP in text:
                    del text[DATA_PROP]
                text[SIDECAR_PROP] = True
                text[SAVE_ID_PROP] = reg.save_id
                _FALLBACK["sidecar"] = True
                _note("ERROR", EMBED_FALLBACK_TEXT)
            except Exception as e2:
                guard.fail("saving", e2, "could not point the .blend at its history folder")


@bpy.app.handlers.persistent
@guard.guarded("saving")
def on_save_post(*args) -> None:
    path = _handler_path(args)
    if not path or not _writes_anything(path):
        return
    mode = _mode()
    folder = mode in ("SIDECAR", "BOTH") or _FALLBACK["sidecar"]
    _FALLBACK["sidecar"] = False
    folder_failed = False
    if folder and _PROTECT["sidecar"] and os.path.isfile(persist.sidecar_file(path)):
        runtime.log.warn("history folder left untouched: it was saved by a newer Hindsight")
    elif folder:
        try:
            _drain_packer()
            persist.save_sidecar(runtime.reg(), path)
        except Exception as e:
            guard.fail("saving", e, "could not write the undo history folder")
            folder_failed = mode == "SIDECAR" or mode == "EMBED"     # BOTH: the .blend has it
            if folder_failed:
                _note("ERROR", SIDECAR_FAILED_TEXT)
    current = _current_blend()
    if current and not _same_path(path, current):
        return   # "Save Copy": the session file itself did not change
    if folder_failed:
        # The folder still holds the save before this one, and the journal goes with that save:
        # keep it. The .blend names this save, so opening it knows the folder is older and its
        # check of every mesh leaves out the histories that no longer fit (D46).
        runtime.missing_message[0] = None
        return
    try:
        j = _open_journal(path)
        j.truncate(0)            # everything before this point is in the saved file
        _mark_saved(j, path, getattr(runtime.reg(), "save_id", None))
    except Exception as e:
        guard.fail("saving", e, "journal error on save")
    runtime.missing_message[0] = None


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
    mode = _mode()
    embedded = text is not None and DATA_PROP in text

    def from_text():
        try:
            # a2b_base64 reads the stored text in place (b64decode would first copy it to bytes)
            return persist.registry_from_bytes(binascii.a2b_base64(text[DATA_PROP]))
        except persist.NewerFormatError as e:
            _PROTECT["embed"] = True
            _note("ERROR", NEWER_TEXT.format(found=e.found))
        except Exception as e:
            runtime.log.error_once(("embed_unreadable", str(e)), f"embedded history unreadable: {e}", e)
            try:
                text[UNREADABLE_PROP] = text[DATA_PROP]   # keep the bytes, out of the way
                del text[DATA_PROP]
            except Exception:
                pass
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
    if mode == "EMBED":
        reg = from_text() if embedded else None
        if reg is None:
            reg = from_sidecar()
    else:
        reg = from_sidecar()
        if reg is None and embedded:
            reg = from_text()
        elif reg is not None and want and getattr(reg, "save_id", None) not in (None, want):
            # the folder wasn't written by the last save of this .blend (writing it failed, or the
            # .blend is an older or newer save of it): the history stored in the .blend, if it is
            # the right one, goes first
            other = from_text() if embedded else None
            if other is not None and getattr(other, "save_id", None) == want:
                reg = other
            else:
                _note("WARNING", STALE_SIDECAR_TEXT)
    if reg is not None:
        _report_load(reg)
    return reg


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
        text = DAMAGED_ALL_TEXT if d.get("before_saved") else DAMAGED_TEXT
        _note("ERROR", text.format(lost=lost, s="" if lost == 1 else "s", obj=obj or "an object",
                                   kept=int(d.get("kept", 0))))
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

    if reg is None and path and text is not None and text.get(SIDECAR_PROP):
        if persist.sidecar_missing(path, True):
            folder = os.path.basename(persist.sidecar_dir(path))
            runtime.missing_message[0] = MISSING_TEXT.format(folder=folder)

    recover = reg is not None and bool(path) and _recovery() == "INTERRUPTED"
    j = None
    # D29: a file without history gets no journal until something is recorded (opened on the first
    # step); one with history, or with a journal left by an interrupted session, gets it now
    if reg is not None or os.path.isfile(journal_path(path)):
        try:
            j = _open_journal(path)
        except Exception as e:
            guard.fail("loading", e, "could not open the journal")
    else:
        _close_journal(remove_untitled=True)

    kept = False          # the journal goes on (recovered from it); otherwise it starts again here
    if reg is not None:
        if recover and j is not None:
            try:
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
            j.truncate(0)
            _mark_saved(j, path, getattr(reg, "save_id", None) if reg is not None else None)
        except Exception as e:
            guard.fail("loading", e, "journal error on load")

    if reg is not None:
        try:
            p = getattr(bpy.context.scene, "hindsight", None)
            if p is not None:
                if hasattr(p, "use_global"):
                    reg.use_global = bool(p.use_global)
                if hasattr(p, "preserve_modes"):
                    reg.preserve_modes = bool(p.preserve_modes)
        except Exception:
            pass
        runtime.set_registry(reg)
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

    runtime.tag_redraw_all()
    problems = ([runtime.missing_message[0]] if runtime.missing_message[0] else []) +         [t for sev, t in runtime.load_notes if sev == "ERROR"]
    if problems:
        more = f" ({len(problems) - 1} more in the Undo History tab)" if len(problems) > 1 else ""
        try:
            from .selfcheck import flash_status
            flash_status("Hindsight: " + problems[0] + more, 15.0)
        except Exception:
            pass


# ----------------------------------------------------------------------------- register
_HANDLERS = (
    ("save_pre", on_save_pre), ("save_post", on_save_post),
    ("load_pre", on_load_pre), ("load_post", on_load_post),
)


def register() -> None:
    try:
        from .selfcheck import addon_version
        persist.WRITER = f"Hindsight {addon_version()}"
    except Exception:
        pass
    for name, fn in _HANDLERS:
        hl = getattr(bpy.app.handlers, name)
        if fn not in hl:
            hl.append(fn)
    runtime.journal_hook = _on_journal


def unregister() -> None:
    for name, fn in _HANDLERS:
        hl = getattr(bpy.app.handlers, name)
        while fn in hl:
            hl.remove(fn)
    if runtime.journal_hook is _on_journal:
        runtime.journal_hook = None
    _close_journal(remove_untitled=True)
