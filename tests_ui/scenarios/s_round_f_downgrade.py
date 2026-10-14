"""Round F, going back: open files saved by the new build with an OLD build (beta 1).

Format 3 (beta 2) changed how steps are stored, so beta 1 can't play those steps back. What must
hold is that going back doesn't destroy anything: beta 1 opens the file without crashing, shows the
history with its saved length and cursor, survives a jump attempt, and when the file is simply
opened and saved again, the history comes through untouched (the new build checks that afterwards
with round_f_check on the copies written here).

args: src=<folder with new.json and the new build's files>  out=<folder for the re-saved copies>
"""
import json
import os
import shutil
import traceback

import bpy

import common as C


def run(d):
    src, out = d.args["src"], d.args["out"]
    exp = json.load(open(os.path.join(src, "new.json"), encoding="utf-8"))
    os.makedirs(out, exist_ok=True)
    work = os.path.join(d.out, "files")
    os.makedirs(work, exist_ok=True)
    yield 1.0
    C.dismiss_splash(d)
    yield 0.3
    rt = C.mod("runtime")
    jump_errors = {}
    for name, meta in exp["files"].items():
        side = name[:-6] + "_hindsight"
        # 1) open a scratch copy, look, and try a jump
        dst = os.path.join(work, name)
        shutil.copyfile(os.path.join(src, name), dst)
        if os.path.isdir(os.path.join(src, side)):
            shutil.copytree(os.path.join(src, side), os.path.join(work, side), dirs_exist_ok=True)
        with d.override():
            bpy.ops.wm.open_mainfile(filepath=dst, load_ui=False)
        yield 1.0
        ob = bpy.data.objects.get("Blob")
        h = rt.history_of(ob) if ob else None
        got = None if h is None else (len(h.entries), h.cursor)
        d.check(f"b1 opens {name}: history shown with the saved length and cursor",
                got == (meta["steps"], meta["cursor"]), (got, meta))
        if h is not None:
            with d.override():
                bpy.context.view_layer.objects.active = ob
                ob.select_set(True)
                try:
                    rt.goto(bpy.context, 0)
                    rt.goto(bpy.context, meta["cursor"])
                except Exception:
                    jump_errors[name] = traceback.format_exc(limit=1)[-300:]
            yield 0.3
        d.check(f"b1 opens {name}: Blender still running after a jump attempt", bpy.data.objects.get("Blob") is not None)
        # 2) open a fresh copy and save it again without touching anything
        clean = os.path.join(out, name)
        shutil.copyfile(os.path.join(src, name), clean)
        if os.path.isdir(os.path.join(src, side)):
            shutil.copytree(os.path.join(src, side), os.path.join(out, side), dirs_exist_ok=True)
        with d.override():
            bpy.ops.wm.open_mainfile(filepath=clean, load_ui=False)
        yield 1.0
        with d.override():
            bpy.ops.wm.save_mainfile(filepath=clean)
        yield 0.5
    shutil.copyfile(os.path.join(src, "new.json"), os.path.join(out, "new.json"))
    d.note("beta1_jump_errors", jump_errors)
    d.check("b1 saved every file again", all(os.path.isfile(os.path.join(out, n)) for n in exp["files"]))
