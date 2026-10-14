import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shutil
import tempfile

import bpy
import tests.harness as H
from hindsight.bl import runtime, persistence
from hindsight.core import persist
from hindsight.core.types import SETTINGS

C = bpy.context
TMP = tempfile.mkdtemp(prefix="hindsight_persist_")
NAME = "Sculpty"


def setup(save_mode="SIDECAR", recovery="LAST_SAVED"):
    H.fresh()
    SETTINGS.save_mode = save_mode
    SETTINGS.crash_recovery = recovery
    runtime.missing_message[0] = None


def strokes(ob, seeds, states):
    for s in seeds:
        H.fake_stroke(ob, s, n=80)
        assert H.commit(ob) is not None, f"no step for seed {s}"
        states.append(H.snapshot(ob))


def sculpt_and_save(path, n=4, mode="SIDECAR"):
    ob = H.make_mesh(1500, name=NAME)
    H.enter(ob, "SCULPT")
    states = [H.snapshot(ob)]
    strokes(ob, range(10, 10 + n), states)
    H.enter(ob, "OBJECT")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    return states


def reopen(path):
    bpy.ops.wm.open_mainfile(filepath=path)
    ob = bpy.data.objects[NAME]
    return ob


def check_scrub(ob, states, order=None):
    H.enter(ob, "SCULPT")
    h = runtime.history_of(ob)
    order = order if order is not None else [0, 1, 2, 3, 4, 2, 4, 0, 3]
    for k in order:
        runtime.goto(C, k)
        assert h.cursor == k, (h.cursor, k)
        H.assert_same(H.snapshot(ob), states[k], f"goto {k}")


def roundtrip(mode):
    setup(mode)
    path = os.path.join(TMP, f"rt_{mode.lower()}.blend")
    states = sculpt_and_save(path, 4, mode)
    side = persist.sidecar_dir(path)
    assert os.path.isdir(side) == (mode in ("SIDECAR", "BOTH")), (mode, os.path.isdir(side))
    ob = reopen(path)
    assert runtime.missing_message[0] is None
    text = bpy.data.texts.get(persistence.TEXT_NAME)
    assert text is not None and text.use_fake_user
    assert bool(text.get("sidecar")) == (mode in ("SIDECAR", "BOTH"))
    assert ("hindsight_data" in text) == (mode in ("EMBED", "BOTH"))
    h = runtime.history_of(ob)
    assert h is not None, "history not restored"
    assert len(h.entries) == 4 and h.cursor == 4, (len(h.entries), h.cursor)
    H.assert_same(H.snapshot(ob), states[4], "loaded mesh")
    check_scrub(ob, states)
    return path, ob, states


def test_roundtrip_sidecar():
    roundtrip("SIDECAR")


def test_roundtrip_embed():
    roundtrip("EMBED")


def test_roundtrip_both_and_resave():
    path, ob, states = roundtrip("BOTH")
    # History persists across further sessions: continue sculpting, save again, reopen.
    runtime.goto(C, 4)
    strokes(ob, [99], states)
    H.enter(ob, "OBJECT")
    bpy.ops.wm.save_mainfile()
    ob = reopen(path)
    h = runtime.history_of(ob)
    assert len(h.entries) == 5 and h.cursor == 5, (len(h.entries), h.cursor)
    check_scrub(ob, states, [5, 0, 5, 3])


def test_embed_survives_without_sidecar():
    setup("BOTH")
    path = os.path.join(TMP, "both_nofolder.blend")
    states = sculpt_and_save(path, 4)
    shutil.rmtree(persist.sidecar_dir(path))
    ob = reopen(path)
    assert runtime.missing_message[0] is None
    h = runtime.history_of(ob)
    assert len(h.entries) == 4 and h.cursor == 4
    check_scrub(ob, states, [0, 4])


def test_missing_sidecar_message():
    setup("SIDECAR")
    path = os.path.join(TMP, "orig.blend")
    sculpt_and_save(path, 2)
    moved = os.path.join(TMP, "renamed.blend")
    os.rename(path, moved)            # .blend moved without its folder
    ob = reopen(moved)
    msg = runtime.missing_message[0]
    assert msg and "Undo History Not Found" in msg, msg
    assert "renamed_hindsight" in msg, msg
    assert runtime.history_of(ob) is None
    assert any("Undo History Not Found" in t for _s, t in runtime.warnings_for(C, ob))
    # deleted folder: same message
    path2 = os.path.join(TMP, "deleted.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path2)
    assert runtime.missing_message[0] is None, "saving clears the message"
    shutil.rmtree(persist.sidecar_dir(path2))
    reopen(path2)
    assert "Undo History Not Found" in (runtime.missing_message[0] or "")
    # a file that never had a sidecar shows nothing
    setup("EMBED")
    path3 = os.path.join(TMP, "embedded_only.blend")
    sculpt_and_save(path3, 1)
    reopen(path3)
    assert runtime.missing_message[0] is None


def interrupted_session(recovery, tag):
    setup("SIDECAR", recovery)
    path = os.path.join(TMP, f"crash_{tag}.blend")
    states = sculpt_and_save(path, 2)
    j = persistence.current_journal()
    assert j is not None and j.path == os.path.abspath(persistence.journal_path(path))
    assert [r[0] for r in j.read()] == ["SAVE"]
    ob = bpy.data.objects[NAME]
    H.enter(ob, "SCULPT")
    strokes(ob, [50, 51], states)            # unsaved work
    recs = [r[0] for r in persistence.current_journal().read()]
    assert recs.count("STEP") == 2 and recs[0] == "SAVE", recs
    H.enter(ob, "OBJECT")
    ob = reopen(path)                         # the session is abandoned without saving
    return path, ob, states


def test_crash_recovery_interrupted():
    path, ob, states = interrupted_session("INTERRUPTED", "int")
    h = runtime.history_of(ob)
    assert len(h.entries) == 4 and h.cursor == 2, (len(h.entries), h.cursor)
    H.assert_same(H.snapshot(ob), states[2], "mesh is the saved state")
    check_scrub(ob, states, [4, 2, 0, 3, 4, 1])
    # interrupted again (nothing saved since): recovered steps are still there
    H.enter(ob, "OBJECT")
    ob = reopen(path)
    h = runtime.history_of(ob)
    assert len(h.entries) == 4 and h.cursor == 2, (len(h.entries), h.cursor)
    check_scrub(ob, states, [4, 0])


def test_crash_recovery_last_saved():
    path, ob, states = interrupted_session("LAST_SAVED", "last")
    h = runtime.history_of(ob)
    assert len(h.entries) == 2 and h.cursor == 2, (len(h.entries), h.cursor)
    H.assert_same(H.snapshot(ob), states[2], "mesh is the saved state")
    assert [r[0] for r in persistence.current_journal().read()] == ["SAVE"], "fresh session journal"
    check_scrub(ob, states[:3], [0, 2, 1])


def test_journal_location_and_hook():
    setup()
    assert runtime.journal_hook is not None
    p1 = persistence.journal_path(os.path.join(TMP, "a.blend"))
    p2 = persistence.journal_path(os.path.join(TMP, "b.blend"))
    assert p1 != p2 and p1.endswith(".hsj") and len(os.path.basename(p1)) == 16 + 4
    untitled = persistence.journal_path("")
    ob = H.make_mesh(800, name=NAME)
    H.enter(ob, "SCULPT")
    strokes(ob, [7], [])
    j = persistence.current_journal()
    assert j is not None and j.path == os.path.abspath(untitled)
    assert [r[0] for r in j.read()] == ["META", "STEP"]
    H.enter(ob, "OBJECT")
    path = os.path.join(TMP, "named.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    assert not os.path.exists(untitled), "untitled journal removed once saved"
    persistence.unregister()
    assert runtime.journal_hook is None and persistence.current_journal() is None
    persistence.register()
    assert runtime.journal_hook is not None


for f in [test_roundtrip_sidecar, test_roundtrip_embed, test_roundtrip_both_and_resave,
          test_embed_survives_without_sidecar, test_missing_sidecar_message,
          test_crash_recovery_interrupted, test_crash_recovery_last_saved,
          test_journal_location_and_hook]:
    H.run(f)
shutil.rmtree(TMP, ignore_errors=True)
H.finish()
