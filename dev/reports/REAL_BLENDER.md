# Real Blender run, whole suite (second run, commit 1939cf5)

After merging `main-session-work` (D99 to D104 and test fixes). Command per version:
`HINDSIGHT_NATIVE=require python3 tools/ci/run_suite.py --blender <exe> --log .tmp/m2/rb<v>.log`.
Result: 5.3 70/71 files pass (911 s), 4.2 70/71 (781 s). The only failure on both is
`test_bl_t1_migrate.py` (needs `.tmp/old/b1.zip`; on 4.2 also `test_format3_file`, which is a
known-fail file). Module suites (bpy53, bpy42): 69/71 each; fails only `test_bl_metadata`
(`test_opens_without_hindsight`) and `test_bl_t1_migrate`.
All red items of the first run below are gone: 4.2 crashes of `t0_gate`/`t0_reload`,
parent_type, `test_damaged_blobs`, adapters denormal, t1_prop.

| file | Blender 5.3.0 | Blender 4.2.23 |
|---|---|---|
| test_bl_adapters.py | ok 9/9 1.7s | ok 9/9 1.5s |
| test_bl_adapters_gn.py | ok 1/1 0.6s | ok 1/1 0.5s |
| test_bl_adapters_physics.py | ok 1/1 0.6s | ok 1/1 0.7s |
| test_bl_audit.py | ok 10/10 1.9s | ok 10/10 3.7s |
| test_bl_beta.py | ok 2/2 0.5s | ok 2/2 0.3s |
| test_bl_camrig.py | ok 15/15 1.6s | ok 15/15 1.3s |
| test_bl_camrig_deform.py | ok 7/7 4.3s | ok 7/7 3.4s |
| test_bl_camrig_handover.py | ok 10/10 1.4s | ok 10/10 1.0s |
| test_bl_camrig_lights.py | ok 4/4 0.8s | ok 4/4 0.6s |
| test_bl_camrig_modifiers.py | ok 15/15 3.6s | ok 15/15 2.8s |
| test_bl_camrig_pixels.py | ok 1/1 0.8s | ok 1/1 0.7s |
| test_bl_camrig_render.py | ok 9/9 2.3s | ok 9/9 1.9s |
| test_bl_datarec_fast_paths.py | ok 16/16 4.1s | ok 16/16 3.3s |
| test_bl_datarec_fast_writes.py | ok 2/2 0.6s | ok 2/2 0.4s |
| test_bl_datarec_perf_counts.py | ok 4/4 1.1s | ok 4/4 1.0s |
| test_bl_draw_filtering.py | ok 2/2 0.7s | ok 2/2 0.4s |
| test_bl_draw_panels.py | ok 4/4 0.6s | ok 4/4 0.5s |
| test_bl_draw_scrubber.py | ok 5/5 0.7s | ok 5/5 0.6s |
| test_bl_dupsep.py | ok 8/8 1.1s | ok 8/8 0.8s |
| test_bl_install.py | ok 2/2 1.0s | ok 2/2 0.7s |
| test_bl_join.py | ok 5/5 1.3s | ok 5/5 0.9s |
| test_bl_linked_dup.py | ok 3/3 0.9s | ok 3/3 0.5s |
| test_bl_menu.py | ok 11/11 0.6s | ok 11/11 0.3s |
| test_bl_metadata.py | ok 6/6 2.6s | ok 6/6 1.5s |
| test_bl_morph.py | ok 10/10 1.4s | ok 10/10 0.9s |
| test_bl_movie.py | ok 14/14 1.9s | ok 14/14 1.5s |
| test_bl_panels.py | ok 10/10 0.5s | ok 10/10 0.4s |
| test_bl_persistence.py | ok 8/8 1.4s | ok 8/8 1.0s |
| test_bl_recorder.py | ok 20/20 2.7s | ok 20/20 2.0s |
| test_bl_recovery.py | ok 11/11 1.9s | ok 11/11 1.2s |
| test_bl_recovery_faults.py | ok 9/9 12.0s | ok 9/9 11.0s |
| test_bl_regressions.py | ok 8/8 0.8s | ok 8/8 0.7s |
| test_bl_rig_ignored.py | ok 9/9 1.0s | ok 9/9 0.8s |
| test_bl_runtime_caps.py | ok 2/2 2.8s | ok 2/2 2.6s |
| test_bl_runtime_topo.py | ok 4/4 0.8s | ok 4/4 0.6s |
| test_bl_runtime_ui.py | ok 4/4 0.9s | ok 4/4 0.6s |
| test_bl_saveload_embed.py | ok 2/2 0.7s | ok 2/2 0.5s |
| test_bl_scrubber.py | ok 9/9 0.4s | ok 9/9 0.3s |
| test_bl_stress.py | ok 1/1 19.2s | ok 1/1 16.3s |
| test_bl_t0_gate.py | ok 6/6 3.2s | ok 6/6 2.4s |
| test_bl_t0_reload.py | ok 4/4 7.3s | ok 4/4 5.8s |
| test_bl_t1_added.py | ok 19/19 2.7s | ok 19/19 2.4s |
| test_bl_t1_corrupt.py | ok 2/2 2.7s | ok 2/2 2.2s |
| test_bl_t1_faults.py | ok 7/7 1.5s | ok 7/7 1.1s |
| test_bl_t1_gate.py | ok 6/6 3.7s | ok 6/6 2.8s |
| test_bl_t1_jumps.py | ok 4/4 1.1s | ok 4/4 0.7s |
| test_bl_t1_meshdata.py | ok 1/1 0.9s | ok 1/1 0.7s |
| test_bl_t1_migrate.py | FAIL (1 pass, 1 fail) | FAIL (0 pass, 2 fail) |
| test_bl_t1_outside.py | ok 7/7 1.1s | ok 7/7 0.9s |
| test_bl_t1_perf.py | ok 3/3 397.5s | ok 3/3 339.6s |
| test_bl_t1_prop.py | ok 1/1 266.5s | ok 1/1 230.1s |
| test_bl_t1_real_undo.py | ok 5/5 2.6s | ok 5/5 2.4s |
| test_bl_t1_reload.py | ok 7/7 17.8s | ok 7/7 15.6s |
| test_bl_t1_warn.py | ok 9/9 54.8s | ok 9/9 51.2s |
| test_bl_tier1.py | ok 11/11 7.7s | ok 11/11 7.1s |
| test_bl_warnings.py | ok 13/13 0.9s | ok 13/13 0.8s |
| test_core_codec.py | ok 9/9 0.9s | ok 9/9 0.8s |
| test_core_format.py | ok 9/9 0.4s | ok 9/9 0.3s |
| test_core_framing.py | ok 16/16 2.2s | ok 16/16 1.8s |
| test_core_history.py | ok 10/10 1.5s | ok 10/10 1.3s |
| test_core_importer.py | ok 4/4 0.5s | ok 4/4 0.4s |
| test_core_journal.py | ok 14/14 0.8s | ok 14/14 0.6s |
| test_core_journal_faults.py | ok 11/11 6.3s | ok 11/11 5.5s |
| test_core_migration.py | ok 4/4 0.7s | ok 4/4 0.7s |
| test_core_native.py | ok 9/9 3.2s | ok 9/9 3.1s |
| test_core_native_steps.py | ok 9/9 12.2s | ok 9/9 11.4s |
| test_core_records.py | ok 5/5 0.9s | ok 5/5 0.7s |
| test_core_saveload_storage.py | ok 10/10 2.9s | ok 10/10 2.7s |
| test_core_store.py | ok 19/19 16.4s | ok 19/19 15.2s |
| test_tools_coverage.py | ok 3/3 0.5s | ok 3/3 0.3s |
| test_tools_precheck.py | ok 7/7 0.5s | ok 7/7 0.3s |

# First run (commit 783221b), kept for the record


Command per version: `HINDSIGHT_NATIVE=require python3 tools/ci/run_suite.py --blender <exe> --log .tmp/rb/<v>.log`.
Blender 5.3.0 and 4.2.23 Linux binaries, headless, native engine required. Commit 783221b.
Result: 5.3 63/66 files pass (601 s), 4.2 60/66 files pass (754 s). Cells: pass/total and seconds.

## Failures, with message

### 5.3.0
- `test_bl_adapters.py` `test_awkward_values_round_trip`: bare `AssertionError` at line 200,
  `assert F.pack(stored[0][0]) == F.pack(-0.0) and stored[0][2] != 0.0` (-0 and a denormal must survive).
  Same cause as the t1_prop failure below: Blender runs with flush-to-zero, denormals become 0.0. Passes on 4.2 and in
  the bpy module. Open; main session owns it.
- `test_bl_t1_migrate.py` `test_beta1_file`: `AssertionError: .tmp/old/b1.zip is missing (the beta 1 release zip, used by Round F too)`. Known: needs the zip. Not a product fault.
- `test_bl_t1_prop.py`: `FloatingPointError: Got allow_subnormal=True, but we can't represent subnormal floats right now ... usually because something was compiled with -ffast-math`. Hypothesis was installed; Blender's flush-to-zero makes it refuse subnormal floats. Fixed in the test (`allow_subnormal=False` on the three `st.floats`, commit after 0115dd2). Rerun in real Blender 5.3: `2000 generated sequences checked in 271 s`, PASS.

### 4.2.23
- `test_bl_t0_gate.py`, `test_bl_t0_reload.py`: Blender crashes, rc=-11 (SIGSEGV), no Python traceback. Crash file `.tmp/profiles/headless-blender-4.2.23-linux-x64/temp/blender.crash.txt`, backtrace has no symbols. Pass on 5.3. Not investigated further. Open.
- `test_bl_t1_gate.py` `test_gate_parenting`: `TypeError: bpy_struct: item.attr = val: enum "" not found in ('OBJECT')`. The known 4.2 parent_type quirk.
- `test_bl_t1_reload.py` (6 of 7 fail): `AssertionError: C at its position 5: ['/rna/parent_type:  != OBJECT']`. Same quirk. Known.
- `test_bl_t1_migrate.py`: `test_format3_file`: `RuntimeError: ... Failed to read blend file '.tmp/migrate/format3/scene.blend', not a blend file` (4.2 cannot read the generated file; 5.3 can). `test_beta1_file`: missing `.tmp/old/b1.zip`, as on 5.3.
- `test_core_native_steps.py` `test_damaged_blobs`: `AssertionError: ('damaged 50', 0, 40, 'native', ('ok', [('A', ['c_boolean|POINT|BOOLEAN', ...])]), ('err', 'CorruptStep', 'compressed data damaged: decompression error: Corrupted block detected'))`. Native engine returns ok on a damaged blob where the Python reference raises CorruptStep. Passes on 5.3 (same wheel). Open: real product issue on 4.2 or a wheel/zstd difference; needs a look.
  `test_mixed_histories` PASSES in real Blender on both versions.

## Per file

| file | Blender 5.3.0 | Blender 4.2.23 |
|---|---|---|
| test_bl_adapters.py | FAIL (7 pass, 1 fail, rc=1) | ok 8/8 2.7s |
| test_bl_audit.py | ok 10/10 1.7s | ok 10/10 1.4s |
| test_bl_beta.py | ok 2/2 0.5s | ok 2/2 0.3s |
| test_bl_camrig.py | ok 15/15 1.6s | ok 15/15 1.3s |
| test_bl_camrig_deform.py | ok 7/7 4.7s | ok 7/7 3.3s |
| test_bl_camrig_handover.py | ok 10/10 1.3s | ok 10/10 1.0s |
| test_bl_camrig_lights.py | ok 4/4 0.7s | ok 4/4 0.6s |
| test_bl_camrig_modifiers.py | ok 15/15 3.5s | ok 15/15 2.7s |
| test_bl_camrig_pixels.py | ok 1/1 1.8s | ok 1/1 1.3s |
| test_bl_camrig_render.py | ok 9/9 2.2s | ok 9/9 2.0s |
| test_bl_datarec_fast_paths.py | ok 16/16 4.0s | ok 16/16 3.3s |
| test_bl_datarec_fast_writes.py | ok 2/2 0.5s | ok 2/2 0.5s |
| test_bl_datarec_perf_counts.py | ok 4/4 1.3s | ok 4/4 1.1s |
| test_bl_draw_filtering.py | ok 2/2 0.6s | ok 2/2 0.5s |
| test_bl_draw_panels.py | ok 4/4 0.6s | ok 4/4 0.5s |
| test_bl_draw_scrubber.py | ok 5/5 0.6s | ok 5/5 0.6s |
| test_bl_dupsep.py | ok 8/8 1.1s | ok 8/8 1.6s |
| test_bl_install.py | ok 2/2 1.6s | ok 2/2 1.0s |
| test_bl_join.py | ok 5/5 1.0s | ok 5/5 0.8s |
| test_bl_menu.py | ok 11/11 0.4s | ok 11/11 0.3s |
| test_bl_metadata.py | ok 6/6 2.5s | ok 6/6 1.5s |
| test_bl_morph.py | ok 10/10 1.1s | ok 10/10 0.9s |
| test_bl_movie.py | ok 14/14 1.9s | ok 14/14 1.4s |
| test_bl_panels.py | ok 10/10 0.4s | ok 10/10 0.4s |
| test_bl_persistence.py | ok 8/8 1.3s | ok 8/8 1.0s |
| test_bl_recorder.py | ok 20/20 2.5s | ok 20/20 2.2s |
| test_bl_recovery.py | ok 11/11 1.8s | ok 11/11 1.3s |
| test_bl_regressions.py | ok 8/8 1.0s | ok 8/8 0.6s |
| test_bl_rig_ignored.py | ok 9/9 1.0s | ok 9/9 0.8s |
| test_bl_runtime_caps.py | ok 2/2 3.0s | ok 2/2 2.7s |
| test_bl_runtime_topo.py | ok 4/4 0.9s | ok 4/4 0.7s |
| test_bl_runtime_ui.py | ok 4/4 0.9s | ok 4/4 0.7s |
| test_bl_saveload_embed.py | ok 2/2 0.6s | ok 2/2 0.5s |
| test_bl_scrubber.py | ok 9/9 0.4s | ok 9/9 0.3s |
| test_bl_stress.py | ok 1/1 18.5s | ok 1/1 17.0s |
| test_bl_t0_gate.py | ok 6/6 3.1s | FAIL (0 pass, 0 fail, rc=-11) |
| test_bl_t0_reload.py | ok 4/4 7.1s | FAIL (0 pass, 0 fail, rc=-11) |
| test_bl_t1_added.py | ok 19/19 3.1s | ok 19/19 2.2s |
| test_bl_t1_corrupt.py | ok 2/2 2.5s | ok 2/2 2.1s |
| test_bl_t1_faults.py | ok 7/7 1.6s | ok 7/7 1.1s |
| test_bl_t1_gate.py | ok 6/6 3.3s | FAIL (5 pass, 1 fail, rc=1) |
| test_bl_t1_jumps.py | ok 4/4 0.9s | ok 4/4 0.7s |
| test_bl_t1_meshdata.py | ok 1/1 0.9s | ok 1/1 0.7s |
| test_bl_t1_migrate.py | FAIL (1 pass, 1 fail, rc=1) | FAIL (0 pass, 2 fail, rc=1) |
| test_bl_t1_outside.py | ok 7/7 1.2s | ok 7/7 0.9s |
| test_bl_t1_perf.py | ok 3/3 376.0s | ok 3/3 342.5s |
| test_bl_t1_prop.py | FAIL (0 pass, 1 fail, rc=1) | ok 1/1 229.6s |
| test_bl_t1_real_undo.py | ok 5/5 2.7s | ok 5/5 2.4s |
| test_bl_t1_reload.py | ok 7/7 17.8s | FAIL (1 pass, 6 fail, rc=1) |
| test_bl_t1_warn.py | ok 9/9 50.3s | ok 9/9 53.8s |
| test_bl_tier1.py | ok 11/11 7.6s | ok 11/11 7.2s |
| test_bl_warnings.py | ok 13/13 1.0s | ok 13/13 0.8s |
| test_core_codec.py | ok 9/9 1.0s | ok 9/9 0.8s |
| test_core_format.py | ok 9/9 0.4s | ok 9/9 0.4s |
| test_core_framing.py | ok 16/16 2.1s | ok 16/16 2.0s |
| test_core_history.py | ok 10/10 1.5s | ok 10/10 1.5s |
| test_core_importer.py | ok 4/4 0.5s | ok 4/4 0.4s |
| test_core_journal.py | ok 14/14 0.7s | ok 14/14 0.6s |
| test_core_migration.py | ok 4/4 0.8s | ok 4/4 0.8s |
| test_core_native.py | ok 9/9 3.3s | ok 9/9 3.5s |
| test_core_native_steps.py | ok 9/9 13.7s | FAIL (8 pass, 1 fail, rc=1) |
| test_core_records.py | ok 5/5 0.7s | ok 5/5 0.7s |
| test_core_saveload_storage.py | ok 10/10 2.8s | ok 10/10 2.7s |
| test_core_store.py | ok 19/19 16.6s | ok 19/19 15.9s |
| test_tools_coverage.py | ok 3/3 0.5s | ok 3/3 0.8s |
| test_tools_precheck.py | ok 7/7 0.4s | ok 7/7 0.3s |
