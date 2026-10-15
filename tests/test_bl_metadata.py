"""What Hindsight adds to a file (METADATA.md), headless.

- A session that uses every feature (sculpt history, Record everything with the scene, Movie Mode
  keys, a changed scene setting, saving with the history both in the folder and embedded) writes
  nothing into the .blend or next to it that METADATA.md doesn't list, and does write each thing it
  lists that the session used (so the check can't pass by finding nothing).
- Saving a file Hindsight never recorded adds nothing (D29), also after entering Sculpt, Vertex
  Paint and Edit Mode (D30: an id given for a stroke that never came is taken off again).
- Opening a file in each of the four modes leaves it byte for byte as it was, with no folder or
  journal next to it.
- A file saved with Hindsight data opens in a Blender without Hindsight: everything loads, the
  meshes and objects are as saved.
- Remove Hindsight data from this file takes all of it out (the folder only when asked) and changes
  nothing else.

Run: blender -b --factory-startup --python tests/test_bl_metadata.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hashlib
import json
import re
import subprocess
import tempfile

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from hindsight.bl import cleanup, datarec, persistence, runtime
from hindsight.core.types import SETTINGS

C = bpy.context
ROWS = ("sculpt.positions", "obj.transforms", "obj.addremove")
TMP = tempfile.mkdtemp(prefix="hs_meta_")


def listed() -> list:
    text = open(os.path.join(H.ROOT, "METADATA.md"), encoding="utf-8").read()
    return re.findall(r"^\| `([^`]+)` \|", text, re.M)


def _ids():
    for name in dir(bpy.data):
        col = getattr(bpy.data, name, None)
        if isinstance(col, bpy.types.bpy_prop_collection) and len(col) and isinstance(col[0], bpy.types.ID):
            yield from col


def found_in_blend() -> set:
    """Everything of Hindsight's in the open file, named the way METADATA.md names it."""
    out = set()
    for idb in _ids():
        if idb.library is not None:
            continue
        for k in idb.keys():
            if "hindsight" in k.lower() and not isinstance(idb, bpy.types.Text):
                out.add(k)
        get = getattr(idb, "bl_system_properties_get", None)        # registered properties (Blender 5)
        sp = get() if get is not None else None
        for k in (sp.keys() if sp is not None else ()):
            if "hindsight" in k.lower():
                out.add(f"{type(idb).__name__ if not isinstance(idb, bpy.types.Scene) else 'Scene'}.{k}")
        if isinstance(idb, bpy.types.Text) and "hindsight" in idb.name.lower():
            out.add(idb.name)
            out |= {f"{idb.name}[{k}]" for k in idb.keys()}
        ad = getattr(idb, "animation_data", None)
        if ad is not None and ad.action is not None:
            for fc in L_fcurves(ad):
                if "hindsight" in fc.data_path:
                    out.add(f"keyframes on {fc.data_path}")
    return out


def L_fcurves(ad):
    try:
        from bpy_extras import anim_utils
        cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        return list(cb.fcurves) if cb is not None else []
    except Exception:
        return list(getattr(ad.action, "fcurves", ()))


def files_next_to(path) -> set:
    """Files and folders next to the .blend other than the .blend and Blender's own backups."""
    d, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    out = set()
    for root, dirs, files in os.walk(d):
        for n in dirs + files:
            full = os.path.join(root, n)
            rel = os.path.relpath(full, d).replace("\\", "/").replace(stem, "<file>", 1)
            if os.path.isdir(full):
                rel += "/"
            if re.fullmatch(r"<file>\.blend\d*", rel):
                continue
            out.add(rel)
    return out


def matches(name, patterns) -> bool:
    """name matches one of METADATA.md's names, where <date> and <id> stand for any text."""
    for p in patterns:
        rx = re.escape(p).replace(re.escape("<date>"), ".+").replace(re.escape("<id>"), ".+")
        if re.fullmatch(rx, name):
            return True
    return False


def full_session(path, mode="BOTH"):
    H.fresh()
    T.build_scene()
    a = bpy.data.objects["A"]
    H.enter(a, "SCULPT")
    H.fake_stroke(a, 3)
    assert H.commit(a) is not None
    H.enter(a, "OBJECT")
    L.track_all()
    T.translate("B", (1, 0, 0)).run()
    L.tick_like("move")
    T.add_primitive("cube", (0, -5, 0)).run()
    L.tick_like("add")
    sc = C.scene
    sc.hindsight.use_global = True
    sc.hindsight.movie_mode = True
    sc.frame_set(1)
    assert bpy.ops.hindsight.quick_auto_key() == {"FINISHED"}
    sc.hindsight.movie_mode = False
    SETTINGS.save_mode = mode
    bpy.ops.wm.save_as_mainfile(filepath=path)


@covers(ROWS, "WARN")
def test_session_writes_only_what_is_listed():
    want = listed()
    path = os.path.join(TMP, "session", "scene.blend")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    full_session(path)
    blend = found_in_blend()
    files = files_next_to(path)
    unlisted = sorted(n for n in blend | files if not matches(n, want))
    assert not unlisted, f"written but not in METADATA.md: {unlisted}"
    for must in ("_hindsight_uid", "Scene.hindsight", ".hindsight_history", ".hindsight_history[sidecar]",
                 ".hindsight_history[hindsight_data]", "keyframes on hindsight.movie_step",
                 "<file>_hindsight/", "<file>_hindsight/history.hsr", "<file>_hindsight/index.json"):
        assert matches(must, blend | files) or must in blend | files, f"the session didn't write {must}"
    j = persistence.journal_path(path)
    assert matches("journal/" + os.path.basename(j), want)


@covers(ROWS, "WARN")
def test_saving_an_unrecorded_file_adds_nothing():
    """D29 and D30."""
    for mode in ("OBJECT", "SCULPT", "VERTEX_PAINT", "EDIT"):
        H.fresh()
        T.build_scene()
        a = bpy.data.objects["A"]
        H.enter(a, mode)
        if mode != "OBJECT":
            H.enter(a, "OBJECT")
        path = os.path.join(TMP, f"plain_{mode.lower()}", "plain.blend")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        assert not found_in_blend(), (mode, found_in_blend())
        assert not files_next_to(path), (mode, files_next_to(path))
        assert not os.path.isfile(persistence.journal_path(path)), mode
        bpy.ops.wm.open_mainfile(filepath=path)
        assert not found_in_blend(), (mode, "after reopening", found_in_blend())


def _hash(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@covers(ROWS, "WARN")
def test_opening_a_file_changes_nothing():
    for mode in ("OBJECT", "SCULPT", "VERTEX_PAINT", "EDIT"):
        H.fresh(register=False)
        T.build_scene()
        T.select("A")
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode=mode)
        path = os.path.join(TMP, f"open_{mode.lower()}", "untouched.blend")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        before = _hash(path)
        H.fresh()
        bpy.ops.wm.open_mainfile(filepath=path)
        C.view_layer.update()
        runtime.check_mode_transition()
        runtime.tick()
        assert _hash(path) == before, mode
        assert not files_next_to(path), (mode, files_next_to(path))
        assert not os.path.isfile(persistence.journal_path(path)), mode
        assert not any(runtime.reg().histories), mode


@covers(ROWS, "WARN")
def test_opens_without_hindsight():
    path = os.path.join(TMP, "nohs", "scene.blend")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    full_session(path, mode="BOTH")
    want = {o.name: O.object_state(o, derived=False) for o in C.scene.objects}
    want = {n: {k: v for k, v in s.items() if k != "custom"} for n, s in want.items()}
    script = os.path.join(TMP, "nohs", "check.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(f"""import sys, json
sys.path.insert(0, {H.ROOT!r})
import bpy
from tests import oracle as O
assert "hindsight" not in " ".join(bpy.context.preferences.addons.keys()), "the add-on is installed"
out = {{o.name: O.object_state(o, derived=False) for o in bpy.context.scene.objects}}
out = {{n: {{k: v for k, v in s.items() if k != "custom"}} for n, s in out.items()}}
json.dump(repr(sorted(out.items())), open({script + '.json'!r}, "w"))
print("LOADED", len(bpy.data.objects))
""")
    r = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", path, "--python-exit-code", "1",
                        "--python", script], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    assert "LOADED" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]
    assert "Traceback" not in r.stdout + r.stderr, (r.stdout + r.stderr)[-1500:]
    got = json.load(open(script + ".json"))
    assert got == repr(sorted(want.items())), "a file with Hindsight data opens differently without Hindsight"


@covers(ROWS, "WARN")
def test_remove_hindsight_data():
    for delete_folder in (False, True):
        path = os.path.join(TMP, f"remove_{int(delete_folder)}", "scene.blend")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        full_session(path, mode="BOTH")
        geometry = {o.name: O.object_state(o, derived=False) for o in C.scene.objects}
        r = cleanup.report()
        assert r["ids"] and r["text"] and r["keyframes"] and r["folder"], r
        done = cleanup.remove(delete_folder=delete_folder)
        assert not found_in_blend(), found_in_blend()
        now = {o.name: O.object_state(o, derived=False) for o in C.scene.objects}
        strip = lambda st: {n: {k: v for k, v in s.items() if k not in ("custom",)} for n, s in st.items()}
        assert strip(now) == strip(geometry), "something other than Hindsight's data changed"
        assert os.path.isdir(os.path.dirname(path) + "/scene_hindsight") != delete_folder
        assert not any(h.entries for h in runtime.reg().histories.values())
        bpy.ops.wm.save_mainfile()
        assert not found_in_blend()
        files = files_next_to(path)
        assert files == (set() if delete_folder else {"<file>_hindsight/", "<file>_hindsight/history.hsr",
                                                       "<file>_hindsight/index.json"}), files
        assert done["ids"] == r["ids"]


for fn in (test_session_writes_only_what_is_listed, test_saving_an_unrecorded_file_adds_nothing,
           test_opening_a_file_changes_nothing, test_opens_without_hindsight, test_remove_hindsight_data):
    H.run(fn)
H.finish()
