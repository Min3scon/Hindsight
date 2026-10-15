"""History from saved versions, core (no bpy): which files count as saved versions, and the steps
built between their meshes (see IMPORTER.md).

- Finding versions: Blender backups, autosaves of this file, numbered saves, git commits; similarly
  named files are never offered; the open file itself never; nothing is written while looking.
- Steps: none between identical meshes; deltas for changed channels (bit-exact, -0.0 and NaN
  included); a topology step when counts or topology differ; played back both ways through History
  every version comes back exactly; identical versions add nothing and are listed.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import math
import shutil
import tempfile
import time
import traceback
from types import SimpleNamespace

import numpy as np

from hindsight.core import importer as I
from hindsight.core.history import History, MeshState
from hindsight.core.types import (CORNER_EDGE_KEY, CORNER_VERT_KEY, EDGE_VERTS_KEY, FACE_OFFSETS_KEY,
                                  MASK_KEY, POSITION_KEY, TOPO, DELTA, Snapshot)


def grid(n=4, z=0.0):
    """An n x n grid of quads as a Snapshot (positions, topology, a mask and a colour)."""
    vs = [(x, y, z) for y in range(n + 1) for x in range(n + 1)]
    faces = [(y * (n + 1) + x, y * (n + 1) + x + 1, (y + 1) * (n + 1) + x + 1, (y + 1) * (n + 1) + x)
             for y in range(n) for x in range(n)]
    edges = sorted({tuple(sorted((f[i], f[(i + 1) % 4]))) for f in faces for i in range(4)})
    eidx = {e: i for i, e in enumerate(edges)}
    cv = [v for f in faces for v in f]
    ce = [eidx[tuple(sorted((f[i], f[(i + 1) % 4])))] for f in faces for i in range(4)]
    counts = {"POINT": len(vs), "EDGE": len(edges), "FACE": len(faces), "CORNER": len(cv)}
    arrays = {POSITION_KEY: np.array(vs, np.float32).ravel(),
              EDGE_VERTS_KEY: np.array(edges, np.int32).ravel(),
              CORNER_VERT_KEY: np.array(cv, np.int32), CORNER_EDGE_KEY: np.array(ce, np.int32),
              FACE_OFFSETS_KEY: np.arange(0, len(cv) + 1, 4, dtype=np.int32),
              MASK_KEY: np.zeros(len(vs), np.float32)}
    return Snapshot(counts, arrays)


def copy(s):
    return Snapshot(dict(s.counts), {k: v.copy() for k, v in s.arrays.items()})


def same(a: Snapshot, b: Snapshot) -> bool:
    return I.snapshot_digest(a) == I.snapshot_digest(b)


def touch(path, t, data=b"BLENDER-v500"):
    with open(path, "wb") as f:
        f.write(data)
    os.utime(path, (t, t))


def listing(root):
    return sorted(os.path.relpath(os.path.join(d, f), root) for d, _ds, fs in os.walk(root) for f in fs)


def test_finding_versions():
    root = tempfile.mkdtemp(prefix="hs_import_")
    try:
        folder, auto = os.path.join(root, "work"), os.path.join(root, "tmp")
        os.makedirs(folder)
        os.makedirs(auto)
        t0 = 1_760_000_000
        blend = os.path.join(folder, "robot_003.blend")
        touch(blend, t0 + 500)
        touch(blend + "1", t0 + 400)
        touch(blend + "2", t0 + 300)
        touch(os.path.join(folder, "robot_002.blend"), t0 + 200)
        touch(os.path.join(folder, "robot_001.blend"), t0 + 100)
        touch(os.path.join(folder, "robot_old.blend"), t0 + 50)          # similar name: not offered
        touch(os.path.join(folder, "robots.blend"), t0 + 50)
        touch(os.path.join(folder, "robot_003.blend.bak"), t0 + 50)
        touch(os.path.join(auto, "robot_003_4242_autosave.blend"), t0 + 450)
        touch(os.path.join(auto, "robot_003_autosave.blend"), t0 + 350)
        touch(os.path.join(auto, "robot_002_autosave.blend"), t0 + 50)   # another file's autosave
        touch(os.path.join(auto, "4242_autosave.blend"), t0 + 50)        # an unsaved file's autosave
        before = listing(root)
        vs = I.find_versions(blend, [auto, auto], git_exe=None)
        names = [(os.path.basename(v.path), v.kind) for v in vs]
        assert names == [("robot_001.blend", "numbered"), ("robot_002.blend", "numbered"),
                         ("robot_003.blend2", "backup"), ("robot_003_autosave.blend", "autosave"),
                         ("robot_003.blend1", "backup"), ("robot_003_4242_autosave.blend", "autosave")], names
        assert [v.time for v in vs] == sorted(v.time for v in vs)
        assert listing(root) == before, "looking wrote something"
        # a file without a number: robot.blend goes with robot1.blend (what Save Incremental makes from it)
        b2 = os.path.join(folder, "robot.blend")
        touch(b2, t0)
        touch(os.path.join(folder, "robot1.blend"), t0 - 10)
        got = sorted(os.path.basename(v.path) for v in I.find_versions(b2, [], None))
        assert got == ["robot1.blend"], got
        # nothing around it: nothing found, nothing written
        lone = os.path.join(root, "lone")
        os.makedirs(lone)
        b3 = os.path.join(lone, "sculpt.blend")
        touch(b3, t0)
        before = listing(root)
        assert I.find_versions(b3, [auto], None) == []
        assert listing(root) == before
        assert I.find_versions(os.path.join(lone, "missing.blend"), [auto], None) == []
        assert I.find_versions("", [auto], None) == []
    finally:
        shutil.rmtree(root)
    print("PASS test_finding_versions")


def test_git_versions():
    calls = []
    sha1, sha2 = "a" * 40, "b" * 40

    def run(cmd, **kw):
        calls.append(cmd)
        if "rev-parse" in cmd:
            return SimpleNamespace(returncode=0, stdout="/repo\n")
        if "log" in cmd:
            out = (f"\x01{sha2}\x001760000200\x00Shape the head\n\nart/robot.blend\n"
                   f"\x01{sha1}\x001760000100\x00First blockout\n\nart/old_name.blend\n")
            return SimpleNamespace(returncode=0, stdout=out)
        return SimpleNamespace(returncode=1, stdout="")
    root = tempfile.mkdtemp(prefix="hs_git_")
    try:
        blend = os.path.join(root, "robot.blend")
        touch(blend, 1_760_000_300)
        orig = os.path.relpath
        I.os.path.relpath = lambda p, start=None: "art/robot.blend"
        try:
            vs = I.git_versions(blend, "git", run)
        finally:
            I.os.path.relpath = orig
        assert [(v.commit, v.relpath, v.subject, v.time) for v in vs] == [
            (sha2, "art/robot.blend", "Shape the head", 1760000200.0),
            (sha1, "art/old_name.blend", "First blockout", 1760000100.0)], vs
        assert I.step_label(vs[0]).startswith("Saved version, committed ")
        assert all("--follow" in c for c in calls if "log" in c)
        # not a repository, or no git: nothing
        assert I.git_versions(blend, "git", lambda cmd, **kw: SimpleNamespace(returncode=128, stdout="")) == []
        assert I.git_versions(blend, None, run) == []

        def boom(cmd, **kw):
            raise OSError("git not found")
        assert I.git_versions(blend, "git", boom) == []
    finally:
        shutil.rmtree(root)
    print("PASS test_git_versions")


def test_steps_between_versions():
    v0 = grid()
    assert I.step_between(v0, copy(v0), "x", "o", 0.0) is None, "identical: no step"
    v1 = copy(v0)
    v1.arrays[POSITION_KEY][5] = -0.0                      # -0.0 differs from 0.0, bit for bit
    v1.arrays[POSITION_KEY][7] = np.float32(math.nan)
    v1.arrays[MASK_KEY][3] = 0.5
    s1 = I.step_between(v0, v1, "Saved version, x", "o", 1.0)
    assert s1.kind == DELTA and sorted(d.key for d in s1.deltas) == sorted([POSITION_KEY, MASK_KEY])
    assert I.summary(s1) == "shape, masks changed", I.summary(s1)
    v2 = copy(v1)
    v2.arrays["Col|POINT|FLOAT_COLOR"] = np.ones(v2.counts["POINT"] * 4, np.float32)   # a colour added
    del v2.arrays[MASK_KEY]                                                           # the mask removed
    s2 = I.step_between(v1, v2, "y", "o", 2.0)
    assert s2.kind == DELTA and I.summary(s2) == "masks, colours changed", I.summary(s2)
    only_masks = I.step_between(v1, v2, "y", "o", 2.0, keep=lambda k: not k.startswith("Col|"))
    assert [d.key for d in only_masks.deltas] == [MASK_KEY], "channels left out by keep"
    uv = copy(v1)
    uv.arrays["UVMap|CORNER|FLOAT2"] = np.ones(uv.counts["CORNER"] * 2, np.float32)
    assert I.step_between(v1, uv, "y", "o", 2.0, keep=lambda k: not k.startswith("UVMap|")) is None
    v3 = grid(6, z=1.0)                                     # a different topology
    v3.arrays["Col|POINT|FLOAT_COLOR"] = np.zeros(v3.counts["POINT"] * 4, np.float32)
    s3 = I.step_between(v2, v3, "z", "o", 3.0)
    assert s3.kind == TOPO and I.summary(s3) == "topology changed: 16 to 36 faces", I.summary(s3)
    # played back through a History, both ways: every version exactly
    versions = [v0, v1, v2, v3]
    h = History("o")
    for n, s in enumerate((s1, s2, s3), 1):
        s.seq = n
        h.push(s)
    states = {"o": MeshState(dict(v3.counts), {k: v.copy() for k, v in v3.arrays.items()})}
    for pos in (2, 0, 3, 1, 0, 2, 3):
        h.goto(pos, states)
        got = states["o"].to_snapshot()
        assert same(got, versions[pos]), f"position {pos}"
    print("PASS test_steps_between_versions")


def test_plan():
    t0 = 1_760_000_000
    va = I.Version("a.blend2", "backup", t0, "Blender backup (.blend2)")
    vb = I.Version("a.blend1", "backup", t0 + 60, "Blender backup (.blend1)")
    vc = I.Version("a_autosave.blend", "autosave", t0 + 120, "Autosave")
    g0 = grid()
    g1 = copy(g0)
    g1.arrays[POSITION_KEY][2] += 1.0
    now = copy(g1)
    now.arrays[POSITION_KEY][4] += 1.0
    rock = grid(2)
    p = I.plan([(va, {"sculpt": g0, "rock": rock}), (vb, {"sculpt": copy(g0)}), (vc, {"sculpt": g1, "rock": rock})],
               {"sculpt": now, "rock": copy(rock), "new": grid(3)}, "Changes since the last save")
    labels = [s.label for s in p.steps["sculpt"]]
    assert labels == [I.step_label(vc), "Changes since the last save"], labels
    assert labels[0] == "Saved version, " + time.strftime("%Y-%m-%d %H:%M", time.localtime(t0 + 120))
    assert [s.extra["source"] for s in p.steps["sculpt"]] == ["Autosave", "this file"]
    assert "rock" not in p.steps, "the rock never changed: nothing to add"
    assert p.missing == ["new"], p.missing
    assert [v.where for v in p.identical] == ["Blender backup (.blend1)"], p.identical
    # everything identical: nothing at all
    p = I.plan([(va, {"sculpt": g0}), (vb, {"sculpt": copy(g0)})], {"sculpt": copy(g0)}, "x")
    assert p.steps == {} and [v.where for v in p.identical] == ["Blender backup (.blend1)"]
    print("PASS test_plan")


def main():
    ok = True
    for t in (test_finding_versions, test_git_versions, test_steps_between_versions, test_plan):
        try:
            t()
        except Exception:
            ok = False
            print("FAIL", t.__name__)
            traceback.print_exc()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
