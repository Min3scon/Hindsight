"""Write METADATA.md: everything Hindsight writes into a .blend or next to it, found from the code.

    blender -b --factory-startup --python tools/make_metadata.py

Runs inside Blender. The list of names comes from the code, not from memory: the properties Blender
gains when Hindsight registers (compared with before), the fields of its property groups, and the
names the code writes with (the id property, the Text datablock and its properties, the history
folder and its files, the crash journal, the Movie Mode keyframes). Every name found must have a
description below and every description must belong to a name found, or this script fails.
tests/test_bl_metadata.py checks that a session that uses every feature writes nothing that isn't
listed in METADATA.md.
"""
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ID_TYPES = ("Scene", "Object", "Mesh", "Collection", "Material", "WindowManager", "Text", "World",
            "NodeTree", "Image", "Action", "Camera", "Light", "Curve", "Armature")

# name -> (kind, where, written when, removed by). "where": in the .blend, next to the .blend, or
# outside both. Kept short and plain: the user-facing list in the docs is made from this table.
DESCRIBE = {
    "_hindsight_uid": (
        "custom property (text, 16 characters)", "in the .blend, on objects, meshes, the scene, collections "
        "and materials Hindsight records or refers to",
        "when an object's first change is recorded (entering Sculpt or Vertex Paint Mode counts), when "
        "Record everything is switched on, and on data a recorded value refers to (a parent, a collection)",
        "Remove Hindsight data from this file (Advanced panel); deleting the object"),
    "Scene.hindsight": (
        "property group on the scene (Blender 5 keeps it with the scene's system properties)", "in the .blend",
        "only once one of its settings is changed (Blender doesn't store unchanged defaults)",
        "Remove Hindsight data from this file"),
    "WindowManager.hindsight_step": (
        "slider property (computed, never stored)", "nowhere: it reads and sets the history position",
        "never", "nothing to remove"),
    ".hindsight_history": (
        "Text datablock (fake user, empty text)", "in the .blend",
        "when a file that has history is saved", "Remove Hindsight data from this file"),
    ".hindsight_history[sidecar]": (
        "custom property (on/off)", "in the .blend, on the Text above",
        "when a file that has history is saved: on when the history is in the folder next to the file",
        "Remove Hindsight data from this file"),
    ".hindsight_history[hindsight_data]": (
        "custom property (text: the whole history)", "in the .blend, on the Text above",
        "when saving with Save mode set to Embed or Both", "saving with Save mode Sidecar; Remove Hindsight data"),
    ".hindsight_history[hindsight_data_unreadable]": (
        "custom property (text)", "in the .blend, on the Text above",
        "only when an embedded history couldn't be read: it is kept, unchanged, in case it can be recovered",
        "Remove Hindsight data from this file"),
    "<file>_hindsight/": (
        "folder", "next to the .blend",
        "when a file that has history is saved with Save mode Sidecar or Both", "Remove Hindsight data (ticked) or "
        "deleting the folder; the .blend opens fine without it"),
    "<file>_hindsight/history.hsr": (
        "file (the history)", "in the folder above", "on save (Sidecar or Both)", "as the folder"),
    "<file>_hindsight/index.json": (
        "file (format, version, file name, time saved)", "in the folder above", "on save (Sidecar or Both)",
        "as the folder"),
    "<file>_hindsight/history.hsr.unreadable-<date>": (
        "file (a history file that couldn't be read, set aside)", "in the folder above",
        "only when a history file couldn't be read, so the next save can't overwrite it", "as the folder"),
    "journal/<id>.hsj": (
        "file (crash journal)", "outside: the extension's user data folder (or hindsight_journal in the "
        "system temp folder when that isn't available)",
        "while you record, so steps after the last save can be recovered after a crash",
        "emptied on every save; removed with the extension"),
    "keyframes on hindsight.movie_step": (
        "animation keyframes on the scene", "in the .blend",
        "only when you use Quick auto key in Movie Mode", "deleting the keyframes; Remove Hindsight data"),
}


def found_names() -> dict:
    """name -> where it was found in the code (property registration or a constant)."""
    import hindsight
    before = {t: {p.identifier for p in getattr(bpy.types, t).bl_rna.properties}
              for t in ID_TYPES if hasattr(bpy.types, t)}
    hindsight.register()
    out = {}
    try:
        for t, props in before.items():
            for p in getattr(bpy.types, t).bl_rna.properties:
                if p.identifier not in props:
                    out[f"{t}.{p.identifier}"] = "registered property"
        from hindsight.bl import idmap, movie, persistence, runtime
        from hindsight.core import persist
        assert idmap.UID_PROP == runtime.UID_PROP
        out[idmap.UID_PROP] = "hindsight/bl/idmap.py UID_PROP"
        out[persistence.TEXT_NAME] = "hindsight/bl/persistence.py TEXT_NAME"
        for const in ("SIDECAR_PROP", "DATA_PROP", "UNREADABLE_PROP"):
            out[f"{persistence.TEXT_NAME}[{getattr(persistence, const)}]"] = f"persistence.{const}"
        folder = os.path.basename(persist.sidecar_dir("<file>.blend")) + "/"
        out[folder] = "persist.sidecar_dir"
        out[folder + persist.HSR_NAME] = "persist.HSR_NAME"
        out[folder + persist.INDEX_NAME] = "persist.INDEX_NAME"
        out[folder + persist.HSR_NAME + ".unreadable-<date>"] = "persist.set_aside"
        j = persistence.journal_path("x.blend")
        assert os.path.basename(os.path.dirname(j)) in ("journal", "hindsight_journal"), j
        out[f"journal/<id>{os.path.splitext(j)[1]}"] = "persistence.journal_path"
        out[f"keyframes on {movie.DATA_PATH}"] = "movie.DATA_PATH"
    finally:
        hindsight.unregister()
    return out


def table() -> str:
    names = found_names()
    missing = sorted(set(names) - set(DESCRIBE))
    stale = sorted(set(DESCRIBE) - set(names))
    if missing or stale:
        raise SystemExit(f"make_metadata: no description for {missing}; described but not in the code: {stale}")
    rows = ["| Name | Kind | Where | Written | Removed by |", "|---|---|---|---|---|"]
    for name in sorted(DESCRIBE, key=lambda n: (DESCRIBE[n][1].startswith("next") or "folder" in DESCRIBE[n][1],
                                                 n.lower())):
        kind, where, when, removed = DESCRIBE[name]
        rows.append(f"| `{name}` | {kind} | {where} | {when} | {removed} |")
    return "\n".join(rows)


HEADER = """# What Hindsight adds to a file

Generated by `tools/make_metadata.py` from the code (the properties Hindsight registers and the names
it writes with). Do not edit by hand. `tests/test_bl_metadata.py` fails if Hindsight writes anything
into a .blend or next to it that isn't listed here.

- Opening a file with Hindsight doesn't change it. Nothing is written until recording starts on an
  object (entering Sculpt or Vertex Paint Mode on it, or switching on Record everything) or you change
  a Hindsight setting, and nothing reaches the disk until you save.
- Saving a file that has no history writes nothing of Hindsight's: no Text, no folder, no journal.
- A file saved with Hindsight data opens fine without Hindsight: the id properties and the Text stay
  as plain data and do nothing.
- Undo History > Advanced > Remove Hindsight data from this file takes all of it out again (the
  folder only if you tick it). Geometry and your other settings aren't touched.

"""


def main():
    text = HEADER + table() + "\n"
    with open(os.path.join(ROOT, "METADATA.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print("make_metadata: wrote METADATA.md")


if __name__ == "__main__":
    main()
