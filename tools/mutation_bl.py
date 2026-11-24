"""Mutation testing of the Blender-side recording code (adapters, data recorder, global timeline,
mesh reads): break it on purpose, one change at a time, and check the tests notice, in real Blender.

    python tools/mutation_bl.py [--blender all|5.1.2] [--only N,M]

Each mutant is a copy of hindsight/ and tests/ under .tmp/mutants_bl/<n>/ with one edit; the listed
test files run inside Blender against it. A mutant is killed when a test fails. A mutant that can't
change behaviour is listed with a reason (`equivalent`) instead of being left to survive. Writes one
result per Blender to .tmp/results/tools/mutation_bl/<blender>/ and exits 1 if any mutant survived without a reason.
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
TESTS = ("test_bl_t1_gate.py", "test_bl_t1_faults.py", "test_bl_t1_jumps.py", "test_bl_t1_added.py",
         "test_bl_t1_real_undo.py", "test_bl_t1_meshdata.py", "test_bl_t1_outside.py", "test_bl_tier1.py",
         "test_bl_adapters.py", "test_bl_rig_ignored.py")

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
    ("bl/adapters.py", '    out = {}\n\n    def add(f):\n        out.setdefault(f.data_path, set())', '    return {}\n    out = {}\n\n    def add(f):\n        out.setdefault(f.data_path, set())',
     "animated and driven paths recorded", ["obj.transforms", "obj.constraints"], None),
    ("bl/adapters.py", '                try:\n                    c.objects.link(idb)\n',
     '                try:\n                    pass\n',
     "collections an object was in not put back", ["obj.collections"], None),
    ("bl/adapters.py", '        if "hide" in values and values["hide"] is not MISSING:\n            _set_hidden(idb, values["hide"])', '        if "hide" in values and values["hide"] is not MISSING:\n            pass',
     "hidden state (H) not put back", ["obj.collections"], None),
    ("bl/adapters.py", '    for c, w in pairs:\n        set_struct_values(c, w.get("values") or {}, ob, f\'{base}constraints["{c.name}"].\')',
     '    for c, w in pairs:\n        pass', "constraint settings not put back",
     ["obj.constraints"], None),
    ("bl/adapters.py", '    if have == [(w["type"], w["name"]) for w in want]:\n        pairs = list(zip(st.constraints, want))',
     '    if True:\n        pairs = list(zip(st.constraints, want))', "constraint stack not rebuilt", ["obj.constraints"], None),
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
    ("bl/datarec.py", '        if mesh and uid in _MESH_DIRTY and not (', '        if False and uid in _MESH_DIRTY and not (',
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
    ("bl/runtime.py", '        u = any_uid(ob)\n        if u is not None and not excluded(ob):',
     '        u = obj_uid(ob)\n        if u is not None and not excluded(ob):',
     "empties left off the global timeline (D20)", ["obj.transforms", "obj.parenting"], None),
    ("bl/runtime.py", "    return commit(obj, label=label, mode=\"EDIT\", full=True, _edit=True)",
     "    return None", "Edit Mode operations not recorded step by step", ["obj.editsteps"], None),
    ("bl/meshio.py", 'CORNER_VERT_KEY: lambda: _corner_ints(me, ".corner_vert", "vertex_index", c["CORNER"]),',
     'CORNER_VERT_KEY: lambda: _corner_ints(me, ".corner_edge", "vertex_index", c["CORNER"]),',
     "corner vertices read from the wrong array", ["obj.editsteps", "obj.transforms"], None),
    ("bl/meshio.py", "    if deep == \"corners\":\n        return True", "    if deep:\n        return True",
     "deep topology check skips edges in Edit Mode", ["obj.editsteps"], None),
    ("bl/runtime.py", 'acc["wrote"] = acc["wrote"] or wrote_data or bool(meshes)',
     'acc["wrote"] = acc["wrote"] or wrote_data', "global jumps that change only meshes not on Blender's undo (D35)",
     ["obj.editsteps"], None),
    ("bl/datarec.py", 'if ob is None or ob.type != "MESH" or rt.mesh_fingerprint(ob) != (fb if back else fa):',
     'if ob is None or ob.type != "MESH":', "Blender's undo of a jump matched without its meshes (D35)",
     ["obj.editsteps"], None),
    ("bl/runtime.py", '    if not final or IN_HANDLER[0] or not datarec.can_push() or ob is None or ob.mode != "EDIT":',
     '    if True:', "a jump from Edit Mode not pushed in Object Mode (D35)", ["obj.editsteps"], None),
    ("bl/datarec.py", "    drop_undone_jumps()\n    _JUMPS.append(", "    _JUMPS.clear()\n    _JUMPS.append(",
     "only the newest jump can be undone (D35)", T1, None),
    ("bl/datarec.py", "    rt = _rt()\n    ob = idmap.find(uid)\n    if ob is None or is_linked(ob) or not isinstance(ob, bpy.types.Object):\n        return",
     "    return", "objects added while the scene is recorded not recorded (D36)", ["obj.addremove"], None),
    ("bl/adapters.py", "    return sorted((p, r) for p, (r, _a, _ap) in d.items() if idmap.find(r) is None)",
     "    return []", "no note about a reference to a deleted object (D37)", ["obj.parenting", "obj.constraints"], None),
    ("bl/datarec.py", '    if not adapters.MISSING_REFS:\n        adapters.drop_recreated_orphans()\n        return\n', '    adapters.drop_recreated_orphans()\n    return\n',
     "references not put back when the object comes back (D37)", ["obj.parenting", "obj.constraints"], None),
    ("bl/adapters.py", '    "parent": ("matrix_parent_inverse",),\n', "",
     "parent inverse not written again after the parent (D38)", ["obj.parenting"], None),
    ("bl/datarec.py", "    _MESH_USERS.update({ptr: [uid] for ptr, uid in owners.items()})",
     "    _MESH_USERS.update({ptr: [u for _n, u, _o in users[ptr]] for ptr in owners})",
     "every linked duplicate records the shared mesh (D39)", ["obj.addremove", "obj.editsteps"], None),
    ("bl/datarec.py", '    me = ob.data\n    if getattr(me, "is_editmode", False):\n        return True',
     '    return False', "a shared mesh read in the middle of an Edit Mode visit (D39)", ["obj.editsteps"], None),
    ("bl/runtime.py", " and not datarec.mesh_owned_elsewhere(obj))", ")",
     "Edit Mode steps recorded on a linked duplicate too (D39)", ["obj.editsteps"], None),
    ("bl/runtime.py", '        if mode == "EDIT" and _edit:\n', '        if False:\n',
     "Blender's Edit Mode undo recorded as a step, dropping later ones (D40)", ["obj.editsteps"], None),
    ("bl/idmap.py", "    if not _session:\n        return 0\n    n = 0", "    if True:\n        return 0\n    n = 0",
     "ids not put back after Blender's undo (D42)", T1, None),
    ("bl/runtime.py", 'datarec.note_jump(acc["hists"], pre, datarec.recorded_values(), acc["meshes"])',
     'datarec.note_jump(acc["hists"], acc["vb"], acc["va"], acc["meshes"])',
     "a global jump's values taken in the middle of the jump (D59)", ["obj.addremove"], None),
    ("bl/datarec.py", "                if ob is not None:\n                    rt.refresh_state_any_mode(ob)",
     "                if ob is not None and ob.mode != \"EDIT\":\n                    rt.ensure_state(ob, refresh=True)",
     "after Blender's undo of a jump, an object in Edit Mode keeps a stale state (D53)", ["obj.editsteps"], None),
    ("bl/runtime.py", '        return datarec.is_tracked(obj)\n    except Exception:\n        return False\n\n\nEXTRAS_TOPO_MAX',
     '        return False\n    except Exception:\n        return False\n\n\nEXTRAS_TOPO_MAX',
     "weights and shape keys not part of recorded objects' states (D43)", ["obj.editsteps"], None),
    ("bl/meshio.py", "        for name, lock in groups:\n            if ob.vertex_groups.get(name) is None:",
     "        for name, lock in []:\n            if ob.vertex_groups.get(name) is None:",
     "vertex groups not kept across a topology restore (D43)", ["obj.editsteps"], None),
    ("bl/meshio.py", "    members = np.nonzero(~np.isnan(arr))[0]", "    members = np.nonzero(arr >= 0)[0]",
     "negative weights read as not in the group (D43)", ["obj.editsteps"], None),
    ("bl/meshio.py", "    if rebuilt or any(len(kb.data) != n for kb in blocks):", "    if False:",
     "shape keys not rebuilt at the new size (D43)", ["obj.editsteps"], None),
    ("bl/runtime.py", "        _clamp_state_weights(obj, st)\n", "",
     "clamped weights left out of the state, recorded as a change (D43)", ["obj.editsteps"], None),
    ("bl/runtime.py", "    if h is None and mode not in RECORD_MODES and not _edit and not extras_on(obj):",
     "    if h is None and mode not in RECORD_MODES and not _edit:",
     "a first Edit Mode visit on a recorded object not recorded (D50)", ["obj.editsteps"], None),
    ("bl/runtime.py", '        editor = ob if ob.mode == "EDIT" else _mesh_editor(ob)',
     '        editor = ob if ob.mode == "EDIT" else None',
     "a shared mesh written while a linked duplicate is in Edit Mode (D51)", ["obj.editsteps"], None),
    ("bl/adapters.py", '        ("data", "ref", "objects"),           # which mesh (or other data) it uses (D44: Make Single User)\n',
     "", "which mesh an object uses not recorded (D44)", ["obj.addremove"], None),
    ("bl/datarec.py", "            rt.ensure_state(ob, refresh=True)\n            _MESH_OF[uid] = ptr",
     "            _MESH_OF[uid] = ptr", "a new owner of a mesh keeps a stale state (D44)", ["obj.addremove"], None),
    ("bl/runtime.py", '        relink_last = pos < before and "data" in data_keys.get("rna", ())', "        relink_last = False",
     "going back over a relink writes the mesh into the earlier one (D44)", ["obj.addremove"], None),
    ("bl/datarec.py", "            uid = min((0 if records_mesh(u) else 1, n, u) for n, u, _o in us)[2]",
     "            uid = min((n, u) for n, u, _o in us)[1]", "the shared mesh's owner after a reload picked by name (D45)",
     ["obj.addremove"], None),
    ("bl/datarec.py", "        if not was_on and h is not None and h.entries and _state(uid).values:",
     "        if False and h is not None and h.entries and _state(uid).values:",
     "changes made while Record everything was off not recorded when it's switched on (D46)", ["obj.transforms"], None),
    ("bl/runtime.py", '    outside = set()\n    for u, rec in (saved or {}).items():',
     '    outside = set()\n    for u, rec in {}.items():',
     "changes made without Hindsight not noticed on opening (D46)", ["obj.transforms", "obj.editsteps"], None),
    ("bl/datarec.py", "        if st.values.get((\"rna\", \"name\")) == wanted and idb.name != wanted:", "        if False:",
     "a name Blender changed on restore recorded as a change (D47)", ["obj.addremove"], None),
    ("bl/idmap.py", "    lib = getattr(idb, \"library\", None)\n    if lib is not None:\n        coll = _collection_of(idb)",
     "    lib = None\n    if lib is not None:\n        coll = _collection_of(idb)",
     "references to linked data recorded as empty (D48)", ["obj.parenting", "obj.constraints"], None),
    ("bl/adapters.py", "    for w in body.get(\"modifiers\") or ():", "    for w in ():",
     "a deleted object brought back without its modifiers (D49)", ["obj.addremove"], None),
    ("bl/adapters.py", '    _set_hidden(ob, body.get("hidden"))', '    pass',
     "a deleted object brought back visible though it was hidden (D49)", ["obj.addremove"], None),
    ("bl/datarec.py", "        editing = [o for o in bpy.context.view_layer.objects if o.mode == \"EDIT\" and o.type == \"MESH\"]",
     "        editing = [active] if active is not None and active.mode == \"EDIT\" else []",
     "with several objects in Edit Mode only the active one gets steps (D52)", ["obj.editsteps"], None),
    ("bl/datarec.py", "    rt.OP_GROUP[0] = [None]\n", "    rt.OP_GROUP[0] = None\n",
     "one operator's data and Edit Mode steps not one global position (D52, D58)", ["obj.editsteps", "obj.addremove"],
     None),
    ("bl/datarec.py", "        if isinstance(idb, bpy.types.Object) and rt.is_excluded(idb):\n            _EXCLUDED.add(uid)",
     "        if False:\n            _EXCLUDED.add(uid)", "objects excluded by Collection Filtering still recorded (D54)",
     ["obj.collections"], None),
    ("bl/datarec.py", "            _EXCLUDED.discard(uid)\n            catch_up(ob, CHANGES_WHILE_EXCLUDED)",
     "            _EXCLUDED.discard(uid)", "changes made while excluded lost when the filter is lifted (D54)",
     ["obj.collections"], None),
    ("bl/adapters.py", '            _note_ref(idb, "collections", missing[0] if missing else None, None, "object", "collections")',
     "            pass", "objects not put back into a collection the global history brings back (D55)",
     ["obj.collections"], None),
    ("bl/adapters.py", '            if hasattr(ch, "rename"):\n                ch.rename(name, mode="ALWAYS")',
     '            if hasattr(ch, "rename"):\n                ch.name = name',
     "a collection brought back doesn't get its name back from a left-over one (D55)", ["obj.collections"], None),
    ("bl/adapters.py", "        if c.users == 0 and not c.objects and not c.children and c.library is None:",
     "        if False:", "empty collections made again are left over (D55)", ["obj.collections"], None),
    ("bl/datarec.py", "    if ref in adapters.COLL_NAMES:\n", "    if False:\n",
     "a deleted collection named as an object in the sidebar (D55)", ["obj.collections"], None),
    ("bl/adapters.py", '            out["hide"] = _hidden(idb)', '            out["hide"] = bool(idb.hide_get())',
     "hidden read for the window's view layer only (D56)", ["obj.collections"], None),
    ("bl/adapters.py", "                    ob.hide_set(bool(h), view_layer=vl)",
     "                    ob.hide_set(bool(h))", "hidden put back in the window's view layer (D56)",
     ["obj.collections", "obj.addremove"], None),
    ("bl/datarec.py", "    sync_missing_refs(idb, st, data_keys)\n    _DIRTY.pop", "    _DIRTY.pop",
     "the next operation records a reference Blender couldn't set as cleared (D60)", ["obj.parenting"], None),
    ("bl/datarec.py", "                    if w is not MISSING:\n                        st.values[(aid, p)] = w",
     "                    if False:\n                        st.values[(aid, p)] = w",
     "a waiting reference not put in when its object is back (D60)", ["obj.parenting", "obj.collections"], None),
    ("bl/datarec.py", "        if changes:\n            adapters.drop_changed_refs(uid, changes)", "        pass",
     "your own change doesn't end a reference's wait (D60)", ["obj.parenting"], None),
    ("bl/runtime.py", "                if miss:\n                    rec[\"missing\"] = miss", "                pass",
     "waiting references not saved with the file (D57)", ["obj.parenting"], None),
    ("bl/runtime.py", "            adapters.REF_NAMES[ref] = item[5]", "            pass",
     "the deleted object's name not saved for the sidebar (D57)", ["obj.parenting"], None),
    ("bl/adapters.py", "                                                if o.library is None and not rigmark.is_rig(o)) if u)",
     "                                                if o.library is None) if u)",
     "Hindsight's own Movie Mode objects recorded as added and removed (INTERFACES 9)", ["obj.addremove"], None),
    ("bl/adapters.py", "        have = {idmap.uid_of(o): o for o in idb.objects if not rigmark.is_rig(o)}",
     "        have = {idmap.uid_of(o): o for o in idb.objects}",
     "the global history removes Hindsight's own Movie Mode objects (INTERFACES 9)", ["obj.addremove"], None),
]


def make(n, fname, old, new):
    """A copy of hindsight/ and tests/ with one edit (none for the control, fname None)."""
    d = os.path.join(OUT, str(n))
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "hindsight"), os.path.join(d, "hindsight"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(d, "tests"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    if fname is None:
        return d
    path = os.path.join(d, "hindsight", fname)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if src.count(old) != 1:
        raise SystemExit(f"mutant {n}: text not found exactly once in {fname}: {old!r}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src.replace(old, new))
    return d


def engine_path():
    """The folder holding this checkout's own Rust engine (its committed wheel, unpacked), or None. Blender
    ignores PYTHONPATH, so each test run puts it on sys.path itself (CI installs the wheel instead)."""
    sys.path.insert(0, os.path.join(ROOT, "bench"))
    try:
        from suite import native_env
    finally:
        sys.path.pop(0)
    pp = native_env(ROOT, {}).get("PYTHONPATH")
    return pp.split(os.pathsep)[0] if pp else None


def run(exe, d, env, engine=None, why=None):
    """The first test file that fails on copy d, or None. why: a list that gets the failing output."""
    for t in TESTS:
        test = os.path.join(d, "tests", t)
        if engine:
            cmd = ["--python-expr", f"import sys, runpy; sys.path.insert(0, {engine!r}); "
                                    f"sys.argv = [{test!r}]; runpy.run_path({test!r}, run_name='__main__')"]
        else:
            cmd = ["--python", test]
        try:
            r = subprocess.run([exe, "-b", "--factory-startup", "--python-exit-code", "1"] + cmd,
                               capture_output=True, text=True, timeout=1200, env=env, cwd=d, encoding="utf-8",
                               errors="replace")
        except subprocess.TimeoutExpired:
            return t + " (timeout)"
        if r.returncode != 0 or "FAIL " in r.stdout:
            if why is not None:
                why.append((r.stdout + r.stderr)[-1500:])
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
    engine = engine_path()
    survived_any = False
    for tag, exe in targets:
        env = _sandbox_env("mut-" + tag)
        rows = []
        t0 = time.time()
        # the control: unchanged code must pass every test file, or no failure below means anything
        # (a Blender without the engine "killed" every mutant once)
        why = []
        d0 = make(0, None, None, None)
        bad = run(exe, d0, env, engine, why)
        shutil.rmtree(d0, ignore_errors=True)
        if bad is not None:
            print(f"[{tag}] control failed in {bad}: no mutant run\n{why[0] if why else ''}", flush=True)
            return 2
        for n, (fname, old, new, what, row_ids, reason) in enumerate(MUTANTS, 1):
            if only and n not in only:
                continue
            d = make(n, fname, old, new)
            killer = run(exe, d, env, engine)
            ok = killer is not None or bool(reason)
            survived_any |= not ok
            print(f"[{tag}] {n:2d} {'killed by ' + killer if killer else ('equivalent: ' + reason if reason else 'SURVIVED')}"
                  f"  {fname}: {what}", flush=True)
            rows.append({"name": f"mutant {n}: {what}", "ok": ok, "killed_by": killer, "reason": reason})
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
