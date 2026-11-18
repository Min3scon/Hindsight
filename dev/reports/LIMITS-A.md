> 2026-11-18. An agent's report as delivered, unchanged below this line. Report only: it changed no code (analysis and design).

LIMITS-A: audit of the 12 "Known limits". No repository file was changed and nothing is fixed.
The report file could not be written: the harness refused to write S/agents/LIMITS-A/report.md ("Subagents should return findings as text"). The full report is below.
Probe scripts and their outputs are in /tmp/claude-0/-home-user-Hindsight/4a339202-445d-5357-a8ee-013fb190a1d0/scratchpad/agents/LIMITS-A/ (each output is saved as `<script>.<venv>.out`).
Main findings: 11 of the 12 limits are still true. Limit 5 is wrong in the default scope: two histories fight over one shared mesh. Library overrides lose their history on reload. Exact Multires recording per visit is possible, but per stroke is not.

# LIMITS-A report

Worktree: agent-a478dfcc48bd0a708 at 66fcdf8. I made no commits.
- **Scripts and versions:** every script ran headless on bpy53 (5.3.0) and bpy42 (4.2.23). The l7 scripts also ran on 5.0.1, 5.1.2 and 5.2.2.
- **No real window:** there is no Blender window, no GPU and no real brush stroke here. Each section says where that matters.
- **Timings:** the machine is shared (load 5–7), so treat numbers as order of magnitude. Runs are single unless min/med is given.

**Baseline of the existing tests that pin these limits** (output in `existing_tests.out`):

| Test file | bpy53 | bpy42 |
|---|---|---|
| test_bl_warnings | 13/13 | 13/13 |
| test_bl_t1_warn | 9/9 | 9/9 |
| test_bl_t1_outside | 7/7 | 6/7 |
| test_bl_t1_added | 19/19 | 18/19 |
| test_core_store | 15/15 | 15/15 |

The two bpy42 failures are already there at 66fcdf8 and are a test-helper bug. `tests/t1_ops.py:177,179` `move_to_collection` passes `collection_uid=`, which 4.2's operator doesn't accept (`keyword "collection_uid" unrecognized`; 4.2 takes `collection_index`). The failing tests are `test_excluded_by_collection_filtering` and `test_a_deleted_collection_comes_back`. This needs a version guard in t1_ops.py.

---

## 1. Multires and Dyntopo pause sculpt recording

**Verdict: still true.**
- **Multires:** recording each stroke is not practical (see the flush cost in 1.3). An exact record of one step per visit is possible.
- **Dyntopo:** fixable per stroke on small meshes, per visit on large ones.
- **Stale doc:** PARITY.md:144-145 says "Dyntopo cannot be enabled headless". It can, on 4.2 and 5.3.

### 1.1 Cause in code and pinning tests
- `warnings.py:58-86`:
  - `_has_dyntopo` is true only while the object is in Sculpt Mode with Dyntopo on.
  - `_has_multires` is true for any MULTIRES modifier.
  - `blocking` and `blocking_reason` build on these two.
- `runtime.py`: `_blocking` at :189-194 and `can_record` at :222-235. In `commit` at :569-571, `if _blocking: ensure_state(refresh=True); return None`, so the change is absorbed into the baseline.
- UI: `ui_panel.py:177-179`. Morph Brush refuses at `morph.py:21-30`.
- Tests: `test_bl_warnings.py:44-63` (Dyntopo is faked with a SimpleNamespace) and `test_bl_morph.py:136-139`.
- Origin: PARITY row 73 copied the reference add-on. RESEARCH.md:104-110 marks both as UNVERIFIED risks. No DECISIONS entry argues they can't be done.

### 1.2 What happens today (`l1_current.py`)
- With Multires present, `can_record` is False and the commit returns None.
- **Strokes made while paused are silently folded into the baseline and can never be undone.** The steps went 1 → 2 across three strokes.

### 1.3 Multires: what Python can read and write (`l1_multires_probe{,2,3}.py`)
Results are the same on 5.3 and 4.2 unless stated.

| Option | What it gives | Cost |
|---|---|---|
| `mesh.attributes` | No displacement layer | – |
| Evaluated mesh, Object Mode | Sculpt-level positions | 3 ms at 129k, 12–21 ms at 520k |
| Evaluated mesh, Sculpt Mode | 5.3: base vertices only; 4.2: **0 vertices**. Unusable. | – |
| `bpy.ops.ed.flush_edits()` in Sculpt Mode | Works headless and writes the grids to MDISPS | **0.36–0.49 s at 129k, 2.5–2.9 s at 520k, 3.0–3.4 s at 2.08M sculpt-level verts** |
| `multires_reshape` from a temp object | Not bit-exact (max error 1.19e-7; 825 verts changed for 200 targeted); no drift over 20 round trips | 0.43–0.48 s at 129k, 2.6–2.7 s at 520k |
| `mesh.copy()` swap | Keeps the displacement; bit-exact | Copy is 0.1 ms, but RSS grows +15 MB per changed step at 129k and +56 MB at 520k |
| `multires_external_save` → read the .btx → `multires_external_pack` | **Exact displacement bytes**: 112-byte header + loops × grid² × 3 float32. Works in Sculpt Mode after a flush. | 2.4 MB in 3–4 ms at 129k; 9.75 MB in 10–14 ms at 520k; 31.6 MB in 45–256 ms at 2.08M |
| Write the .btx back + pack | Does not reload the file: restores nothing | – |
| `bpy.data.libraries.write` the mesh while its layer is external → put the recorded .btx bytes in place → `libraries.load` → `old.user_remap(new)` → remove old, rename → pack | **Bit-exact restore** on 5.3 and 4.2 (`library restore: equals A True`) | – |
| Enter / leave Sculpt Mode | – | Enter 0.08–0.95 s, exit 0.4–4.4 s (129k–2.08M) |

Conclusion for Multires: a per-stroke record needs `flush_edits` after every stroke, which costs as much as leaving Sculpt Mode. That is how Blender is built: the grids live in SubdivCCG, and Python sees them only after a full flush. A per-visit record is exact and compact.

### 1.4 Multires fix design (one step per visit)
- **Who it is for:** a Multires object is no longer paused. Each Sculpt Mode visit becomes one step, plus one step at save. Blender's own sculpt undo covers strokes inside a visit. Jumps apply only on slider release, because in Sculpt Mode a jump must leave and re-enter the mode.
- **New `bl/multires.py`:**
  - `read_disps(ob)`: external save to `bpy.app.tempdir` with `relative_path=False`, read the file, pack, delete the file.
  - `write_disps(ob, arr)`: do the library round trip. Clear materials on the copy before writing and copy the material list back after loading. Carry `_hindsight_uid` and the other ID properties across. Use `user_remap`. Then refresh `datarec._MESH_OWNER` / `_MESH_OF`, which are keyed by mesh pointer (datarec.py:35-36).
  - Channel key: `~mdisps:CORNER:FLOAT`.
- **Other files:**
  - `warnings.py`: Multires no longer blocks; the text becomes "one step per Sculpt Mode visit".
  - `runtime._enter` / `check_mode_transition` (:801-853): commit on Sculpt Mode exit. Read after Blender's own flush on exit, so there is no extra flush.
  - `runtime._write_state` (:466-498): route the mdisps key to `multires.write_disps`.
  - `meshio` snapshots: add the array. A level change counts as TOPO.
- **Format:** old builds would try to write the new key as an attribute, and `write_changes` is unguarded. So bump the step blob version (`core/step.py`) for steps that carry it. Old builds then refuse those steps. No migration is needed.
- **Cost:** a 3–14 ms read at visit end plus a diff of 2.4–9.75 MB. Restore is a library write/load of the base mesh (not timed). A jump done from Sculpt Mode adds the mode toggle (0.5–7 s).
- **Risks:**
  - `external_save` and `pack` are UNDO operators (from Blender source, not checked here). They may add undo entries in the real UI; this needs a window test.
  - The operators may require a saved .blend. Untested.
  - Shape keys on a Multires mesh.
- **Tests:**
  - Headless: use reshape as a stand-in for a stroke, then check .btx bytes are bit-exact across jumps and reloads.
  - Needs a real window: real strokes and Blender's Undo History.
- **Size:** about 350 lines, 2–3 days.

### 1.5 Dyntopo (`l1_dyntopo_probe.py`, `l1_dbg.py`)
- `sculpt.dynamic_topology_toggle()` works headless.
- `flush_edits` writes the BMesh back to the mesh, and `meshio.read_snapshot` reads it.
- On 5.3 the flushed mesh carries `.sculpt_dyntopo_node_id_face` / `.sculpt_dyntopo_node_id_vertex`. These must go into `meshio._SKIP_PREFIX` (meshio.py:16).
- Writing the mesh while Dyntopo is on gets overwritten by the next flush. Restore must leave Sculpt Mode, write, and re-enter.
  - 5.3 turns Dyntopo back on at re-entry.
  - **4.2 does not** (`dyntopo after re-enter False`), so the toggle must be called again.

Costs (5.3 / 4.2):

| | 100k tris | 1M tris |
|---|---|---|
| flush | 105 / 108 ms | 1,247 / 1,374 ms |
| read_snapshot | 20 / 23 ms | 166 / 280 ms |
| Raw snapshot | 7.9 MB | 79 MB |
| TOPO blob (pack time) | 5.8 MB (125–158 ms) | 64 MB (1.1–1.6 s) |
| Restore (exit + write + enter) | 30 + 75 + 132 ms | 859 + 737 + 1,508 ms, +2 s toggle on 4.2 |

So recording each stroke as a TOPO step is fine at 100k and not acceptable at 1M (about 1.4 s and 32–64 MB per stroke).

Side finding (headless, 5.3): entering Sculpt Mode on a mesh whose Dyntopo flag is set, the depsgraph handler runs inside `mode_set` before Dyntopo turns on. So "Changes Outside History" is recorded. A real window may order this differently and absorb the change silently. Needs a window check.

### 1.6 Dyntopo fix design
- **Rule:** each stroke is a step up to `DYNTOPO_STEPS_MAX` (proposed 200k tris). Above that, one step per visit, with a note.
- `runtime.commit`: for a Dyntopo object, call `flush_edits` (never while `stroke_running()`), then use the normal `_observe` TOPO path. Compare topology with `deep="corners"`.
- `_write_state` TOPO branch (:470-488): re-enable Dyntopo after re-entry when it came back off (4.2).
- **TOPO chain:** each `before` equals the previous `after`, so dedupe it in `core/step.py` and `store.py`. Otherwise memory doubles. This needs a step-version bump.
- **Tests:** headless with a topology change in Object Mode as the stand-in; the 4.2 Dyntopo state; the skip prefix. A real window is needed for real strokes.
- **Size:** about 250 lines plus 150 for the chain, 2 days.

### 1.7 Small fix
Record strokes made while paused as one "Changes while paused" step on resume, instead of refreshing the baseline at runtime.py:569-571. About 20 lines.

---

## 2. Animated or driven properties aren't recorded

**Verdict: inherent to Blender for live F-curves; still true and fixable for some cases the code also drops.**

- **Cause:** `adapters.py:67-98` `animated_paths` collects data paths from the action, NLA strips and drivers. `RNAAdapter.capture` (:241-251) skips the whole path, and `struct_values` puts `~animated` in place of the value.
- **Decision:** DECISIONS.md:261-262.
- **Tests:** `test_bl_adapters.py:169-186`, `test_bl_t1_warn.py:71-82`, `test_bl_t1_faults.py:222-247`.

Evidence (`l2_animated.py`, same on 5.3 and 4.2):
- (a) A keyed location set by hand is reset on the next `frame_set`. The F-curve rewrites the value, so the DECISIONS reason holds.
- When a path becomes animated, the next commit records `location: ([0,0,0], MISSING)`, a step the user didn't make.
- (b) **Only X keyed**: the whole `location` vector is skipped. A Y change is not recorded, although it survives frame changes.
- (c) **All F-curves muted**: still treated as animated. The code has no mute check for F-curves, NLA or drivers.
- (d) A driver on a constraint setting: only that setting is `~animated`. This is correct.

Fix design (`adapters.py` only):
- `animated_paths` returns `(path, array_index)` pairs and ignores muted F-curves, muted NLA tracks and strips, and muted or invalid drivers.
- Capture partly animated vectors with `ANIMATED` in the animated slots; write the other slots element by element.
- Don't record a "path became animated" step.
- **Format:** none (`~animated` already exists).
- **Tests:** turn cases (b) and (c) into regression tests; headless.
- **Size:** about 80 lines, half a day.

The rest (recording keyframes) belongs to the other agent's "Animation isn't recorded" item.

---

## 3. Physics and particle systems don't come back

**Verdict: still true, and the site wording is off.** The modifiers do come back, but with default settings. Baked caches can't come back; that part is inherent.

- **Cause:** `adapters.object_body` (:557-584) stores only the top-level values of each modifier. Nested structs are lost: Cloth/SoftBody `settings`, `ob.collision`, `ob.field`, `ob.rigid_body`, rigid body constraints, the particle system and its settings ID, Fluid and Dynamic Paint settings. `_recreate_object` (:690-750) then calls `modifiers.new`, which gives defaults.
- **Decision:** DEFECTS D49.
- **Tests:** `test_bl_t1_warn.py:114-123` and `test_bl_t1_added.py:315`.

Evidence (`l3_physics.py`, same on 5.3 and 4.2):

| Setting | Before delete | After the global history brings it back |
|---|---|---|
| Cloth quality / mass | 9 / 2.5 | 5 / 0.3 |
| Collision damping | 0.42 | 0.1 |
| Soft body mass | 3.25 | 1.0 |
| Particles (settings, count, seed) | 'MyParticles', 77, 5 | 'ParticleSettings', 1000, 0 |
| Rigid body (type, mass) | ACTIVE, 7.5 | ACTIVE, 1.0 |

- The rigid body comes back with defaults because the object is relinked into the RigidBodyWorld collection.
- The 'MyParticles' settings datablock is still in `bpy.data` (0 users).
- A fake user would keep the deleted object, but Blender's delete still strips the rigid body, and deleted objects would stay saved in the file. Not recommended.

Fix design:
- Add a `"physics"` key to `object_body` with nested `struct_values` for all of the above. For particles, store the settings ref `idmap.claim(ps.settings)` plus the seeds, and the settings' own values in case the ID is gone after a reload.
- In `_recreate_object`:
  - Set the nested values.
  - Reassign the ParticleSettings ID and remove the default one Blender made.
  - Rigid body: link into the recorded collections first, then set values.
  - Rigid body constraint: use the operator with a context override.
  - Fluid: set `fluid_type` first.
- **Format:** none. The body is JSON read with `.get`, so old builds ignore the new key.
- **Note text:** "physics and particle settings come back, baked caches don't".
- **Tests:** turn l3 into a test, including after a reload; headless.
- **Size:** about 180 lines, 1 day.

---

## 4. Edit Mode step by step up to 300,000 vertices

**Verdict: still true.** Half of the cost is inherent (Blender's BMesh-to-Mesh conversion). The other half is a slow read that can be made ~30× faster. The cap is a constant, not a setting.

- **Cause:** `runtime.py:533` `EDIT_STEPS_MAX = 300_000`, used in `edit_steps_on` (:544-550).
- **Step path:** `commit_edit` → `update_from_editmode` → `mesh.copy()` → `_observe` → `meshio.topology_equal(deep=True)` (meshio.py:289-305), which reads `me.edges.foreach_get("vertices")` and the corner vertices.
- **Decision:** DECISIONS.md:278-281 (55 ms per 100k, so 300k).
- **Tests:** `test_bl_t1_warn.py:146-152`, `test_bl_t1_added.py:122-158`, `test_bl_tier1.py:402-425` (120 ms budget at 100k).

Evidence (`l4_edit_cost.py`, bpy53, grid meshes, 1% of vertices moved):

| Vertices | commit_edit min / med | update_from_editmode | Deep topology check | Native diff | RSS |
|---|---|---|---|---|---|
| 100k | 37 / 89 ms | 51 ms | 31 ms | 1 ms | 430 MB |
| 300k | 166 / 270 ms | 160 ms | 120 ms | 3 ms | 717 MB |
| 1M | 483 / 802 ms | 391 ms | 296 ms | 11 ms | 1.7 GB |
| 3M | 2,590 / 2,830 ms | 1,630 ms | 809 ms | – | 4.3 GB |

`mesh.copy()` costs 0 ms.

Read speeds at 1M (`l4_topo_read.py`, min, 5.3 / 4.2):

| Read | Slow path | Attribute path |
|---|---|---|
| Edge vertices | `edges.foreach_get('vertices')` 116 / 137 ms | `.edge_verts` attribute 1.8 / 1.4 ms |
| Corner vertices | `loops.foreach_get('vertex_index')` 223 / 208 ms | `.corner_vert` attribute 2.3 / 2.1 ms |

It looks like `_corner_ints` falls back to the loops path on the copy; the fixer should check that.

Fix design:
1. **Attribute reads** in `meshio._topology_readers` (:110-124) and `topology_equal`, keeping the old reads as fallback.
   - Output is identical int32 data; prove it with a test comparing the old and new readers.
   - This also speeds up TOPO snapshots, the D14 Object Mode mesh check, and fingerprints.
   - Expected (estimate, re-measure): 300k ~270 → ~170 ms; 1M ~800 → ~420 ms; 3M ~2.8 → ~1.8 s.
2. **Make the cap a preference:** `prefs.py` plus `Settings`, default 300,000 and max 5M.
   - Don't raise the default: plain Blender already spends ~0.4 s per Edit Mode operator at 1M.
3. Removing the limit is not possible. `update_from_editmode` is 1.6 s at 3M and there is no faster Python route; bmesh has no `foreach_get`.

- **Format:** none.
- **Tests:** headless.
- **Size:** about 190 lines, under 1 day.

---

## 5. Linked duplicates (Alt+D): changes recorded once, in one object's history

**Verdict: half stale.**
- **Record everything:** true, on purpose (D39).
- **Default scope (Sculpting only): false.** Each object gets its own sculpt history over the one mesh, and they fight.
- **Warning text is wrong too:** the Alt+D warning `TXT_LINKED` ("they share one history", warnings.py:21-22) is false in the default scope.

- **Cause:** the D39 owner logic lives only in `datarec.refresh_mesh_users` (:221-254), which loops over `tracked_uids()`, i.e. Record everything objects only.
  - In the default scope `dupsep.fix_uids` gives the duplicate its own uid, so `runtime.commit` creates a second History.
  - `_write_state` then writes whole channels from that object's state.
- **Decision:** DECISIONS.md:324-328.
- **Tests:** `test_bl_t1_added.py:469` (Everything scope only), `test_bl_dupsep.py:95-110` (warning only).

Evidence (`l5_linked_dup.py`, same on 5.3 and 4.2):
- After a stroke on A, Alt+D to B, and a stroke on B: A has 1 step, B has 1 step, and they are separate histories.
- **A goto(0) puts the mesh back to the start, wiping B's stroke as well.** B goto(0) gives the state after A's stroke.
- A stroke on B after A's jump would record A's jump inside B's step.

Fix design: one history per shared mesh, using the existing `History.members` and `Registry.attach`.
- Attach the duplicate to the owner's history at Alt+D and on load.
- Resolve the owner for any shared mesh in `history_of`, `can_record`, `commit` and `_enter`, by generalising `refresh_mesh_users` to every mesh user that has a history.
- Detach on Make Single User or when the owner is deleted (reuse the D44 logic).
- Fix the warning text.
- **Format:** none. Members are already saved, as for join.
- **Risks:** Preserve Modes, the global timeline, `joinsep.py`, and `t1_gate_lib.check_object_histories`.
- **Tests:** l5 as a regression test (it fails today), plus reload, Make Single User and deleting the owner; headless.
- **Files:** runtime.py, datarec.py, dupsep.py, warnings.py.
- **Size:** about 200 lines, 1.5 days.

---

## 6. Objects linked from another .blend aren't recorded

**Verdict: inherent for linked objects.** Library overrides are recorded in the session, but **their history is lost on reload**. The site doesn't mention this.

- **Cause:** `datarec.is_linked` (:59-62), `runtime.find_object` (:154-166), `history_of` (:207-211), `SceneAdapter.capture` (:594-598).
- **Tests:** `test_bl_t1_warn.py:163-171`, `test_bl_t1_reload.py:170`, `test_bl_t1_added.py:301`.

Evidence (`l6_linked.py`, same on 5.3 and 4.2):
- A linked object has `is_editable False`. Python can write to it in memory, but Blender reloads linked data from the library, so the stated reason holds.
- An override (`override_hierarchy_create`) is recorded, and goto(0) restores its location. Its mesh stays linked, so Sculpt Mode is refused ("Cannot edit library linked or non-editable override object").
- **After save and reopen, `_hindsight_uid` is gone** (`uid prop None`, no history found). Blender rebuilds overrides from the library on load. `property_overridable_library_set('["_hindsight_uid"]', True)` returns True but doesn't help (run with `L6_OVERRIDABLE=1`).

Fix design:
- In `idmap`, identify an override by `(reference.library.filepath, reference.name, hierarchy_root.name)` and map that to its uid.
- Save the map in `reg.saved["overrides"]`. persist.py:137 writes `saved` as JSON, so there is no version bump.
- Re-stamp the uid on load, before `check_outside_changes`.
- **Tests:** the override part of l6, plus two overrides of one reference.
- **Files:** idmap.py, persistence.py.
- **Size:** about 120 lines, 1 day.

---

## 7. A name another object holds can't be given back

**Verdict: still true when the holder is not being restored; that is a choice, not a limit, from 5.0 on.** Real defect: two objects that swapped names, restored by the same global jump, come back wrong.

- **Cause:** `RNAAdapter.write` assigns `name` with `setattr` (adapters.py:265-275); also `note_name` (:148-156) and `datarec.sync_taken_names` (:691-700).
- **Decision:** DEFECTS D47.
- **Tests:** `test_bl_t1_added.py:410` and `:440`.

What Blender does (`l7_rename_api.py`):
- **Plain assignment:** `ob.name = taken` gives the renamed object ".001" on 4.2, 5.0, 5.1, 5.2 and 5.3.
- **`ID.rename(name, mode=...)`** exists on **5.0 to 5.3**, not on 4.2:
  - NEVER: same as plain assignment.
  - ALWAYS: takes the name; the holder becomes ".001"; returns RENAMED_COLLISION_FORCED.
  - SAME_ROOT: renamed object gets ".001", like NEVER.
- Swapping through a temporary name works on every version.

Hindsight today (`l7_swap_global.py`): two recorded objects swap Left/Right, then the global history goes back.
- 5.3 gives `Left` / `Right.001`; 4.2 gives `Left.001` / `Right`. The result depends on write order.
- A note appears, although no other object holds the name once the jump is done.

Fix design:
- Add `datarec.resolve_taken_names()` and call it at the end of every jump, next to `resolve_missing_refs`. For each NAME_TAKEN entry whose wanted name is now free: rename, update the state, drop the entry. This fixes swaps and chains.
- Do not use `rename(mode='ALWAYS')` by default: it renames an object the user didn't scrub.
- **Format:** none.
- **Tests:** l7_swap_global as a regression test, a 3-cycle, and a holder outside the jump (keeps its note).
- **Size:** about 100 lines, half a day.

---

## 8. Up to 10,000 steps per object

**Verdict: still true, on purpose (parity).** Only the preference's maximum enforces it, and nothing breaks at 50,000.

- **Cause:** `prefs.py:123-125` `max_steps` has default 10000, **min 1 and max 10000**: the user can lower it but not raise it. Also `core/types.py:261`, `core/store.py:188-202`, `runtime._enforce` (:759-791).
- **Origin:** PARITY row 18 (the reference add-on).
- **Tests:** `test_core_store.py:402`, `test_bl_panels.py:152` (asserts max == 10000), `test_bl_recorder.py:393-400`, `test_bl_stress.py`.

Evidence (`l8_steps.py 50000 100000`, bpy53, 300-vertex strokes on a 100k mesh):
- **Commit time is flat:** median 0.60 / 0.60 / 0.59 / 0.65 / 0.60 ms per 10k steps, p95 about 4.7 ms.
- **Memory:** 86.7 MB of history (1.7 KB per step); VmHWM 418 → 746 MB.
- **Jumps:** all bit-exact; time grows linearly with distance. 50k steps: 2.2–2.6 s; 25k: 1.2 s; 1 step: 1 ms.
- **Save and load:** 83 MiB saved in 0.5 s, loaded in 0.4 s. Counts in the file are u32.
- **Global timeline:** `globaltl.global_groups` takes 317 ms at 50k steps. It is rebuilt after every new step when Global history is on (`runtime._groups`, :1208-1221). That cost already exists today for, say, 5 objects × 10k steps, and is worth fixing anyway.
- **Scrubber:** at ~1,000 px, one pixel is 50 steps. The counter width adapts.

Fix design:
- Raise the preference max to 100,000 and keep the default at 10,000. The assertion at `test_bl_panels.py:152` changes on purpose.
- Make `_groups` incremental (append when one step was added), with a test proving it matches a full rebuild.
- Site text: "up to 10,000 by default; Preferences can raise it to 100,000; jumps over tens of thousands of steps take seconds".
- **Size:** about 70 lines, half a day.

---

## 9. Back over a topology change: weights and shapes need Record everything

**Verdict: still true, on purpose** (D43 covered only Record everything objects). Fixable, with the cost paid only by objects that have vertex groups or shape keys.

- **Cause:** `runtime.extras_on` (:271-278) is true only for Record everything objects. It gates `read_mesh_snapshot` (:281-294) and `_observe` (:398). `meshio.write_snapshot` (:308-344) keeps group names, but `clear_geometry` drops the weights, and shape keys are rebuilt from the mesh positions.
- **Decision:** DEFECTS D43.
- **Test:** `test_bl_t1_meshdata.py:60`.

Evidence (`l9_topo_extras.py`, same on 5.3 and 4.2):
- **Default scope:** vertex count back True, group kept True, **weights exact False** (0 of 155 vertices left in the group), shape key count right, **shape exact False**.
- **Record everything:** all True.

Fix design:
- For objects that have vertex groups or shape keys, put extras into snapshots (TOPO before/after and the baseline) in every scope.
- Per-stroke deltas stay without extras, so strokes cost the same.
- Re-read weights on entering Sculpt or Vertex Paint Mode, since weight paint between visits isn't recorded.
- Re-read shape keys at each commit when the object has them, since sculpting on a non-basis key changes them per stroke.

Read cost (`l9_extras_cost.py`, 3 groups and 2 keys, 5.3 / 4.2):

| Vertices | `read_extras` | Shape keys alone | Data |
|---|---|---|---|
| 100k | 148 / 158 ms | 18 / 15 ms | 3.6 MB |
| 1M | 2,301 / 1,321 ms | 250 / 166 ms | 36 MB |

- The weight read loops over vertices in Python. Paid once per mode entry it is fine at 100k and too slow at 1M. So above about 300k, skip and keep the note, unless a faster weight read is found.
- **Format:** none. The `vg:` / `sk:` keys already exist.
- **Files:** runtime.py, meshio.py.
- **Size:** about 140 lines, 1 day.

---

## 10. An object's own history can't bring back a deleted parent or target

**Verdict: still true, on purpose (D37), and sound.** An object's own history can't create another object without making the scene history wrong about which objects exist.

- **Code:** `adapters._note_ref` and `missing_refs` (:165-200), `datarec.resolve_missing_refs` (:703-726).
- **Decisions:** D37, D57, D60.
- **Tests:** `test_bl_t1_added.py:80, 554, 576`.
- **Evidence** (`l10_missing_ref.py`, 5.3 and 4.2): after the own-history jump, parent is None and the note lists the ref. After a global jump, the parent exists and is set again.

Optional improvement: a "Bring back {name}" button in the note.
- It would record a new scene-history step that recreates the object from the body already stored at deletion. This needs the scene to be recorded.
- **Files:** ui_panel.py, an operator in ui_menu.py or ops_core.py, datarec.py.
- **Size:** about 120 lines, 1 day.

---

## 11. Collections you make or delete need "Objects added and deleted"

**Verdict: still true. It is a design coupling, not a Blender limit.**

- **Cause:** the collection tree is part of the scene history (`SceneAdapter.capture` :590-599, `_write_collection_tree` :637-678). The scene is recorded only when the toggle is on (ui_panel.py:295-306).
- **Decision:** DECISIONS.md:310-311.
- **Test:** `test_bl_t1_warn.py:155-160`.

Evidence (`l11_collections.py`, 5.3 and 4.2):
- **Scene not recorded:** going back over M > New Collection leaves the empty "Props" collection. Going back over deleting it leaves the object in Scene Collection with a missing-reference note.
- **Scene recorded:** both cases are exact.

Fix design:
- With Record everything, track the scene with only the existing `scene_collections` kind. The `objects` part runs only when the toggle is on (that part stays opt-in because it stores mesh copies).
- **Format:** none.
- The assertion in `test_bl_t1_warn.py:155-160` changes on purpose.
- This needs an owner decision, because it changes what Record everything records.
- **Size:** about 80 lines, 1 day.

---

## 12. Mesh changed without Hindsight: that object's history is left out

**Verdict: still true, for sculpt-only histories too.** Partly fixable at no recording cost.

- **Cause:** `runtime.saved_prints` (:1412-1442) and `check_outside_changes` (:1445-1470). On a fingerprint mismatch they call `h.reset()`, remove the members and add a note. DELTA steps have no base mesh to bridge from.
- **Decision:** D46.
- **Tests:** `test_bl_t1_outside.py:171-207`.

Evidence (`l12_outside.py`, 5.3 and 4.2):
- History becomes None and the note shows.
- **Recovery found:** Save Versions is on by default (1), so `<file>.blend1` exists and holds the mesh as Hindsight saved it. Its `mesh_fingerprint` matches the saved one.

Fix design:
- Before dropping a history, load that mesh from `.blend1` (`libraries.load`). If its fingerprint matches, push a TOPO step "Changes Outside Hindsight" (before = the .blend1 mesh, after = the current mesh) and keep the history.
- Otherwise keep today's behaviour, for example after two outside saves.
- Storing a full snapshot in the sidecar at each save would always work, but costs disk; not recommended by default.
- **Format:** none.
- **Size:** about 140 lines, half a day.

---

## Summary

| # | Limit | Verdict | Fixable here | Size | Files |
|---|---|---|---|---|---|
| 1a | Multires | Still true; per-stroke impractical (flush 0.4–3 s); exact per-visit possible | partly | 350 lines, 2–3 d | new bl/multires.py, runtime, warnings, meshio, core/step |
| 1b | Dyntopo | Still true; per stroke up to ~200k tris, per visit above | partly | 400 lines, 2 d | runtime, warnings, meshio, core/step, core/store |
| 1c | Strokes made while paused lost (not on the site) | Still true | yes | 20 lines | runtime |
| 2 | Animated or driven | Inherent for live F-curves; one-index keys and muted curves fixable | partly | 80 lines | adapters |
| 3 | Physics and particles | Still true (modifiers come back with defaults) | yes, except caches | 180 lines | adapters, warnings |
| 4 | Edit Mode 300k | Still true; half inherent, half slow reads | partly | 190 lines | meshio, runtime, prefs, core/types, warnings |
| 5 | Linked duplicates | True with Everything; wrong in the default scope | yes | 200 lines | runtime, datarec, dupsep, warnings |
| 6 | Linked objects | Inherent; overrides lose their history on reload | overrides yes | 120 lines | idmap, persistence |
| 7 | Taken names | Still true; swaps in one jump fixable | partly | 100 lines | datarec, runtime |
| 8 | 10,000 steps | Still true (preference max); fine at 50k | yes | 70 lines | prefs, runtime, core/globaltl |
| 9 | Topology extras | Still true (D43) | yes, up to ~300k | 140 lines | runtime, meshio |
| 10 | Deleted parent or target | Still true, sound | improvement only | 120 lines | ui_panel, ui_menu, datarec |
| 11 | Collections need the scene | Still true, coupling | yes (owner decision) | 80 lines | datarec, adapters, ui_panel, warnings |
| 12 | Mesh changed outside | Still true; .blend1 recovery works | partly | 140 lines | runtime |

**Stale texts:**
- PARITY.md:144-145 ("Dyntopo cannot be enabled headless").
- `warnings.TXT_LINKED` in the default scope.
- Site item 3: the physics modifiers do come back, with default settings.
- `tests/t1_ops.py:177,179`: the test helper fails on 4.2 (a bug, not a text).

## Work packages (non-overlapping file ownership)

- **WP-A, mesh reads and caps** (limit 4, the step-cap preference of limit 8, the `.sculpt_dyntopo` skip):
  - Owns: bl/meshio.py, bl/prefs.py, core/types.py, tests/test_bl_t1_perf.py, new tests/test_bl_meshio_reads.py.
  - Sends as patches: the `EDIT_STEPS_MAX` change to WP-C; the panels-test assertion to WP-E.
- **WP-B, adapters** (limits 2, 3):
  - Owns: bl/adapters.py, tests/test_bl_adapters.py.
  - Adds a new physics test to tests/test_bl_t1_added.py (coordinate: only the new test).
- **WP-C, runtime** (limits 1b commit and restore, 1c, 5, 9, 12):
  - Owns: bl/runtime.py, bl/dupsep.py, tests/test_bl_recorder.py, tests/test_bl_dupsep.py, tests/test_bl_t1_outside.py, tests/test_bl_t1_meshdata.py, new tests/test_bl_limits_runtime.py.
  - Order: 5 first, then 9, 12 and 1c.
- **WP-D, datarec and timeline** (limits 7, 11, incremental groups for 8):
  - Owns: bl/datarec.py, core/globaltl.py, tests/test_bl_t1_warn.py.
  - Limit 11 waits for the owner's decision.
  - The runtime call site for limit 7 goes to WP-C as a patch.
- **WP-E, warnings and UI text, limit 10's button:**
  - Owns: bl/warnings.py, bl/ui_panel.py, bl/ui_menu.py, tests/test_bl_warnings.py, tests/test_bl_panels.py.
  - Applies text patches from the other packages.
- **WP-F, Multires and step format** (limit 1a, the Dyntopo TOPO chain):
  - Owns: new bl/multires.py, core/step.py, core/store.py, tests/test_core_format.py, new tests/test_bl_multires.py.
  - Needs a real-window check of undo before release.
- **WP-G, overrides** (limit 6):
  - Owns: bl/idmap.py, bl/persistence.py, tests/test_bl_t1_reload.py.
- **Docs and site text** (DEFECTS, COVERAGE, PARITY, CHANGELOG, site): only after each package lands, by whoever owns the docs.
- **tests/t1_ops.py** 4.2 guard: give it to WP-D.

## Not verifiable here
- Real Multires and Dyntopo brush strokes.
- The undo entries pushed by `multires_external_save` and `pack`.
- The order of the depsgraph handler versus Dyntopo coming back on at Sculpt Mode entry, in a real window.
- `multires_external_save` on a never-saved file.
- The scrubber at 50k steps on screen.
- Timings are single runs on a loaded machine except where min/med is given.