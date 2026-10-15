# SPDX-License-Identifier: GPL-3.0-or-later
"""Remove Hindsight data from this file (Undo History > Advanced).

Takes out exactly what METADATA.md lists for a .blend: the id property on every datablock, the
scene settings, the history Text, Movie Mode's keyframes (only those) and, only when ticked, the
history files in the folder next to the file. Geometry and every other setting are never touched.
The history in memory is cleared too, so the next save writes nothing (recording starts again on
the next stroke or when Record everything is switched on).
"""
from __future__ import annotations

import os

import bpy

from . import idmap, log, runtime

# Files Hindsight writes in the history folder (see core/persist.py); nothing else is ever deleted.
_FOLDER_FILES = ("history.hsr", "index.json")
_SET_ASIDE = "history.hsr.unreadable-"


def _id_collections():
    for name in dir(bpy.data):
        col = getattr(bpy.data, name, None)
        if isinstance(col, bpy.types.bpy_prop_collection) and len(col) and isinstance(col[0], bpy.types.ID):
            yield col


def _ids_with_uid() -> list:
    out = []
    for col in _id_collections():
        for idb in col:
            if idb.library is None and idmap.UID_PROP in idb.keys():
                out.append(idb)
    return out


def _movie_fcurves(scene) -> list:
    from .movie import DATA_PATH
    ad = scene.animation_data
    if ad is None or ad.action is None:
        return []
    out = []
    try:
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        curves = cb.fcurves if cb is not None else ()
    except Exception:
        curves = getattr(ad.action, "fcurves", ())
    for fc in curves:
        if fc.data_path == DATA_PATH:
            out.append((curves, fc))
    return out


def folder() -> str | None:
    from ..core import persist
    path = bpy.data.filepath
    return persist.sidecar_dir(path) if path else None


def folder_files() -> list:
    d = folder()
    if not d or not os.path.isdir(d):
        return []
    return [os.path.join(d, n) for n in sorted(os.listdir(d))
            if n in _FOLDER_FILES or n.startswith(_SET_ASIDE)]


def _stored(idb):
    """Where Blender keeps an ID's registered properties: the system properties (Blender 5), or its
    custom properties (Blender 4)."""
    get = getattr(idb, "bl_system_properties_get", None)
    return get() if get is not None else idb


def _has_settings(scene) -> bool:
    sp = _stored(scene)
    return sp is not None and "hindsight" in sp.keys()


def report() -> dict:
    """What Remove Hindsight data would take out of this file."""
    from .persistence import TEXT_NAME
    ids = _ids_with_uid()
    kinds = {}
    for idb in ids:
        kinds[type(idb).__name__] = kinds.get(type(idb).__name__, 0) + 1
    settings = [sc for sc in bpy.data.scenes if sc.library is None and _has_settings(sc)]
    keys = sum(len(_movie_fcurves(sc)) for sc in bpy.data.scenes if sc.library is None)
    files = folder_files()
    return {"ids": len(ids), "kinds": kinds, "settings": len(settings), "text": TEXT_NAME in bpy.data.texts,
            "keyframes": keys, "folder": folder() if files else None,
            "folder_bytes": sum(os.path.getsize(f) for f in files),
            "steps": sum(len(h.entries) for h in runtime.reg().histories.values())}


def describe(r: dict) -> list:
    """Plain lines for the confirmation dialog."""
    lines = []
    if r["steps"]:
        lines.append(f"The history in this session: {r['steps']} step{'s' if r['steps'] != 1 else ''}")
    if r["ids"]:
        what = ", ".join(f"{n} {k.lower()}{'s' if n != 1 else ''}" for k, n in sorted(r["kinds"].items()))
        lines.append(f"The Hindsight id on {what}")
    if r["settings"]:
        lines.append("Hindsight's scene settings")
    if r["text"]:
        lines.append("The history stored in this .blend (Text .hindsight_history)")
    if r["keyframes"]:
        lines.append("Movie Mode keyframes")
    return lines


def remove(delete_folder: bool = False) -> dict:
    """Take Hindsight's data out of this file. Returns what was removed."""
    from .persistence import TEXT_NAME, journal_path
    from . import datarec
    r = report()
    for idb in _ids_with_uid():
        del idb[idmap.UID_PROP]
    for sc in bpy.data.scenes:
        if sc.library is None and _has_settings(sc):
            sc.property_unset("hindsight")
        for curves, fc in _movie_fcurves(sc):
            curves.remove(fc)
    text = bpy.data.texts.get(TEXT_NAME)
    if text is not None and text.library is None:
        bpy.data.texts.remove(text)
    removed_files = []
    if delete_folder:
        for f in folder_files():
            os.remove(f)
            removed_files.append(os.path.basename(f))
        d = folder()
        if d and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
    runtime.set_registry(runtime.Registry())
    runtime._STATES.clear()
    datarec.forget()
    idmap.forget()
    try:
        jp = journal_path(bpy.data.filepath or "")
        from .persistence import _close_journal
        _close_journal()
        if os.path.isfile(jp):
            os.remove(jp)
    except Exception as e:
        log.error_once(("cleanup_journal", str(e)), f"could not remove the crash journal: {e}", e)
    runtime.tag_redraw_all()
    return dict(r, removed_files=removed_files)


class HINDSIGHT_OT_remove_data(bpy.types.Operator):
    bl_idname = "hindsight.remove_data"
    bl_label = "Remove Hindsight Data from This File"
    bl_description = ("Take everything Hindsight added out of this file: its ids, settings, stored history and "
                      "Movie Mode keyframes. Geometry and your other settings aren't touched")
    bl_options = {'REGISTER'}

    delete_folder: bpy.props.BoolProperty(
        name="Also delete the history folder next to the file", default=False, options={'SKIP_SAVE'},
        description="Delete the history files in the folder next to the .blend (only Hindsight's own files)")

    def invoke(self, context, event):
        self._report = report()
        if not describe(self._report) and not self._report["folder"]:
            self.report({'INFO'}, "This file has no Hindsight data")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        r = getattr(self, "_report", None) or report()
        col = self.layout.column(align=True)
        col.label(text="This takes out of the file:")
        for line in describe(r):
            col.label(text="  " + line)
        if r["folder"]:
            self.layout.prop(self, "delete_folder")
            mb = r["folder_bytes"] / 1e6
            self.layout.label(text=f"  {os.path.basename(r['folder'])} ({mb:.1f} MB)")
        self.layout.label(text="Geometry and your other settings aren't touched.")
        self.layout.label(text="Recording starts again the next time you sculpt or switch it on.")

    def execute(self, context):
        done = remove(self.delete_folder)
        self.report({'INFO'}, f"Hindsight data removed ({done['ids']} ids"
                              + (f", {len(done['removed_files'])} files" if done["removed_files"] else "") + ")")
        return {'FINISHED'}


classes = (HINDSIGHT_OT_remove_data,)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
