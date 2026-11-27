"""R061: Save mode Embed keeps the history in the .blend as bytes (custom property hindsight_data_bin) instead
of base64 text (hindsight_data): no encoding when saving and no decoding when opening, and a quarter less in the
file. Files from earlier builds (base64 text) open as before; when both are there, the text is the newer one
(an earlier build saved the file after this one) and is used. A damaged copy is kept aside, as before."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import binascii
import tempfile

import bpy
import tests.harness as H
from hindsight.bl import persistence, runtime
from hindsight.core import persist
from hindsight.core.types import SETTINGS

C = bpy.context


def recorded(n=5):
    H.fresh()
    SETTINGS.save_mode = "EMBED"
    ob = H.make_mesh(3000, name="E")
    H.enter(ob, "SCULPT")
    for k in range(n):
        H.fake_stroke(ob, k + 1)
        assert runtime.commit(ob) is not None
    H.enter(ob, "OBJECT")
    return ob


def path():
    return os.path.join(tempfile.mkdtemp(prefix="hs_eb_"), "e.blend")


def positions(ob):
    import numpy as np
    a = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", a)
    return a.tobytes()


def test_saved_as_bytes_and_read_back():
    ob = recorded()
    h = runtime.history_of(ob)
    states = []
    for pos in range(len(h.entries) + 1):
        runtime.goto(C, pos)
        states.append(positions(ob))
    p = path()
    bpy.ops.wm.save_as_mainfile(filepath=p)
    text = bpy.data.texts[persistence.TEXT_NAME]
    assert persistence.DATA_BIN_PROP in text and persistence.DATA_PROP not in text, list(text.keys())
    assert isinstance(text[persistence.DATA_BIN_PROP], bytes)
    bpy.ops.wm.open_mainfile(filepath=p)
    ob = bpy.data.objects["E"]
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == len(states) - 1
    for pos in (0, 3, len(states) - 1):
        runtime.goto(C, pos)
        assert positions(ob) == states[pos], pos


def test_older_builds_text_still_opens():
    recorded()
    p = path()
    bpy.ops.wm.save_as_mainfile(filepath=p)
    raw = bytes(bpy.data.texts[persistence.TEXT_NAME][persistence.DATA_BIN_PROP])
    persistence.unregister()                        # the file as an earlier build left it: base64 text only
    try:
        bpy.ops.wm.open_mainfile(filepath=p)
        text = bpy.data.texts[persistence.TEXT_NAME]
        text.pop(persistence.DATA_BIN_PROP, None)
        text[persistence.DATA_PROP] = binascii.b2a_base64(raw, newline=False).decode("ascii")
        bpy.ops.wm.save_mainfile(filepath=p)
    finally:
        persistence.register()
    bpy.ops.wm.open_mainfile(filepath=p)
    h = runtime.history_of(bpy.data.objects["E"])
    assert h is not None and len(h.entries) == 5, h and len(h.entries)


def test_text_wins_when_both_are_there():
    recorded(5)
    p = path()
    bpy.ops.wm.save_as_mainfile(filepath=p)
    old = bytes(bpy.data.texts[persistence.TEXT_NAME][persistence.DATA_BIN_PROP])
    recorded(2)                                         # what an earlier build saved over it later
    bpy.ops.wm.save_as_mainfile(filepath=p)
    newer = bytes(bpy.data.texts[persistence.TEXT_NAME][persistence.DATA_BIN_PROP])
    persistence.unregister()
    try:
        bpy.ops.wm.open_mainfile(filepath=p)
        text = bpy.data.texts[persistence.TEXT_NAME]
        text[persistence.DATA_BIN_PROP] = old
        text[persistence.DATA_PROP] = binascii.b2a_base64(newer, newline=False).decode("ascii")
        bpy.ops.wm.save_mainfile(filepath=p)
    finally:
        persistence.register()
    bpy.ops.wm.open_mainfile(filepath=p)
    h = runtime.history_of(bpy.data.objects["E"])
    assert h is not None and len(h.entries) == 2, h and len(h.entries)
    # the next save writes the bytes and leaves no text behind
    bpy.ops.wm.save_mainfile(filepath=p)
    text = bpy.data.texts[persistence.TEXT_NAME]
    assert persistence.DATA_PROP not in text and persistence.DATA_BIN_PROP in text


def test_damaged_bytes_are_kept_aside(older=None):
    recorded()
    p = path()
    bpy.ops.wm.save_as_mainfile(filepath=p)
    raw = bytearray(bpy.data.texts[persistence.TEXT_NAME][persistence.DATA_BIN_PROP])
    raw[:4] = b"JUNK"
    persistence.unregister()
    try:
        bpy.ops.wm.open_mainfile(filepath=p)
        t = bpy.data.texts[persistence.TEXT_NAME]
        del t[persistence.DATA_BIN_PROP]           # Blender cuts bytes at a zero when it replaces them
        t[persistence.DATA_BIN_PROP] = bytes(raw)
        if older is not None:
            t[persistence.UNREADABLE_PROP] = older
        bpy.ops.wm.save_mainfile(filepath=p)
    finally:
        persistence.register()
    bpy.ops.wm.open_mainfile(filepath=p)
    text = bpy.data.texts[persistence.TEXT_NAME]
    assert runtime.history_of(bpy.data.objects["E"]) is None
    assert persistence.UNREADABLE_PROP in text and bytes(text[persistence.UNREADABLE_PROP]) == bytes(raw)
    assert persistence.DATA_BIN_PROP not in text


def test_damaged_bytes_replace_an_older_unreadable_copy_whole():
    # a copy kept aside earlier is replaced by the new one in full (Blender would cut a replaced bytes
    # property at its first zero byte)
    test_damaged_bytes_are_kept_aside(older=b"old\x00copy")


def test_file_is_smaller():
    recorded(40)
    p = path()
    bpy.ops.wm.save_as_mainfile(filepath=p)
    text = bpy.data.texts[persistence.TEXT_NAME]
    n = len(text[persistence.DATA_BIN_PROP])
    assert n == len(persist.registry_to_bytes(runtime.reg())), n      # the registry's bytes, not their base64


if __name__ == "__main__":
    for t in (test_saved_as_bytes_and_read_back, test_older_builds_text_still_opens, test_text_wins_when_both_are_there,
              test_damaged_bytes_are_kept_aside, test_damaged_bytes_replace_an_older_unreadable_copy_whole, test_file_is_smaller):
        H.run(t)
    H.finish()
