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
   - No GPU in the container. Since 2026-11-18 the "Hindsight" environment allows `download.blender.org`:
     Blender 4.2 and 5.3 Linux binaries are unpacked in `/home/user/blender/` and
     `python3 tools/ci/run_suite.py --blender <binary>` runs the suite in them. Real Blender also runs on GitHub: workflow `Blender tests` (`.github/workflows/blender.yml`, started by
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
  Pages source. No pull requests: work is merged into this branch after its tests pass.
- Main line (2026-11-19): everything up to D118 and the Rust engine M1 (merge `7dc4853`, CI dependency
  fix `847bcdd`). Work since then is on the main session's local branches `main-session-3` and
  `main-session-4` (worktrees `.claude/worktrees/ms-p8` and `ms-eng`): D119 to D134 (poses, animation,
  curves and other shapes, node groups and zones, collection and view layer settings, bones, Grease
  Pencil, hair and points, Bring back, whole-file switch). It is merged after its full suite on 5.3 and
  4.2. The Rust agent works on `worktree-agent-a388cd589ccb15b22` (M2, custom normals D-d, Weight Paint
  D-f).
- Version: `hindsight/blender_manifest.toml` still says `1.0.0-beta.2`, and `docs/download/hindsight.zip`
  is the beta.2 zip. `CHANGELOG.md` has a `1.0.0-beta.3` section. 1.0.0-beta.3 is not released.
- The site (`site/`, `docs/`) is not edited outside a release (owner: "don't edit site, just get the add-on
  updated").

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

Engine `hindsight_native` 0.3.0, API 3 on the main line (M1, merged 2026-11-19); the Rust agent's branch
has 0.4.0, API 4 (M2 part: `record_step`, custom normals), not merged yet. The engine is required: if it
can't load, the add-on says so in the sidebar and records nothing (D118); there is no Python fallback in
the add-on. The Python implementations it replaced are in `tests/reference/` as the oracle, and a test
fails if anything under `hindsight/` imports them.

| In Rust (API 3) | Used by |
|---|---|
| `diff`, `apply`, `pack`, `unpack` (channels, records) | `core/delta.py` |
| `apply_steps` (runs of DELTA steps, GIL released, helper threads) | `History.goto` |
| codecs zstd, lz4, zlib, none | `core/codec.py` (facade) |
| step container: formats 1 to 4 read, 3 and 4 written, all step kinds, recompress, CRC | `core/step.py` (facade) |
| errors `CorruptStep`, `CorruptBlob`, `CorruptDelta`, `NewerFormatError`, `EngineMissing` | `core/native.py` |

Still Python: history, store, persist, journal, registry, global timeline, framing, morph, and the whole
Blender side (by design: it needs bpy). `HINDSIGHT_NATIVE=0` only switches `delta.py` to its Python paths
for tests and A/B runs.

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
| L2 | Objects only once switched on | one whole-file switch; default decided at release (Phase 3) | partial: "Everything in this file" switches every object and the scene on in one click, objects added later follow (D36); the default scope is still the owner's release decision | `tests/test_bl_record_file.py` |
| L3 | Multires and Dyntopo pause recording | Multires: one exact step per Sculpt Mode visit; Dyntopo: a step per stroke up to about 200k triangles, per visit above; strokes made while paused become a step (Phase 4, Phase 1) | not started (design: LIMITS-A sections 1.1 to 1.7) | none |
| L4 | Animated or driven properties | the animation itself is recorded (P5); un-keyed axes and muted curves now (Phase 1, P5) | partial: un-keyed axes, per-index keys and muted curves are recorded (D103); an object's actions, keyframes, drivers and NLA are recorded (D120); the animation of other data not yet | `tests/test_bl_adapters.py` `test_partly_animated_and_muted` |
| L5 | Physics and particles don't come back | settings come back; baked caches cannot (files), the note says so (Phase 1, P2) | done for settings: physics and particle settings come back with a deleted object (D102); Dynamic Paint surfaces and baked caches can't (files), the note says so | `tests/test_bl_adapters_physics.py` |
| L6 | Edit Mode up to 300,000 vertices | topology reads much faster; cap becomes a preference; limit re-measured (Phase 0, Phase 1) | partial: the faster reads are merged (R014 in OPTIMISATION_LOG: "70 to 150 times faster", no absolute numbers); the preference and the re-measurement are not done | `tests/test_bl_runtime_topo.py` `test_topology_read_matches_legacy`, `test_write_snapshot_matches_legacy`, `test_fingerprint_same_both_paths`, `test_topology_step_round_trip` |
| L7 | Linked duplicates recorded once | one shared history per shared mesh, shown on every user (Phase 1). LIMITS-A: wrong in the default scope, two histories fight over one shared mesh | done: a shared mesh is recorded in one history, the owner's; other users' commits go to it (D104) | `tests/test_bl_linked_dup.py` |
| L8 | Linked objects not recorded | stays (Blender); library overrides keep their history across reload (Phase 1). LIMITS-A: overrides lose their history on reload today | done: library overrides keep their history across reload; library paths in references are absolute (D109) | `tests/test_bl_override.py` |
| L9 | A taken name can't be given back | swaps and chains inside one jump fixed; a name held by an untouched object stays, with the note (Phase 1) | done: swaps and chains of names inside one jump come back (D105); a name held by an untouched object stays, with the note | `tests/test_bl_t1_names.py` |
| L10 | 10,000 steps | preference up to 100,000, default stays 10,000; incremental global timeline (Phase 1) | done: Steps per object goes up to 100,000; the global timeline groups are extended per step instead of rebuilt (R032) | `tests/test_bl_panels.py`, `tests/test_bl_runtime_groups.py` |
| L11 | Topology change loses weights and shapes | recorded in every scope, size threshold measured (Phase 1, P1) | done: vertex group weights and shape keys kept on topology steps up to 300,000 vertices (EXTRAS_TOPO_MAX), refreshed on entering and leaving record modes (D106) | `tests/test_bl_topo_extras.py` |
| L12 | Deleted parent or target | "Bring back" from the note (Phase 1; lower priority) | done: Bring back button in the Warnings panel (D123) | `tests/test_bl_t1_bring_back.py` |
| L13 | Collections need "Objects added and deleted" | recorded with Everything (Phase 1, P8) | not started (deferred: needs a registry flag, journal and panel plumbing) | none |
| L14 | Changes without Hindsight | kept when Blender's `.blend1` has the saved mesh, otherwise today's safe behaviour (Phase 1) | done: a mesh changed while Hindsight wasn't there is bridged from Blender's .blend1 when it has the saved mesh and nothing is ahead of the cursor (D107); otherwise today's safe behaviour | `tests/test_bl_outside_backup.py`, `tests/test_bl_t1_outside.py` |
| L15 | Modifiers, Geometry Nodes, materials, weight paint, UVs, texture paint, animation, shape keys, armatures, other object types, scene settings | P1 to P8, and G (a generic recorder for any datablock, so third-party add-ons are covered) (Phase 3); needs the P0 seam first | partial, with Record everything: vertex group, UV map and shape key lists (D108), modifiers and Geometry Nodes inputs (D110, D100), material slots (D111), materials and their node trees (D112), custom properties and any add-on's data on objects, object data, scenes and materials (D114, the generic part of G for ID properties), scene, render and world settings (D116), object settings and the settings of lights, cameras, curves, text, metaballs, speakers, light probes and meshes (D117), armature poses with bone constraints and properties (D119), animation: actions, F-curves, keyframes, drivers and NLA of objects, their data, shape keys, material node trees, scenes and worlds (D120, D125), the shape of curves, surfaces, lattices and metaballs (D121), node groups of Geometry Nodes modifiers and materials with zones and item lists (D126, D130), collection settings (D127), view layer settings (D128), armature bones in Edit Mode (D129), Grease Pencil drawings (D131), the compositor and node trees no object uses (D133), hair curves and point clouds (D134), particle settings (D137). Not started: weight paint per stroke (Rust agent, D-f), texture paint, add-on datablocks no object or scene uses | `tests/test_bl_t1_lists.py`, `tests/test_bl_t1_modifiers.py`, `tests/test_bl_t1_material_slots.py`, `tests/test_bl_t1_materials.py`, `tests/test_bl_t1_addon_props.py` (stand-in add-on `tests/addons/hs_fake_addon.py`), `tests/test_bl_t1_scene_settings.py`, `tests/test_bl_t1_settings.py`, `tests/test_bl_t1_pose.py`, `tests/test_bl_t1_anim.py`, `tests/test_bl_t1_geometry.py`, `tests/test_bl_t1_node_groups.py`, `tests/test_bl_t1_collection_settings.py` |
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

Latest runs (2026-11-19):

| Level | Code | Result |
|---|---|---|
| bpy module in the container, 5.3.0 and 4.2.23, whole suite, engine required | main line 7dc4853 (D111 to D118, Rust M1) | 84/86 each; the two failures are the known ones below |
| same | main-session-4 4d856db (adds D119 to D134) | 5.3: 94/96; 4.2: the known two failures in the part that ran before the code moved on (a final run is pending) |
| Real Blender binaries on GitHub (Blender tests, run 44439641564) | 7dc4853 | 5.3.0: 84/86; 4.2.23: 84/86. Besides `test_bl_t1_migrate`, only `test_core_reference` failed: libzstd version (fixed in 099815c) and one engine divergence in `test_hostile_blobs` (with the Rust agent) |
| bpy module on GitHub, Windows and macOS (same run) | 7dc4853 | macOS: green. Windows: 83/86, `test_core_reference` as above plus the two known |
| Window probe under Xvfb, 5.1.2 and 5.3 (same run) | 7dc4853 | green |
| Native engine on GitHub, 12 jobs (Windows, macOS, Linux; x64 and ARM64; Python 3.11 and 3.13) | 7dc4853, 847bcdd, 099815c | 7dc4853: test_core_reference failed for a missing lz4 (CI dependencies fixed in 847bcdd); 847bcdd: 2 libzstd byte comparisons (fixed in 099815c); 099815c: rerun pending |

Known failing tests:

| Test | Where | Why |
|---|---|---|
| `test_bl_metadata::test_opens_without_hindsight` | bpy module | needs a Blender binary |
| `test_bl_t1_migrate` | everywhere | needs `.tmp/old/b1.zip` (the beta 1 release zip), which is not in the repository |
| `test_core_reference::test_hostile_blobs` | real Blender 5.3, Windows module (random draws) | engine and reference both accept a damaged step but the engine's recompress keeps a header byte the reference writes as 0; with the Rust agent |

Other limits of the verification:

- Optional `lz4` is installed in the venvs but not in the owner's Blender; it exposed D73.
- The venvs' python-zstandard has libzstd 1.5.7 like the engine; CI pins 0.23.0 (1.5.6) and Blender 4.2
  bundles an older one, so zstd bytes are compared only where the versions match.
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

- Rules: `dev/AGENT_RULES.md`. Reports of the agents of 2026-11-18 are in `dev/reports/`; the Rust
  agent's design is `dev/RUST_DESIGN.md` and its report `dev/reports/RUST.md`.
- Since 2026-11-19 (owner): the main session does the engineering itself and stays busy without pause,
  with about one agent beside it (two running at once at most). The agents use the middle tier
  ("sonnet"). Every agent reads `.claude/skills/ultracave/SKILL.md` and answers in that style.
- Running: RUST (`a388cd589ccb15b22`, branch `worktree-agent-a388cd589ccb15b22`): M1 merged; now M2
  (`record_step` wired into `runtime.commit`, `delta.py` fallbacks to `tests/reference/`), D-d custom
  normals (done on its branch) and D-f Weight Paint per stroke.
- Each agent owns disjoint files and works in its own git worktree; agents never spawn agents, never
  push; their work is merged only after the main session re-runs its tests.

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

1. Merge `main-session-4` (D119 to D134) once its full suite passes on 5.3 and 4.2; push; run
   `Blender tests` and `Native engine` on GitHub.
2. Measure the cost of D116 to D134 on an operator at 500 objects (`bench/ab.py --only dr_finish,dr_prime`
   and the 500-material scene) on a quiet machine; log rounds in `OPTIMISATION_LOG.md`.
3. Review and merge the Rust agent's M2, D-d and D-f; then M3 (native `History`).
4. Texture paint per stroke (off by default), after D-f (same record-mode code).
5. Release 1.0.0-beta.3 when the owner asks: bump the manifest, build the zip, install-test, update
   `docs/download/hindsight.zip`, push without `[skip ci]`.
6. Multires (one step per Sculpt Mode visit) and Dyntopo (per stroke), behind one preference (Phase 4).
7. More optimisation rounds toward the owner's 100 (37 logged).
