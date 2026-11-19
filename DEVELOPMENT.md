# Development status

The page a new session reads first. Written 2026-11-18 (evening) at the end of the optimisation and
audit session, from the repository, the approved plan, the agents' reports in `dev/reports/` and the
commit history. It is the record of the project: the session scratchpad and the plan file were outside
the repository and are gone. Update this file at every merge and before any session limit.

Owner rules stay in `HANDOFF.md` ("Owner rules", "Test guarantee", "Beta 3 job"). Decisions are in
`DECISIONS.md`, defects in `DEFECTS.md`, what the add-on may claim in `COVERAGE.md`, interfaces in
`INTERFACES.md`, speed and memory rounds in `OPTIMISATION_LOG.md` and `PERF_LOG.md`.

Where a fact has no source, this page says "not measured" or "no report".

## a. How to resume

1. Clone `Min3scon/Hindsight` and stay on the default branch `claude/lucid-brahmagupta-9gsdsn`. Read this
   file, then `dev/AGENT_RULES.md`.
2. Environment. Run `bash tools/dev_env.sh` (add `--all` for Blender 5.0, 5.1 and 5.2): it builds one
   virtual environment per Blender version under `/home/user/venvs` with `bpy` as a Python module
   (`bpy42` 4.2.23 and `bpy50` 5.0.1 on Python 3.11; `bpy51` 5.1.2, `bpy52` 5.2.2 and `bpy53` 5.3.0 on
   Python 3.13), the test dependencies, the committed engine wheel, and a `tools` venv with maturin and
   zig. The claude.ai cloud environment named "Hindsight" (personal, saved for this repository) runs
   that script when a session's container is built; it was created on 2026-11-18 and its setup script
   is unproven until a fresh session has started on it. `bash tools/build_native.sh` rebuilds the six
   engine wheels into `hindsight/wheels/`.
   - There is no Blender binary and no GPU in the container (`download.blender.org` is blocked).
     Real Blender runs on GitHub: workflow `Blender tests` (`.github/workflows/blender.yml`, started by
     hand with inputs `versions`, `filter`, `run_module`, `run_ui`) runs the suite in Blender binaries
     4.2 to 5.3, on Windows and macOS as a module, and a windowed probe under Xvfb. Workflow
     `Native engine` proves the wheels on Windows, macOS and Linux, x64 and ARM64. Both are triggered
     from a session with the GitHub tools (`actions_run_trigger`); commits carry `[skip ci]`.
3. Tests (from `dev/AGENT_RULES.md` and `HANDOFF.md`):
   - all: `HINDSIGHT_PY=<venv>/bin/python python3 tests/run_tests.py`
   - some: `HINDSIGHT_PY=<venv>/bin/python python3 tests/run_tests.py recovery` (a substring of the file name)
   - one file: `<venv>/bin/python tests/test_bl_recovery.py`; each test prints `PASS test_name` or `FAIL test_name`
   - `HINDSIGHT_NATIVE=require` forces the Rust engine, `HINDSIGHT_NATIVE=0` forces Python. Run both
     where the code touches the engine. The runner sets `HINDSIGHT_VERIFY=1` (every written step is read
     back and compared).
   - Sculpt brush operators crash headless; tests simulate strokes with `tests/harness.py`.
   - User-facing text: `python3 tools/check_copy.py`.
4. Benchmarks: see section f.
5. Commits: small, subject ends with ` [skip ci]` so no Pages deploy runs. A commit without it deploys the
   site, which is only done at a release (HANDOFF "TOP PRIORITY").

## b. Repository and branch state

- Repository `Min3scon/Hindsight`. One branch, `claude/lucid-brahmagupta-9gsdsn`, is the default and the
  Pages source. There is no open pull request and none is planned: work goes straight to this branch.
- Main line: see `git log`. Merged and tested by the orchestrator on 2026-11-18: crash recovery D77 to
  D83, redraw work (D84), save and load work (D85, D86), data recorder work (D87), the Rust engine 0.2.0
  (`apply_steps`), the runtime work (D88 to D90, topology reads through attributes, cached slider info
  wired into the slider, header and panel), Movie Mode listing the visible objects once per frame, this
  record, the CI workflow and scripts, and engine wheels for Windows ARM64 and Linux ARM64.
- In progress on agent branches (local to the session's container until merged; check `git branch`):
  CAMERA-FINISH (review and completion of the Movie Mode camera fixes S2-1, S3-1, S3-2 from the
  unmerged branch `worktree-agent-ac952619567257298`), BENCH (`bench/suite.py`, `bench/ab.py`),
  RECOVERY-REVIEW (fault injection for the journal and saves), RUST (core design, then migration).
- Version: `hindsight/blender_manifest.toml` still says `1.0.0-beta.2`, and `docs/download/hindsight.zip`
  is the beta.2 zip. `CHANGELOG.md` has a `1.0.0-beta.3` section. 1.0.0-beta.3 is not released.
- The site (`site/`, `docs/`) was not edited in this job (owner: "don't edit site, just get the add-on
  updated"). `COVERAGE.md` and `TEST_EVIDENCE.md` are unchanged.

## c. Architecture

### One page

- `hindsight/core/`: pure Python plus numpy, no `bpy`. The storage engine: `types.py` (Snapshot, Step,
  Entry), `delta.py` (diff, apply, packing of records), `step.py` (step container), `codec.py` (zstd,
  lz4, zlib), `history.py` (one object's history, cursor, `goto`), `store.py` (caps, memory tiers, the
  background `Packer`), `registry.py`, `persist.py` (file formats 1 to 4, journal replay), `journal.py`
  (crash journal), `globaltl.py` (global timeline), `framing.py` (camera framing for Movie Mode),
  `morph.py`, `importer.py`, `native.py` (loader of the Rust module).
- `hindsight/bl/`: the Blender side. `runtime.py` (recording, jumps, timers), `meshio.py` (mesh reads and
  writes), `datarec.py` and `adapters.py` (Record everything: objects, constraints, collections, RNA
  values), `idmap.py`, `persistence.py` (save, load, recovery), `scrubber.py` (on-screen slider),
  `ui_panel.py`, `ui_menu.py`, `header.py`, `warnings.py`, `prefs.py`, `props.py`, `filtering.py`,
  `movie.py`, `camrig.py`, `rigmark.py` (Movie Mode), `dupsep.py`, `joinsep.py`, `morph.py`, `log.py`,
  `guard.py`, `selfcheck.py`, `cleanup.py`, `beta.py`.
- `native/`: the Rust crate (PyO3 0.22.6, abi3 for Python 3.11 and later, `zstd-safe` 8.3.0, `crc32fast`),
  module `hindsight_native`, version 0.2.0, API 2, one source file `native/src/lib.rs`. Wheels for four
  platforms are committed in `hindsight/wheels/` and listed in the manifest: Linux x86-64, Windows
  x86-64, macOS arm64, macOS x86-64. Proven by `.github/workflows/native.yml` (run 44305350583: four
  systems, Python 3.11 and 3.13; the run was of the 0.1.0 wheels, the 0.2.0 wheels were cross-built and
  have not been run on Windows or macOS). They have not run inside a real Blender on any platform.
- Tests in `tests/`, UI tests in `tests_ui/`, benchmarks in `bench/`, tools in `tools/`.
- `HINDSIGHT_NATIVE`: unset or `1` uses the module if it loads (any API from 1 to 2, per function
  fallback), `0` forces Python, `require` demands API 2.

### What runs in Rust today

Exactly five functions of `hindsight_native`:

| Function | What it does | Used by |
|---|---|---|
| `diff` | changed elements of a channel and their wrapping differences | `core/delta.py` |
| `apply` | applies a delta in place (ADD both ways, XOR) | `core/delta.py` |
| `pack` (`pack_delta`) | format 3 record: index coding, prediction orders 0 to 3, zigzag, byte planes | `core/delta.py` |
| `unpack` (`unpack_at`) | the same transforms read back | `core/delta.py` |
| `apply_steps` | a run of packed DELTA steps of one object: CRC, header, zstd, decode, apply, GIL released, up to 3 helper threads for long runs | `History.goto` |

Everything else is Python: step headers and the container, error types, compression calls (zstd, lz4,
zlib are C libraries), history, store, persist, journal, registry, global timeline, framing, morph and
the whole Blender side. Steps that `apply_steps` does not take (TOPO, JOIN, DATA, extras, format 1,
zlib or lz4 bodies, channels that appear or go, any damage) are crossed by Python, which also raises
every error. The Python implementations are still the fallback in `hindsight/`; the owner's decision
that Rust is required, with the Python copies moved to `tests/reference/`, has not been carried out
(`tests/reference/` does not exist, and nothing fails if the module is missing).

### Migration plan to Rust (from the approved plan)

Rule for every step: the Python module it replaces moves to `tests/reference/`; a bit-exact equivalence
test (hypothesis, a corpus of real step, journal and registry bytes from formats 1 to 4, hostile input)
gates it; `bench/suite.py` A/B proves the gain; then the Python copy leaves `hindsight/`. Proof that
Rust runs: every suite runs with the engine required; a test fails if anything under `hindsight/` imports
a reference module; Copy diagnostics names the engine and its API; CI loads the wheel on Windows, macOS
and Linux (x64 and ARM64) and in real Blender binaries. One agent owns `native/` at a time. The
interface design (module layout, buffer ownership, error mapping, threading) is the first deliverable of
the Rust lead agent and must be reviewed before code is written: the design agent was interrupted
before it reported, so none exists.

| Step | Content | Status |
|---|---|---|
| R1 | Merge `apply_steps` (API 2) | done: merged in `f092d7c`, `tests/test_core_native_steps.py` passes |
| R2 | Formats in Rust: step container (header, CRC, codecs zstd/lz4/zlib via zstd-safe, lz4_flex, miniz_oxide; extras JSON as bytes), journal framing and crash-tolerant reader, registry stream writer and reader (formats 1 to 4 read, 4 written); removes python-zstandard from the hot path | not started |
| R3 | History engine: a `History` pyclass owning entries, cursor, goto planning and application through borrowed numpy buffers, caps, eviction and tier accounting, drop, truncate and join bookkeeping; Python keeps a thin facade | not started |
| R4 | Recording pipeline: one native `record()` per stroke (diff, pack, compress, journal append, GIL released, reusing the `foreach_get` buffers); fingerprints from arrays already read | not started |
| R5 | Background packer as a Rust thread (zstd 19 off the GIL, idle policy kept) | not started |
| R6 | Global timeline (incremental groups, transition), `framing.history_extents`, morph blending | not started |
| Rust required | Python copies to `tests/reference/`, clear error panel if the module cannot load, never record with a broken engine | not started |
| Wheels for Windows ARM64 and Linux ARM64 | manifest `platforms` lists exactly what has a wheel | not started |

Stays Python (needs `bpy`): `foreach_get` and `foreach_set`, RNA capture in datarec and adapters,
vertex group weight reads, handlers, UI and drawing, operators. Their cost is attacked by doing less
work (incremental, cached). Measured basis (PERF_LOG "The Rust question"): at 100k vertices and 6,000
strokes 95% of Hindsight's memory is step bytes and arrays that a native core holds at the same size;
the gain from Rust is speed and removal of temporaries, not stored size.

## d. Features, defects and findings

Status words, exactly one per row:

- **done**: merged on the main line and its test passes. The orchestrator's merges re-ran the tests; in
  addition the RECORD agent re-ran these files on `04af744` on Blender 5.3.0 as a Python module with
  `HINDSIGHT_NATIVE=require`, all tests passing: `test_bl_recovery` (11), `test_core_journal` (14),
  `test_bl_draw_scrubber` (5), `test_bl_audit` (10), `test_core_store` (19), `test_bl_runtime_topo` (4),
  `test_bl_runtime_ui` (4), `test_bl_runtime_caps` (2), `test_core_native_steps` (8),
  `test_core_saveload_storage` (10), `test_bl_datarec_fast_writes` (2), `test_bl_t0_gate` (6). Not run
  in a real Blender or a window.
- **implemented-unverified**: code exists on an unmerged branch or without review; not merged.
- **partial**: part of the item is done; the row says which.
- **blocked**: cannot start until something else exists.
- **not started**: nothing was written.

### The Limitations page (the plan's table, one row per line)

The site page is changed only as part of a release, when a line has passed the real-window gate (owner's
rule). Design for each line: `dev/reports/LIMITS-A.md` (known limits, 12 numbered sections) and
`dev/reports/LIMITS-B.md` (not recorded, adapter seam, packages P0 to P8).

| # | Line | Planned outcome (phase) | Status | Test that proves it |
|---|---|---|---|---|
| L1 | Vertex Paint not claimed (one check failed on 5.0.1) | check fixed (D74, D71); proven on a real 5.0.1 binary in CI, then claimed (Phase 0 CI) | partial: the check is fixed and passes in module runs; no real-binary run, COVERAGE row `sculpt.colors` stays "not covered" | `tests/test_bl_recorder.py` `test_restored_colour_layer_is_active`, `test_vertex_paint_layer_added_late`; `tests/test_bl_t0_gate.py` `test_gate_colors` |
| L2 | Objects only once switched on | one whole-file switch; default decided at release (Phase 3) | not started | none |
| L3 | Multires and Dyntopo pause recording | Multires: one exact step per Sculpt Mode visit; Dyntopo: a step per stroke up to about 200k triangles, per visit above; strokes made while paused become a step (Phase 4, Phase 1) | not started (design: LIMITS-A sections 1.1 to 1.7) | none |
| L4 | Animated or driven properties | the animation itself is recorded (P5); un-keyed axes and muted curves now (Phase 1, P5) | partial: un-keyed axes, per-index keys and muted curves are recorded (D103); the animation itself (P5) not started | `tests/test_bl_adapters.py` `test_partly_animated_and_muted` |
| L5 | Physics and particles don't come back | settings come back; baked caches cannot (files), the note says so (Phase 1, P2) | done for settings: physics and particle settings come back with a deleted object (D102); Dynamic Paint surfaces and baked caches can't (files), the note says so | `tests/test_bl_adapters_physics.py` |
| L6 | Edit Mode up to 300,000 vertices | topology reads much faster; cap becomes a preference; limit re-measured (Phase 0, Phase 1) | partial: the faster reads are merged (R014 in OPTIMISATION_LOG: "70 to 150 times faster", no absolute numbers); the preference and the re-measurement are not done | `tests/test_bl_runtime_topo.py` `test_topology_read_matches_legacy`, `test_write_snapshot_matches_legacy`, `test_fingerprint_same_both_paths`, `test_topology_step_round_trip` |
| L7 | Linked duplicates recorded once | one shared history per shared mesh, shown on every user (Phase 1). LIMITS-A: wrong in the default scope, two histories fight over one shared mesh | done: a shared mesh is recorded in one history, the owner's; other users' commits go to it (D104) | `tests/test_bl_linked_dup.py` |
| L8 | Linked objects not recorded | stays (Blender); library overrides keep their history across reload (Phase 1). LIMITS-A: overrides lose their history on reload today | done: library overrides keep their history across reload; library paths in references are absolute (D109) | `tests/test_bl_override.py` |
| L9 | A taken name can't be given back | swaps and chains inside one jump fixed; a name held by an untouched object stays, with the note (Phase 1) | done: swaps and chains of names inside one jump come back (D105); a name held by an untouched object stays, with the note | `tests/test_bl_t1_names.py` |
| L10 | 10,000 steps | preference up to 100,000, default stays 10,000; incremental global timeline (Phase 1) | done: Steps per object goes up to 100,000; the global timeline groups are extended per step instead of rebuilt (R032) | `tests/test_bl_panels.py`, `tests/test_bl_runtime_groups.py` |
| L11 | Topology change loses weights and shapes | recorded in every scope, size threshold measured (Phase 1, P1) | done: vertex group weights and shape keys kept on topology steps up to 300,000 vertices (EXTRAS_TOPO_MAX), refreshed on entering and leaving record modes (D106) | `tests/test_bl_topo_extras.py` |
| L12 | Deleted parent or target | "Bring back" from the note (Phase 1; lower priority) | not started | none |
| L13 | Collections need "Objects added and deleted" | recorded with Everything (Phase 1, P8) | not started (deferred: needs a registry flag, journal and panel plumbing) | none |
| L14 | Changes without Hindsight | kept when Blender's `.blend1` has the saved mesh, otherwise today's safe behaviour (Phase 1) | done: a mesh changed while Hindsight wasn't there is bridged from Blender's .blend1 when it has the saved mesh and nothing is ahead of the cursor (D107); otherwise today's safe behaviour | `tests/test_bl_outside_backup.py`, `tests/test_bl_t1_outside.py` |
| L15 | Modifiers, Geometry Nodes, materials, weight paint, UVs, texture paint, animation, shape keys, armatures, other object types, scene settings | P1 to P8, and G (a generic recorder for any datablock, so third-party add-ons are covered) (Phase 3); needs the P0 seam first | partial, with Record everything: vertex group, UV map and shape key lists (D108), modifiers and Geometry Nodes inputs (D110, D100), material slots (D111), materials and their node trees (D112), custom properties and any add-on's data on objects, object data, scenes and materials (D114, the generic part of G for ID properties), scene, render and world settings (D116), object settings and the settings of lights, cameras, curves, text, metaballs, speakers, light probes and meshes (D117), armature poses with bone constraints and properties (D119). Not started: weight paint per stroke, texture paint, animation, armature Edit Mode (bones), curve and lattice points, view layer settings, add-on datablocks and custom node trees outside materials and worlds | `tests/test_bl_t1_lists.py`, `tests/test_bl_t1_modifiers.py`, `tests/test_bl_t1_material_slots.py`, `tests/test_bl_t1_materials.py`, `tests/test_bl_t1_addon_props.py` (stand-in add-on `tests/addons/hs_fake_addon.py`), `tests/test_bl_t1_scene_settings.py`, `tests/test_bl_t1_settings.py`, `tests/test_bl_t1_pose.py` |
| L16 | Six defects in rows the site already claims | fixed with regression tests (rows D-a to D-f below) | partial: D-a, D-b, D-c, D-e fixed (D100, D110, D108, D103); D-d custom normals and D-f Weight Paint per stroke not started | see the table below |

Also in the plan and not started: the whole-file "Record > Everything" switch with a real-window CI gate
(Phase 3), the datablock snapshot fallback for data RNA cannot express (Phase 3), "File history" for data
no object owns (Phase 3), more of the third-party add-on tests in `tests/addons/` (a custom node tree, add-on
datablocks; the property part exists: `hs_fake_addon.py`) (Phase 3), and
Multires and Dyntopo behind one preference, off until real strokes pass in a windowed CI job (Phase 4).

### The six defects of LIMITS-B (rows the site already claims)

| Id | Defect | Status | Test |
|---|---|---|---|
| D-a | Blender 5.2 and 5.3: an object deleted and brought back loses its Geometry Nodes input values (`adapters._id_props` raises TypeError on 5.2+; fix is a `gn_inputs()` helper used by `object_body` and `_recreate_object`) | done (D100) | `tests/test_bl_adapters_gn.py` |
| D-b | Going back over Apply Modifier gives the mesh from before Apply without the modifier's effect | done (D110) | `tests/test_bl_t1_modifiers.py` |
| D-c | Adding, removing or renaming a vertex group, shape key or UV map makes a step whose going back leaves the list as it is now (D43) | done (D108) | `tests/test_bl_t1_lists.py` |
| D-d | Custom normals on 5.0+ (`custom_normal`, INT16_2D) are dropped by `meshio._recordable`; going back over Set Custom Normals keeps the new normals on the old mesh | blocked: needs the INT16_2D channel type in `core/types.py` and `native/` (P1) | none |
| D-e | The first keyframe on a recorded property makes a step that does nothing when going back (the property appears as MISSING) | done (D103) | `tests/test_bl_adapters.py` `test_animation_is_not_recorded` |
| D-f | Weight Paint strokes are not steps (`datarec.commit` reads only Object Mode meshes); needs a real window to confirm | blocked: needs P1 and a real window | none |

Fixed ones carry their DEFECTS.md numbers. They were found by `probe_limits.py` (80 cases on the five
Blender versions, now lost); the verdict table is in `dev/reports/LIMITS-B.md` section 2.

### Hostile review (`dev/reports/review.md`, findings S1-1 to S4-3)

| Id | Finding | Status | Test that proves it |
|---|---|---|---|
| S1-1 | Crash recovery: a DROP larger than the saved cursor leaves a history that does not match the mesh | done (D77) | `tests/test_bl_recovery.py` `test_cap_drops_past_the_saved_step`, `test_cap_drops_after_saving_scrubbed_back`, `test_delete_earlier_steps_past_the_saved_step`; `tests/test_core_journal.py` `test_replay_keeps_the_saved_state_through_drops` |
| S1-2 | Crash recovery after scrubbing back past the save and recording | done (D78) | `tests/test_bl_recovery.py` `test_new_branch_below_the_saved_step`; `tests/test_core_journal.py` `test_replay_stops_where_the_saved_state_is_left_behind` |
| S2-1 | Movie Mode camera crops models with modifiers (Mirror, Multires, Subdivision, shape keys, Armature) | implemented-unverified: on branch `worktree-agent-ac952619567257298`, not merged, not reviewed | on that branch: `tests/test_bl_camrig_modifiers.py`, `tests/test_core_framing.py` (not run by RECORD) |
| S3-1 | The user's camera is not given back if it was renamed while Movie Mode was on | implemented-unverified (same branch) | on that branch: `tests/test_bl_camrig_handover.py` |
| S3-2 | A light that does not render counts as "the scene has its own light" | implemented-unverified (same branch) | on that branch: `tests/test_bl_camrig_lights.py` |
| S4-1 | `scrubber._DIGITS` is never pruned | done (D84) | `tests/test_bl_draw_scrubber.py` `test_digits_bounded_and_forgotten_on_load` |
| S4-2 | `log.error_once` has no cap | done (5e6ca36) | `tests/test_bl_audit.py` `test_error_once_stays_once_after_many_errors`, `test_error_once_is_bounded_when_the_text_varies` |
| S4-3 | Memory limit: a full rescan on every stroke when nothing more can be dropped | done (9c2c77b) | `tests/test_core_store.py` `test_caps_memo_stuck_between_target_and_limit`, `test_caps_memo_same_as_whole_pass`, `test_caps_memo_reads_only_the_changed_history`; `tests/test_bl_runtime_caps.py` |

Not reproduced in the review: Vertex Paint on 5.3 (D71) and the Morph brush on 5.3 (D75): the headless
module cannot exercise those paths the way a window does.

### Defects D77 to D86 (`DEFECTS.md`)

| Id | What | Status | Test |
|---|---|---|---|
| D77 | Recovery: drops past the saved position (S1-1) | done | `test_bl_recovery` (`test_cap_drops_past_the_saved_step` and two more), `test_core_journal` |
| D78 | Recovery: new branch below the saved step (S1-2) | done | `test_bl_recovery` (`test_new_branch_below_the_saved_step`), `test_core_journal` |
| D79 | Recovery: clearing a history somewhere else than the saved position | done | `test_bl_recovery` (`test_clear_somewhere_else_leaves_later_strokes_out`) |
| D80 | Recovery: a second interruption after a recovery | done | `test_bl_recovery` (`test_second_interruption_starts_from_the_recovered_history`) |
| D81 | Recovery: a join made since the last save | done | `test_bl_recovery` (`test_join_after_the_save`) |
| D82 | Recovery: strokes after a mesh changed without Hindsight | done | `test_bl_recovery` (`test_strokes_after_a_mesh_changed_without_hindsight`) |
| D83 | Recovery: steps after values changed without Hindsight | done | `test_bl_recovery` (`test_steps_after_values_changed_without_hindsight`) |
| D84 | Slider digit counts never dropped (S4-1) | done | `test_bl_draw_scrubber` (`test_digits_bounded_and_forgotten_on_load`) |
| D85 | Idle recompression of a loaded file did not start until the 65th stroke | done | `test_core_saveload_storage` (`test_packer_without_activity_recompresses_as_before`, `test_packer_skips_steps_with_nothing_to_gain`, `test_can_gain_matches_recompress`) |
| D86 | Journal: failed fsync reported as durable; a cut-off record hid later records | done | `test_core_saveload_storage` (`test_journal_fsync_failure_is_reported`, `test_journal_full_disk_keeps_records_before_and_after`, `test_journal_failed_flush_is_reported_and_cut_back`) |

Other fixes in the same job with their own tests: constraint restore gave later constraints the wrong
values when one could not be made (`tests/test_bl_datarec_fast_writes.py`, done, not numbered in
DEFECTS.md).

### Other open items from the audits

- `dev/reports/TOOLS.md` (static analysis): dead code and duplicates. Done by NATIVE: dead branch and
  `_IDX_ENC` in `delta.py`, unused local in `history.py`. Not done: unused locals in `joinsep.py:342`,
  unused imports in `header.py`, `meshio.py`, `runtime.py`, duplicated keymap boilerplate in `joinsep.py`
  and `ops_core.py`, the unused `@fontsource-variable/*` devDependencies and the undeclared
  `chrome-launcher` in the site's tooling. 103 empty-except and 34 cyclic-import findings from CodeQL were
  not individually triaged (the cyclic imports are lazy imports on purpose).
- `dev/reports/ponytail_audit.md`: items 1, 2, 3, 4, 5, 6, 9, 10, 11 and 13 were done by earlier work in
  this job (see `DEFECTS.md` D63 to D69 and `OPTIMISATION_LOG.md`); item 7 (dead code, about 60 lines) and
  item 8 (duplicate helpers) are open except as noted; item 12 is replaced by the Rust `diff`.
- `tools/mutation_bl.py` compiles again, but mutants 6, 7, 8, 17, 35, 56 and 60 need re-targeting.
- `tests/oracle.py`: the one-line fix for the Blender 4.2 `parent_type` test failure (in
  `dev/reports/DATAREC.md` section 6) is not applied.
- Edit Mode step cost at 100k vertices is in `runtime.commit_edit` (`update_from_editmode` about 34 ms and
  topology reads about 20 ms of 55 ms before R014): not re-measured after R014.

## e. Test matrix

Headless runs use Blender as a Python module (`pip install bpy`), in the container, not a Blender
binary and not a window. Three levels exist; only the first has run for the work of 2026-11-18.

| Level | Blender versions | State |
|---|---|---|
| Python module in the container | 4.2.23, 5.0.1 (Python 3.11.18); 5.1.2, 5.2.2, 5.3.0 (Python 3.13.18) | The beta 3 work (HANDOFF "Beta 3 job") was tested on all five. The agents ran their areas on 5.3.0 and 4.2.23 (the NATIVE twin-history tests with the engine required, on, and off). The RECORD agent re-ran the 12 files in section d on 5.3.0 only. No single full-suite result across the five versions after the last merge (`04af744`) is recorded in any source; the DATAREC report gives 48 of 50 files on 5.3.0 before the later merges. |
| Real Blender binaries | 5.3.0 and 4.2.23, headless, whole suite, `HINDSIGHT_NATIVE=require` | Run here on 2026-11-18 (`tools/ci/run_suite.py`, `dev/reports/REAL_BLENDER.md`) and on GitHub (blender.yml run 44408088741, commit 8090ffa). Here: 5.3 63/66 files, 4.2 60/66. GitHub: 5.3 65/68, 4.2 61/68 (68 files: it has the two new recovery fault tests and `test_core_journal_faults`). Failures are listed below. The beta 2 gate (October 2026) ran real 5.0.1, 5.1.1 and 5.1.2 with a window on the owner's Windows PC (`tests_ui/hs_ui.py`, `TEST_ROUNDS.md`). |
| bpy module on GitHub runners | Windows and macOS, same run | Both green (whole suite, native engine required). |
| A real window (UI gate) | Blender 5.1.2 and 5.3 under Xvfb plus Mesa llvmpipe (OpenGL 4.5), here (5.3) and on GitHub (both) | `regress_slider_visible` passes 3 of 3 on both GitHub jobs. Earlier it failed 1 of 3 on 5.3 here: not the product. Blender's own screenshot reads the front buffer, which is all black under Xvfb. The slider is drawn (orange track, 2/2, above the shelf). Fix, CI side: `tests_ui/bl_driver.py` grabs the X root window when `HS_UI_XSHOT=1`, the Xvfb screen is 1600x900 like the window, ImageMagick installed. Not yet run in a window: the Movie Mode panel rows, "Use this view", camera and lamps put back, a real timelapse render with EEVEE and Workbench, 4.2 in a window. |
| native.yml | GitHub, run 44408094816 | Green. |

Known failing tests:

| Test | Where | Why |
|---|---|---|
| `test_bl_metadata::test_opens_without_hindsight` | everywhere in the container | needs a Blender binary |
| `test_bl_t1_migrate` | everywhere in the container | needs `.tmp/old/b1.zip`, which is not in the repository |
| `test_bl_t1_gate::test_gate_parenting` | Blender 4.2 | Blender 4.2 Clear Parent keeps a hidden vertex parent type; RNA reads `''` and refuses to set it without a parent. Fails on beta 2 code too. The fix belongs in `tests/oracle.py` (patch in `dev/reports/DATAREC.md` section 6), not applied |
| `test_bl_t1_reload` (6 to 7 of 7) | Blender 4.2 | not investigated |
| test files that use `collection_uid` | Blender 4.2 | fixed on the main line by `6b3d012` (`tests/t1_ops.py`); the agents' worktrees still had the old helper |
| `test_bl_t0_gate`, `test_bl_t0_reload` | real Blender 4.2.23 | Blender crashes (SIGSEGV, rc -11), on GitHub and here. Pass on 5.3. Not investigated |
| `test_core_native_steps::test_damaged_blobs` | real Blender 4.2.23 | native engine returns ok on a damaged blob (`damaged 50`) where Python raises `CorruptStep`. Passes on 5.3. Open |
| `test_core_native_steps::test_mixed_histories` | real Blender 5.3 on GitHub (not here, not on 4.2) | native and Python both raise `TypeError` but the text differs in operand order (`'str' and 'tuple'` against `'tuple' and 'str'`, seed 4). Expected red product test |
| `test_bl_adapters::test_awkward_values_round_trip` | real Blender 5.3 | Blender runs with flush-to-zero: the denormal does not survive. Passes on 4.2 and in the bpy module. Open |
| `test_bl_t1_prop` | real Blender, locally | was red: Hypothesis refuses subnormal floats under flush-to-zero. Fixed in the test (`allow_subnormal=False`); 2000 sequences pass on real 5.3 |
| `test_bl_t1_corrupt::test_damage_before_the_saved_step`, `test_bl_t1_migrate::test_format3_file` | Blender 4.2 | same parent_type quirk as `test_gate_parenting` |

Other limits of the verification:

- Optional `lz4` is installed in the venvs but not in the owner's Blender; it exposed D73.
- Real Blender bundles its own numpy (for example 1.26 in 5.0); only the planned CI binary jobs would test
  that. The venvs use whatever pip resolved.
- The macOS and Windows 0.2.0 wheels were cross-built and never run; the 0.1.0 wheels were run by CI.
- Timings in the container come from a shared 4-core VM with load average often 5 to 14.

## f. Benchmarks

- Measured rounds: `OPTIMISATION_LOG.md` (31 rows: 26 kept, 4 reverted, 1 not merged). The target is at
  least 100 rounds; the log is the only count.
- Earlier prose and tables: `PERF_LOG.md` (compression format 3, tier 1 budgets, memory and the Rust
  question, Movie Mode playback, Rust engine, redraw cost, saving and the re-packer).
- Scripts in `bench/`: `bench_bl.py`, `bench_core.py`, `bench_codec.py`, `bench_format.py`,
  `bench_dict.py`, `bench_native.py` (`--jumps` compares apply_steps, the per-function module and
  Python), `bench_movie.py`, `mem_profile.py`, `mem_speed.py`, `mem_speed_core.py`, `saveload_bench.py`,
  `packer_bench.py`, `perf_log.py`, `scrub_ab.py`, `scrub_compare.py`, `bench/lab/`. Results as JSON:
  `bench/results_*.json`; the older prose is `bench/RESULTS.md`.
- Not in the repository (they lived in the session scratchpad and are lost): the DRAW redraw benchmark
  (`draw_bench.py`, `ab.py`), the DATAREC scripts (`bench_dr.py`, `ab2.sh`, `mem_dr.py`), the NATIVE
  `jb.py` and `ab.sh`, the LIMITS-B probe scripts, the hostile review scripts `r1` to `r10`, the audit
  scripts `m1` to `m11`. The reports in `dev/reports/` describe what they did and the numbers they gave.
- Planned and not built: `bench/suite.py` (fixed scenarios: stroke 100k and 1M, jumps 1, 30, 300, 3000,
  drag over 5,000, save and load sidecar and embed, Edit step 100k and 300k, data recorder with 500
  objects, redraw, movie frame, memory peaks), run as alternating A/B processes with a JSON baseline per
  commit under `bench/results/`. The baseline for A/B runs of this job was commit `66fcdf8`.
- How to measure here: alternating runs (A B A B, at least 5 rounds) against a baseline checkout, or both
  paths in one process; report min and median; measure memory with tracemalloc and VmHWM too. Think of
  weak computers: 4 to 8 GB RAM, 2 to 4 slow cores.

## g. Agents

- Rules: `dev/AGENT_RULES.md` (the brief every agent had, adapted). Reports of the agents of 2026-11-18
  are in `dev/reports/`: NATIVE, SAVELOAD, DRAW, DATAREC (merged), TOOLS, LIMITS-A, LIMITS-B (reports
  only), `review.md` and `ponytail_audit.md`. There is no report for RUNTIME (merged; the report was lost,
  its seven commits and tests are the record: `6d8f06d`, `5e6ca36`, `9c2c77b`, `2475d91`, `ff67677`,
  `07996c0`, `a207a88`), MOVIE (2 commits, not merged), CAMERA (2 commits, not merged). A design agent
  for the Rust interface was interrupted before it reported.
- Model tiers (plan): the orchestrator is the main session on the strongest model. Agents: the strongest
  tier ("opus") for Rust, data integrity, recovery, recording semantics, concurrency and reviews; the
  middle tier ("sonnet") for CI workflows, routine tests, docs, benchmark harness; the smallest ("haiku")
  for running suites and summarising. Never the orchestrator's own model for agents.
- Limit: at most 4 agents at once, at most 3 of them on the strongest tier. Reason: bursts of 10
  strongest-tier agents hit the 5-hour usage limit twice in the session of 2026-11-18. On a rate-limit
  sign: start no new agents, commit and push, update this file, report.
- Each agent owns disjoint files and works in its own git worktree; agents never spawn agents, never
  push; work is merged only after the orchestrator re-runs its tests; agents cannot write report files,
  the final message is the report. Agents that were not finished can be resumed with the same context
  (MOVIE, CAMERA were meant to be).
- Commit trailers: the brief asked for "Claude Fable 5.2"; the agents' session instructions said
  "Claude Opus 5.5" and replaced it, so most agent commits carry the latter. Not a defect.

## h. Blockers and decisions

Owner decisions (2026-11-18, from the plan):

- Rust is required. The Python core implementations move to `tests/reference/` as the bit-exact oracle;
  the add-on ships one implementation. Wheels for every Blender platform, adding Windows ARM64 and Linux
  ARM64 (the manifest `platforms` lists exactly what has a wheel). The add-on shows a clear error panel
  if the module cannot load and never records with a broken engine.
- Publish a beta per phase to the site's download zip (only the zip and the generated changelog page
  change). Beta 3 first, after Phase 0.
- The unmerged agent work was to be merged first (RUNTIME merged; CAMERA and MOVIE not).
- Earlier owner instructions of the job: "don't edit site, just get the add-on updated"; "use as many sub
  agents as you want to optimise it"; "rewrite in rust"; "check all this and see if these are still true
  and if true fix" (the two lists on the Limitations page); the site page changes only at a release when a
  line has passed the gate.
- Owner rules in `HANDOFF.md`: whenever work stops, the last thing is making the newest working site live
  after the safety check (top priority); the test guarantee; no fake history.

Decisions taken by the orchestrator for the next phase (the Phase 2 notes of the session, `agents/PHASE2.md`,
now lost; the owner can overrule; they were to be recorded in `DECISIONS.md`, where they are not yet):

- Derived datablocks (materials, node groups, images, actions, armature, light, camera, curve data) are
  recorded in the history of the recorded object that uses them, shared ones by the D39 owner rule.
- New record kinds make the file format 5 only when used; beta 2 and beta 3 refuse format 5.
- Moving a node alone is not a step; a node's position is stored with it.
- Texture paint is its own kind, off by default. Other new kinds are on by default, under Record >
  Everything. The default of the whole-file switch stays "Sculpting only" until a kind has passed the
  real-Blender gate; turning Everything on by default is one owner decision at release time.
- Multires (one step per Sculpt Mode visit) and Dyntopo (per stroke up to 200k triangles) sit behind one
  preference, default off, labelled experimental, transactional (never delete the `.btx` before the pack
  is verified).
- Step limit: preference maximum 100,000, default stays 10,000. Collections recorded with Everything.
  "Bring back {name}" button for a deleted parent or target (lower priority). Taken names resolved at the
  end of a jump, never renaming objects the user did not scrub. Linked objects stay unrecorded; overrides
  keep their history across reload.
- `COVERAGE.md` Status stays "not covered" for new rows until the gate has run in a real Blender window
  (owner's machine or CI).

What cannot be recorded, and what Hindsight does instead (said in the tab): data linked from another
.blend, an add-on's Python state that is not stored in Blender data, files on disk (image files, baked
physics and bake caches), Blender's preferences and window layout.

Blockers:

- No Blender binary and no GPU in the container (download blocked). A real-window gate needs the planned
  GitHub Actions job (real binaries, xvfb and Mesa llvmpipe) or the owner's machine.
- The environment is not reproducible yet: `tools/dev_env.sh` and a personal cloud environment "Hindsight"
  whose setup script runs it do not exist (creating one needs the owner's approval prompt).
- The Rust interface design does not exist (R2 and later wait for it).
- Items that need the P0 seam first: D-b, D-c, D-d, D-f and all of Phase 3.
- Dropping the embedded history text after a save would free about 69 MB at 6,000 steps but would leave
  autosaves without a history: an owner decision, not made.

## i. Next actions, in priority order

1. Merge this record (`dev/`, `DEVELOPMENT.md`, `OPTIMISATION_LOG.md`) into the default branch and push.
   Review and merge the two unmerged branches, or re-brief them: CAMERA (S2-1, S3-1, S3-2) needs a full
   review of its 1,548 lines and a run of the whole suite; MOVIE needs its patch for `runtime.goto` with
   `vis=` and a report. They exist only in the old container.
2. Make the environment reproducible: commit `tools/dev_env.sh` (venvs 4.2 and 5.3 by default, `--all`
   adds 5.0, 5.1, 5.2; test dependencies; maturin and zig; installs the committed wheel) and
   `tools/build_native.sh`; propose the personal cloud environment "Hindsight".
3. First wave, at most 4 agents:
   - CI on real Blender (middle tier): `.github/workflows/blender.yml` with `workflow_dispatch` and push on
     `hindsight/** tests/** native/**`, real 4.2 LTS, 5.0, 5.1, 5.2, 5.3 (Linux tarballs cached), the full
     suite headless with Rust required, a bpy-module job on Windows and macOS for 5.3; then the UI gate
     under xvfb with `tests_ui/hs_ui.py`.
   - BENCH (middle tier): `bench/suite.py`, move the lost scratch benches into `bench/`, a baseline JSON at
     the merged commit.
   - RECOVERY-REVIEW (strongest tier): an independent review of D77 to D86 and fault-injection tests
     (journal cut at every record boundary and byte offset, interrupted sidecar writes, crash mid-jump,
     mid-save, mid-join). Owns `tests/test_bl_recovery_faults.py`, `core/journal.py`, `persist.replay`.
   - Rust formats R2 (strongest tier), starting with the interface design for review.
4. Release 1.0.0-beta.3: bump the manifest, build the wheels, build the zip with `blender_ext.py build`,
   install-test, update `docs/download/hindsight.zip`, `tools/check_copy.py`, push without `[skip ci]`,
   verify the Pages run, check the live zip. Before the release, check in a real window what the
   HANDOFF "Beta 3 job" lists.
5. Phase 1, data integrity first, in waves of 3 to 4 agents, one agent per file: linked duplicates (L7),
   strokes made while paused, taken-name swaps (L9), D-a, D-e, the Blender 4.2 parenting test and
   `test_bl_t1_reload` on 4.2. Resume CAMERA for S2-1, S3-1, S3-2. File ownership as in LIMITS-A work
   packages: runtime.py and dupsep.py; datarec.py and globaltl.py; adapters.py; warnings, ui_panel,
   ui_menu and prefs (texts). Then the cheap limits: animated per-index and muted curves (L4), physics
   settings (L5), topology extras, overrides, `.blend1` recovery (L14), history maximum 100,000 (L10),
   incremental groups.
6. Phase 2, Rust migration R2 to R6 as in section c, one agent on `native/` at a time.
7. Phase 3, the P0 seam, then P1 and P2 (they fix D-b to D-d, D-f and D-a), then P3 to P8 and the generic
   recorder (LIMITS-B section 6 and 7 give sizes and the test plan).
8. Phase 4, Multires and Dyntopo.
9. Throughout: optimisation rounds toward the target of 100, in the areas not yet covered (startup,
   shutdown and idle cost, recording when nothing changed, Edit Mode steps, large scenes with 500 or more
   objects, long histories of 50,000 steps, memory growth over long sessions, timelapse frames, weight and
   shape key reads); clear the audit's dead code and the stale mutants; a beta per phase.
10. At every merge: changed suites on bpy53 and bpy42 locally, with Rust required and off where relevant;
    at every phase end the full suite on five versions plus `blender.yml`; update this file's test matrix
    with what really ran. Nothing is marked done on an agent's word alone.
