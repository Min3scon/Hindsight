> 2026-11-18. An agent's report as delivered, unchanged below this line. Audit report only: other agents made the fixes (see OPTIMISATION_LOG.md and DEFECTS.md).

# Ponytail audit: Hindsight (hindsight/, 13,100 lines)

What this repo does: Hindsight is a Blender 4.2+ add-on that records every sculpt stroke and object change as a step (only the changed vertices; a full copy of the mesh when the topology changes), so the artist can scrub back and forth with a slider, mixed with Blender's own undo. History is saved beside or inside the .blend, a crash journal keeps steps between saves, and Movie Mode plays the history back as a timelapse.
Load assumed: one artist, one Blender session of several hours, meshes up to a few million vertices, up to 10,000 steps per object (the default cap), a few to a few hundred objects. Hot paths: commit per stroke, `runtime.tick` (10 times a second, always), viewport and sidebar redraws, the 0.2 s slider timer.

Scope note: camrig.py, rigmark.py, core/framing.py, the Movie Mode playback path, `runtime.goto` / `global_goto` and save/load memory in core/persist.py are being changed by other agents and were not judged. Measurements: Blender 5.3.0 as a Python module (`/home/user/venvs/bpy53/bin/python`), headless; scripts in this folder (m1.py to m11.py).

## Must fix

1. **Crash recovery rebuilds the wrong history once the step cap has dropped steps** (`hindsight/bl/runtime.py:744-773`, `hindsight/core/persist.py:333-344`)
   - **What this is:** Each stroke writes a `STEP` record with `at` (its index in the history) to the crash journal. `_enforce` then drops the oldest steps when a history passes "Steps per object" (default 10,000) or the memory limit.
   - **Problem:** Those drops are never written to the journal. After a drop the next step has the same `at` as the one before, and replay cuts the earlier step off. Measured (`m6.py`, cap set to 5, 8 strokes): live history holds steps `[4, 5, 6, 7, 8]`, the journal says `STEP at 0,1,2,3,4,5,5,5`, replay builds `[1, 2, 3, 4, 5, 8]`. Steps 6 and 7 are lost, and step 8's change is applied on top of step 5, so scrubbing gives meshes that never existed (the D27 class of bug).
   - **Fix:** Journal what the caps drop. In `_enforce`, note `len(h.entries)` per history before the drops and write `_journal("DROP", {"hist": h.uid, "n": dropped})` for each history that got shorter (incremental path and full path). `replay` already handles `DROP`. About 8 lines.
   - **If we skip it:** With "On open: Interrupted session" on, any hours-long session that reached 10,000 steps (or the memory limit) and then crashed comes back with a corrupted history.
   - Risk of the fix: low. `tests/test_core_journal.py` covers `DROP` replay; no test combines the cap with the journal (add one: cap 5, 8 strokes, replay equals live). `tools/mutation_test.py` and `tools/mutation_bl.py` patch nothing in `_enforce` or `replay`'s `STEP` branch (mutation_test patches only the checksum and `bad_at` lines of persist.py).

2. **The memory limit can drop steps another object has not reached yet, so its mesh no longer matches its history** (`hindsight/core/store.py:180-221`)
   - **What this is:** When all histories together pass the memory limit (default 4 GB), `enforce_caps` drops the oldest steps by step number, across all objects.
   - **Problem:** It ignores each history's cursor. If object B is scrubbed back to step 1 (local slider, or the global slider moved back and then a new stroke on A), and eviction drops B's 3 oldest steps, B's cursor is clamped to 0 while its mesh still shows "after step 1". Going forward then applies step 4 on top of step 1. Reproduced in pure core (`m9.py`): after eviction, B at its last step is not equal to the real last state.
   - **Fix:** Never drop at or past the cursor: seed the heap only with histories whose `cursor > 0`, push the next entry only while `j + 1 < histories[i].cursor`, and cap `over` in the max-steps loop with `h.cursor`. Three small conditions.
   - **If we skip it:** Silent wrong meshes on objects the artist had scrubbed back, once a long session reaches the memory limit.
   - Risk of the fix: low. `tests/test_core_store.py` (`test_caps_memory`, `test_caps_memory_skewed`, `test_caps_max_steps`) covers the plan but always with the cursor at the end; add one with a cursor at 1. No mutants in store.py.

3. **Global history rebuilds and sorts the whole timeline several times per redraw and 5 times a second** (`hindsight/core/globaltl.py:36-63`, `hindsight/bl/runtime.py:1241-1289`, `hindsight/bl/slider_prop.py:15-71`, `hindsight/bl/scrubber.py:350-365, 594-597`, `hindsight/bl/header.py:47`)
   - **What this is:** With global history on, `scrub_info` calls `global_length` and `global_position`; each one runs `global_groups`, which walks every step of every history and sorts them. `step_list` does it twice more.
   - **Problem:** Measured (`m1.py`, 20 objects): `scrub_info` costs 2.3 ms at 2,000 steps, 18 ms at 10,000, 152 ms at 50,000; `step_list` 21 ms at 10,000. It runs on every viewport redraw in the slider draw callback, again in the gizmo poll, again in the header button, again in the sidebar (`draw_main` and the slider property getter), and the slider timer calls it 5 times a second even when nothing changes. At 10,000 steps that is at least 3 x 18 = 54 ms per viewport redraw and 90 ms of CPU per second idle. Movie Mode calls it on every frame too (and `global_goto` rebuilds the groups 4 more times per jump).
   - **Fix:** Cache the groups and the position on the Registry, keyed on `(reg.next_seq, per history (uid, len(entries), first seq, cursor), visible set)`. Building that key costs microseconds; measured with the groups cached only, `scrub_info` drops from 17.4 ms to 2.3 ms at 10,000 steps (`m10.py`), and to well under 0.1 ms with the position cached too. In `step_list`, take labels only for `lo..hi`, not for every entry. Fold `slider_prop._timer` into `runtime.tick` (one persistent timer instead of two).
   - **If we skip it:** The viewport stutters once the global timeline has a few thousand steps and becomes unusable near 50,000; Movie Mode renders pay the same cost every frame.
   - Risk of the fix: medium. Covered by `tests/test_bl_tier1.py` (`test_step_list`), `tests/t1_gate_lib.py` (global position checks in every T1 gate row), `test_bl_panels.py`, `test_bl_movie.py`. `tools/mutation_bl.py` patches two lines in globaltl (`sorted(undo, reverse=True)` in `transition`, `cur[hid] = max(cur[hid], i + 1)` in `plan_global_goto`) and the `any_uid(ob)` lines of `_visible_uids`: keep those exact lines. Coordinate with the agent working on `global_goto`, which calls the same functions.

## Should fix

4. **Up to 512 whole-scene copies of recorded values are kept for Blender's undo, though Blender keeps 32 undo steps** (`hindsight/bl/datarec.py:733, 755-776`)
   - **What this is:** Each global jump that goes onto Blender's undo stack stores `recorded_values()` before and after: a copy of every tracked object's value dict.
   - **Problem:** The list keeps the last 512 jumps. Measured (`m11.py`, 200 cubes plus the scene recorded): 469 KB per jump, so 235 MB at 512 jumps. Blender's default undo steps is 32 (its maximum 256), so jumps older than that can never be matched again.
   - **Fix:** Cap at Blender's own setting: `del _JUMPS[:-max(1, bpy.context.preferences.edit.undo_steps)]` instead of `JUMPS_MAX`. One line (15 MB at the default 32).
   - **If we skip it:** A long session with Record everything holds hundreds of MB that can never be used.
   - Risk of the fix: low. `tests/test_bl_t1_real_undo.py` and `test_bl_t1_jumps.py` cover jump matching. `tools/mutation_bl.py` patches the two lines just above (`drop_undone_jumps()` / `_JUMPS.append(`): change only the `del` line. Tell the `global_goto` agent.

5. **`log.error_once` stops being "once" after 256 different errors** (`hindsight/bl/log.py:41-51`)
   - **What this is:** Every handler and timer logs errors through `error_once`, keyed by place and message, so a repeating error prints once.
   - **Problem:** When `_SEEN` holds 256 keys, new keys are no longer added, but the error is still printed. From then on any error that repeats in `tick` (10 times a second) prints its full traceback 10 times a second. Keys include the message text (object names, values), so 256 is reachable in a long session with many objects.
   - **Fix:** Drop the cap: always `_SEEN.add(key)` (a key is a short tuple; 10,000 keys are about 1 MB). Or, if a cap is wanted, return False once full.
   - **If we skip it:** Console spam and a slow Blender (printing tracebacks on Windows is slow) after a long, error-prone session.
   - Risk of the fix: low. No test covers `_MAX_SEEN`; add one that logs 300 keys and then the same key twice. No mutants nearby.

6. **Once the memory limit is reached, every stroke rescans every step** (`hindsight/bl/runtime.py:744-773`)
   - **What this is:** `_enforce` keeps an estimate so the full rescan (`enforce_caps`, `enforce_tiers`, `total_bytes`) runs only every 64 commits.
   - **Problem:** After a full rescan the estimate is set to the real total, which is right under the limit, so the next stroke crosses it again and every stroke does the full scan. Measured (`m2.py`): 1.7 ms per stroke at 10,000 steps, 5.8 ms at 30,000, next to a 1 ms commit at 100k vertices.
   - **Fix:** Evict down to a little below the limit (for example 95%) in `enforce_caps`, so the next full scan comes about 5% of the limit later. One constant.
   - **If we skip it:** Commits get 2 to 6 times slower for the rest of the session once the limit is hit.
   - Risk of the fix: low. `tests/test_core_store.py` `test_caps_memory*` assert exact totals and need the new target. No mutants in store.py.

7. **Dead code** (lean, delete)
   - **What this is / Problem:** Never called anywhere in hindsight/, tests/, tools/, tests_ui/ or bench/ (grepped, including strings): `runtime.settings` (89-90, only listed in INTERFACES.md), `runtime._sync_edit` (262-267), `runtime._PENDING_TRUNCATE` (written at 631 and popped at 712, never read), `adapters.timed_capture` (776-779), `step.read_header` (core/step.py:274-284), `globaltl.global_order` (26-33, only in INTERFACES.md), `delta._zigzag`, `_unzigzag`, `_difference` (core/delta.py:159-167, 178-184), `Step.is_topo` (core/types.py:184-185), the `Entry._nbytes` slot (types.py:207, 214), `History.positions_of` (history.py:158-159, used only by its own test). `delta._HDR_V1` is the same Struct as `_HDR`.
   - **Fix:** Delete them, the one test lines in `tests/test_core_history.py:394-396`, and their INTERFACES.md lines.
   - **If we skip it:** About 60 lines to read and keep in step for nothing.
   - Risk of the fix: none for behaviour. `tools/mutation_test.py` patches `a ^= sign` / `a <<= 1` in `_zigzag_inplace` and other delta.py lines, not these functions: check its exact strings still match after the delete.

8. **The same helper written twice** (lean, reuse)
   - **What this is / Problem:** `runtime.commit` (573-580) builds the step label from `window_manager.operators[-1].bl_label` exactly like `datarec._last_label` (387-395). `runtime.stroke_running` (1100-1108) and `datarec._operation_running` (367-375) are the same window/modal-operator loop with a different filter. `_scene_props` exists in runtime.py:236, filtering.py:18 and ui_panel.py:52 (and `_props` in movie.py:14, camrig.py:28). `UID_PROP` is defined in runtime.py:19 and idmap.py:13, with two separate uid-to-name caches (`runtime._UID_CACHE`, `idmap._cache`).
   - **Fix:** `label = label or datarec._last_label(default)`; one `_modal_ops()` generator used by both loops; one `_scene_props` in ctx.py; runtime imports `UID_PROP` from idmap.
   - **If we skip it:** Two places to fix when Blender changes the operator API (it did in 5.0 for D13).
   - Risk of the fix: low. `test_bl_regressions` (stroke split), `test_bl_t1_gate` (labels). No mutants on these lines.

## Nice to have

9. **The exclusion check runs over every tracked object on every tick** (`hindsight/bl/datarec.py:141-153`)
   - **What this is:** `datarec.tick` calls `check_exclusion`, which looks up every tracked object and asks Collection Filtering about it.
   - **Problem:** Measured (`m3.py`, 200 tracked empties, filter off): 1.6 ms per tick, so 16 ms of CPU per second while idle and 80 ms per second while busy (the tick runs every 0.02 s then). Idle tick without Record everything: 0.01 ms.
   - **Fix:** Return at once when the scene's `filter_mode` is `NONE` and `_EXCLUDED` is empty (rig objects are never tracked, `set_tracked` refuses them).
   - **If we skip it:** A steady few percent of one core in big recorded scenes.
   - Risk of the fix: low. `tests/test_bl_t1_outside.py` covers D54 (Changes While Excluded). No mutants nearby.

10. **A topology step copies the whole mesh one time too many** (`hindsight/bl/runtime.py:395-399, 433-441`)
    - **What this is:** For a topology change (Remesh, Edit Mode exit with new faces), `_observe` stores `before=st.to_snapshot()` (a full copy), then `_apply_to_state` copies `after` into the state.
    - **Problem:** The state's old arrays are thrown away right after, so the `before` copy is not needed. Measured (`m7` sizes, 1M vertices, 88 MB of arrays): 35 to 68 ms and 88 MB extra peak memory per topology step.
    - **Fix:** `before=Snapshot(dict(st.counts), st.arrays)` (hand over the arrays; `set_snapshot` replaces `st.arrays` with a new dict right after, and the paths that keep `st` never write into those arrays).
    - **If we skip it:** Remesh on big meshes stays slower and needs more memory than needed.
    - Risk of the fix: medium (aliasing). Covered by `test_bl_t0_gate` / `t0_ops.py` (remesh) and `test_bl_regressions`; `HINDSIGHT_VERIFY=1` checks each written step. No mutants nearby.

11. **Matching Blender's undo after a topology step copies both meshes to bytes** (`hindsight/bl/runtime.py:369-385`)
    - **What this is:** `_inverse_matches.same` compares two snapshots with `a.tobytes() != b.tobytes()`, up to 4 times per undo.
    - **Problem:** Measured (`m7.py`, 88 MB snapshot): 66 ms and 61 MB extra peak per compare; comparing in place is 26 ms.
    - **Fix:** `np.array_equal(a.view(bits), b.view(bits))` with the same-dtype bit view `delta.view_bits` already gives (bit-exact, NaN-safe).
    - **If we skip it:** Ctrl+Z after a remesh on a big mesh lags by a quarter of a second.
    - Risk of the fix: low. `test_bl_regressions` (native undo, D6) covers it. No mutants nearby.

12. **Finding the changed vertices makes a full-size XOR array first** (`hindsight/core/delta.py:66-91`)
    - **What this is:** `diff` builds `ob ^ nb` for the whole mesh, then ORs the columns.
    - **Problem:** Measured (`m5.py`, 1M vertices, 2% changed): the mask takes 4.0 ms; comparing columns directly (`m = ob != nb; m[:,0] | m[:,1] | m[:,2]`) takes 3.4 ms and a 4x smaller temporary. About 0.6 ms per stroke at 1M.
    - **Fix:** Replace `_changed_mask(ob ^ nb)` with the column compare (same rows by definition).
    - **If we skip it:** Nothing breaks; each stroke is a little slower on big meshes.
    - Risk of the fix: low. `tests/test_core_store.py` `test_diff_apply_all_types`, `test_nan_negzero_bits`; `tools/mutation_test.py` patches the `return ChannelDelta(... nb[idx] - ob[idx] ...)` line just below: keep it unchanged.

13. **The "weights were clamped" note never goes away** (`hindsight/bl/runtime.py:443-455`, `hindsight/bl/warnings.py:204`)
    - **What this is:** After a restore puts back vertex group weights outside 0 to 1, the object's id goes into `WEIGHTS_CLAMPED` and the sidebar shows a note.
    - **Problem:** Nothing ever removes the id (not a later restore without clamping, not `set_registry` on file load), so the note stays for the whole session, and for the same object after reopening the file.
    - **Fix:** In `_clamp_state_weights`, `else: WEIGHTS_CLAMPED.discard(uid)`; clear the set in `set_registry`.
    - **If we skip it:** A stale, confusing note.
    - Risk of the fix: low. `tests/test_bl_t1_meshdata.py:82` checks the set is filled; add the "cleared" side. No mutants nearby.

Left out: 2 smaller items (the Memory panel sums every step on each redraw, but it is closed by default; `meshio.read_extras` loops over every vertex in Python, but only for Record everything objects with vertex groups).

Verdict: fix 1 and 2 first (they can corrupt history silently), then 3 (global history is unusable at the stated 10,000-step load and slows Movie Mode renders).

Lean: -70 lines, -0 dependencies possible.

Not checked:
- Not judged (other agents): camrig.py, rigmark.py, core/framing.py, movie.py playback, `runtime.goto` / `_goto_history` / `global_goto`, save/load memory in core/persist.py. Pointer for that agent: in EMBED/BOTH mode the whole history stays in RAM a second time as a base64 string in the `.hindsight_history` Text after every save and after loading (`bl/persistence.py:230`).
- Read only in part: adapters.py, joinsep.py, dupsep.py, morph.py, selfcheck.py, cleanup.py, ui_menu.py, prefs.py, core/step.py, core/codec.py, core/importer.py (302 lines shipped with no Blender side yet; HANDOFF says it is planned, so not counted as dead).
- Not run: the real Blender UI (redraw counts per frame are from reading the code, not from a trace), Blender 5.0/5.1 (measured on 5.3.0 module), the full test suite and mutation tools.
