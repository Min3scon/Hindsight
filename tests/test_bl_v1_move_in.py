import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""#67 (11.4): V1 histories, formats 1 to 4, move into format 5 at the next save (dev/FORMAT5_V1.md).

Formats 1 to 3 are tests/fixtures/*.hsr, each written by that build's own code (make_legacy_fixtures.py), put in a
.blend the way each of V1's save modes left it: a history folder, inside the .blend (base64), and both. Format 4 is
beta 2's own files (test_bl_beta2_upgrade.py checks their move-in, crash journal included). For each one the opened
file holds every step as it was saved, and Blender's copy of a history inside the .blend goes at once (held once).
After one save the .blend holds format 5: the stub, `sidecar` False, no `hindsight_data_bin` and no save id, and parts
with every step's bytes, CRC-32 and meta as V1 saved them; the folder is untouched and the user told once it is no
longer used. Opened alone, the file holds the same steps, and every position plays back bit for bit.
"""
import base64
import hashlib
import shutil
import tempfile

import bpy

import tests.harness as H
from tests.test_core_migration import FILES, FIX, check_all_positions
from hindsight.bl import persistence, runtime
from hindsight.core import persist

TMP = tempfile.mkdtemp(prefix="hindsight_v1_move_in_")


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def v1_blend(name, mode):
    """(a .blend whose history is the V1 file `name`, as save mode `mode` left it, and the file's registry). It is
    saved with Hindsight off, so nothing of this build is in it; the objects carry the ids the history records."""
    with open(os.path.join(FIX, name), "rb") as fh:
        raw = fh.read()
    blend = os.path.join(TMP, f"{os.path.splitext(name)[0]}_{mode}", "scene.blend")
    os.makedirs(os.path.dirname(blend))
    H.fresh(register=False)
    reg = persist.registry_from_bytes(raw)
    for uid in reg.by_object:
        ob = bpy.data.objects.new(uid, bpy.data.meshes.new(uid))
        bpy.context.scene.collection.objects.link(ob)
        ob[runtime.UID_PROP] = uid
    text = bpy.data.texts.new(persistence.TEXT_NAME)
    text.use_fake_user = True
    text[persistence.SIDECAR_PROP] = mode != "embed"
    if mode != "folder":
        text[persistence.DATA_PROP] = base64.b64encode(raw).decode("ascii")
    if mode != "embed":
        os.makedirs(persist.sidecar_dir(blend))
        with open(persist.sidecar_file(blend), "wb") as fh:
            fh.write(raw)
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    return blend, reg


def open_file(blend):
    H.fresh()
    bpy.ops.wm.open_mainfile(filepath=blend)


def move_in(name, mode):
    what = f"{name}, {mode}"
    blend, v1 = v1_blend(name, mode)
    want = H.saved_steps(v1)
    folder = persist.sidecar_file(blend)
    had = sha(folder) if mode != "embed" else None
    on_disk = sha(blend)
    H.fresh(register=False)
    bpy.ops.wm.open_mainfile(filepath=blend)
    unchanged = bpy.data.is_dirty          # what opening alone says: the bpy module of 4.2 says changed after any open

    open_file(blend)
    assert H.saved_steps(runtime.reg()) == want, f"{what}: opened"
    assert not runtime.load_notes and runtime.missing_message[0] is None, (what, runtime.load_notes)
    text = bpy.data.texts[persistence.TEXT_NAME]
    assert not persistence._holds_history(text), f"{what}: Blender still holds its copy of the history"
    assert mode == "folder" or text[persistence.DATA_PROP] == persist.STUB, what
    assert bpy.data.is_dirty == unchanged, f"{what}: opening marked the file as changed"
    H.fresh()
    assert sha(blend) == on_disk, f"{what}: closed without saving, the .blend changed"

    open_file(blend)
    bpy.ops.wm.save_mainfile(filepath=blend)
    stored = H.stored_text(blend)
    assert stored.get(persistence.DATA_PROP) == persist.STUB and not stored[persistence.SIDECAR_PROP], what
    assert persistence.PARTS_PROP in stored and persistence.DATA_BIN_PROP not in stored, (what, list(stored))
    assert persistence.SAVE_ID_PROP not in stored, (what, list(stored))
    assert H.saved_steps(H.stored_registry(blend)) == want, f"{what}: the parts don't hold V1's steps as saved"
    notes = [t for _sev, t in runtime.load_notes]
    moved = persistence.MOVED_IN_TEXT.format(folder=os.path.basename(persist.sidecar_dir(blend)))
    assert notes == ([moved] if had else []), (what, notes)
    if had:
        assert sha(folder) == had, f"{what}: the history folder changed"
    bpy.ops.wm.save_mainfile(filepath=blend)
    assert not runtime.load_notes, f"{what}: the note shows again at the next save: {runtime.load_notes}"

    if had:
        shutil.rmtree(persist.sidecar_dir(blend))
    open_file(blend)
    assert H.saved_steps(runtime.reg()) == want, f"{what}: opened alone after the save"
    assert not runtime.load_notes and runtime.missing_message[0] is None, (what, runtime.load_notes)
    check_all_positions(H.stored_registry(blend), what + ", moved into format 5")


def make_test(name, mode):
    def t():
        move_in(name, mode)
    t.__name__ = f"test_format{FILES[name][0]}_{mode}"
    return t


TESTS = [make_test(name, mode) for name in FILES for mode in ("folder", "embed", "both")]


if __name__ == "__main__":
    try:
        for t in TESTS:
            H.run(t)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    H.finish()
