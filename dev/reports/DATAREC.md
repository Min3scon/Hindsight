> 2026-11-18. An agent's report as delivered, unchanged below this line. Its work is merged into the default branch.

# DATAREC report

Branch `worktree-agent-a9678ada7ac5986c7`, last commit `4859074`, on top of 66fcdf8. The commits are 5ccb570 and 4859074.
I could not write a report file here (the tool refuses report files), so the full report is this message.
Files changed: `hindsight/bl/datarec.py`, `hindsight/bl/adapters.py`, `hindsight/bl/idmap.py`.
New tests: `tests/test_bl_datarec_fast_paths.py`, `tests/test_bl_datarec_perf_counts.py`, `tests/test_bl_datarec_fast_writes.py`. No existing test was changed.
Commit trailer: COMMON.md asks for "Claude Fable 5.2". The session's system instruction replaced that line, so the commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and the same Claude-Session line.
Untracked and left alone: `tests/corpus/t1_prop/c68566cba171.json`, which the t1_prop run wrote.

## Results first
- **500 objects, everything recorded:**
  - An operator that changes nothing (a selection click, for example) went from 110 to 46 ms.
  - A move went from 120 to 49 ms.
  - The depsgraph handler went from 7.4 to 0.2 ms per update. It runs on every update, including every mouse move of a G drag.
  - `prime()` at file load went from 240 to 117 ms.
  - Memory held per recorded object went from 16.9 to 10.7 KB.
- **Recording what it recorded before:** every Tier 1 operation sequence is recorded twice in one process, with the shortcuts and without. The steps match byte for byte, including runs with Blender's undo and redo.
- **With Record off**, the timer now reads nothing at all.
- **Edit Mode step by step** costs the same as before. Almost all of its cost is in runtime.py, which is not mine (section 6).
- **Both extra items are covered.** The `_write_constraints` bug is fixed, with a regression test that fails on 66fcdf8. The Blender 4.2 `parent_type` failure comes from Blender 4.2 itself; the fix belongs in the test oracle, and the patch is in section 6.

## 1. Bottlenecks found (profile evidence)
Tools are in `S/agents/DATAREC/`:
- `bench_dr.py ROOT N MODE`: ms per call, plus tracemalloc of the remembered state.
- `prof_dr.py`: cProfile of one path.
- `parts.py`: per-object micro costs.
- `bench_edit.py`: Edit Mode step.
- `mem_dr.py`: bytes per object, by part.
- `ab.sh` and `ab2.sh`: baseline (S/baseline) against the worktree, alternating, 5 rounds.
- `ab_table.py`: builds the tables below.

Test scenes are N cubes, a third with a Copy Rotation constraint. Off means Record set to "Sculpting only". One means one object switched on. All means every object plus the scene. Runs are bpy 5.3 headless on the shared 4-core VM.

At baseline, with 500 objects all on, `finish_operation` with nothing changed cost 104 ms. This is the cost of every operator finish, selection included. The profile:
- **`_diff` (45%):** it compared value by value through the recursive `adapters.same`.
- **`capture` (35%):**
  - Blender's `Object.users_collection` is a Python property that lists the objects of every collection for each object it is asked about. That cost 27 µs per object at 500.
  - `normalise` was called once per vector item.
  - Preferences and the adapter list were read again for every object.
- **`idmap.find` (10%):** a cached lookup is `bpy.data.objects.get(name)`, which is a linear search costing about 10 µs at 500 objects.
- **`commit` overhead:**
  - `tracked_uids()` was rebuilt for every target, which is O(n²).
  - `_is_scene_uid` searched every id collection for each non-scene id, at 14 µs each.

Other paths:
- **`on_update`:** 7 to 10 ms per depsgraph update at 500 all, almost all of it `_is_scene_uid` on every recorded id.
- **`prime`:**
  - Each owner's mesh was read twice: once in `refresh_mesh_users`, then again in `baseline`.
  - After `idmap.forget()`, every `find` was a full scan: 132k `get` calls.
- **Memory:** recorded values took 5.6 KB per object and kept bodies 8.6 KB per object.
- **Edit Mode step at 100k vertices:** 55 ms in total, of which datarec takes 0.3 ms.

## 2. What changed and why
Every shortcut gives exactly the old result, and each is tested against the plain path (section 4).

- **`idmap.frame()`:** a frame is a stretch of Hindsight's own work in which no object is added, removed or renamed. It covers commit, refresh_mesh_users, check_exclusion, prime and on_undo.
  - After 8 lookups in a frame, `find` reads `bpy.data.objects` once into two maps: name to object, and id to first local object.
  - The cached path still checks the library and the id.
  - A name that appears twice falls back to Blender's `get`.
  - Ids given out inside the frame (claim, or `runtime.obj_uid` through `note()`) are added to the index.
  - A stale entry falls back to the live scan.
  - Frames are kept out of `view_layer.update()` and Edit Mode steps.
  - `frame_cache()` gives other code a dict that lives for one frame.
- **`adapters.users_collection(ob)`:** inside a frame, after 4 objects, it builds one table of every collection's members. The order is the same as Blender's property.
- **`adapters.normalise`:** a fast path by exact type. mathutils vectors become `list(v)`, and number arrays become a list without a call per item.
- **`datarec._same_values` and the per-value check in `_diff`:** if the marshal v2 bytes are identical (same keys, same order, same types, floats bit for bit), `_diff` and `Adapter.changed` are skipped.
  - This applies only while every adapter uses the base `Adapter.changed` (checked by `_fast_diff_ok`).
  - An adapter with its own comparison, or the fault test's patched `changed`, takes the old path.
- **`commit`:**
  - The tracked set is computed once, and read again after `_track_new`.
  - Scene ids are found by looking at the scenes only (`_scene_uids_in`, plus an early out in `_is_scene_uid`).
  - Preferences and the adapter list are read once per frame.
- **`on_update`:** scene ids are read once per update.
- **`tick`:**
  - With nothing recorded, an operator finish or settled updates now only leave behind the state that `finish_operation` and `commit` used to leave: empty `_DIRTY`, `_MESH_OWNER` and `_MESH_USERS`.
  - It no longer calls `view_layer.update()` and no longer walks the objects.
  - `any_tracked()` replaces building the tracked set on every tick.
- **`prime`:** one frame, and an owner's mesh is read once.
- **Memory:**
  - The `(adapter, path)` key tuples are shared across objects.
  - Bodies are kept as marshal bytes and decoded only when an object is added or deleted. The round trip is exact for these JSON values.

## 3. Before and after
Method: `ab2.sh` runs baseline and new alternately, 5 rounds, each value the median of 5 calls. The table shows medians of the round medians, old / new, in ms. "held" is tracemalloc bytes of the recorder's state per recorded object after `prime`.

| objects | setting | idle tick | on_update | finish, no change | finish, move | settle commit | finish, move all | prime | held B/obj |
|---|---|---|---|---|---|---|---|---|---|
| 1 | off | 0.0033 / 0.002 | 0.0008 / 0.0008 | 0.015 / 0.016 | 0.027 / 0.032 | 0.018 / 0.018 | 0.025 / 0.030 | 0.007 / 0.005 | 712 / 712 |
| 1 | one | 0.0034 / 0.004 | 0.021 / 0.015 | 0.23 / 0.13 | 0.49 / 0.33 | 0.32 / 0.31 | 0.28 / 0.30 | 0.36 / 0.19 | 7021 / 7757 |
| 1 | all | 0.0033 / 0.004 | 0.023 / 0.019 | 0.20 / 0.15 | 0.56 / 0.53 | 0.50 / 0.50 | 0.51 / 0.43 | 0.41 / 0.29 | 6234 / 5886 |
| 50 | off | 0.0034 / 0.002 | 0.0007 / 0.0011 | 0.016 / 0.021 | 0.064 / 0.105 | 0.057 / 0.044 | 0.27 / 0.35 | 0.004 / 0.006 | 712 / 712 |
| 50 | one | 0.0033 / 0.0039 | 0.027 / 0.021 | 0.18 / 0.14 | 0.52 / 0.48 | 0.47 / 0.44 | 0.92 / 0.93 | 0.39 / 0.18 | 7021 / 7757 |
| 50 | all | 0.0032 / 0.004 | 0.196 / 0.053 | 10.7 / 4.65 | 10.8 / 4.62 | 1.38 / 1.15 | 16.7 / 16.7 | 16.3 / 12.0 | 15174 / 10359 |
| 500 | off | 0.0035 / 0.002 | 0.0035 / 0.0036 | 0.083 / 0.066 | 1.64 / 1.52 | 1.38 / 1.33 | 7.0 / 6.3 | 0.006 / 0.007 | 712 / 712 |
| 500 | one | 0.0033 / 0.0039 | 0.081 / 0.073 | 0.28 / 0.23 | 2.17 / 2.18 | 1.97 / 1.99 | 9.2 / 7.4 | 0.27 / 0.27 | 7021 / 7757 |
| 500 | all | 0.0033 / 0.0044 | 7.41 / 0.20 | 110 / 45.8 | 120 / 49.1 | 20.6 / 8.5 | 292 / 242 | 240 / 117 | 16851 / 10742 |

How to read some of these numbers:
- **"finish" in the off rows** calls `finish_operation` directly, as the old timer did. The real timer with nothing recorded now returns early; `test_bl_datarec_perf_counts` checks it does no capture, find, commit or evaluation.
- **The "one" setting** shows a held bytes rise of about 700 B. That is the shared key table being created once, counted against a single object.
- **The idle tick differences** of about 1 µs are noise level.
- **The first round** (`ab.sh`, before the lazy frame index) showed "500 one finish, no change" at 0.27 / 0.63 ms. Building the index for one lookup cost more than it saved. The index is now built only after 8 lookups, and the regression is gone (0.28 / 0.23).

**Memory by part** (`mem_dr.py`, 300 objects):
- Recorded values: 5622 → 4699 B per object.
- Kept bodies: 8564 → 3583 B per object.

**Edit Mode step by step** (`bench_edit.py`, 5 rounds, ms per operator, old / new):

| vertices | whole step | of which datarec |
|---|---|---|
| 10k | 4.8 / 6.1 | 0.29 / 0.21 |
| 100k | 76 / 90 | 0.34 / 0.33 |
| 300k | 287 / 206 | 0.32 / 0.32 |

The whole-step numbers are `runtime.commit_edit`, so the differences there are noise. In a profile at 100k, `update_from_editmode` takes about 34 ms per step and the topology compare reads (`foreach_get`) about 20 ms.

**`tests/test_bl_t1_perf.py` budgets** (unchanged, all pass), bpy53:
- Heavy scene: move 7.2 ms, relations 43.5 ms, add 49.4 ms.
- Medium scene: move 2.2, relations 6.1, add 7.3 ms.
- Mesh check: 10k 1.8 ms, 100k 5.8 ms, 1M 85.4 ms.

## 4. Tests
**New: `tests/test_bl_datarec_fast_paths.py`** (16 tests, pass on bpy53).
- Unit checks against the plain path:
  - `_same_values` over 4000 random dict pairs: -0.0 against 0.0, 1 against 1.0 against True, NaN payloads, nested lists, refs, key order. It is never True when the plain `_diff` finds a change, and the per-value shortcut agrees with the plain `_diff`.
  - `normalise` against the old one, on awkward values and every allowlisted path.
  - `users_collection` against Blender's property, with nested collections, two scenes and an orphan collection.
  - `find` in a frame against the plain `find`: cached and cold, a copy carrying an id named so it comes first, a renamed object, an id given out inside the frame, scene, collection and mesh ids, and an id nobody has.
  - Scene-id checks against the plain check.
- Whole recordings: every Tier 1 sequence (transforms, parenting, constraints, collections, add and remove, edit steps) is recorded with the shortcuts and with the plain paths, with fixed ids. Steps, cursors and recorded values are compared byte for byte. The same runs again with Blender's undo and redo after every third operation.
  - Blender doesn't give the same edge and face order every time for some primitives and Edit Mode results; a second UV sphere in a session differs from the first even without Hindsight. So mesh copies (topology before and after, snapshots of added and deleted objects) are compared by sizes and sorted positions. Everything else is compared byte for byte.
- `prime` with one mesh read gives the same states as reading twice.
- The cases from the brief:
  - A change right after an operator that changed nothing, including a change only the sign bit shows (-0.0).
  - The timer with nothing recorded leaves the same state as the full path.
  - Changes made while Record was off become one step after it is turned on again.
  - 40 objects changed by one operator give one step each and one position on the global timeline.
  - A rename is recorded and the object is found again.
  - An object deleted and another made with its name: different ids, the step goes back and forth.
  - Undo, redo, then a change.
- Mutation checks: making `_same_values` compare keys only fails 9 of these tests. Disabling the index update for new ids fails 3.

**New: `tests/test_bl_datarec_perf_counts.py`** (4 tests). It counts work instead of timing it:
- With Sculpting only, the timer does no capture, find, commit, refresh or exclusion check.
- A depsgraph update doesn't search every id.
- A commit reads `bpy.data.objects` at most once per frame.
- `prime` reads each mesh once.

**New: `tests/test_bl_datarec_fast_writes.py`:** the constraint regression (section 6). It fails on 66fcdf8 and passes now.

**Whole suite, bpy53:** 48 of 50 files pass. The two failures are the known sandbox ones: `test_bl_metadata` (needs a Blender binary) and `test_bl_t1_migrate` (needs b1.zip). Earlier, `run_tests.py t1_` on bpy53 passed 12 of 13 files, migrate being the exception.

**bpy42, my area:**
- Pass: `test_bl_adapters`, `test_bl_t1_faults`, `jumps`, `meshdata`, `perf`, `warn`, `datarec_fast_writes`, `datarec_perf_counts`.
- Every bpy42 failure has one of three causes, all present before my changes:
  1. `t1_ops.move_to_collection` and `link_to_collection` use `collection_uid`, which only exists from Blender 4.3. The coordinator fixed this on the main line; my worktree still has the old helper. This hits `added`, `corrupt`, `gate` (collections), `outside`, `prop`, `real_undo`, `reload`, and two tests in my `datarec_fast_paths` that run the collections sequence.
  2. The `parent_type` issue in section 6.
  3. `migrate`, which fails in this sandbox.

**Native engine:** not tested both ways. I did not run `HINDSIGHT_NATIVE=require` and `HINDSIGHT_NATIVE=0` separately; the suite used the default engine. None of my changes touch the mesh or native paths.

## 5. Tried, not kept, or not done
- **Comparing only the objects the depsgraph reports as updated.** Not done. Several recorded values change without the object being tagged: view-layer hide (`hide_set` tags the scene), `hide_select`, renames. A selection click tags the scene anyway, so the fallback to "compare all" would run on most operators. A miss would move a change into a later step, which is not acceptable. With the exact shortcuts, an unchanged object now costs about 0.09 ms at 500 objects instead of about 0.22 ms.
- **Caching each object's marshal bytes.** Not done. `st.values` is changed in place by playback, undo and core code, so a cache would be a correctness risk for about 4 µs per object.
- **Building the frame index on the first lookup.** Tried and replaced. With 1 of 500 objects recorded it made a no-change finish 0.27 → 0.63 ms. It is now built after 8 lookups.

## 6. Risks, limits and the two extra items
**Limits:**
- No Blender binary, no GPU, no UI here: everything was verified with headless bpy 4.2 and 5.3.
- `window_manager.operators` is empty headless, so the "operator finished" path is driven through `finish_operation` and the marker, as the existing tests do.
- **The frame index assumes no object is deleted or renamed inside a frame.** I checked the paths that run inside frames: commit, refresh_mesh_users, prime, on_undo and check_exclusion only create and remove temporary mesh copies, which are not indexed. If code that deletes objects is ever added inside `commit`, it has to end the frame first.
- **`_is_scene_uid` no longer warms `idmap._cache`** for non-scene ids as a side effect. The next real lookup in the same commit warms it the same way; the byte-for-byte test covers this.

**Bug fixed (coordinator item): constraint restore gave later constraints the wrong values.**
- **Cause:** `adapters._write_constraints` zipped the rebuilt stack with the recorded list. When one `constraints.new` failed (a type this Blender lacks), every later constraint got the previous entry's values.
- **Fix:** each constraint made is paired with its own entry; one that can't be made is skipped and logged once.
- **Test:** `test_bl_datarec_fast_writes.py`. On 66fcdf8 constraint C gets B's values (`use_x` True, influence 0.5); now it keeps its own.

**Blender 4.2 `test_gate_parenting` (C at its position 5, `/rna/parent_type: '' != OBJECT`):** not a recorder bug; fails on 66fcdf8 too.
- **Reproduced** with `S/agents/DATAREC/ptype.py`. On 4.2, "Clear Parent (Keep Transform)" on a Vertex Triangle child leaves Blender's internal parent type at vertex-3 with no parent. RNA then reads `parent_type` as `''` (warning "current value '6' matches no enum") and refuses to set VERTEX or VERTEX_3 without a parent ("not found in ('OBJECT')"). On 5.3 the same clear resets the type to OBJECT.
- **Why it can't be restored exactly:** Hindsight records `''`, which is all RNA gives. Restoring that position after one with OBJECT can't write `''`, so OBJECT stays. The hidden vertex type isn't readable or writable through Python on 4.2. The only difference a user could see is the type a later `ob.parent = x` set from Python would use.
- **Fix belongs in `tests/oracle.py`** (not mine, not edited): in `object_state`, right after `rna = rna_dump(...)`:
```python
    if ob.parent is None and rna.get("parent_type") == "":
        rna["parent_type"] = "OBJECT"   # Blender 4.2: Clear Parent keeps a vertex parent type that can't be read or set without a parent
```

## 7. Patches for files I don't own
No patch is needed for the recorder to work.
- **For RUNTIME (`hindsight/bl/runtime.py`):** the Edit Mode step cost is `commit_edit`, not datarec. At 100k vertices, `update_from_editmode` takes about 34 ms per step and the topology reads (`foreach_get`, including `meshio.topology_equal`) about 20 ms.
- **For the main line (`tests/t1_ops.py` and `tests/oracle.py`):** the `collection_uid` fix the coordinator mentioned, and the oracle line above.

## 8. Proposed text
**DEFECTS.md:**
- "Restoring an object's constraints when one of them can't be made again (a constraint type this Blender doesn't have) gave every later constraint the settings of the one before it. Fixed: each constraint gets its own settings, the one that can't be made is skipped and logged once. Test: tests/test_bl_datarec_fast_writes.py."
- "Blender 4.2 only: Clear Parent keeps a vertex parent type after the parent is gone. Blender shows it as empty and won't let it be set without a parent, so Hindsight can't put it back exactly. Nothing visible changes. The gate test's oracle needs to treat it as Object."

**PERF_LOG.md, new section "Data recorder cost (Record everything), 2026-11-18":** the table in section 3, the memory numbers, the method (`S/agents/DATAREC/ab2.sh`, 5 alternating rounds, bpy 5.3 headless, shared 4-core VM), and the profile summary from section 1.

**CHANGELOG.md:** "Record everything is cheaper in big scenes: an operation with 500 recorded objects takes less than half the time to record, the update handler no longer slows interactive moves, and loading a file reads its baselines twice as fast. Each recorded object takes about a third less memory. With Record set to Sculpting only the recorder does no work at all. What is recorded is unchanged."
