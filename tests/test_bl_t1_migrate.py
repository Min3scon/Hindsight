"""Tier 1 gate, older files (gate item RELOAD.MIGRATE), headless.

Files saved by earlier builds, written by those builds' own code (format 3: the compression build,
git fc2b6d3; format 1: beta 1, the release zip in .tmp/old/b1.zip) with a sculpt history, are
opened with this build: the old history loads and plays back exactly, Tier 1 recording works on top
(the full set of gate checks), and after saving in the current format and reopening everything still
plays back exactly. The old files are made fresh in .tmp by a separate Blender run of the old build
(never committed: a .blend keeps the path it was saved to).

Run: blender -b --factory-startup --python tests/test_bl_t1_migrate.py (or tests/run_tests.py).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import io
import json
import subprocess
import tarfile
import zipfile

import bpy

import tests.harness as H
from tests import oracle as O
from tests import t1_gate_lib as L
from tests import t1_ops as T
from tests.gate import covers
from hindsight.bl import datarec, idmap, runtime
from hindsight.core.types import SETTINGS

C = bpy.context
ROOT = H.ROOT
WORK = os.path.join(ROOT, ".tmp", "migrate")
ROWS = ("obj.transforms", "obj.parenting", "obj.constraints", "obj.collections", "obj.addremove", "obj.editsteps")
OLD = {"format3": ("git", "fc2b6d3"), "beta1": ("zip", os.path.join(ROOT, ".tmp", "old", "b1.zip"))}

GEN = r'''
import sys, json
sys.path.insert(0, {build!r})
sys.path.append({root!r})
import bpy
import numpy as np
import hindsight
bpy.ops.wm.read_factory_settings(use_empty=True)
hindsight.register()
from hindsight.bl import runtime
from tests import t1_ops as T
from tests import oracle as O
T.build_scene()
ob = bpy.data.objects["B"]
T.select("B")
bpy.ops.object.mode_set(mode="SCULPT")
runtime.check_mode_transition()
states = [O.mesh_state(ob.data)["co"]]
rng = np.random.default_rng(5)
for k in range(3):
    co = np.empty(len(ob.data.vertices) * 3, np.float32)
    ob.data.vertices.foreach_get("co", co)
    idx = rng.choice(len(co), 12, replace=False)
    co[idx] += rng.normal(0, 0.05, 12).astype(np.float32)
    ob.data.vertices.foreach_set("co", co)
    ob.data.update()
    assert runtime.commit(ob) is not None, "the old build recorded no step"
    states.append(O.mesh_state(ob.data)["co"])
bpy.ops.object.mode_set(mode="OBJECT")
runtime.check_mode_transition()
bpy.ops.wm.save_as_mainfile(filepath={blend!r})
json.dump({{"states": states, "version": getattr(hindsight, "bl_info", {{}}).get("version")}},
          open({blend!r}[:-6] + ".json", "w"))
print("GENERATED")
'''


def old_build(tag) -> str:
    """The old build's package folder in .tmp/old_builds/<tag>/ (made once)."""
    kind, src = OLD[tag]
    base = os.path.join(ROOT, ".tmp", "old_builds", tag)
    pkg = os.path.join(base, "hindsight")
    if os.path.isfile(os.path.join(pkg, "__init__.py")):
        return base
    os.makedirs(pkg, exist_ok=True)
    if kind == "git":
        tar = subprocess.run(["git", "-C", ROOT, "archive", src, "hindsight"], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            t.extractall(base)
    else:
        assert os.path.isfile(src), f"{src} is missing (the beta 1 release zip, used by Round F too)"
        with zipfile.ZipFile(src) as z:
            z.extractall(pkg)
    return base


def old_file(tag) -> tuple:
    """(path of a .blend saved by the old build, expected positions per step)."""
    d = os.path.join(WORK, tag)
    blend = os.path.join(d, "scene.blend")
    if not os.path.isfile(blend):
        os.makedirs(d, exist_ok=True)
        script = os.path.join(d, "gen.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write(GEN.format(build=old_build(tag), root=ROOT, blend=blend))
        env = dict(os.environ)
        r = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", "--python-exit-code", "1",
                            "--python", script], capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace", timeout=600)
        assert "GENERATED" in r.stdout, f"the {tag} build couldn't make its file:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}"
    with open(blend[:-6] + ".json", encoding="utf-8") as fh:
        return blend, json.load(fh)["states"]


def open_old(blend):
    def setup():
        H.fresh()
        bpy.ops.wm.open_mainfile(filepath=blend)
    return setup


def check_old_history(want):
    """The sculpt history the old build recorded, at every position, both ways, bit for bit."""
    b = bpy.data.objects["B"]
    h = runtime.history_of(b)
    assert h is not None and len(h.entries) == len(want) - 1, (None if h is None else len(h.entries))
    for pos in list(range(len(want))) + list(range(len(want) - 1, -1, -1)) + [len(want) - 1]:
        runtime._goto_history(h, pos, True)
        assert O.mesh_state(b.data)["co"] == want[pos], f"old sculpt step {pos}"


def migrate(tag):
    blend, want = old_file(tag)
    for row in ROWS:
        open_old(blend)()
        check_old_history(want)
        r = L.Run(T.sequences()[row], setup=open_old(blend))
        L.check_steps(r)
        L.recheck(r)
        out = os.path.join(WORK, tag, f"saved_{row.replace('.', '_')}.blend")
        SETTINGS.save_mode = "SIDECAR"
        T.select()
        bpy.ops.wm.save_as_mainfile(filepath=out)
        bpy.ops.wm.open_mainfile(filepath=out)
        datarec.FORCE_SCOPE[0] = "EVERYTHING"
        L.recheck(r)
        if bpy.data.objects.get("B") is not None:
            h = runtime.history_of(bpy.data.objects["B"])
            runtime._goto_history(h, len(h.entries), True)


@covers(ROWS, "RELOAD.MIGRATE")
def test_format3_file():
    migrate("format3")


@covers(ROWS, "RELOAD.MIGRATE")
def test_beta1_file():
    migrate("beta1")


for fn in (test_format3_file, test_beta1_file):
    H.run(fn)
H.finish()
