# Handoff

**Test guarantee (owner, 2026-10-14): what counts as finished.** Order of work unchanged. No time
pressure: never cut tests, rounds or review loops to finish sooner. Stop early only on the usage
stop-loss (then push).
- Files: TEST_AUDIT.md (blunt inventory per tier and row: code written, tests by file and name,
  real Blender or headless only, what's missing; sculpt tests never count for other rows).
  COVERAGE.md statuses are `supported`, `partial` (says exactly what works and what doesn't) or
  `not covered`; a row moves up only through the gate. MANUAL_TESTS.md lists what the harness can't do (exact
  steps); those rows stay `partial` until the owner confirms.
- **Tier gate**, every row, on 5.0.1, 5.1.1 and 5.1.2: (1) real operators (bpy.ops with context
  overrides and the event harness; never poking data); (2) exactness at every step both ways, mesh
  bit-exact, the rest value-exact; (3) independent oracle (snapshot code separate from adapter capture)
  plus a replay oracle (same operations in a fresh scene without Hindsight); (4) exactly one step per
  user operation, operator-end and settle paths, counted; (5) native undo: Ctrl+Z / Ctrl+Shift+Z mixed
  with scrubbing, across mode switches, Global Undo on and off, undo steps default, min and max;
  (6) save and reload in sidecar, embed and both, history reattaches, rename, copy-paste to another
  file, append and link, Shift+D and Alt+D, previous-format files; (7) Hypothesis: 2,000+ random
  sequences per adapter, shrinking, failing seeds kept in the repo; (8) mutation testing of adapter
  diff/apply code, survivors explained or killed; (9) fault injection: an adapter raising in capture,
  diff or apply disables only itself (one log line, sidebar message, history intact, undo and
  sculpting fine); corrupt stored data gives a clear message, never a crash or a silent wrong state;
  (10) cost and memory per step on small, medium and heavy scenes against budgets, 30-minute soak per
  adapter (2-hour mixed soak later), no growth in memory, threads, timers, file handles; (11) every
  unsupported or partial thing shows a message in the add-on and a COVERAGE line, tested; (12)
  screenshots of the row's UI in real Blender, looked at; cut off or overlapping is a defect.
  Then update COVERAGE from real results, commit, push, verify, and only then start
  the next tier. No later tier while an earlier one has open S1 or S2.
- Per tier minimum coverage and cross-feature/system tests: see the owner's list (copied in
  TEST_AUDIT.md under "Required by the test guarantee").
- Anti-gaming: never weaken, skip, loosen or delete a test (a wrong test is fixed with a reason in
  DEFECTS.md); every bug gets a regression test that fails without the fix; intermittent failures are
  S2 defects (run suspicious tests 20 times); a fix that breaks an earlier test resets the clean-pass
  counter.
- Cadence: after each tier gate a full regression pass on all Blenders, commit, push, verify, HANDOFF.
  Every few hours the whole suite from a clean sandbox with the zip from the branch (TEST_ROUNDS.md).
  After each tier, one fresh-context Opus hostile QA reviewer (one at a time); fix all, regression
  tests, rerun the gate; a second reviewer if it found any S1 or S2. Three clean passes count from
  after the last completed tier, over everything.
- Test data: make_test_models.py gets a scene per COVERAGE row plus heavy versions (table and robot
  from primitives, parented hierarchy, modifier-heavy object, big GN graph, shader tree, painted
  weights and textures, animated rig with NLA, every object type, 20+ object scene). Owner's model
  copies run through the new rows too; delete the copies at the end.
- Final report adds: per tier rows supported / partial / not covered, tests per layer, defects by
  severity and open count, per-adapter cost and memory per step, reviewer findings, MANUAL_TESTS
  items, and a table of all rows.

**No fake history (owner, 2026-10-15), for the "history from existing files" feature.** (The
earlier message that described this feature never arrived in this session; it is built from this
clarification, which overrides anything looser. Noted in NEEDS-JACK.md.) A .blend Hindsight never
recorded has no history: show nothing, say so plainly, offer the honest alternative. Never invent
steps, ticks, counters, thumbnails, labels or progress.
- Empty state (no .blend1/.blend2, autosave, matching names or git history): Find saved versions
  shows "No saved versions found for this file." and under it "Hindsight can only rebuild history
  from versions that exist on disk. Start recording now and everything from here on is kept stroke
  by stroke." Buttons: Start recording, Add file, Close. No list, previews, progress or "0 of 0".
  Sidebar keeps "No history yet" with Start recording; the on-screen slider and editor scrubbers show
  an empty track labelled 0 steps (or are hidden), never ticks or "1 / N"; checked in Object, Sculpt,
  Vertex Paint and Edit Mode.
- Walk the stack only when there is a real stack (2+ modifiers, shape keys, Geometry Nodes inputs,
  Multires levels), otherwise hidden or disabled with a one-line reason; labelled "construction
  order, not the order you worked in".
- Imported steps only for real saved versions, one per version, with source file and time; never
  interpolate, never split a version, never claim a time or action the file doesn't prove; identical
  versions import nothing and say so. Label "Saved version, <date and time>" plus a plain summary of
  what changed where known ("3 objects changed, topology changed"). No unmeasured speed or time
  estimates, no result before the background read finishes, Cancel leaves no steps. Uncertain
  matching is said to be uncertain.
- README, release notes and add-on strings follow the same rule; check_copy fails on
  wording implying recovery of unsaved work, individual strokes, "go back on any file", and on any
  caption showing a populated timeline for a file with no saved versions.
- Tests (in the tier gate): untouched generated file, no siblings, all four modes,
  three Blenders, with and without a stack: zero steps, empty-state text, no ticks or counters, no
  files written anywhere, original byte-identical, no sidecar. Same with one .blend1, only an
  autosave, only a git commit, and an unrelated similarly named file (not offered unless added).
  Cancel before, during and after the read: zero steps, no temp files. Screenshots of every empty
  state, looked at. A scan of add-on strings for the banned phrases.
- METADATA.md (audit now): everything Hindsight writes into or next to a .blend (properties, custom
  properties, scene and object data, files and folders) with name, type, when written, what removes
  it; generated from the registered property names by a script, plus a test that fails when the
  code writes something not listed. Verify and state: opening a file doesn't change it until
  recording is enabled or the file is saved; importing never writes to the originals; a file saved
  with Hindsight data opens fine without Hindsight (tested in a profile without the add-on).
  Advanced gets "Remove Hindsight data from this file" (lists what goes, confirms, removes ids and
  settings, the sidecar folder only if ticked, never geometry), tested. Docs and FAQ: "What does
  Hindsight add to my file?" and "What happens if I only have one file?" (the one-file case first on
  the "Using it on an existing file" page, with a real screenshot of the empty-state dialog).

Read this first. Then DECISIONS.md (top section), TEST_ROUNDS.md, STYLE.md.

## Beta 3 job (owner, 2026-11-18, cloud sandbox: no Blender binary, no GPU)
Read DECISIONS.md "Beta 3 job" first. Owner's instruction for this job: "don't edit site, just get the
add-on updated": nothing under site/ was touched; docs/download/hindsight.zip and the generated
changelog page change with the version.
- Done and tested headless on Blender 4.2.23, 5.0.1, 5.1.2, 5.2.2 and 5.3.0 (Blender as a Python module):
  Movie Mode camera and lighting (hindsight/bl/camrig.py, rigmark.py, core/framing.py; INTERFACES.md
  section 9), the recorder ignoring those objects, the on-screen slider centred (D62), Steps per frame
  and faster playback (PERF_LOG "Movie Mode playback"), less memory on save, load and topology steps
  (PERF_LOG "Memory, and the Rust question"), the global timeline kept between redraws, audit fixes
  D63 to D69, Blender 5.2 and 5.3 fixes D70 to D76 (D74 also closes the 5.0 colour-layer failures of
  test_bl_t0_gate and test_bl_t0_reload in the module run).
- NOT done, and why it matters: the release gate (real Blender 5.0.1, 5.1.1, 5.1.2 with a window) was
  not run for any of this. COVERAGE.md is unchanged. Before the next gate run,
  check in a real window: the slider's position with the toolbar, the sidebar and both open; the Movie
  Mode panel's new rows; "Use this view"; the camera and lamps being put back after deleting them, and
  not during a stroke, a drag, playback or a render; a real render of a timelapse (EEVEE and Workbench
  could not run here: only Cycles on the CPU); Blender 5.3 as a whole in a window.
- Rust engine (owner: "rewrite in rust"): native/ (crate), hindsight/core/native.py (loader),
  hindsight/wheels/ (four committed wheels, listed in the manifest), .github/workflows/native.yml (proves
  them on Windows, macOS and Linux runners; run it by hand after any change under native/). To rebuild
  the wheels: PERF_LOG "Rust engine" has the commands. Copy diagnostics shows "Engine: Rust" or why the
  Python engine is in use.
- Still open from the audit (report: DECISIONS.md, "The two linked repositories"): duplicate helpers
  (finding 8) and about 60 lines of dead code (finding 7).
- Crash recovery (D77 to D83, DECISIONS "Crash recovery keeps the saved state"): done, tested on 4.2 and
  5.3 (tests/test_bl_recovery.py, tests/test_core_journal.py). 1.0.0-beta.3 is NOT released yet: the
  manifest still says beta.2 and docs/download/hindsight.zip is the beta.2 zip.
- Since then (2026-11-18 evening to 2026-11-19): every agent branch was merged or replaced; the Rust
  engine is required (0.5.1, API 5; Python copies in tests/reference); both lists of the limitations
  page were checked line by line and almost every line is fixed or recorded now (DEVELOPMENT.md
  section d, DEFECTS.md D77 to D141). The current state, branches, test matrix and next actions are in
  DEVELOPMENT.md. 1.0.0-beta.3 is still not released. Real-window checks (2026-11-19, in this container: Xvfb,
  Mesa llvmpipe, Blender binaries 5.3.0 and 4.2.23, `tests_ui/hs_ui.py`): the slider with toolbar, sidebar,
  both and neither; Movie Mode (camera and lamps, Use this view, put back after Delete but not during a
  stroke, a drag, playback or a render, Workbench and EEVEE timelapse renders); Weight, Vertex, Texture Paint
  and Image Editor painting; Multires and Dyntopo; Rounds A to D on 5.3 (Round C all passed). They found
  D142 and D144 (texture painting) and out-of-date scenarios, all fixed. The Movie Mode panel's rows were looked at
  in a screenshot (s_movie_panel; two cut labels fixed, D148). Not checked here: real 5.0.1/5.1/5.2
  binaries (only 4.2.23 and 5.3.0 are downloaded here; download.blender.org is allowed, the others were simply not fetched; the GitHub window job runs all five). COVERAGE.md is unchanged.
- Known and not ours to fix here: on Blender 4.2 tests/test_bl_t1_gate.py::test_gate_parenting fails
  (parent_type at one position; it fails on beta.2 code too; handed to DATAREC to find the cause).
- tools/mutation_bl.py compiles again; mutants 6, 7, 8, 17, 35, 56 and 60 need re-targeting.
- Blender 4.2 (below the gate): the module run has failures that were there before this job
  (test_bl_t0_gate and test_bl_t0_reload crash, test_bl_t1_reload, test_bl_t1_corrupt and others).
- Test environment here: `HINDSIGHT_PY=<venv>/bin/python python3 tests/run_tests.py` (venvs in `venvs/` next to the checkout)
  (venvs bpy42, bpy50, bpy51, bpy52, bpy53: `pip install bpy==<version> numpy zstandard hypothesis`).

## Publish 1.0.0-beta.2 site (owner, 2026-10-15, top priority, live by 09:00 UK)
Done in worktree `.tmp/wt_site` (branch site-beta2), merged to the default branch without [skip ci].
- Version 1.0.0-beta.2 (manifest, site, changelog, download button); `hindsight/build_info.json` holds the
  source commit. Zip built with real Blender 5.1.2 (`tests_ui/hs_ui.py build`), install-tested in a fresh
  sandboxed 5.1.2 profile (`.tmp/smoke_run.py`: enables, Undo History tab, a sculpt stroke records, a move
  isn't recorded until Record is Everything and the object is switched on, then it is). Other Blender
  versions weren't install-tested in this step.
- Sidebar: in Object Mode with Record = Sculpting only it says object recording is off and how to switch it on.
- COVERAGE: object rows and sculpt rows (except sculpt.colors) are `partial`, each with the exact open gate
  items (DECISIONS.md, 2026-10-15). sculpt.colors stays not covered (D61
  fails on 5.0.1), so the site doesn't claim Vertex Paint.
- Pages workflow: the zip step now checks the committed, tested zip instead of rebuilding it.
- Site: landing, docs home, Install (with "Turn on object recording"), Limitations, changelog, README.
- Open after this: D61, the 15 second-review findings (record as D62+), soaks, the Hypothesis runs, Tier 1
  gate, Tier 2. The main checkout's tests/ has untracked copies of the Tier 0 tests (now committed here).

## Current job: beta 2 ("broken and sloppy" to polished), started 2026-10-14
Brief: steps 0-6 (PC check, real Blender, root cause, new UI, hardening and test rounds, new website,
ship). Work happens on the owner's Windows PC. Only branch: `claude/lucid-brahmagupta-9gsdsn` (default,
Pages source). WIP commits that must not deploy carry `[skip ci]`.

### Status
- [x] Step 0: tools checked. git, gh (logged in as Min3scon), Python 3.13 and 3.11, Node 22, ffmpeg 7.
  Installed (repo devDependencies, `npm install`): Playwright 1.63.0 with Chromium, Firefox, WebKit;
  Lighthouse 13.5.0; Fraunces and Inter variable fonts. fonttools in `.tools/venv` (git-ignored).
- [x] Step 1: real Blender 5.0.1, 5.1.1 (Steam), 5.1.2 driven with a window through `tests_ui/hs_ui.py`.
- [x] Step 2: root cause in DECISIONS.md.
- [x] Step 3: new in-Blender UI (sidebar tab with a real slider, header popover, F3 menu, self-check,
  diagnostics, guards).
- [~] Step 4: rounds A-G written (tests_ui/rounds.py). Pass 1 ran; see TEST_ROUNDS.md. Still to do:
  pass 2 and 3 (need two clean passes in a row), the 10,000-step 100k session (`--big`) in the final pass,
  the new round_b_features scenario, and delete `my_models_copies/` at the very end.
- [x] Step 5: the V1 website was built and published from `site/` into `docs/`; V2 removed it (ticket #5;
  the last commit with it is tagged `website-final`).
- [ ] Step 6: bump to 1.0.0-beta.2, release notes, push, verify live, pre-release, repo settings.

## v2 scope (owner's update, 2026-10-14, replaces the earlier "all of Blender" and "objects only" notes)
Extend scrubbable history from sculpting to what people build with, in this order, gate and push after
each tier, nothing starts until the previous tier passes:
0. Finish: the two found causes fixed with regression tests. DONE (D1, D2 in DEFECTS.md).
1. Step 3B architecture: data adapters (capture / diff / apply both ways / cost), stable UUIDs on every
   recorded datablock, one step per user operation (operator end, modal end, settle window fallback),
   native undo interplay (push an undo step after a restore, resync on native undo/redo, Global Undo on
   and off), format bump with migration, memory model per datablock plus global limit, per-datablock
   opt-out, preference "sculpt only" or "everything", nothing recorded until enabled for an object, only
   touch what changed, per-adapter time budget enforced by tests. Sculpt is adapter one, no behaviour change.
2. Tier 1 object-built models: transforms, parenting, constraints, collections, visibility; add, delete,
   duplicate, rename; Edit Mode step by step; global timeline with creation and deletion.
3. Tier 2: modifiers (incl. Geometry Nodes inputs), node trees, materials.
4. Tier 3: weight paint, vertex groups, UVs, colour attributes, seams, sharp, creases, custom normals,
   texture paint as tile deltas with its own memory cap.
5. Tier 4: keyframes, F-curves, shape keys, armatures and pose bones, NLA basics, frame range. Scrubbing
   never changes the current frame or playback state.
6. Tier 5: curves, text, metaballs, lattices, grease pencil, lights, cameras, empties.
7. Tier 6 (only if everything above is solid): scene, world, render settings, collection structure.
COVERAGE.md is the honesty source (status, test, limits, measured cost per row); UI, docs and site read
from it. Scrubbers in the
Shader Editor, Geometry Nodes editor, Image Editor, Dope Sheet and Graph Editor; a step list popover.
Testing extends Step 4 (property-based, mutation, real operators, cross-feature, undo interplay, soak,
fault injection, test models per COVERAGE row). Website repositioned ("from sculpts to whole scenes"),
second demo mode, new docs pages (What it records, How it works with Blender's undo, one page per tier).
Stop-loss: finish tiers in order; ship the last tier that fully passes; the rest is "planned".
The three-clean-passes rule counts only from after the last completed tier.

## Compression scope lock (owner's update, 2026-10-14)
Target: 4-6 KB average per step at 100k vertices for light strokes (about 2% touched), down from about
12.4 KB, fully lossless at every age and tier, behaviour identical (every step restorable, bit-exact).
Removed from scope: lossy anything, step thinning or merging, stroke replay, "Compact/Thin old history"
preferences. Lossless techniques in order, keep only measured gains, stop at the target: one reversible
array per step; component split plus byte shuffle; smarter index coding; per-type codec choice incl. a
trained zstd dictionary shipped and versioned (pure Python fallback stays correct); grouped compression
frames (8-32 steps) with an index; cross-step context, neighbour prediction, chunk dedup; verified
background recompression. Round-trip property tests with adversarial floats, mutation testing, 500+
fuzzed corrupt inputs per codec, checksums per step and group, format tag with migration, no scenario
more than 10% slower. Report in PERF_LOG.md (100k, 500k, 1M at 2/10/30% strokes).

### Compression status (2026-10-14)
Done in core, file format 3: wrapping differences instead of XOR, per-component prediction with zigzag
and byte planes, binary step header with CRC, header CRC trailer in the file, quick form for the journal
and saves, idle background recompression (zstd 19, verified), faster apply. Old beta files load as they
are (fixtures in tests/fixtures, written by the old builds' own code). Tests: test_core_codec (adversarial
round trips, 1,800 damaged blobs), test_core_migration, mutation testing (tools/mutation_test.py, all
killed). Real-stroke corpora: `tests_ui/scenarios/s_corpus.py` -> `.tmp/corpus` (git-ignored); tables:
`bench/perf_log.py`; speed: `bench/scrub_ab.py` (core, old vs new) and `.tmp/commit_probe.py` /
`bench/bench_bl.py` in Blender. Results and what didn't help: PERF_LOG.md. The 4-6 KB target is not
reachable losslessly on real strokes (best: 7.9 KB at 100k, 2%; see PERF_LOG). Decode-ahead threads
were removed: inside Blender they made long jumps slower.

### Push log (verified with git ls-remote after each push)
- 2026-10-14 d200842: tier 0 gate, default branch and hindsight-wip.
- 2026-10-14 f0d94ef: compression (format 3), hindsight-wip. The owner then merged it into the
  default branch on GitHub (PR #1, 934df91); that merge ran CI, which failed the copy check (a docs
  paragraph over 55 words), so nothing deployed.
- 2026-10-14 def2188 (merge of 934df91) and fc2b6d3 (copy fix): compression gate, both branches.
  Pushes keep `[skip ci]` until the new site is ready to go live.
- Work on Step 3B happens on branch `step3b` in the worktree `.tmp/wt3b` (git-ignored folder).
- 2026-10-14 b3f25c7: Step 3B gate (merge), both branches.
- 2026-10-14 9e8bf7a: Tier 1 gate (merge), both branches.

### Step 3B status (2026-10-14)
Done: adapter layer (core records, format 4, ids, RNA adapter with object transforms as the first
rows, data recorder with operation boundaries, playback, Blender undo interplay, Record preference and
per-object opt-in, COVERAGE.md and its copy-check rule). Tests: test_core_records, test_bl_adapters,
tests_ui s_adapters (15 checks, all three Blenders), mutation testing 28/28. Next: Tier 1.

### Tier 1 status (2026-10-14)
Done: transforms, parenting, constraints, collections, visibility, names, Edit Mode step by step,
adding/deleting/duplicating objects with the scene history and the global timeline, step list,
Advanced panel. Tests: test_bl_tier1 (11), s_tier1 (17 checks, all three Blenders), run_all 30/30.
COVERAGE rows filled. round_b_features now 18/18 on all three Blenders: it found D13 (Morph History
brush broken in Blender 5.x, fixed) and a test-setup gap (an object kept Sculpt Mode after another
object was added, so Ctrl+J ran in Sculpt Mode). Next: Tier 2.

### Tier 1 gate status (test guarantee, 2026-10-15)
Headless gate on all three Blenders: OPS, EXACT, ORACLE+REPLAY, STEPS, UNDO (emulated), RELOAD (all
variants incl. previous formats), FAULT and FAULT.CORRUPT, WARN, PERF pass (`tests/test_bl_t1_*.py`).
Hypothesis at 2,000 examples per row with the corpus in `tests/corpus/t1_prop`. Mutation:
`tools/mutation_bl.py` 28 mutants. Defects found and fixed so far: D14-D34 (DEFECTS.md).
Real-interface gate: `python tests_ui/hs_ui.py run t1_gate --blender <v> --out-tag <v>[-variant]`
(variants: `--arg variant=globaloff|undomin|undomax`, `--arg debug=1` prints every history's cursor
around Blender's undo). 48/48 on 5.1.2 after D35 (jumps on Blender's undo). The Hypothesis run then
found D36 (objects added while the scene is recorded lost later changes) and D37 (a reference to a
deleted object); fixes in `.tmp/fix_d36_d37.py` and the parenting-deform note in
`.tmp/warn_parent.py` are applied after the headless pass, with tests/test_bl_t1_added.py.
Soak per adapter: `python tests_ui/hs_ui.py run soak --blender <v> --out-tag <v>-<focus> --arg
focus=rna|object|scene|edit|mesh` (30 minutes each), mixed (`focus=mixed`, 2 hours) later.
Still to do for the gate: t1_gate default and three variants on all three Blenders, visual review of
the shots, the soaks, the final Hypothesis and mutation runs on the final
code, the hostile reviewer, a full regression pass. COVERAGE statuses change only through the gate.
Hostile review 1 (2026-10-15, repros in `.tmp/review/p01..p38`): D42-D59 found, D60 found while
fixing D57; all fixed with regression tests that fail without the fix and mutants in
`tools/mutation_bl.py` (each fix's sanity run: `.tmp/sanity_d5x.py`). Next: full headless suite on
all three Blenders, then hostile review 2 (S1/S2 were found), then the remaining gate runs above.

## Order of work from here
Tier 0 done -> compression (core codec, file format 3) -> Step 3B adapter architecture (format 4 adds
adapter records, migration from 1-3) -> Tiers 1..6 -> final test passes -> ship.
Since the test guarantee: Tier 2 code is parked on the local branch `tier2` (worktree `.tmp/wt3b`,
commit 2ac7e45, also `.tmp/tier2_wip.patch`). Now: audit (TEST_AUDIT.md, COVERAGE statuses), then the full gate for sculpting (existing rows) and Tier 1, including D14 (Object Mode mesh
changes on recorded objects weren't recorded: Apply Scale, Apply Modifier, Set Origin; the fix is in
the tier2 commit and gets ported), then Tier 2 resumes from that branch.
Branches: see CLAUDE.md ("Working a ticket"): every ticket is a pull request, every commit ends with `[skip ci]`.

### How to run things
- List Blenders: `python tests_ui/hs_ui.py list`
- Build the zip with real Blender: `python tests_ui/hs_ui.py build` (writes .tmp/build/hindsight.zip)
- Real-UI scenario: `python tests_ui/hs_ui.py run <name> --blender all [--fresh]`
- Full test pass: `python tests_ui/rounds.py --pass N [--big]` (about 75 minutes on three Blenders)
- Headless suite inside real Blender: `python tests/run_tests.py --blender all`
- Copy check: `python tools/check_copy.py`.
- Pre-commit hook runs `tools/precommit_check.py`: blocks big files, secrets and personal paths.

### Safety rules for this job
- Never touch the owner's Blender prefs or add-ons: always go through the sandbox env in hs_ui.py.
- Owner's .blend files: only copies in `my_models_copies/` (git-ignored, made by
  `tests_ui/copy_own_models.py` from `.tmp/own/list.txt`). Delete the folder at the end.
- Nothing from `.tmp/`, `test_models/`, `my_models_copies/` goes into git.

## Map
- Contracts: INTERFACES.md. Parity: PARITY.md. Decisions and known issues: DECISIONS.md.
- Core (no bpy): hindsight/core. Blender layer: hindsight/bl. Headless tests: tests/. Real-UI tests:
  tests_ui/. Benchmarks: bench/. Tools: tools/. ADRs: docs/adr/.
