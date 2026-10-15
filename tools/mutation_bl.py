"""Mutation testing of the Blender-side recording code (adapters, data recorder, global timeline,
mesh reads): break it on purpose, one change at a time, and check the tests notice, in real Blender.

    python tools/mutation_bl.py [--blender all|5.1.2] [--only N,M]

Each mutant is a copy of hindsight/ and tests/ under .tmp/mutants_bl/<n>/ with one edit; the listed
test files run inside Blender against it. A mutant is killed when a test fails. A mutant that can't
change behaviour is listed with a reason (`equivalent`) instead of being left to survive. Writes one
result per Blender to .tmp/results/tools/mutation_bl/<blender>/ for tools/make_evidence.py (gate item
MUT, for the rows each mutant belongs to) and exits 1 if any mutant survived without a reason.
"""
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".tmp", "mutants_bl")
RESULTS = os.path.join(ROOT, ".tmp", "results", "tools", "mutation_bl")
T1 = ["obj.transforms", "obj.parenting", "obj.constraints", "obj.collections", "obj.addremove", "obj.editsteps"]
TESTS = ("test_bl_t1_gate.py", "test_bl_t1_faults.py", "test_bl_tier1.py", "test_bl_adapters.py")

# (file under hindsight/, exact text, replacement, what it breaks, rows, reason if equivalent)
MUTANTS = [
    ("bl/adapters.py", "            if path not in values:\n                continue",
     "            if path not in values or path == 'location':\n                continue",
     "RNA write skips location", ["obj.transforms"], None),
    ("bl/adapters.py", ' ("parent_vertices", "value", "parenting"),', "",
     "vertex parent indices not recorded (D31)", ["obj.parenting"], None),
    ("bl/adapters.py", '    "rotation_mode": ("rotation_euler", "rotation_quaternion", "rotation_axis_angle"),',
     '    "rotation_mode": (),', "rotation values not rewritten after the rotation mode", ["obj.transforms"], None),
    ("bl/adapters.py", "        return mathutils.Matrix(v)", "        return v",
     "matrices written as nested lists (transposed)", ["obj.parenting"], None),
    ("bl/adapters.py", "        return _F64.pack(a) == _F64.pack(b)", "        return a == b",
     "float values compared with == (-0.0 equals 0.0, NaN never equal)", ["obj.transforms"], None),
    ("bl/adapters.py", "    out = {f.data_path for f in ad.drivers}", "    return set()",
     "animated and driven paths recorded", ["obj.transforms", "obj.constraints"], None),
    ("bl/adapters.py", "                if c is not None:\n                    try:\n                        c.objects.link(idb)",
     "                if False:\n                    try:\n                        c.objects.link(idb)",
     "collections an object was in not put back", ["obj.collections"], None),
    ("bl/adapters.py", "                idb.hide_set(bool(values[\"hide\"]))", "                pass",
     "hidden state (H) not put back", ["obj.collections"], None),
    ("bl/adapters.py", "    for c, w in zip(ob.constraints, want):\n        set_struct_values(c, w.get(\"values\") or {})",
     "    for c, w in zip(ob.constraints, want):\n        pass", "constraint settings not put back",
     ["obj.constraints"], None),
    ("bl/adapters.py", "    if have != [(w[\"type\"], w[\"name\"]) for w in want]:",
     "    if False:", "constraint stack not rebuilt", ["obj.constraints"], None),
    ("bl/adapters.py", "        for uid in sorted(set(have) - want):\n            _remove_object(have[uid])",
     "        for uid in sorted(set(have) - want):\n            pass", "added objects not removed again",
     ["obj.addremove"], None),
    ("bl/adapters.py", "    set_struct_values(ob, props)                 # exact location, rotation, scale and the rest",
     "    pass", "objects brought back without their exact settings (D18)", ["obj.addremove"], None),
    ("bl/adapters.py", '"mode", "select", "active_shape_key_index", "dimensions"})',
     '"mode", "select", "active_shape_key_index"})',
     "objects brought back set their Dimensions (wrong scale, D26)", ["obj.addremove"], None),
    ("bl/adapters.py", "            elif same(normalise(cur), v):\n                continue",
     "            elif False:\n                continue", "unchanged settings written again",
     ["obj.constraints", "obj.addremove"],
     "only spares Blender's error reports for no-op writes; the values end up the same"),
    ("bl/adapters.py", "            _write_collection_tree(idb, values[\"collections\"])", "            pass",
     "collection tree not written (D19)", ["obj.collections"], None),
    ("bl/adapters.py", "                    ob.parent = par\n", "                    pass\n",
     "parents of objects brought back not set", ["obj.addremove", "obj.parenting"], None),
    ("bl/datarec.py", "        if b is MISSING and not adapters.kind_on(adapters.kind_of(*k) or \"\"):\n            continue",
     "        if False:\n            continue", "a data kind switched off read as removed",
     ["obj.transforms", "obj.collections"], None),
    ("bl/datarec.py", "        if mesh and uid in _MESH_DIRTY:", "        if False and uid in _MESH_DIRTY:",
     "Object Mode mesh changes not recorded (D14)", ["obj.transforms"], None),
    ("bl/datarec.py", "            step.extra = dict(step.extra or {}, op=op_first)", "            pass",
     "one operation on several objects is several global positions (D16)",
     ["obj.transforms", "obj.collections"], None),
    ("bl/datarec.py", "            want[(r.adapter, p)] = (n, o) if inverse else (o, n)",
     "            want[(r.adapter, p)] = (o, n)", "Blender's undo matched to the wrong neighbour",
     T1, None),
    ("bl/datarec.py", "            order = ((h.cursor, False), (h.cursor - 1, True)) if direction == \"redo\" else \\",
     "            order = ((h.cursor - 1, True), (h.cursor, False)) if direction == \"redo\" else \\",
     "Blender's redo matched as an undo when both neighbours match (D22)", T1, None),
    ("bl/datarec.py", "            mesh_step = rt._observe(idb, st, True, \"Undo\", \"OBJECT\", deep=True)",
     "            mesh_step = rt._observe(idb, st, True, \"Undo\", \"OBJECT\", deep=\"corners\")",
     "undo of an edges-only change not seen (D23)", ["obj.editsteps"], None),
    ("bl/datarec.py", "            if p.startswith(\"body:\"):\n                continue        # stored with the step",
     "            if False:\n                continue        # stored with the step",
     "Blender's undo of adding or deleting objects not matched (D24)", ["obj.addremove"], None),
    ("core/globaltl.py", "    for _seq, hid, i in sorted(undo, reverse=True):",
     "    for _seq, hid, i in sorted(undo, key=lambda x: x[1]):", "global jumps not in step order (D17)", T1, None),
    ("core/globaltl.py", "            cur[hid] = max(cur[hid], i + 1)", "            cur[hid] = i",
     "global jump one step short", T1, None),
    ("bl/runtime.py", "        u = any_uid(ob)\n        if u is not None and not is_excluded(ob):",
     "        u = obj_uid(ob)\n        if u is not None and not is_excluded(ob):",
     "empties left off the global timeline (D20)", ["obj.transforms", "obj.parenting"], None),
    ("bl/runtime.py", "    return commit(obj, label=label, mode=\"EDIT\", full=True, _edit=True)",
     "    return None", "Edit Mode operations not recorded step by step", ["obj.editsteps"], None),
    ("bl/meshio.py", '    cv = _corner_ints(me, ".corner_vert", "vertex_index", c["CORNER"])',
     '    cv = _corner_ints(me, ".corner_edge", "vertex_index", c["CORNER"])',
     "corner vertices read from the wrong array", ["obj.editsteps", "obj.transforms"], None),
    ("bl/meshio.py", "    if deep == \"corners\":\n        return True", "    if deep:\n        return True",
     "deep topology check skips edges in Edit Mode", ["obj.editsteps"], None),
]


def make(n, fname, old, new):
    d = os.path.join(OUT, str(n))
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "hindsight"), os.path.join(d, "hindsight"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(d, "tests"),
                    ignore=shutil.ignore_patterns("__pycache__", "site"))
    path = os.path.join(d, "hindsight", fname)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if src.count(old) != 1:
        raise SystemExit(f"mutant {n}: text not found exactly once in {fname}: {old!r}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src.replace(old, new))
    return d


def run(exe, d, env):
    for t in TESTS:
        try:
            r = subprocess.run([exe, "-b", "--factory-startup", "--python-exit-code", "1",
                                "--python", os.path.join(d, "tests", t)], capture_output=True, text=True,
                               timeout=1200, env=env, cwd=d, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return t + " (timeout)"
        if r.returncode != 0 or "FAIL " in r.stdout:
            return t
    return None


def main():
    args = sys.argv[1:]
    which = args[args.index("--blender") + 1] if "--blender" in args else "all"
    only = set(int(x) for x in args[args.index("--only") + 1].split(",")) if "--only" in args else None
    sys.path.insert(0, os.path.join(ROOT, "tests_ui"))
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import hs_ui
    from run_tests import _sandbox_env
    targets = [(tag, str(exe)) for tag, exe in hs_ui.select(hs_ui.find_blenders(), which)]
    survived_any = False
    for tag, exe in targets:
        env = _sandbox_env("mut-" + tag)
        rows = []
        t0 = time.time()
        for n, (fname, old, new, what, row_ids, reason) in enumerate(MUTANTS, 1):
            if only and n not in only:
                continue
            d = make(n, fname, old, new)
            killer = run(exe, d, env)
            ok = killer is not None or bool(reason)
            survived_any |= not ok
            print(f"[{tag}] {n:2d} {'killed by ' + killer if killer else ('equivalent: ' + reason if reason else 'SURVIVED')}"
                  f"  {fname}: {what}", flush=True)
            rows.append({"name": f"mutant {n}: {what}", "ok": ok, "killed_by": killer, "reason": reason,
                         "covers": [[r, "MUT"] for r in row_ids]})
            shutil.rmtree(d, ignore_errors=True)
        dst = os.path.join(RESULTS, tag)
        os.makedirs(dst, exist_ok=True)
        log = "\n".join(f"{r['name']}: " + (f"killed by {r['killed_by']}" if r["killed_by"] else
                                             f"equivalent ({r['reason']})" if r["reason"] else "SURVIVED")
                        for r in rows)
        with open(os.path.join(dst, "mutation_bl.log"), "w", encoding="utf-8") as fh:
            fh.write(log + "\n")
        with open(os.path.join(dst, "mutation_bl.json"), "w", encoding="utf-8") as fh:
            json.dump({"unit": "tools/mutation_bl.py", "blender": tag, "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "tests": rows, "log": "mutation_bl.log"}, fh, indent=1)
        print(f"[{tag}] {sum(1 for r in rows if r['killed_by'])}/{len(rows)} killed, "
              f"{sum(1 for r in rows if not r['ok'])} survived, {time.time() - t0:.0f} s")
    return 1 if survived_any else 0


if __name__ == "__main__":
    sys.exit(main())
