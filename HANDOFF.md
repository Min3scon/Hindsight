# Handoff

**TOP PRIORITY (owner, 2026-10-14): whenever work stops for any reason (everything finished,
stop-loss, rate limit, low usage, an error that can't be got past), the LAST thing done is making the
newest working site live:**
1. **Before deploying, check the site is safe to show:** no placeholder or empty screenshot slots (hide
   any section whose real media isn't captured yet); every claim matches what is actually tested and
   shipped (COVERAGE.md supported rows only); `python tools/check_copy.py` and the link check pass on
   the built site; version and release notes match. If it isn't all safe, deploy the parts that are and
   report what was left out.
2. **Deploy:** remove `[skip ci]` from the final commit (or run the Pages workflow), wait for the run
   to finish.
3. **Verify live:** the live URL serves the new site, and `download/hindsight.zip` is the new build
   (compare its size and the manifest version with the local build).
4. If this happens before all tiers are done, the report says plainly which tiers landed and labels
   the rest as planned.
Until then: keep the current order of work and keep `[skip ci]` on every commit.

Read this first. Then DECISIONS.md (top section), TEST_ROUNDS.md, SITE_PLAN.md, STYLE.md.

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
- [~] Step 5: site rebuilt from scratch in `site/` (built into `docs/` by `python site/build.py`).
  Done: SITE_PLAN, STYLE, check_copy, design system, demo (passes in 3 browsers), docs, changelog, 404,
  OG cards, icons, Lighthouse runner (all pages 99-100). To do: run `tests_ui/scenarios/s_capture.py`
  (real Blender media), `python tools/encode_media.py`, tune crops in `tools/media_crops.json`, sculpt
  art for the OG card (`site/og/art.png`, then `node site/render_assets.js`), benchmarks
  (`python tools/bench_table.py`), design review rounds at 6 widths x 3 browsers.
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
from it and check_copy fails if the site names a data type that isn't a supported row. Scrubbers in the
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

## Order of work from here
Tier 0 done -> compression (core codec, file format 3) -> Step 3B adapter architecture (format 4 adds
adapter records, migration from 1-3) -> Tiers 1..6 -> website v2 -> final test passes -> ship.
Branches: gated commits go to the default branch with [skip ci] until the site is ready to deploy;
WIP goes to `hindsight-wip`.

### How to run things
- List Blenders: `python tests_ui/hs_ui.py list`
- Build the zip with real Blender: `python tests_ui/hs_ui.py build` (writes .tmp/build/hindsight.zip)
- Real-UI scenario: `python tests_ui/hs_ui.py run <name> --blender all [--fresh]`
- Full test pass: `python tests_ui/rounds.py --pass N [--big]` (about 75 minutes on three Blenders)
- Headless suite inside real Blender: `python tests/run_tests.py --blender all`
- Site: `python scripts/build_zip.py --blender-ext ""` then `python site/build.py`, preview with
  `node tools/serve_site.js` (http://127.0.0.1:8765/Hindsight/), QA with `node tests/site/site_qa.js`,
  Lighthouse with `node tests/site/lighthouse.js`, copy check with `python tools/check_copy.py`.
- Pre-commit hook runs `tools/precommit_check.py`: blocks big files, secrets and personal paths.

### Safety rules for this job
- Never touch the owner's Blender prefs or add-ons: always go through the sandbox env in hs_ui.py.
- Owner's .blend files: only copies in `my_models_copies/` (git-ignored, made by
  `tests_ui/copy_own_models.py` from `.tmp/own/list.txt`). Delete the folder at the end.
- Nothing from `.tmp/`, `test_models/`, `my_models_copies/` goes into git. Site media come only from
  generated models.

## Map
- Contracts: INTERFACES.md. Parity: PARITY.md. Decisions and known issues: DECISIONS.md.
- Core (no bpy): hindsight/core. Blender layer: hindsight/bl. Headless tests: tests/. Real-UI tests:
  tests_ui/. Benchmarks: bench/. Website source: site/ (output: docs/). Tools: tools/.
