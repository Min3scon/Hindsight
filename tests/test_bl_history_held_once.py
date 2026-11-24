"""#9 / #54: after a .blend with its history inside opens, Hindsight holds the history and Blender's copy of it
(the bytes on the Text ``.hindsight_history``) is dropped, so it is in memory once. A history Hindsight doesn't
read (from a newer Hindsight, or unreadable) is not dropped: it stays in the file untouched, as before.
#55: a save puts the copy back only for the write (Save, Save As and Save Copy alike) and takes it out after."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import json
import struct
import tempfile

import bpy
import tests.harness as H
from hindsight.bl import persistence, runtime
from hindsight.core import persist
from hindsight.core.types import SETTINGS

C = bpy.context
U32 = struct.Struct("<I")


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
    return os.path.join(tempfile.mkdtemp(prefix="hs_once_"), "e.blend")


def positions(ob):
    import numpy as np
    a = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", a)
    return a.tobytes()


def copies():
    text = bpy.data.texts[persistence.TEXT_NAME]
    return [k for k in (persistence.DATA_PROP, persistence.DATA_BIN_PROP) if k in text]


def saved_with_states():
    ob = recorded()
    h = runtime.history_of(ob)
    states = []
    for pos in range(len(h.entries) + 1):
        runtime.goto(C, pos)
        states.append(positions(ob))
    p = path()
    bpy.ops.wm.save_as_mainfile(filepath=p)
    assert copies() == [], copies()                 # #55: the save took Blender's copy out again
    return p, states


def reopens_with(p, states):
    bpy.ops.wm.open_mainfile(filepath=p)
    ob = bpy.data.objects["E"]
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == len(states) - 1, p
    for pos in list(range(len(states))) + [0]:
        runtime.goto(C, pos)
        assert positions(ob) == states[pos], (p, pos)


def stored_as(p, raw):
    """Open ``p`` without Hindsight, put ``raw`` in place of the stored history and save it."""
    persistence.unregister()
    try:
        bpy.ops.wm.open_mainfile(filepath=p)
        t = bpy.data.texts[persistence.TEXT_NAME]
        del t[persistence.DATA_BIN_PROP]           # Blender cuts bytes at a zero when it replaces them
        t[persistence.DATA_BIN_PROP] = raw
        bpy.ops.wm.save_mainfile(filepath=p)
    finally:
        persistence.register()


def test_blenders_copy_goes_after_opening_and_every_step_plays_back():
    p, states = saved_with_states()
    bpy.ops.wm.open_mainfile(filepath=p)
    assert copies() == [], copies()
    text = bpy.data.texts[persistence.TEXT_NAME]
    assert text.get(persistence.SAVE_ID_PROP) == runtime.reg().save_id      # the rest of the Text stays
    ob = bpy.data.objects["E"]
    h = runtime.history_of(ob)
    assert h is not None and len(h.entries) == len(states) - 1
    for pos in list(range(len(states))) + [0, len(states) - 1]:
        runtime.goto(C, pos)
        assert positions(ob) == states[pos], pos


def test_the_next_save_still_holds_the_history():
    # #55: opened and saved without recording anything new, twice over: every step is still there
    p, states = saved_with_states()
    for _ in range(2):
        bpy.ops.wm.open_mainfile(filepath=p)
        bpy.ops.wm.save_mainfile(filepath=p)
        assert copies() == [], copies()
    reopens_with(p, states)


def test_save_as_save_and_save_copy_each_take_blenders_copy_out_again():
    p, states = saved_with_states()                         # Save As (the copy is checked gone there)
    reopens_with(p, states)
    ob = bpy.data.objects["E"]
    runtime.goto(C, len(states) - 1)                        # a new step on top of every step (not a new branch)
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 99)
    assert runtime.commit(ob) is not None
    H.enter(ob, "OBJECT")
    states.append(positions(ob))
    bpy.ops.wm.save_mainfile(filepath=p)                    # Save
    assert copies() == [], copies()
    reopens_with(p, states)
    c = os.path.join(os.path.dirname(p), "c.blend")
    bpy.ops.wm.save_as_mainfile(filepath=c, copy=True)      # Save Copy
    assert copies() == [], copies()
    assert bpy.data.filepath == p
    reopens_with(c, states)


def test_a_newer_history_stays_and_is_saved_back_untouched():
    p, _ = saved_with_states()
    blob = H.stored_text(p)[persistence.DATA_BIN_PROP]
    (jlen,) = U32.unpack_from(blob, 4)
    doc = json.loads(blob[8:8 + jlen])
    doc["format"] = doc["version"] = persist.FORMAT_VERSION + 1
    js = json.dumps(doc).encode()
    newer = persist.REG_MAGIC + U32.pack(len(js)) + js + blob[8 + jlen:]
    stored_as(p, newer)
    bpy.ops.wm.open_mainfile(filepath=p)
    assert runtime.history_of(bpy.data.objects["E"]) is None
    assert copies() == [persistence.DATA_BIN_PROP], copies()
    assert bytes(bpy.data.texts[persistence.TEXT_NAME][persistence.DATA_BIN_PROP]) == newer
    for _ in range(2):
        bpy.ops.wm.save_mainfile(filepath=p)
        assert copies() == [persistence.DATA_BIN_PROP], copies()    # #55: not taken out, nothing puts it back
    bpy.ops.wm.open_mainfile(filepath=p)
    assert bytes(bpy.data.texts[persistence.TEXT_NAME][persistence.DATA_BIN_PROP]) == newer


def test_an_unreadable_history_stays_and_is_saved_back_untouched():
    p, _ = saved_with_states()
    raw = bytearray(H.stored_text(p)[persistence.DATA_BIN_PROP])
    raw[:4] = b"JUNK"
    stored_as(p, bytes(raw))
    bpy.ops.wm.open_mainfile(filepath=p)
    assert runtime.history_of(bpy.data.objects["E"]) is None
    text = bpy.data.texts[persistence.TEXT_NAME]
    assert bytes(text[persistence.UNREADABLE_PROP]) == bytes(raw)          # kept aside, as before
    bpy.ops.wm.save_mainfile(filepath=p)
    bpy.ops.wm.open_mainfile(filepath=p)
    text = bpy.data.texts[persistence.TEXT_NAME]
    assert bytes(text[persistence.UNREADABLE_PROP]) == bytes(raw)


def test_blenders_undo_after_opening_does_not_bring_the_copy_back():
    p, states = saved_with_states()
    bpy.ops.wm.open_mainfile(filepath=p)
    assert copies() == [], copies()
    # Outside background mode Blender takes its first undo step right after the load_post handlers
    # (wm_file_read_post); in background mode it waits for the first push
    bpy.ops.ed.undo_push(message="Original")
    ob = bpy.data.objects["E"]
    ob.location.x += 1.0
    bpy.ops.ed.undo_push(message="move")
    bpy.ops.ed.undo()                         # back to the state the file opened in
    assert bpy.data.objects["E"].location.x == 0.0, "the undo didn't go back"
    assert copies() == [], copies()
    bpy.ops.ed.redo()
    assert copies() == [], copies()
    h = runtime.history_of(bpy.data.objects["E"])
    assert h is not None and len(h.entries) == len(states) - 1


if __name__ == "__main__":
    for t in (test_blenders_copy_goes_after_opening_and_every_step_plays_back, test_the_next_save_still_holds_the_history,
              test_save_as_save_and_save_copy_each_take_blenders_copy_out_again,
              test_a_newer_history_stays_and_is_saved_back_untouched,
              test_an_unreadable_history_stays_and_is_saved_back_untouched,
              test_blenders_undo_after_opening_does_not_bring_the_copy_back):
        H.run(t)
    H.finish()
