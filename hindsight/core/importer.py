"""History from saved versions of a .blend file, without bpy (see IMPORTER.md).

Finding the versions that exist on disk (Blender's backups, autosaves, numbered saves, git commits,
files the user adds), and building one step per version between the meshes read from them. Nothing
here reads a .blend or writes anywhere: the Blender side reads versions in a second Blender and
hands over Snapshots.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass, field

import numpy as np

from . import delta as D
from .types import (CHANNEL_TYPES, CORNER_EDGE_KEY, CORNER_VERT_KEY, DELTA, EDGE_VERTS_KEY,
                    FACE_OFFSETS_KEY, TOPO, Snapshot, Step, split_key)

TOPO_KEYS = (EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY)
BACKUPS_MAX = 32                    # Blender's Save Versions goes up to 32
GIT_TIMEOUT = 15
MODE = "OBJECT"                     # imported steps aren't tied to a mode


@dataclass
class Version:
    """One saved version of the open file."""
    path: str                       # the file to read ("" for a git commit until it's extracted)
    kind: str                       # backup, autosave, numbered, git, added
    time: float                     # modified time, or the commit time for git
    where: str                      # for the dialog: "Blender backup (.blend1)", "git commit abc1234"
    commit: str = ""
    relpath: str = ""               # path inside the git repository at that commit
    subject: str = ""               # commit message (first line)
    notes: list = field(default_factory=list)

    def key(self) -> str:
        return f"git:{self.commit}" if self.kind == "git" else "file:" + os.path.normcase(os.path.abspath(self.path))


def when_text(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def step_label(v: Version) -> str:
    """'Saved version, <date and time>'; a git version says it is the commit time."""
    if v.kind == "git":
        return f"Saved version, committed {when_text(v.time)}"
    return f"Saved version, {when_text(v.time)}"


# ----------------------------------------------------------------------------- finding versions
def _stem(blend: str) -> str:
    name = os.path.basename(blend)
    return name[:-6] if name.lower().endswith(".blend") else name


def backups(blend: str) -> list:
    out = []
    for n in range(1, BACKUPS_MAX + 1):
        p = blend + str(n)
        if os.path.isfile(p):
            out.append(Version(p, "backup", os.path.getmtime(p), f"Blender backup (.blend{n})"))
    return out


def autosaves(blend: str, folders) -> list:
    """Blender's autosaves of this file: <name>_autosave.blend or <name>_<number>_autosave.blend."""
    pat = re.compile(r"^" + re.escape(_stem(blend)) + r"(?:_\d+)?_autosave\.blend$", re.IGNORECASE)
    out, seen = [], set()
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            p = os.path.join(folder, name)
            if pat.match(name) and os.path.isfile(p) and os.path.normcase(p) not in seen:
                seen.add(os.path.normcase(p))
                out.append(Version(p, "autosave", os.path.getmtime(p), "Autosave"))
    return out


_NUM = re.compile(r"^(.*?)(\d+)$")


def numbered(blend: str) -> list:
    """Numbered saves (File > Save Incremental): the same name with another number at the end.
    robot_003.blend goes with robot_004.blend; robot.blend with robot1.blend (what Blender makes).
    Other similar names (robot_old.blend, robots.blend) are never offered on their own."""
    folder = os.path.dirname(os.path.abspath(blend))
    stem = _stem(blend)
    m = _NUM.match(stem)
    if m:
        pat = re.compile(r"^" + re.escape(m.group(1)) + r"\d+\.blend$", re.IGNORECASE)
    else:
        pat = re.compile(r"^" + re.escape(stem) + r"\d+\.blend$", re.IGNORECASE)
    out = []
    me = os.path.normcase(os.path.abspath(blend))
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        p = os.path.join(folder, name)
        if pat.match(name) and os.path.normcase(os.path.abspath(p)) != me and os.path.isfile(p):
            out.append(Version(p, "numbered", os.path.getmtime(p), f"Numbered save ({name})"))
    return out


def _git(git_exe, args, run, cwd=None):
    r = run([git_exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=GIT_TIMEOUT, cwd=cwd)
    return r.stdout if r.returncode == 0 else None


def git_versions(blend: str, git_exe: str | None, run=subprocess.run) -> list:
    """Commits that changed the file (following renames), newest first as git lists them."""
    if not git_exe:
        return []
    folder = os.path.dirname(os.path.abspath(blend))
    try:
        top = _git(git_exe, ["-C", folder, "rev-parse", "--show-toplevel"], run)
        if not top:
            return []
        top = top.strip()
        rel = os.path.relpath(os.path.abspath(blend), top).replace(os.sep, "/")
        if rel.startswith(".."):
            return []
        log = _git(git_exe, ["-C", top, "log", "--follow", "--format=%x01%H%x00%ct%x00%s", "--name-only",
                             "--", rel], run)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    out = []
    for block in (log or "").split("\x01"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines or lines[0].count("\x00") != 2:
            continue
        sha, ct, subject = lines[0].split("\x00")
        path = lines[1].strip() if len(lines) > 1 else rel
        try:
            t = float(int(ct))
        except ValueError:
            continue
        out.append(Version("", "git", t, f"git commit {sha[:7]}", commit=sha, relpath=path,
                           subject=subject.strip(), notes=[top]))
    return out


def git_extract(v: Version, git_exe: str, dest: str, run=subprocess.run) -> bool:
    """Write the file as it was in commit v.commit to dest (inside the temporary folder)."""
    top = v.notes[0] if v.notes else None
    try:
        r = run([git_exe, "-C", top, "cat-file", "blob", f"{v.commit}:{v.relpath}"], capture_output=True,
                timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0 or not r.stdout:
        return False
    with open(dest, "wb") as f:
        f.write(r.stdout)
    v.path = dest
    return True


def added(path: str) -> Version:
    return Version(path, "added", os.path.getmtime(path), f"Added ({os.path.basename(path)})")


def find_versions(blend: str, autosave_folders=(), git_exe: str | None = None, run=subprocess.run) -> list:
    """Every saved version of the open file found on disk, oldest first. Lists files and asks git;
    reads and writes nothing."""
    if not blend or not os.path.isfile(blend):
        return []
    found = backups(blend) + autosaves(blend, autosave_folders) + numbered(blend) + git_versions(blend, git_exe, run)
    me = os.path.normcase(os.path.abspath(blend))
    out, seen = [], set()
    for v in found:
        if v.kind != "git" and os.path.normcase(os.path.abspath(v.path)) == me:
            continue
        if v.key() in seen:
            continue
        seen.add(v.key())
        out.append(v)
    out.sort(key=lambda v: (v.time, v.kind, v.where))
    return out


# ----------------------------------------------------------------------------- steps between versions
def snapshot_digest(snap: Snapshot) -> str:
    """Digest of a mesh: counts, topology and every channel (same as meshio.fingerprint)."""
    h = hashlib.blake2b(digest_size=16)
    h.update(repr(sorted(snap.counts.items())).encode())
    for key in sorted(snap.arrays):
        a = np.ascontiguousarray(snap.arrays[key])
        h.update(key.encode() + b"\0" + str(a.dtype).encode() + b"\0")
        h.update(a.view(np.uint8))
    return h.hexdigest()


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return a.dtype == b.dtype and a.shape == b.shape and np.array_equal(D.view_bits(a), D.view_bits(b))


def topology_changed(before: Snapshot, after: Snapshot) -> bool:
    return before.counts != after.counts or not all(_same(before.arrays.get(k), after.arrays.get(k))
                                                   for k in TOPO_KEYS)


def step_between(before: Snapshot, after: Snapshot, label: str, target: str, when: float,
                 keep=None) -> Step | None:
    """The step from one version's mesh to the next: a topology step when counts or topology differ,
    otherwise one delta per changed channel (as the recorder makes them; with keep, only the channels
    keep(key) accepts). None if nothing (kept) changed."""
    if topology_changed(before, after):
        return _topo_step(before, after, label, target, when)
    deltas = []
    for key in sorted((set(before.arrays) | set(after.arrays)) - set(TOPO_KEYS)):
        if keep is not None and not keep(key):
            continue
        old, new = before.arrays.get(key), after.arrays.get(key)
        _name, domain, dtn = split_key(key)
        native, width = CHANNEL_TYPES[dtn][0], CHANNEL_TYPES[dtn][1]
        n = before.counts[domain] * width
        if old is None:
            d = D.diff(key, np.zeros(n, native), new, (False, True))
        elif new is None:
            d = D.diff(key, old, np.zeros(n, native), (True, False))
        else:
            if old.size != new.size or old.dtype != new.dtype:
                return _topo_step(before, after, label, target, when)    # a channel changed type
            d = D.diff(key, old, new, (True, True))
        if d is not None:
            deltas.append(d)
    if not deltas:
        return None
    return Step(seq=0, label=label, mode=MODE, target=target, time=when, kind=DELTA, deltas=deltas)


def _topo_step(before: Snapshot, after: Snapshot, label: str, target: str, when: float) -> Step:
    return Step(seq=0, label=label, mode=MODE, target=target, time=when, kind=TOPO,
                before=Snapshot(dict(before.counts), {k: v.copy() for k, v in before.arrays.items()}),
                after=Snapshot(dict(after.counts), {k: v.copy() for k, v in after.arrays.items()}))


_KIND_WORDS = {"position": "shape", ".sculpt_mask": "masks", ".sculpt_face_set": "face sets",
               ".hide_vert": "hidden parts", ".hide_edge": "hidden parts", ".hide_poly": "hidden parts"}


def summary(step: Step) -> str:
    """What changed, in plain words: 'shape changed', 'topology changed: 64 to 90 faces'."""
    if step.kind == TOPO:
        a, b = step.before.counts["FACE"], step.after.counts["FACE"]
        return f"topology changed: {a:,} to {b:,} faces" if a != b else "topology changed"
    words = set()
    for d in step.deltas:
        name, _dom, dtn = split_key(d.key)
        words.add(_KIND_WORDS.get(name) or ("colours" if dtn in ("FLOAT_COLOR", "BYTE_COLOR") else "other mesh data"))
    order = ("shape", "masks", "face sets", "hidden parts", "colours", "other mesh data")
    return ", ".join(w for w in order if w in words) + " changed"


@dataclass
class Plan:
    """What an import adds: per object key, its steps oldest first; what was left out and why."""
    steps: dict = field(default_factory=dict)          # object key -> [Step]
    identical: list = field(default_factory=list)      # versions that added nothing
    missing: list = field(default_factory=list)        # object keys in no version


def plan(versions: list, current: dict, current_label: str, keep=None) -> Plan:
    """versions: [(Version, {object key: Snapshot})] oldest first; current: {object key: Snapshot}
    of the open file. Each object's first version is its start; each later version that differs
    from the one before it is a step; the last step leads to the mesh as it is now. keep: see
    step_between."""
    out = Plan()
    used = set()
    for key, now in current.items():
        chain = [(v, snaps[key]) for v, snaps in versions if key in snaps]
        if not chain:
            out.missing.append(key)
            continue
        steps = []
        prev = chain[0][1]
        for v, snap in chain[1:]:
            s = step_between(prev, snap, step_label(v), key, v.time, keep)
            if s is not None:
                s.extra = {"source": v.where, "summary": summary(s), "imported": True}
                steps.append(s)
                used.add(v.key())
                prev = snap
        s = step_between(prev, now, current_label, key, time.time(), keep)
        if s is not None:
            s.extra = {"source": "this file", "summary": summary(s), "imported": True}
            steps.append(s)
        if steps:
            out.steps[key] = steps
            used.add(chain[0][0].key())
    for v, _snaps in versions[1:]:
        if v.key() not in used:
            out.identical.append(v)
    return out
