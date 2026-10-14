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

import base64
import hashlib
import os
import tempfile

import bpy

from ..core import persist
from ..core.journal import Journal
from ..core.registry import Registry
from ..core.types import SETTINGS
from . import guard, runtime

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
UNREADABLE_PROP = "hindsight_data_unreadable"

# Set while loading a file whose history this version must not overwrite (newer format).
_PROTECT = {"sidecar": False, "embed": False}

_ROOT_PACKAGE = (__package__ or "hindsight").rsplit(".bl", 1)[0]

_J = {"journal": None, "path": None}    # live journal of the current session file


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
    j = _J["journal"]
    if j is None or j.closed:
        j = _open_journal(_current_blend())
    payload = payload_fn() if payload_fn is not None else b""
    j.append(rtype, header, payload)
    j.flush(fsync=False)


def _mark_saved(j: Journal, blend_path: str) -> None:
    j.append("SAVE", {"blend": os.path.abspath(blend_path) if blend_path else ""})
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
@bpy.app.handlers.persistent
@guard.guarded("saving")
def on_save_pre(*args) -> None:
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
            data = persist.registry_to_bytes(runtime.reg(), _codec())
            text[DATA_PROP] = base64.b64encode(data).decode("ascii")
        elif DATA_PROP in text:
            del text[DATA_PROP]
    except Exception as e:
        guard.fail("saving", e, "could not store undo history in the .blend")


@bpy.app.handlers.persistent
@guard.guarded("saving")
def on_save_post(*args) -> None:
    path = _handler_path(args)
    if not path:
        return
    mode = _mode()
    if mode in ("SIDECAR", "BOTH") and _PROTECT["sidecar"] and os.path.isfile(persist.sidecar_file(path)):
        runtime.log.warn("history folder left untouched: it was saved by a newer Hindsight")
    elif mode in ("SIDECAR", "BOTH"):
        try:
            _drain_packer()
            persist.save_sidecar(runtime.reg(), path)
        except Exception as e:
            guard.fail("saving", e, "could not write the undo history folder")
    current = _current_blend()
    if current and not _same_path(path, current):
        return   # "Save Copy": the session file itself did not change
    try:
        j = _open_journal(path)
        j.truncate(0)            # everything before this point is in the saved file
        _mark_saved(j, path)
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
            return persist.registry_from_bytes(base64.b64decode(text[DATA_PROP]))
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
        if not path:
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

    if mode == "EMBED":
        reg = from_text() if embedded else None
        if reg is None:
            reg = from_sidecar()
    else:
        reg = from_sidecar()
        if reg is None and embedded:
            reg = from_text()
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
        _note("ERROR", DAMAGED_TEXT.format(lost=lost, s="" if lost == 1 else "s", obj=obj or "an object",
                                           kept=int(d.get("kept", 0))))
    if rep.get("migrated"):
        runtime.log.info(f"loaded history format {rep.get('format')} ({rep.get('writer')}); "
                         f"it is saved as format {persist.FORMAT_VERSION} from now on")


@bpy.app.handlers.persistent
@guard.guarded("loading")
def on_load_post(*args) -> None:
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
    try:
        j = _open_journal(path)
    except Exception as e:
        guard.fail("loading", e, "could not open the journal")

    if reg is not None:
        if recover and j is not None:
            try:
                persist.replay(reg, j.read())
            except Exception as e:
                guard.fail("loading", e, "crash recovery failed")
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

    if j is not None:
        try:
            if recover:
                # Keep the records since the last real save: the .blend on disk still matches
                # that save, so a second interruption must recover them again.
                if not any(r[0] == "SAVE" for r in j.read()):
                    _mark_saved(j, path)
            else:
                j.truncate(0)    # fresh session
                _mark_saved(j, path)
        except Exception as e:
            guard.fail("loading", e, "journal error on load")
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
