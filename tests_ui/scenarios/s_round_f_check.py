"""Round F, part 2: open files written by another build and check them position by position.

Run with the build under test installed. args: src=<folder> label=<label of the build that wrote
the files> (reads <src>/<label>.json from s_round_f_make). Works on old builds too (reverse test).
For every file: history length and cursor as saved, the mesh at the cursor bit-exact, every
position bit-exact after a jump, no error notes, no Python errors.
"""
import hashlib
import json
import os
import shutil

import bpy

import common as C


def mesh_hash(ob):
    snap = C.mod("meshio").read_snapshot(ob.data, sculpt_only=False)
    h = hashlib.blake2b(digest_size=16)
    for k in sorted(snap.arrays):
        h.update(k.encode())
        h.update(snap.arrays[k].tobytes())
    return h.hexdigest()


def run(d):
    src, label = d.args["src"], d.args["label"]
    exp = json.load(open(os.path.join(src, f"{label}.json"), encoding="utf-8"))
    work = os.path.join(d.out, "files")
    os.makedirs(work, exist_ok=True)
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    rt = C.mod("runtime")
    mark = d.console_mark()
    for name, meta in exp["files"].items():
        # work on a copy (with its history folder) so the source files stay pristine for other runs
        dst = os.path.join(work, name)
        shutil.copyfile(os.path.join(src, name), dst)
        side = os.path.join(src, name[:-6] + "_hindsight")
        if os.path.isdir(side):
            shutil.copytree(side, dst[:-6] + "_hindsight", dirs_exist_ok=True)
        with d.override():
            bpy.ops.wm.open_mainfile(filepath=dst, load_ui=False)
        yield 1.0
        ob = bpy.data.objects.get("Blob")
        h = rt.history_of(ob) if ob else None
        got = None if h is None else (len(h.entries), h.cursor)
        d.check(f"{label} {name}: history loaded with the saved length and cursor",
                got == (meta["steps"], meta["cursor"]), (got, meta))
        if h is None:
            continue
        d.check(f"{label} {name}: mesh at the cursor bit-exact", mesh_hash(ob) == exp["hashes"][h.cursor])
        with d.override():
            bpy.context.view_layer.objects.active = ob
            ob.select_set(True)
        bad = []
        for p in list(range(meta["steps"] + 1)) + [0, meta["steps"], meta["cursor"]]:
            with d.override():
                rt.goto(bpy.context, p)
            if mesh_hash(ob) != exp["hashes"][p]:
                bad.append(p)
        d.check(f"{label} {name}: every position bit-exact after a jump", not bad, bad)
        notes = getattr(rt, "load_notes", [])
        errors = [t for s, t in notes if s == "ERROR"] + ([rt.missing_message[0]] if rt.missing_message[0] else [])
        d.check(f"{label} {name}: no load errors", not errors, errors)
        rep = getattr(rt.reg(), "load_report", None)
        if rep:
            d.note(f"{name}_load_report", rep)
    log = d.console_since(mark)
    d.check(f"{label}: no Python errors", "Traceback" not in log, log[-1500:])
