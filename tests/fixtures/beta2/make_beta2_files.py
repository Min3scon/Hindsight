"""Make the beta 2 fixture files that tests/test_bl_beta2_upgrade.py opens: real files saved by the real 1.0.0-beta.2
code (git tag v1.0.0-beta.2, run as it was, pure Python), one per save mode, and one left by a crash.

    python3 tests/fixtures/beta2/make_beta2_files.py BLENDER_4_2_BINARY

Beta 2 supported Blender 4.2 to 5.1, so the files are made in Blender 4.2 (any later Blender opens them). Each case
runs in its own Blender process: a sculpt history on "S" (three strokes, a topology step from Edit Mode Subdivide,
two more strokes) and two moves of "O" recorded with Record everything, then a save with beta 2's save mode. The
crash case (history folder, "Unsaved steps" on open) makes two more strokes after the save and then the process
ends without saving; beta 2's crash journal for the file is copied next to it as scene.hsj. expect.json holds what
beta 2 itself read back at every position (positions as float32 bytes in base64, locations as floats).

The files keep no absolute path that matters: the history folder is found next to the .blend by name, and the
test copies each case to a new place (and the journal to where this build looks for it) before opening it.
"""
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import io

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
TAG = "v1.0.0-beta.2"
CASES = (("folder", "SIDECAR", False), ("embed", "EMBED", False), ("both", "BOTH", False), ("crash", "SIDECAR", True))

GEN = r'''
import sys, os, json, base64
BUILD, OUT, MODE, CRASH = sys.argv[sys.argv.index("--") + 1:]
CRASH = CRASH == "1"
sys.path.insert(0, BUILD)
import bpy
import numpy as np
import tests.harness as H          # beta 2's own test helpers
import hindsight
from hindsight.bl import datarec, persistence, runtime
from hindsight.core.types import SETTINGS

H.fresh()
SETTINGS.save_mode = MODE
SETTINGS.crash_recovery = "INTERRUPTED"


def pos(ob):
    a = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", a)
    return base64.b64encode(a.tobytes()).decode()


ob = H.make_mesh(300, name="S")
H.enter(ob, "SCULPT")
states = [pos(ob)]
for s in range(3):
    H.fake_stroke(ob, 10 + s, n=40)
    assert H.commit(ob) is not None, "beta 2 recorded no stroke"
    states.append(pos(ob))
H.enter(ob, "EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.subdivide()
H.enter(ob, "SCULPT")
h = runtime.history_of(ob)
assert h.entries[-1].kind == "TOPO", [e.kind for e in h.entries]
states.append(pos(ob))
for s in range(2):
    H.fake_stroke(ob, 20 + s, n=60)
    assert H.commit(ob) is not None
    states.append(pos(ob))
H.enter(ob, "OBJECT")
o2 = H.make_mesh(100, name="O", location=(3.0, 0.0, 0.0))
datarec.FORCE_SCOPE[0] = "EVERYTHING"
datarec.set_tracked(o2, True)
datarec.finish_operation("baseline")
locs = [list(o2.location)]
for dx in (0.5, -1.25):
    o2.location.x += dx
    bpy.context.view_layer.update()
    datarec.finish_operation("Move")
    locs.append(list(o2.location))
ho = runtime.history_of(o2)
assert ho is not None and len(ho.entries) == 2, None if ho is None else len(ho.entries)
blend = os.path.join(OUT, "scene.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
info = {"tag": "v1.0.0-beta.2", "blender": bpy.app.version_string, "mode": MODE, "S": states, "O": locs}
if CRASH:
    H.enter(ob, "SCULPT")
    extra = []
    for s in range(2):
        H.fake_stroke(ob, 30 + s, n=50)
        assert H.commit(ob) is not None
        extra.append(pos(ob))
    info["S_unsaved"] = extra
    j = persistence.current_journal()
    if j is not None and getattr(j, "_fh", None) is not None:
        j._fh.flush()
        os.fsync(j._fh.fileno())
    import shutil
    shutil.copy(persistence.journal_path(blend), os.path.join(OUT, "scene.hsj"))
json.dump(info, open(os.path.join(OUT, "expect.json"), "w"), indent=0)
print("GENERATED")
sys.stdout.flush()
if CRASH:
    os._exit(0)                     # ends like a crash: no save, the journal stays
'''


def main():
    blender = sys.argv[1]
    work = os.path.join(tempfile.gettempdir(), "hindsight_beta2_fixtures")   # the journal keeps this path
    shutil.rmtree(work, ignore_errors=True)
    build = os.path.join(work, "build")
    os.makedirs(build)
    tar = subprocess.run(["git", "-C", ROOT, "archive", TAG, "hindsight", "tests"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(build)
    script = os.path.join(work, "gen.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(GEN)
    for name, mode, crash in CASES:
        out = os.path.join(work, name)
        os.makedirs(out)
        env = dict(os.environ, BLENDER_USER_RESOURCES=os.path.join(work, "user_" + name))
        r = subprocess.run([blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python", script, "--",
                            build, out, mode, "1" if crash else "0"], capture_output=True, text=True, env=env,
                           timeout=600)
        if "GENERATED" not in r.stdout:
            sys.exit(f"{name}: beta 2 couldn't make its file:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        dst = os.path.join(HERE, name)
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(out, dst)
        print(name, sorted(os.listdir(dst)))


if __name__ == "__main__":
    main()
