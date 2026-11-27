"""Make the files tests/test_bl_older_builds.py opens (#68): a format 5 .blend saved by this build (v2/), and that file
as each older build left it after opening it, recording in it and saving it over itself, in each of its save modes.
The older builds are run as they were, by their own code: V1 (beta 2, the tag v1.0.0-beta.2, pure Python) and the
main-branch build (beta 3, commit 6168382, with its own engine wheel, 0.9.0).

    python3 tests/fixtures/older_builds/make_older_build_files.py BLENDER_4_2_BINARY_OR_BPY_PYTHON

Beta 2 supported Blender 4.2 to 5.1, so the files are made in 4.2. This build's engine must be importable (installed,
or on PYTHONPATH); the older builds get their own (none for beta 2). Each run is a process of its own:

- this build: a sculpt history on "S" (two strokes, a topology step from Edit Mode Subdivide, one more stroke) and
  two moves of "O" recorded with Record everything, saved in format 5;
- each older build, in each save mode: opens a copy of that file and notes what it loaded and said (expect.json),
  then makes a sculpt stroke on "S" and a history of its own on a new object "N" (two strokes), moves "O" (not
  recorded: Record everything is off), and saves over the file. That save leaves this build's file as scene.blend1
  (Save Versions); it is not kept, since it is v2/scene.blend byte for byte, and the test puts it back.

expect.json holds what each build read back (positions as float32 bytes in base64, locations as floats).
"""
import io
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
BUILDS = {"beta2": "v1.0.0-beta.2", "beta3": "6168382"}
MODES = {"folder": "SIDECAR", "embed": "EMBED", "both": "BOTH"}

COMMON = r'''
import sys, os, json, base64
CODE, BLEND, MODE = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, CODE)
import bpy
import numpy as np
import tests.harness as H          # the build's own test helpers
from hindsight.bl import datarec, runtime
from hindsight.core.types import SETTINGS


def pos(ob):
    a = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", a)
    return base64.b64encode(a.tobytes()).decode()


def strokes(ob, seeds, out):
    H.enter(ob, "SCULPT")
    for s in seeds:
        H.fake_stroke(ob, s, n=30)
        assert H.commit(ob) is not None, f"no stroke recorded on {ob.name}"
        out.append(pos(ob))
    H.enter(ob, "OBJECT")
'''

V2 = COMMON + r'''
H.fresh()
ob = H.make_mesh(100, name="S")
states = [pos(ob)]
strokes(ob, (10, 11), states)
H.enter(ob, "EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.subdivide()
H.enter(ob, "SCULPT")
assert runtime.history_of(ob).entries[-1].kind == "TOPO"
states.append(pos(ob))
strokes(ob, (12,), states)
o2 = H.make_mesh(50, name="O", location=(3.0, 0.0, 0.0))
from hindsight.bl import prefs
prefs.FORCE["record_scope"] = "EVERYTHING"
datarec.set_tracked(o2, True)
datarec.finish_operation("baseline")
locs = [list(o2.location)]
for dx in (0.5, -1.25):
    o2.location.x += dx
    bpy.context.view_layer.update()
    datarec.finish_operation("Move")
    locs.append(list(o2.location))
assert len(runtime.history_of(o2).entries) == 2
bpy.ops.wm.save_as_mainfile(filepath=BLEND, compress=True)
print("RESULT " + json.dumps({"blender": bpy.app.version_string, "S": states, "O": locs}))
sys.stdout.flush()
'''

OLD = COMMON + r'''
H.fresh()
SETTINGS.save_mode = MODE
bpy.ops.wm.open_mainfile(filepath=BLEND)
out = {"blender": bpy.app.version_string, "mode": MODE, "notes": [list(n) for n in runtime.load_notes],
       "histories": sorted(ob.name for ob in bpy.data.objects if runtime.history_of(ob) is not None)}
s, o = bpy.data.objects["S"], bpy.data.objects["O"]
after = []
strokes(s, (40,), after)
out["S"] = after[0]
o.location.x += 0.75
bpy.context.view_layer.update()
out["O"] = list(o.location)
n = H.make_mesh(80, name="N", location=(-3.0, 0.0, 0.0))
out["N"] = [pos(n)]
strokes(n, (50, 51), out["N"])
bpy.ops.wm.save_mainfile(filepath=BLEND)
print("RESULT " + json.dumps(out))
sys.stdout.flush()
'''


def run(blender, script, code, blend, mode="", env=None):
    path = os.path.join(os.path.dirname(blend), "gen.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(script)
    py = os.path.basename(blender).lower().startswith("python")
    cmd = ([blender] if py else [blender, "-b", "--factory-startup", "--python-exit-code", "1", "--python"])
    r = subprocess.run(cmd + [path, "--", code, blend, mode], capture_output=True, text=True, env=env, timeout=600,
                       encoding="utf-8", errors="replace")
    os.remove(path)
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    if not lines:
        sys.exit(f"{code} couldn't make its file:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    out = json.loads(lines[-1][7:])
    out["traceback"] = "Traceback" in r.stdout + r.stderr          # an error the build caught and printed
    return out


def old_build(work, name):
    """The older build's code, as git has it, with its own engine wheel for this platform if it has one."""
    code = os.path.join(work, "build_" + name)
    tar = subprocess.run(["git", "-C", ROOT, "archive", BUILDS[name], "hindsight", "tests"], capture_output=True,
                         check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(code)
    plat = {"win-amd64": "win_amd64", "linux-x86_64": "manylinux2014_x86_64"}.get(sysconfig.get_platform(), "?")
    wheels = os.path.join(code, "hindsight", "wheels")      # shortcut: Windows and Linux x86-64 only
    for w in (os.listdir(wheels) if os.path.isdir(wheels) else []):
        if plat in w:
            zipfile.ZipFile(os.path.join(wheels, w)).extractall(code)
    return code


def main():
    blender = sys.argv[1]
    work = os.path.join(tempfile.gettempdir(), "hindsight_older_build_fixtures")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(work, "v2"))
    v2_blend = os.path.join(work, "v2", "scene.blend")
    expect = {"v2": run(blender, V2, ROOT, v2_blend), "cases": {}}
    old_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}     # each older build has its own engine
    for name in BUILDS:
        code = old_build(work, name)
        for mode, setting in MODES.items():
            case = f"{name}_{mode}"
            blend = os.path.join(work, case, "scene.blend")
            os.makedirs(os.path.dirname(blend))
            shutil.copyfile(v2_blend, blend)
            got = run(blender, OLD, code, blend, setting, old_env)
            got["build"] = BUILDS[name]
            expect["cases"][case] = got
            os.remove(blend + "1")              # v2/scene.blend byte for byte; the test puts it back
            print(case, got["histories"], got["notes"], "traceback" if got["traceback"] else "")
    for d in ["v2"] + list(expect["cases"]):
        shutil.rmtree(os.path.join(HERE, d), ignore_errors=True)
        shutil.copytree(os.path.join(work, d), os.path.join(HERE, d))
    with open(os.path.join(HERE, "expect.json"), "w", encoding="utf-8") as fh:
        json.dump(expect, fh, indent=0)


if __name__ == "__main__":
    main()
