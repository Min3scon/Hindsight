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
   virtual environment per Blender version under `venvs/` next to the repository checkout with `bpy` as a Python module
   (`bpy42` 4.2.23 and `bpy50` 5.0.1 on Python 3.11; `bpy51` 5.1.2, `bpy52` 5.2.2 and `bpy53` 5.3.0 on
   Python 3.13), the test dependencies, the committed engine wheel, and a `tools` venv with maturin and
   zig. The claude.ai cloud environment named "Hindsight" (personal, saved for this repository) runs
   that script when a session's container is built; it was created on 2026-11-18 and its setup script
   is unproven until a fresh session has started on it. `bash tools/build_native.sh` rebuilds the six
   engine wheels into `hindsight/wheels/`.
   - No GPU in the container. Since 2026-11-18 the "Hindsight" environment allows `download.blender.org`:
     Blender 4.2 and 5.3 Linux binaries are unpacked in `blender/` next to the repository checkout and
     `python3 tools/ci/run_suite.py --blender <binary>` runs the suite in them. Real Blender also runs on GitHub: workflow `Blender tests` (`.github/workflows/blender.yml`, started by
     hand with inputs `versions`, `filter`, `run_module`, `run_ui`) runs the suite in Blender binaries
     4.2 to 5.3, on Windows and macOS as a module, and a windowed probe under Xvfb. Workflow
     `Native engine` proves the wheels on Windows, macOS and Linux, x64 and ARM64. Both are triggered
     from a session with the GitHub tools (`actions_run_trigger`); commits carry `[skip ci]`.
   - Network: the "Hindsight" environment is Limited: package registries, MCP servers and
     `download.blender.org` are allowed, nothing else (2026-11-19: www.blender.org, docs.blender.org, reddit,
     Wikipedia, Stack Overflow and GitHub's artifact storage are refused). Two different refusals look alike,
     and only the first one means "not allowed here":
     - the proxy refuses the connection: curl says `CONNECT tunnel failed, response 403` (HTTP code 000), and
       `curl -sS "$HTTPS_PROXY/__agentproxy/status"` lists the host under `recentRelayFailures` as
       `connect_rejected`. The request never reached the site. Adding the host to the environment's allowed
       domains fixes it.
     - the site itself refuses: the connection opens and the site answers 403, 429 or 503, often from
       Cloudflare (`server: cloudflare`, a `cf-ray` or `cf-mitigated` header, a "Just a moment" or "verify you
       are human" page) or its own bot check (reddit answers data-center clients that way even when allowed;
       crates.io wants a User-Agent). Allowing the host doesn't help; use the site's API (with a key) or
       another source. Say which of the two it was, never just "blocked".
3. Tests (from `dev/AGENT_RULES.md` and `HANDOFF.md`):
   - all: `HINDSIGHT_PY=<venv>/bin/python python3 tests/run_tests.py`
   - some: `HINDSIGHT_PY=<venv>/bin/python python3 tests/run_tests.py recovery` (a substring of the file name)
   - one file: `<venv>/bin/python tests/test_bl_recovery.py`; each test prints `PASS test_name` or `FAIL test_name`
   - The engine is always used (no Python path ships; the
     Python versions are in `tests/reference/`). The runner sets `HINDSIGHT_VERIFY=1` (every written step
     is read back and compared).
   - Sculpt brush operators crash headless; tests simulate strokes with `tests/harness.py`.
   - Each test process keeps its temporary files (crash journals, saved test files, history folders) in one folder that `tests/harness.py` removes when the process ends, with Blender's own temporary folder; `HS_KEEP_TMP=1` keeps them for a look. Without it, a day of runs left 19 GB in the system temp folder and filled the disk (headless Blender never unregisters the add-on, so the journals stayed).
   - Real windows here (Xvfb, Mesa llvmpipe, the binaries in `blender/` next to the repository checkout): put a `blender` shim
     first on PATH that runs the binary with `--gpu-backend opengl` (keep `--command` first, as in
     `.github/workflows/blender.yml`), set `LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
     MESA_GL_VERSION_OVERRIDE=4.5 MESA_GLSL_VERSION_OVERRIDE=450 HS_UI_XSHOT=1`, then
     `xvfb-run -a -s "-screen 0 1600x900x24 +extension GLX" python3 tests_ui/hs_ui.py run <scenario>
     --blender 5.3 --fresh --geometry 0,0,1600,900` (a window at 0,0 so screenshots crop right). Each run
     uses the worktree's `.tmp/profiles`: never two at once in one worktree. `tests_ui/rounds.py --pass N
     --blender 5.3 --rounds ABCD` runs the rounds with the right flags (`hs_ui.py models` first). Never
     install `hindsight_native` into a Blender binary's own Python here: a stale copy shadowed the add-on's
     wheel on Install from Disk (moved aside 2026-11-19).
   - User-facing text: `python3 tools/check_copy.py`.
4. Benchmarks: see section f.
5. Commits: small, subject ends with ` [skip ci]` (CLAUDE.md, "Working a ticket").

## b. Repository and branch state

- Repository `Min3scon/Hindsight`. One branch, `claude/lucid-brahmagupta-9gsdsn`, is the default. No pull requests: work is merged into this branch after its tests pass.
- Main line (2026-11-19, evening): `f95fc2f`, everything up to R080 with engine 0.9.0 (API 8), full suites on 5.3 and
  4.2 before the push. The main session works in the worktree `.claude/worktrees/main-session` (detached; merged
  into the branch through a temporary worktree with a `[skip ci]` merge commit). No agent is running: the Rust
  agent was stopped on 2026-11-19 at about 17:00 after an hour waiting on a permission prompt; its R078 was
  measured by the main session and reverted (OPTIMISATION_LOG).
- Version: `hindsight/blender_manifest.toml` still says `1.0.0-beta.2`, and the beta.2 zip is on GitHub
  Releases (`v1.0.0-beta.2`). `CHANGELOG.md` has a `1.0.0-beta.3` section. 1.0.0-beta.3 is not released.

## c. Architecture

### One page

- `hindsight/core/`: pure Python plus numpy, no `bpy`. The storage engine: `types.py` (Snapshot, Step,
  Entry), `delta.py` (diff, apply, packing of records), `step.py` (step container), `codec.py` (zstd,
  lz4, zlib), `history.py` (one object's history, cursor, `goto`), `store.py` (caps, memory tiers, the
  background `Packer`), `registry.py`, `persist.py` (file formats 1 to 4, journal replay), `journal.py`
  (crash journal), `globaltl.py` (global timeline), `framing.py` (camera framing for Movie Mode),
  `morph.py`, `native.py` (loader of the Rust module).
- `hindsight/bl/`: the Blender side. `runtime.py` (recording, jumps, timers), `meshio.py` (mesh reads and
  writes), `datarec.py` and the `adapters/` package (Record everything: objects, constraints, collections, RNA
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
### What runs in Rust today

Engine `hindsight_native` 0.11.0, API 10 (0.11.0: `extents_into`, the Movie Mode camera's extents of points in place, R085; `morph_dab` refuses arrays of the wrong type; 0.10.0: `morph_dab`, one Morph History dab in one pass, R083; 0.9.0: `same_values`, the recorder's strict "nothing changed" check, R070; `diff` on two threads from 4 MB per array, R071; idle recompression at zstd level 15 for data up to 1 MB and 19 above, R072; 0.8.0: `Hasher128`, xxh3-128 for the mesh fingerprints, R060; 0.7.0: `crc32` for the registry, the journal and the step checksums, M5; 0.6.0: zstd bodies over 1 MB get a long window and long distance matching, R049; M1 and the M2 part merged 2026-11-19: `delta.py` calls the engine for
diff, apply, pack and unpack, its numpy code is `tests/reference/delta.py`; `record_step` exists and is
tested but `runtime.commit` doesn't use it, since the stroke is already diffed once). The engine is required: if it
can't load, the add-on says so in the sidebar and records nothing (D118); there is no Python fallback in
the add-on. The Python implementations it replaced are in `tests/reference/` as the oracle, and a test
fails if anything under `hindsight/` imports them.

| In Rust (API 3) | Used by |
|---|---|
| `diff`, `apply`, `pack`, `unpack` (channels, records); `diff` splits arrays of 4 MB and more over two threads, GIL released, same bytes (R071, API 8) | `core/delta.py` (`DIFF_THREADS` sets the threads for tests and A/B) |
| `apply_steps` (runs of DELTA steps, GIL released, helper threads) | `History.goto` |
| codecs zstd, lz4, zlib, none; the strong zstd setting is level 15 for data up to 1 MB, 19 above (R072; `_strong_level` sets one level for measurements) | `core/codec.py` (facade) |
| `crc32` (crc32fast, same values as zlib.crc32, API 6) | `core/crc.py`: registry, journal, step checksums |
| `Hasher128` (xxh3-128 by xxhash-rust, streaming, GIL released for bytes of 64 KB and more, API 7) | `bl/meshio.fingerprint`: mesh fingerprints, written "x3:" + 32 hex digits (saved prints, the RESET of a mesh changed without Hindsight, Blender's undo after a jump); a value without a tag (earlier builds) is compared by hashing with blake2b again, an unknown tag is never a change |
| step container: formats 1 to 4 read, 3 and 4 written, all step kinds, recompress, CRC | `core/step.py` (facade) |
| `same_values` (strict equality: exact types at every level, floats by bits, dicts in the same key order; API 8) | `bl/datarec._same_values`: the "nothing changed" check of every recorded object at every operation (R070); False leaves the marshal compare deciding |
| `morph_dab` (one Morph History dab: box test, distance, falloff, mask, the stroke's strongest weight and the blend, in place; API 9) | `bl/morph.apply_morph_at` (R083); its numpy code is `tests/reference/morph_dab.py` |
| `extents_into` (extents of points, or of the rows an index names, for k camera bases, merged in place; the product written out left to right; API 10) | `core/framing.extents`, `extents_multi`, `history_extents_multi` (R085); their numpy code is `tests/reference/framing.py` |
| errors `CorruptStep`, `CorruptBlob`, `CorruptDelta`, `NewerFormatError`, `EngineMissing` | `core/native.py` |

Still Python: history, store, persist, journal, registry, global timeline, framing, morph, and the whole
Blender side (by design: it needs bpy). The Python paths of `delta.py` live in `tests/reference/` for tests and A/B runs.

### Migration plan to Rust (from the approved plan)

Rule for every step: the Python module it replaces moves to `tests/reference/`; a bit-exact equivalence
test (hypothesis, a corpus of real step, journal and registry bytes from formats 1 to 4, hostile input)
gates it; `bench/suite.py` A/B proves the gain; then the Python copy leaves `hindsight/`. Proof that
Rust runs: every suite runs with the engine required; a test fails if anything under `hindsight/` imports
a reference module; Copy diagnostics names the engine and its API; CI loads the wheel on Windows, macOS
and Linux (x64 and ARM64) and in real Blender binaries. One agent owns `native/` at a time. The
interface design (module layout, buffer ownership, error mapping, threading, milestones M1 to M6) is
`dev/RUST_DESIGN.md`; the agents' reports are in `dev/reports/RUST.md`.

| Step | Content | Status |
|---|---|---|
| R1 | Merge `apply_steps` (API 2) | done: merged in `f092d7c`, `tests/test_core_native_steps.py` passes |
| R2 | Formats in Rust: step container (header, CRC, codecs zstd/lz4/zlib via zstd-safe, lz4_flex, miniz_oxide; extras JSON as bytes), journal framing and crash-tolerant reader, registry stream writer and reader (formats 1 to 4 read, 4 written); removes python-zstandard from the hot path | done as far as it pays: step container, codecs, delta, crc32, the fingerprint hash and the recorder's value compare are in the engine (0.9.0, API 8); M5 measured reading the registry's steps and scanning the journal in Rust and reverted both (R051, R053: the reads are the kernel's copy); the checksum moved (R052). python-zstandard and lz4 aren't used |
| R3 | History engine: a `History` pyclass owning entries, cursor, goto planning and application through borrowed numpy buffers, caps, eviction and tier accounting, drop, truncate and join bookkeeping; Python keeps a thin facade | done as far as it pays: measured first, Python glue is about 3% of a jump (OPTIMISATION_LOG "Tried"), so no pyclass; `History.goto` crosses packed steps only through the engine's `apply_steps`, the Python loop is `tests/reference/history.py` |
| R4 | Recording pipeline: one native `record()` per stroke (diff, pack, compress, journal append, GIL released, reusing the `foreach_get` buffers); fingerprints from arrays already read | partial: `record_step` exists in the engine (API 4) but commit doesn't use it (it would diff twice; saving below 4%, see "Tried"); the journal blob is lz4 (R043) |
| R5 | Background packer as a Rust thread (zstd 19 off the GIL, idle policy kept) | not started |
| R6 | Global timeline (incremental groups, transition), `framing.history_extents_multi`, morph blending | partial: the Morph History dab (R083, engine 0.10.0), the camera extents and the history walk's measuring (R085, 0.11.0; the walk itself, stepping through the history, stays Python); the global timeline not started |
| Rust required | Python copies to `tests/reference/`, clear error panel if the module cannot load, never record with a broken engine | done: no Python path ships (`tests/reference/`), `EngineMissing` and the panel's engine message (`test_bl_engine_missing`), HINDSIGHT_NATIVE selects nothing any more |
| Wheels for Windows ARM64 and Linux ARM64 | manifest `platforms` lists exactly what has a wheel | done: six 0.11.0 wheels (Linux and Windows x64 and ARM64, macOS x64 and arm64) in `hindsight/wheels`, listed in the manifest; `native.yml` loads them on GitHub runners |

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

### The limitations (the plan's table, one row per line)

Design for each line: `dev/reports/LIMITS-A.md` (known limits, 12 numbered sections) and
`dev/reports/LIMITS-B.md` (not recorded, adapter seam, packages P0 to P8).

| # | Line | Planned outcome (phase) | Status | Test that proves it |
|---|---|---|---|---|
| L1 | Vertex Paint not claimed (one check failed on 5.0.1) | check fixed (D74, D71); proven on a real 5.0.1 binary in CI, then claimed (Phase 0 CI) | partial: the check is fixed and passes in module runs and in the real Blender 5.0.1 binary (`test_bl_recorder`, Blender tests run 44476010656); real Vertex Paint strokes pass in windows on 4.2, 5.0.1, 5.1.2, 5.2 and 5.3 (window job, `paint_modes`). COVERAGE row `sculpt.colors` stays "not covered" until `tools/make_evidence.py` takes it through the gate; claiming it on the site is a release step | `tests/test_bl_recorder.py` `test_restored_colour_layer_is_active`, `test_vertex_paint_layer_added_late`; `tests/test_bl_t0_gate.py` `test_gate_colors` |
| L2 | Objects only once switched on | one whole-file switch; default decided at release (Phase 3) | partial: "Everything in this file" switches every object and the scene on in one click, objects added later follow (D36); the default scope is still the owner's release decision | `tests/test_bl_record_file.py` |
| L3 | Multires and Dyntopo pause recording | Multires: one exact step per Sculpt Mode visit; Dyntopo: a step per stroke up to about 200k triangles, per visit above; strokes made while paused become a step (Phase 4, Phase 1) | done behind two preferences, off by default: Multires is one exact step per Sculpt Mode visit (Record Multires; real window 5/5 on 5.3.0 and 4.2.23, D143 fixed); Dyntopo strokes are steps on meshes up to 200,000 faces (Record Dyntopo, D145; real window 8/8 on 5.3.0 and 4.2.23), on bigger meshes, and with Record Dyntopo off, a visit is one step when it ends (D153; real window 9/9 on 5.3.0 and 4.2.23). Their defaults are the owner's call | `tests/test_bl_multires.py`, `tests/test_bl_dyntopo.py`, `tests_ui/scenarios/s_multires.py`, `tests_ui/scenarios/s_dyntopo.py` |
| L4 | Animated or driven properties | the animation itself is recorded (P5); un-keyed axes and muted curves now (Phase 1, P5) | done: un-keyed axes, per-index keys and muted curves are recorded (D103); an object's actions, keyframes, drivers and NLA are recorded (D120), and the animation of its data, its shape keys, its materials' node trees, the scene and the world (D125); and a node group's own animation (D146) | `tests/test_bl_adapters.py` `test_partly_animated_and_muted` |
| L5 | Physics and particles don't come back | settings come back; baked caches cannot (files), the note says so (Phase 1, P2) | done for settings: physics and particle settings come back with a deleted object (D102); Dynamic Paint surfaces and baked caches can't (files), the note says so | `tests/test_bl_adapters_physics.py` |
| L6 | Edit Mode up to 300,000 vertices | topology reads much faster; cap becomes a preference; limit re-measured (Phase 0, Phase 1) | done: the faster reads are merged (R014); the cap is the preference "Edit Mode steps up to" (default 300,000, 10,000 to 5,000,000); re-measured on 5.3 (bench/scenarios/editmode.py, load 6): an Edit Mode step costs 14 to 18 ms at 100k vertices and 60 to 83 ms at 300k, about a third of the old 55 ms per 100k | `tests/test_bl_runtime_topo.py` `test_topology_read_matches_legacy`, `test_write_snapshot_matches_legacy`, `test_fingerprint_same_both_paths`, `test_topology_step_round_trip`; `tests/test_bl_edit_cap.py` |
| L7 | Linked duplicates recorded once | one shared history per shared mesh, shown on every user (Phase 1). LIMITS-A: wrong in the default scope, two histories fight over one shared mesh | done: a shared mesh is recorded in one history, the owner's; other users' commits go to it (D104) | `tests/test_bl_linked_dup.py` |
| L8 | Linked objects not recorded | stays (Blender); library overrides keep their history across reload (Phase 1). LIMITS-A: overrides lose their history on reload today | done: library overrides keep their history across reload; library paths in references are absolute (D109) | `tests/test_bl_override.py` |
| L9 | A taken name can't be given back | swaps and chains inside one jump fixed; a name held by an untouched object stays, with the note (Phase 1) | done: swaps and chains of names inside one jump come back (D105); a name held by an untouched object stays, with the note | `tests/test_bl_t1_names.py` |
| L10 | 10,000 steps | preference up to 100,000, default stays 10,000; incremental global timeline (Phase 1) | done: Steps per object goes up to 100,000; the global timeline groups are extended per step instead of rebuilt (R032) | `tests/test_bl_panels.py`, `tests/test_bl_runtime_groups.py` |
| L11 | Topology change loses weights and shapes | recorded in every scope, size threshold measured (Phase 1, P1) | done: vertex group weights and shape keys kept on topology steps up to 300,000 vertices (EXTRAS_TOPO_MAX), refreshed on entering and leaving record modes (D106) | `tests/test_bl_topo_extras.py` |
| L12 | Deleted parent or target | "Bring back" from the note (Phase 1; lower priority) | done: Bring back button in the Warnings panel (D123) | `tests/test_bl_t1_bring_back.py` |
| L13 | Collections need "Objects added and deleted" | recorded with Everything (Phase 1, P8) | done with the whole-file switch: "Everything in this file" records the scene too, so collections made, filled and deleted come back on the global timeline; an object switched on by itself without the scene keeps the note (TXT_EV_NEW_COLLECTIONS) | `tests/test_bl_record_file.py` `test_collections_made_and_deleted_with_the_file_switch` |
| L14 | Changes without Hindsight | kept when Blender's `.blend1` has the saved mesh, otherwise today's safe behaviour (Phase 1) | done: a mesh changed while Hindsight wasn't there is bridged from Blender's .blend1 when it has the saved mesh and nothing is ahead of the cursor (D107); otherwise today's safe behaviour | `tests/test_bl_outside_backup.py`, `tests/test_bl_t1_outside.py` |
| L15 | Modifiers, Geometry Nodes, materials, weight paint, UVs, texture paint, animation, shape keys, armatures, other object types, scene settings | P1 to P8, and G (a generic recorder for any datablock, so third-party add-ons are covered) (Phase 3); needs the P0 seam first | partial, with Record everything: vertex group, UV map and shape key lists (D108), modifiers and Geometry Nodes inputs (D110, D100), material slots (D111), materials and their node trees (D112), custom properties and any add-on's data on objects, object data, scenes and materials (D114, the generic part of G for ID properties), scene, render and world settings (D116), object settings and the settings of lights, cameras, curves, text, metaballs, speakers, light probes and meshes (D117), armature poses with bone constraints and properties (D119), animation: actions, F-curves, keyframes, drivers and NLA of objects, their data, shape keys, material node trees, scenes and worlds (D120, D125), the shape of curves, surfaces, lattices and metaballs (D121), node groups of Geometry Nodes modifiers and materials with zones and item lists (D126, D130), collection settings (D127), view layer settings (D128), armature bones in Edit Mode (D129), Grease Pencil drawings (D131), the compositor and node trees no object uses (D133), hair curves and point clouds (D134), particle settings (D137). texture painting behind a preference that is off (D141, D142), in the Image Editor's Paint mode too (D144). Not started: add-on datablocks no object or scene uses | `tests/test_bl_t1_lists.py`, `tests/test_bl_t1_modifiers.py`, `tests/test_bl_t1_material_slots.py`, `tests/test_bl_t1_materials.py`, `tests/test_bl_t1_addon_props.py` (stand-in add-on `tests/addons/hs_fake_addon.py`), `tests/test_bl_t1_scene_settings.py`, `tests/test_bl_t1_settings.py`, `tests/test_bl_t1_pose.py`, `tests/test_bl_t1_anim.py`, `tests/test_bl_t1_geometry.py`, `tests/test_bl_t1_node_groups.py`, `tests/test_bl_t1_collection_settings.py`; and (D151) data no object or scene uses, in the scene's history: texts, unused materials and worlds, custom properties and add-on data of every other datablock; an add-on's own node tree type is tested (test_custom_node_tree). making or deleting texts, unused materials and worlds and node groups no object uses is taken back (D152); making, changing and deleting images, unused actions, palettes, brushes, line styles, masks, textures and the other file-level datablocks is taken back from a .blend snapshot of each (D153) |
| L16 | Six defects in rows the site already claims | fixed with regression tests (rows D-a to D-f below) | done: D-a, D-b, D-c, D-d, D-e, D-f fixed (D100, D110, D108, D139, D103, D140) | see the table below |

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
| D-d | Custom normals on 5.0+ (`custom_normal`, INT16_2D) are dropped by `meshio._recordable`; going back over Set Custom Normals keeps the new normals on the old mesh | done (D139; 4.2 keeps them in a hidden layer: not recorded there) | `tests/test_bl_custom_normals.py` |
| D-e | The first keyframe on a recorded property makes a step that does nothing when going back (the property appears as MISSING) | done (D103) | `tests/test_bl_adapters.py` `test_animation_is_not_recorded` |
| D-f | Weight Paint strokes are not steps (`datarec.commit` reads only Object Mode meshes); needs a real window to confirm | done (D140) | `tests/test_bl_weight_paint.py` |

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
  `_IDX_ENC` in `delta.py`, unused local in `history.py`. Done since: the unused call in `joinsep.py` (HINDSIGHT_OT_join.invoke). Not done:
  unused imports (pyflakes is clean on `hindsight/` since 2026-11-19 except the deliberate import check in
  `ui_panel.engine_error`), duplicated keymap boilerplate in `joinsep.py`
  and `ops_core.py` (the site's tooling findings went with the website, #5). 103 empty-except and 34 cyclic-import findings from CodeQL were
  not individually triaged (the cyclic imports are lazy imports on purpose).
- `dev/reports/ponytail_audit.md`: items 1, 2, 3, 4, 5, 6, 9, 10, 11 and 13 were done by earlier work in
  this job (see `DEFECTS.md` D63 to D69 and `OPTIMISATION_LOG.md`); item 7 (dead code, about 60 lines) and
  item 8 (duplicate helpers) are open except as noted; item 12 is replaced by the Rust `diff`.
- `tools/mutation_bl.py`: the 12 mutants whose text no longer matched (6 to 10, 18, 26, 28, 36, 45, 57, 61) were re-targeted; the tool now runs an unchanged control first and gives the Blender runs this checkout's engine (an engine-less Blender had "killed" every mutant at import). 12/12 killed in Blender 5.3.0, by five different test files. The other 66 weren't re-run.
- `tests/oracle.py`: the Blender 4.2 `parent_type` fix (`dev/reports/DATAREC.md` section 6) is applied
  (the oracle reads a cleared vertex parent type as OBJECT, as the recorder does).
- Edit Mode step cost: re-measured after R014 (L6): 14 to 18 ms at 100k vertices, 60 to 83 ms at 300k
  (bench/scenarios/editmode.py, 5.3, load 6).

## e. Test matrix

Latest runs (2026-11-19 and 20):

| Level | Code | Result |
|---|---|---|
| bpy module in the container, 5.3.0 and 4.2.23, whole suite, engine 0.9.0 required | main-session e0257e3 (R081, the R080 and same_values test fixes) | 119/121 each, only the two known failures (m098) |
| Real Blender binaries, bpy modules and real windows on GitHub (Blender tests run 44607176687) | 9474c1c | all green: the suite in the 4.2, 5.0.1, 5.1.2, 5.2 and 5.3 binaries (only the known test_bl_t1_migrate), the Windows and macOS bpy modules, the window jobs on all five versions |
| same (Blender tests run 44570506405, Native engine run 44570510722) | f95fc2f | Native engine green; Blender tests green except `test_core_same_values` in the 5.3 binary (hypothesis refuses subnormal floats where Blender flushes them to zero; fixed in e0257e3, DEFECTS test changes) |
| bpy module in the container, 5.3.0 and 4.2.23, whole suite, engine required | main line 7dc4853 (D111 to D118, Rust M1) | 84/86 each; the two failures are the known ones below |
| same | main-session-3 45924a0 (D119 to D145, Multires, Dyntopo, journal lz4; merged as 9280a71) | 5.3: 103/105, 4.2: 103/105 (the 4.2 run's wrapper was killed after test_bl_warnings; the files after it were run one by one, all passing); the two failures are the known ones below |
| same | main-session-4 4d856db (adds D119 to D134) | 5.3: 94/96; 4.2: the known two failures in the part that ran before the code moved on (a final run is pending) |
| Real Blender binaries, bpy modules and real windows on GitHub (Blender tests run 44507055217, Native engine run 44507061544) | 8ed62b3 | all green: the suite in the 4.2, 5.0.1, 5.1.2, 5.2 and 5.3 binaries (only the known test_bl_t1_migrate, which needs an old release zip), the bpy module on Windows and macOS, the window job's 14 scenarios on all five versions, and the engine wheels on Windows, macOS and Linux, x64 and ARM64 |
| Real Blender binaries on GitHub (Blender tests, run 44439641564) | 7dc4853 | 5.3.0: 84/86; 4.2.23: 84/86. Besides `test_bl_t1_migrate`, only `test_core_reference` failed: libzstd version (fixed in 099815c) and one engine divergence in `test_hostile_blobs` (with the Rust agent) |
| bpy module on GitHub, Windows and macOS (same run) | 7dc4853 | macOS: green. Windows: 83/86, `test_core_reference` as above plus the two known |
| Window probe under Xvfb, 5.1.2 and 5.3 (same run) | 7dc4853 | green |
| Real windows in the container (Xvfb, Mesa llvmpipe, Blender binaries 5.3.0 and 4.2.23): `tests_ui/hs_ui.py run regress_slider_visible` and `run paint_modes` | main-session-4 after 8c1838d | slider 3/3 on 5.3.0; paint_modes 12/12 on 5.3.0 and 4.2.23 (after D142; before it the texture painting checks failed) |
| same, `run movie_mode` and `run multires` | main-session-3 dbad258 | Movie Mode 16/16 and Multires 5/5 on 5.3.0 and 4.2.23 (Movie Mode: camera, lamps, Use this view, put back after Delete but not during a stroke, a drag, playback or a render, Workbench and EEVEE timelapse renders; Multires: one step per visit, slider back and forward exact) |
| same, `run image_paint` and `run slider_layout` | main-session-3 245df4e | Image Editor painting 5/5 (D144) and the slider with toolbar, sidebar, both and neither 15/15 (clear of the panels, visible, drags to the right position, placed as D62 says), on 5.3.0 and 4.2.23 |
| same, older scenarios on 4.2.23 | main-session-3 de59263 and later | tier1 17/17, adapters 15/15, fail_inject 13/13, regress_stroke_split 3/3, regress_native_undo 7/7, regress_file_load 3/3, movie_mode 16/16, paint_modes 12/12, round_b_features 19/19 (after its 4.2 tool id fix), t1_gate 47/48 (the simulated knife clicks make no cut on 4.2: no change, so no step) |
| same, `run addon`, `run dyntopo` | main-session-3 45924a0 | a third-party add-on's data (stand-in tests/addons/hs_fake_addon.py: settings on objects and the scene, a list) 6/6 and Dyntopo 8/8 on 5.3.0 and 4.2.23: the add-on's operator run from the window and a menu-style setting change are one step each, the global slider puts every value back |
| same, `run settings_keys` | main-session-3 7129d5a | 5/5 on 5.3.0 and 4.2.23: a material's node input, the render resolution and a light's power changed by operators run from keys are one step each; the global slider puts them back both ways |
| same, `run modifier_keys` | main-session-3 8ff10f9 | 5/5 on 5.3.0 and 4.2.23: Ctrl+1, Ctrl+3, Ctrl+0 are one step each; the global slider puts the modifier stack and its levels back both ways |
| same, `run pose_keys` | main-session-3 694622d | 5/5 on 5.3.0 and 4.2.23: in Pose Mode R and G (mouse, click) and I are one step each; the global slider puts the pose and the keyframes back both ways |
| same, `run grease_pencil` and the add-on panel drag (`run addon`) | main-session-3 a9e9a2c | Grease Pencil 5/5 (v3 on 5.3.0, legacy on 4.2.23: three strokes drawn with the mouse, three steps, the global slider takes them away and brings them back with their points); addon 9/9 on both (D147: a drag on the add-on's panel slider is one step) |
| same, `run dyntopo` with Blender's own undo | main-session-3 6d6191d | 11/11 on 5.3.0 and 4.2.23: Ctrl+Z after a Dyntopo stroke moves the history one position back (no new step, the stroke's triangles gone), Ctrl+Shift+Z forward again, and the slider still puts step 2 back exactly |
| same, `run select_ops` (R048) | main-session-3, R048 working tree | 5/5 on 5.3.0 and 4.2.23: Blender lists a click as `VIEW3D_OT_select` and A in Edit Mode as `MESH_OT_select_all`; the click reads 2 of 34 recorded datablocks and records nothing, G after it reads all and is one step; A and a click in Edit Mode read no mesh, G after them is one Edit Mode step |
| same, `run global_two` (R056) | main-session-3 2fa9f41 | 7/7 on 5.3.0 and 4.2.23: eight real strokes on two objects in turn are eight global positions; the global slider dragged to 0, 4, 8, 3, 0, 8 puts both meshes exactly as after that many strokes |
| same, `run dyntopo_paused` (D153) | main-session-3 | 9/9 on 5.3.0 and 4.2.23: with Record Dyntopo off, two real strokes are two steps, two Dyntopo strokes no step one by one, Dyntopo off makes the visit one step; the slider gives back the quads before it and the earlier strokes exactly, and the visit's mesh forward |
| same, `run crash --arg phase=make` then `--arg phase=check` (R064, R065, R067) | main-session | 1/1 and 8/8 on 5.3.0 and 4.2.23: three real strokes, File > Save, four more, Blender killed (SIGKILL); opened again with Interrupted, the seven steps come back at the saved one with the file's mesh and the slider gives the mesh after every stroke; with Last saved, the three saved steps and a journal started again |
| same, `run topo_pack` (R069) | main-session | 8/8 on 5.3.0 and 4.2.23: two real strokes, a pause packs the baseline's topology, a real Voxel Remesh is one more step with a new topology, a stroke; the slider back over the remesh and forward again gives every mesh exactly |
| same, `run crash_topo` make then check (R079) | main-session | 1/1 and 8/8 on 5.3.0 and 4.2.23: three saved strokes, a stroke, a real Voxel Remesh to about 200k vertices (its journal record written by the helper), two strokes, Blender killed; opened again with Interrupted every step comes back and the slider is exact at all eight positions; Last saved as in `crash` |
| same, Rounds A to D (`tests_ui/rounds.py --pass 90 --blender 5.3 --rounds ABCD`) | e4165f2 | 9/16 runs in 76 min; Round C all passed (10k, 10k with 1,000 steps, 100k, 500k, stroke split, file load, native undo); the failures were scenarios out of date (round_b_features, round_d, fail_inject: fixed and passing since), round_b redraw times under software GL, and these findings: a stale hindsight_native 0.2.0 in the Blender binaries' own site-packages here shadowed the add-on's wheel on Install from Disk (moved aside; container only), round_b redraw times are software GL under load, rounds.py's window offset breaks the Xvfb screenshot crop |
| Native engine on GitHub, 12 jobs (Windows, macOS, Linux; x64 and ARM64; Python 3.11 and 3.13) | 7dc4853, 847bcdd, 099815c | 7dc4853: test_core_reference failed for a missing lz4 (CI dependencies fixed in 847bcdd); 847bcdd: 2 libzstd byte comparisons (fixed in 099815c); 099815c: rerun pending |
| same | 9280a71, 3a58015 | 9280a71 (run 44466199267): every platform failed one test, the libzstd byte comparison in test_record_equals_diff_and_encode (Windows' earlier `resource` failure was gone); fixed in cd046ab; 3a58015 (run 44466670259): all 12 jobs green |

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

- Measured rounds: `OPTIMISATION_LOG.md` (79 rows: 68 kept, 11 reverted). The target is at
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
- RUST (`a388cd589ccb15b22`, branch `worktree-agent-a388cd589ccb15b22`, stopped 2026-11-19; its R070 to R072 merged, R078 reverted): M1 and the M2 part merged; D-d
  custom normals (D139), D-f Weight Paint (D140), texture painting (D141) and the subnormal-time engine
  fix (0.5.1) merged into `main-session-3` on 2026-11-19. Next for it: Multires, one exact step per
  Sculpt Mode visit (a datablock snapshot step kind), then M3 (native `History`).
- Each agent owns disjoint files and works in its own git worktree; agents never spawn agents, never
  push; their work is merged only after the main session re-runs its tests.

## h. Blockers and decisions

Owner decisions (2026-11-18, from the plan):

- Rust is required. The Python core implementations move to `tests/reference/` as the bit-exact oracle;
  the add-on ships one implementation. Wheels for every Blender platform, adding Windows ARM64 and Linux
  ARM64 (the manifest `platforms` lists exactly what has a wheel). The add-on shows a clear error panel
  if the module cannot load and never records with a broken engine.
- Publish a beta per phase (since the website was removed in #5: on GitHub Releases). Beta 3 first, after
  Phase 0.
- The unmerged agent work was to be merged first (RUNTIME merged; CAMERA and MOVIE not).
- Earlier owner instructions of the job: "use as many sub agents as you want to optimise it"; "rewrite in
  rust"; "check all this and see if these are still true and if true fix" (the two lists on the website's
  former Limitations page).
- Owner rules in `HANDOFF.md`: the test guarantee; no fake history.

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

- No GPU in the container. Blender 4.2.23 and 5.3.0 binaries are there (`blender/` next to the repository checkout), so
  windowed scenarios run locally under Xvfb and Mesa llvmpipe (`tests_ui/hs_ui.py`, a `blender` shim on
  PATH adding `--gpu-backend opengl`, as in `.github/workflows/blender.yml`); the GitHub job runs the same.
- The environment is not reproducible yet: `tools/dev_env.sh` and a personal cloud environment "Hindsight"
  whose setup script runs it do not exist (creating one needs the owner's approval prompt).
- The Rust interface design does not exist (R2 and later wait for it).
- Items that need the P0 seam first: D-b, D-c, D-d, D-f and all of Phase 3.
- Unpacked newest steps (preference "Unpacked steps", default 64 per history): kept for quick undo, they hold about 19 MB per history at 1M vertices (64 strokes of 2%; packed about 6 MB) and grow with the mesh. A byte cap beside the count would bound it on 4M+ meshes at the cost of a decode (about 1 to 3 ms) on undoing past it; it changes what the preference means for big meshes, so it is the owner's call. Not done.
- Record everything on big scenes: every operation reads every recorded object again (exact by design: some changes, a custom property or hiding, tell no one). Measured 2026-11-19 on bpy 5.3 with cubes (a third with a constraint): 8.6 ms per operation at 100 objects, about 42 ms at 500, about 227 ms at 2,000 (the cyclic GC plays no part; the per-object reads are spread over many small parts, R062 and R063 show what is left). Clicks (R048) and slider drags (R057) don't read everything. Reading only the objects an operator touched could miss a change, and reading the rest later puts a change into the wrong step; either is an owner decision, as is a note in the tab above some number of recorded objects. Not decided.
- Dropping the embedded history after a save would free about 52 MB at 6,000 steps (since R061 the bytes, not
  base64 text) but would leave autosaves without a history: an owner decision, not made.

## i. Next actions, in priority order

The plan lives on GitHub as a wayfinder map: [Map: Hindsight V2](https://github.com/Min3scon/Hindsight/issues/3),
18 tickets (2026-11-22). Its destination: V2 (2.0.0) ready to release, everything built first, then optimised until
nothing more can be gained without losing function. Work the map, not this list: the next ticket is the first open,
unblocked, unassigned one (each blocked ticket starts with a `Blocked by:` line). The decisions behind it are in
DECISIONS.md, the words in GLOSSARY.md.

In order: the build phase (remove the website; check the codebase for slop and fix what it finds; design and build
format 5, where saves only write new steps; history always inside the .blend; Blender's copy dropped after opening;
Record everything by default; record making and deleting file-level datablocks; test with real third-party add-ons
once the owner names them; call it V2 in the changelog), then the optimisation phase (real brush strokes at 1M as test
data; smaller steps; Record everything fast in big scenes; fast and light at 100,000 steps on 1M vertices, with the
Rust moves where they pay; shrink the code; keep optimising until nothing is left). Proving V1 histories upgrade
intact is done.

Older notes, kept:

- Window scenarios on 4.2 for the older rounds: Rounds A to D pass 91 on 4.2.23 14/16 (round_b passed 18/18 on a rerun with its software-GL check; round_c with a 1,000-step session runs over an hour under software GL and left no result).
- Looked at and left (no round): add-on start 25 ms, idle timers 8 ms of CPU a minute, Weight Paint weight reads (77 ms at 100k on 5.3; no bulk read in Blender, every loop form measured within 10%), Edit Mode step at 300k (26 of 35 ms are Blender's update_from_editmode), entering and leaving Sculpt Mode at 1M (Hindsight's share under 0.1 ms), saving with the history folder at 6,000 steps (fsync, rename and write are 118 of about 165 ms), opening it (about 100 ms; the native parse of the step table was R078, reverted: within the spread). Also left (2026-11-19): a stroke at 1M (2.7 ms: the diff and the read, 1 ms each), a Dyntopo step at 100k triangles after R079 (about 55 ms, 45 to 50 of them Blender's `ed.flush_edits`), long histories (a commit 0.14 ms at 1,000 steps and 0.17 ms at 50,000; every redraw path the same at 1,000 and 50,000 steps), Morph History's start of a stroke (22 to 31 ms at 1M, 3 to 33 steps from the target), Embed save at 6,000 steps (Hindsight 103 of 206 ms: building the 55 MB copy and Blender's own copy of it, both page faults), jumps and drags unchanged since dd9f35b (bench/results/ab_dd9f35b_vs_head.md). Memory over a long session (5,000 strokes at 100k with jumps and saves): the add-on's heap grows by the history and about 0.65 KB of objects per step, nothing else (tracemalloc); the process grows a little more, as it does with the same strokes unrecorded (bench/results/soak_2026-11-19.txt).
- Dyntopo memory: done as far as it goes without a format bump (R049: packed TOPO steps halved, `before` shares the previous step's `after` in memory). What is left is one mesh per link of the chain; a delta-coded `after` needs a step format bump and the neighbouring entry at read time.
