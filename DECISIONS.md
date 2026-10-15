# Decisions log

## Status "partial" (owner, 2026-10-15)
Owner's instruction for the beta 2 site: mark rows "partial" only where part of the release gate really
passed, with exact limits in COVERAGE.md, never faked. make_evidence used to require every gate item for
partial, the same as supported, so no row could be partial. It now checks the owner's definition: the core
items (EXACT, ORACLE, REPLAY, STEPS) count on Blender 5.0.1, 5.1.1 and 5.1.2, and the row's Limits ends with
"Open gate items: ..." naming exactly the items that don't count yet (computed by make_evidence, checked on
every commit). "supported" still needs every item. The owner can revert this rule; the site would then claim
nothing until the full gate passes.

## Beta 2 job (2026-10-14, on the owner's Windows PC)

### Environment
- Real Blender found: 5.0.1 and 5.1.2 (Blender Foundation installers), 5.1.1 (Steam). No 4.2-4.5 build is
  installed (a leftover "Blender 4.0" folder has no blender.exe). The manifest minimum stays 4.2.0; 4.2 is
  covered by code review only and is called out as untested on real 4.2 in the release notes.
- Every real-Blender run uses a sandbox profile under `.tmp/profiles/<version>/` (BLENDER_USER_RESOURCES,
  CONFIG, SCRIPTS, EXTENSIONS, DATAFILES, TEMP). The owner's preferences are never read or written.
- Harness: `tests_ui/hs_ui.py` launches Blender with a window, `--enable-event-simulate` (real OS input is
  ignored, simulated mouse and keys drive it), and runs a scenario from `tests_ui/scenarios/` on a timer.
- The headless suite now also runs inside real Blender: `python tests/run_tests.py --blender all`.

### Root cause of "installs but shows no UI" (evidence: `.tmp/ui/*` runs, images in test_rounds/evidence)
Reproduced with the live 1.0.0-beta.1 zip from the website, installed through Blender's own
Install from Disk operator in a fresh profile, on 5.0.1, 5.1.1 and 5.1.2. Registration itself is fine:
all 14 modules register, preferences resolve, keymaps and the draw handler are installed, no traceback.
What breaks is everything a user can see:

1. **Recording stops after any file load (main cause).** `runtime.on_depsgraph` and `runtime.on_undo`
   were appended to `bpy.app.handlers` without `@persistent`, and nothing re-added them in `load_post`.
   Blender removes non-persistent handlers on every File > Open, File > New, Revert and when a .blend is
   opened by double-click (add-ons register first, then the file loads). After that no stroke is ever
   recorded. Evidence: `repro_load` on all three versions: before the load
   `depsgraph_update_post` has `runtime.on_depsgraph`; after `wm.open_mainfile` only `dupsep._on_depsgraph`
   is left, and a real stroke adds no step (`before=1 after=1`).
2. **The on-screen slider only exists once a history exists.** `scrub_info()` returns None until the first
   recorded step, so a new user sees nothing in the viewport. Combined with (1), most users never get a
   first step, so they never see the slider at all.
3. **In Sculpt Mode on Blender 4.3+ the slider is drawn under the brush asset shelf.** The shelf is a
   bottom-aligned overlapping region (y 87-170 in a 1080x900 window). The slider is drawn 18 px above
   the bottom of the WINDOW region, which is exactly under the shelf. Evidence: `repro_scrubber`:
   with the shelf shown nothing is visible; hiding the shelf reveals "1 / 10000" at the bottom.
4. **The sidebar tab is the only always-present UI, and Blender keeps the sidebar closed by default.**
   The panel has no poll and does appear (screenshot `install_gui/02`), but only after pressing N and
   finding the "Undo History" tab. There was no header button, no status message, nothing in F3.

Checklist items that were checked and are NOT the cause:
- register() swallowing errors: required modules re-raise (Blender prints the traceback and disables the
  add-on); optional ones print one line. No error happened here, but a failure would have been nearly
  silent for optional modules. Fixed anyway: full traceback plus a one-time popup.
- Extension id, zip layout, package name, imports: manifest at the zip root, id `hindsight`, installs as
  `bl_ext.user_default.hindsight`, relative imports only, all names valid identifiers.
- Panel poll: the main panel has no poll; sub-panels poll only on `scene.hindsight`.
- gpu/blf: `UNIFORM_COLOR`, `batch_for_shader`, `blf.size(font, size)` and POST_PIXEL all work on 5.0-5.1
  (the slider draws once uncovered).
- Keymaps: registered (13 items). Preferences: `bl_idname` equals the package, preferences resolve.
- Timers: `runtime.tick` is persistent and survives loads. Handlers are registered in register(), not at
  import time; the problem is only the missing `@persistent` (cause 1).

### Fix plan (Step 3)
- `@persistent` on every handler, plus a `load_post` check that re-installs any that went missing.
- The slider shows in Sculpt and Vertex Paint Mode from the start (empty track and a short hint), and sits
  above any bottom-aligned overlapping region (asset shelf).
- A sidebar tab that works without any custom drawing, a header popover, F3 entries, a first-enable
  status message, self-check and copy diagnostics.

### Step 3 UI decisions
- Sidebar slider: `WindowManager.hindsight_step`, an IntProperty with get/set bound to the active object's
  cursor (or the global position). Blender has no per-object range, so the property is redefined with a
  new max from a 0.2 s timer whenever the history length changes (never from a draw callback). It needs
  no custom drawing and keeps working if the on-screen slider is switched off.
- Sidebar back/forward buttons and the F3 menu entries never fall through to Blender's own undo
  (`native_fallback=False`); Ctrl+Z in Sculpt/Vertex Paint keeps the fallback.
- Header button: appended to `VIEW3D_MT_editor_menus` (right after View, Sculpt, Mask...). Appending to
  `VIEW3D_HT_header` put it at the far right, where it was cut off at a 1080 px wide window.
- F3: Blender's F3 is a menu search, so a "Hindsight" submenu is added to the Object, Sculpt, Paint and
  Mesh menus. Search "hindsight" lists Step Back, Step Forward, Open History Menu, Run Self-check,
  Copy Diagnostics. Operator labels are "Hindsight: Step Back" etc. for the keymap editor.
- First-run message: `WorkSpace.status_text_set` for 12 s (operator reports raised from a timer only
  reach the console, not the status bar). Shown once; a hidden preference remembers it.
- Registration never raises any more: a failing module is logged with its traceback, listed in a
  one-time popup and in the sidebar, and the rest loads. Raising from register() made Blender disable
  the whole add-on, which is the worst case for "no UI".
- Guards (`bl/guard.py`): each handler, draw callback, timer and modal runs in a guard. First failure:
  traceback logged once, that feature switched off, sidebar says so. Run Self-check switches failed
  features back on. Save/load errors are reported but never switch saving off.
- History Menu and button wording rewritten in plain words of my own (beta 1 reused the reference
  product's menu labels word for word).
- Entering Vertex Paint on a mesh without colour attributes makes Blender add one; Hindsight records
  that as a step. Kept: it is a real change to the mesh and scrubbing back removes the layer again.

### Step 4 decisions (hardening)
- History file format 2: a `format` number, the writer's version, and a CRC-32 per step. The binary layout
  is unchanged, so beta 1 reads format 2 (it ignores the new keys; Round F checks this both ways).
  Loading never applies a damaged step: a history keeps the steps before the first bad one and the tab
  says what was lost. An unreadable file is renamed to `history.hsr.unreadable-<time>` instead of being
  overwritten by the next save. A newer format is refused and protected from overwrite until the file is
  reopened by a version that understands it.
- Stroke end: Blender 5 exposes `Window.modal_operators`, so a stroke is committed the moment its operator
  ends (tick every 20 ms while changes are pending). The 0.15 s debounce stays only for changes made
  without a stroke operator (menu filters, scripts). This fixed both merged quick strokes and split slow
  strokes. On a Blender without `modal_operators` the old debounce still applies.
- Native undo: Hindsight writes the mesh directly, which Blender's sculpt undo doesn't know about. After
  Hindsight has written an object's mesh in its current mode session, Ctrl+Z never falls back to Blender's
  undo, and a Blender undo that lands outside the history is reversed (from a timer, never inside the
  handler) with a status message. Re-entering the mode clears this, because Blender pushes a full undo
  step on mode change. Alternative considered: push an undo step after every jump; rejected, because a
  generic undo push in Sculpt Mode stores no geometry and would not help.
- No jump while a stroke operator runs: a mode switch mid-stroke crashes Blender 5.1 itself.
- Memory: a cheap running estimate (new steps added at their unpacked size) triggers the exact check, so
  a memory overrun is noticed right away instead of on the 64th commit.
- Removed the "object transforms are not recorded" note. Steps are stored in object space, so moving an
  object doesn't change how its history plays back. The note appeared on almost every object.

### Compression decisions (format 3; numbers in PERF_LOG.md)
- Measured on real strokes first. The 12.4 KB figure in the brief came from the old synthetic benchmark
  (strokes with 5% random noise per vertex). Real mouse strokes recorded in Blender
  (`tests_ui/scenarios/s_corpus.py`: 18 brushes, three sizes, five meshes) store about 9.9 KB per 2%
  step at 100k on format 2. All decisions below were taken on that corpus.
- One reversible array per step was already true (XOR). It is now the wrapping difference new - old of
  the bit patterns: forward adds, backward subtracts, exact for every bit pattern. XOR can't be predicted
  from neighbours (its low bits depend on the old value); the difference can. Steps from format 1 and 2
  stay XOR and play back as they are.
- Per component, the encoder picks a predictor (difference applied 0 to 3 times along the record) by
  estimated bits, then zigzag-maps the residuals and stores byte planes. A higher order is used only for
  an estimated 3% gain, since each order costs a pass when decoding. The estimate reads the float32
  exponent of each value (one stacked array for all orders): trial compression picked within 0.2% of it
  at several times the cost, and a sum-of-values estimate picked worse orders than none at all.
- Kept off the main thread: packing (background thread, full encoder). The crash journal and saving
  write the quick form (values as they are, smaller than format 2 already); those steps, and steps from
  old files, are re-packed later by the idle packer.
- Background recompression: when the packer has been idle for a second it re-packs one step at a time at
  zstd level 19, using at most a quarter of one core, and swaps a blob only if it decodes to the same
  step (`same_step`). Steps over 8 MB are left alone.
- Step container: binary header (no JSON) with a CRC-32 of the whole blob; the file keeps its per-step
  CRCs and gains a CRC of its JSON header (trailer `HSRT`). File format 3. Beta 1 opens format 3 files
  but can't play the new steps back; opening and saving there keeps them intact (Round F checks this).
- Decoding speed: decode work grew (zigzag and prediction undo), so the rest got faster to keep jumps
  within budget: apply uses take/setitem (about 35% faster) and a plain slice for one contiguous run,
  the index decoder has a one-operation path for single runs, and records are parsed once. Which
  objects a jump moves is checked as each step comes up, because a join crossed backwards brings
  objects back mid-jump (D12 was caught by test_bl_join when that was first got wrong).
- The packer waits until no new step has arrived for 0.3 s before packing (unless a save is waiting
  for it): packing right after a commit competed with the main thread for Python and slowed commits
  fired back to back.
- Not done, measured: grouped frames of 8-32 steps (at most 0.4% smaller), cross-step context and chunk
  dedup (no gain beyond grouping), a trained zstd dictionary (plus or minus 0.5%), run-length and bitmap
  index coding (under 1%, slower to decode; the reader accepts them), lzma (4% smaller, 10-15x slower to
  decode), an offset instead of zigzag (12-47% bigger), rotation into the stroke direction (3-10%
  estimated before storing the rotations),
  Rice-style split coding (bigger than zstd on byte planes), prediction from mesh neighbours (an upper
  bound of about a fifth on remeshed meshes and nothing on grid-like ones, at a large decode cost and
  needing topology in the decoder),
  decoding ahead on worker threads (faster in plain Python, 70% slower inside Blender: removed).
- The 4-6 KB target at 100k for 2% strokes is not reachable losslessly on real strokes with these
  techniques; what was reached is in PERF_LOG.md. Lossy storage, thinning and replay are out of scope.

### Step 3B decisions (adapters; design in ADAPTERS.md)
- The mesh recorder stays as it is (adapter one, its own fast path). Everything else is a data record:
  `DataChange(adapter, target, {path: (old, new)})` with JSON values. Playback folds records into the
  same per-datablock baseline the mesh uses (`MeshState.values`) and writes only the changed paths at
  the end of a jump. Records are undone in reverse order within a step.
- Ids: the existing `_hindsight_uid` custom property on any datablock. Blender copies it with the
  datablock, so `idmap.claim` gives a fresh id to a copy while the original still exists.
- Opt-in: preference Record (Sculpting only, the default, or Everything) plus "Record everything on
  this object". With the defaults nothing changes for anyone (the sculpt regressions pass unchanged).
- Step boundaries for data: an operator finished (the newest entry of `window_manager.operators`
  changed) while no modal operator other than navigation runs; otherwise 0.3 s of quiet (fields edited
  in the UI, scripts). G, R, S, a typed value and a sculpt stroke are one step each in the real UI.
- Blender's undo: a jump that wrote data pushes an undo step ("Hindsight jump"); Ctrl+Z then undoes the
  jump and the cursor follows (and redo), checked in the real UI with Global Undo on and off. A Blender
  undo that lands on a neighbouring recorded state moves the cursor one step; anything else becomes the
  new baseline without recording.
- Side effects: setting `rotation_mode` makes Blender convert the rotation, so playback writes modes
  first and then rewrites the rotation values (found by test, now a regression test).
- Animated or driven properties change with the frame and are never recorded by the generic adapter;
  nothing is recorded while animation plays. Tier 4 will record the animation data itself.
- Format: step blob version 4 when a step has records (version 3 otherwise), file format 4.
- COVERAGE.md is the list of what may be claimed; check_copy fails when the built site names a data
  type that isn't supported or opt-in, outside a sentence that says it isn't recorded or is planned.

### Tier 1 decisions (objects)
- Parenting, transforms, visibility flags and names are allowlist rows of the generic adapter, written
  in allowlist order (parent first). Matrices are written as `mathutils.Matrix`: Blender reads a nested
  list assigned to a matrix property column by column (found by test: restored parent inverses came
  back transposed).
- The object adapter records the collections an object is in (scene collections as "scene:<id>"), the
  view-layer hide state (H, Alt+H; these don't send the object an update, so every tracked datablock is
  compared when an operator finishes) and the constraint stack as a list of {type, name, values}. A
  changed stack shape is rebuilt; otherwise only values are set.
- Per-kind switches (Advanced panel, preference `record_kinds`): a kind switched off is not read, and a
  path that is no longer read is not taken as removed.
- Edit Mode step by step: each operator that finishes in Edit Mode is a step, read with
  `update_from_editmode` and a plain copy of the mesh (in Edit Mode `mesh.attributes` are the edit-mode
  layers and have no positions; found by test). About 55 ms per step at 100k vertices, so above 300,000
  vertices Edit Mode stays one step per visit. Blender's Ctrl+Z in Edit Mode moves the cursor (checked
  in the real UI).
- Adding, deleting and duplicating objects are steps of a scene history ("Record adding and deleting
  objects"): the scene records which objects exist; a creation or deletion step also stores the
  object's details and a copy of its mesh, so it can come back after a reload with the same id (its own
  history reconnects). The original mesh is reused while it still exists.
- Only the newest Hindsight jump can be undone with Blender's undo, and only while nothing was recorded
  after it (an older jump matched by coincidence once moved the cursor: found in the real UI).
- A hidden active object stays reachable: Blender's `context.active_object` is None for hidden objects,
  so Hindsight falls back to the view layer's active object (found in the real UI after H).
- Step list (Steps sub-panel and the header popover): the steps around the cursor with their labels;
  clicking one jumps there.

### Test guarantee decisions (Tier 1 gate)
- Blender's own mesh operators don't give a stable element order. The UV sphere primitive after a file
  reset, Extrude after one extra view-layer update, Fill: the same operations give edges, faces and
  corners in a different order depending on memory allocation (shown without Hindsight). Fill and
  Bridge also leave the UVs of the faces they make unset (different values each run). So the replay
  oracle compares meshes in a canonical form (positions in order, edges and faces as sets, other values
  sorted) and leaves out the corner values of faces Fill and Bridge make. Restores are still compared
  bit for bit against the state recorded in the same run.
- Test selections in Edit Mode pick faces and vertices by position, never by index (indices change with
  that element order).
- Child Of's set_inverse_pending is a one-shot request that the next evaluation consumes: it is not
  recorded (putting it back would recompute the inverse instead of restoring the recorded one), and the
  oracle doesn't compare it. Which constraint or modifier is highlighted ("active") is UI state.
- Adapters fail on their own: an error in one adapter's capture, diff or apply switches off that adapter
  only (guard feature "adapter:<id>", listed in the sidebar), and Self-check takes a fresh baseline when
  it switches it back on.
- With the scene recorded, the collection tree is recorded too (part of the Tier 6 row "collection
  structure"), because M > New Collection is a Tier 1 operation and can't be undone without it.
- Jumps and Blender's undo (D35): every finished jump that changes a recorded object, its mesh
  included, is one entry on Blender's undo, pushed in Object Mode. A jump started in Edit Mode leaves
  Edit Mode for the push and goes back into it afterwards (a push in Edit Mode keeps only the edit mesh,
  so Blender's later undo would put back stale data for everything else). Scripts switching modes don't
  add undo entries in Blender, so this adds exactly one entry per jump. Hindsight remembers the jumps
  Blender can still undo or redo (up to 512) and matches one only when the values and every mesh it
  wrote (a fingerprint of counts, topology and recorded attributes before and after) are exactly the
  state on its other side. Sculpt-only histories in Sculpt or Vertex Paint Mode keep the Tier 0
  behaviour (Blender's sculpt undo; see NATIVE_UNDO_TEXT). Cost: two mesh reads per recorded mesh a
  finished jump writes (measured with the Tier 1 PERF tests).
- Objects added or duplicated while the scene is recorded are recorded fully (D36): with only their adding
  recorded, the global history brought them back as they were when added and lost every later change.
- A mesh shared by linked duplicates is recorded in one history (D39), its owner's: two histories of
  deltas over one mesh can't both stay exact. The other users record their object data; their Edit Mode
  visits become one step in the owner's history when they end. Sculpting a linked duplicate keeps the
  Tier 0 behaviour (its own sculpt history; the Alt+D warning says so), and the owner records the result
  once the duplicate leaves Sculpt Mode, so the owner's history stays exact.
- An object's own history can't bring back another object it referred to (D37). That reference stays
  empty with a note; when the global history brings the object back, the recorded references are set.

### Step 5 decisions (website)
- Hand-written HTML templates and a 300-line Python build (`site/build.py`) instead of a static site
  generator: no dependencies in CI, full control over the output, facts (version, size, numbers) injected
  from the build. CSS is inlined on every page (about 25 KB), so there is no render-blocking request.
- Type: Fraunces (variable, optical size and weight) for headings, Inter (variable weight) for text, both
  subset to Basic Latin: 42 KB and 23 KB. Accent: the slider's own orange.
- The demo is plain WebGL 1 with an icosphere of 10,242 vertices. It stores each stroke as moved-vertex
  indices with before and after values, the same idea as the add-on, so jumps are exact. Normals and
  screen positions are updated only around moved vertices. WebGL starts only when the demo is about to
  scroll into view (Lighthouse mobile went from 68 to 99 for the home page).
- Social cards are HTML rendered by Playwright, not screenshots of the page.

## Earlier jobs (cloud session, headless bpy only)

### Timing
- First start: 2026-10-14 11:49:28 UTC. Schedule reset by the follow-up instructions at **11:56:12 UTC**
  (deadline 12:56 UTC).

### Environment / research
- The four reference URLs (80.lv, superhivemarket.com x2, the docs site on github.io) are blocked by this
  cloud environment's network policy (proxy 403 / DNS). The docs site is a public GitHub repo, so its
  HTML source was read from a clone (behaviour text only; nothing copied). The product page and FAQ text
  came from that repo's landing page source and from web-search summaries. To let the agent read them
  directly, allow those hosts in the environment's network settings.
- Blender binaries cannot be downloaded here (download.blender.org blocked). All headless tests run with
  Blender's `bpy` module from PyPI: bpy 4.2.23 (Python 3.11) and bpy 5.2.2 (Python 3.13), not
  `blender -b --python`. The runner (`tests/run_tests.py`) uses `$HINDSIGHT_PY` so the same tests can run
  under `blender -b --python`.
- In bpy-module (background) mode sculpt operators (brush strokes, filters, mask/face-set ops) segfault
  or fail their poll, so tests simulate strokes by writing mesh attributes directly and calling the
  same `runtime.commit()` the stroke-end detector calls.

### Behaviour guesses (please verify against the original)
1. **Stroke-end detection**: a depsgraph handler marks the object dirty; a 0.1 s timer commits once no
   geometry update arrived for 0.15 s. `Window.modal_operators` does not exist in 4.2 or 5.2. A long
   pause mid-stroke with a brush that stops sending updates could split one stroke into two steps.
2. **Step counter** shows `position / step cap` (e.g. `214 / 10000`), as in the spec's example.
3. **Truncation Warning Threshold** default = 10. When the confirmation is needed the stroke is reverted
   immediately and the dialog asks; on OK the future steps are dropped and the user repeats the stroke.
4. **F9 redo** on a filter: detected as "native undo matched the last step, then one new change"; the
   step is replaced silently even if the threshold is 1.
5. **Ctrl+Z / Ctrl+Shift+Z** are bound in the Sculpt and Vertex Paint keymaps only. In Edit/Object mode
   Blender's native undo is untouched. When there is nothing left to step, the operator falls back to
   `ed.undo` / `ed.redo`.
6. **Turn On after Turn Off**: changes made while off are recorded as one bridging step named
   "Changes While Off" (so earlier steps stay reachable and the current mesh is the new start).
7. **Reset Undo History** keeps the scrubber visible at 0 steps.
8. **Edit Mode does not start a history** (only a sculpt or vertex paint stroke does).
9. **Global history + branching**: sculpting after scrubbing back on the global timeline truncates the
   future of the object being sculpted (per-object branch).
10. **Global position** = number of applied steps; moving on the global timeline snaps every object to
    the global prefix.
11. **Preserve Modes** default on. When on, the object owning the step reached becomes active and enters
    the mode it was recorded in.
12. **Shift+D duplicate** starts its own, empty history (it is not a copy of the original's history).
13. **Changes made outside recording modes** (e.g. Object Mode operations that alter the mesh) are
    recorded as one "Changes Outside History" step when the object next enters Sculpt/Vertex Paint.
14. **Memory limit** default 4096 MB covers all histories; oldest steps (by global sequence) evicted first.
15. **Sidecar folder** name: `<blend name>_hindsight` next to the .blend. Missing-folder message:
    "Undo History Not Found".
16. **Morph History** respects the sculpt mask (masked areas are not morphed).
17. Scrubber drawn at the bottom of the viewport (the reference GIFs show it at the top; the written docs
    say bottom, which the spec follows).
18. In Sculpt Mode the viewport may need a mode toggle to show positions written from Python; a hidden
    preference `sculpt_refresh_toggle` is read if present (default off). **Manual check needed.**

### Scope cuts (per the follow-up instructions)
- STRETCH features dropped. No memory-mapped cold tier, no checkpoints, no dirty-chunk tracking
  (full numpy diff per stroke).

### Manual UI checks (cannot be tested headless)
- Scrubber drawing, drag in Sculpt Mode (clicks must beat brush strokes), dot opens History Menu,
  step counter, locked look in Movie Mode.
- Real brush strokes / filters / mask / face set / hide operators produce exactly one step each;
  F9 tweak of a filter updates the same step.
- Ctrl+Z / Cmd+Z in Sculpt Mode step the Hindsight history.
- Truncation confirmation dialog appears and works.
- Morph History tool appears at the end of the Sculpt toolbar and paints.
- Dyntopo/Multires viewport warning text.
- Viewport refresh after scrubbing in Sculpt Mode.

### Screenshots to capture for the website
1. Scrubber at the bottom of the viewport in Sculpt Mode (with the step counter visible).
2. History Menu open from the dot.
3. Undo History sidebar tab (Movie Mode, Scrubber, Memory, Collection Filtering open).

### Repository settings you need to change
- **GitHub Pages is not enabled yet.** The `pages` workflow builds the zip fine but fails at
  `configure-pages` with "Get Pages site failed ... Not Found". Turn it on: **Settings > Pages >
  Build and deployment > Source: GitHub Actions**, then re-run the latest `pages` workflow (or push).
  The site will be at https://min3scon.github.io/Hindsight/ and the download at
  https://min3scon.github.io/Hindsight/download/hindsight.zip.
- The repository had no commits before this work, so the working branch
  `claude/lucid-brahmagupta-9gsdsn` became the default branch. There is no other branch to open a pull
  request against, so no PR was created. The Pages workflow deploys from whatever the default branch is.
- **Tag v1.0.0 could not be pushed from this session** (the git proxy answers HTTP 403 for tags; only the
  working branch is pushable). The tag exists locally at commit `1c1acf7`. To publish the release, do
  one of: (a) on GitHub: Releases > Draft a new release > Choose a tag: type `v1.0.0` > Target:
  `claude/lucid-brahmagupta-9gsdsn` > Publish; or (b) locally: `git fetch origin && git tag v1.0.0
  origin/claude/lucid-brahmagupta-9gsdsn && git push origin v1.0.0`. Either triggers
  `.github/workflows/release.yml`, which builds `hindsight.zip` and attaches it to the release. The
  website's Download button does not depend on this: it serves `docs/download/hindsight.zip` directly.
- If the `github-pages` environment has deployment branch rules, allow the default branch and `v*` tags.

### Known issues
- Morph target set on a step that later gets dropped by branching (scrub back, sculpt) is cleared; set a
  new target after branching.
- A stroke that exactly reverses the previous step bit-for-bit is treated like a native undo (the
  cursor moves back instead of adding a step). Practically only possible with a full-strength Morph
  History stroke over the whole changed area.
- Movie Mode playback that crosses a topology step while the object is in Sculpt Mode applies that
  step from a timer right after the frame change (operators can't run inside the frame handler), so a
  render of that exact frame may show the previous topology. Keep objects in Object Mode when rendering
  a timelapse.
- Linked-duplicate (Alt+D) popup and Mask Extract confirmation are untested in a real UI.

### Benchmarks (real, measured; see bench/RESULTS.md)
Headless bpy 4.2.23 module, 4-core 2.1 GHz Xeon, strokes touching ~2% of vertices:
- 100k verts: 12.4 KB/step packed (32.1 KB raw), record 1.07 ms, scrub 1/100/300 steps 0.4/13/36 ms,
  5,000-step jump 724 ms; save/load of 300 steps 105/118 ms.
- 500k verts: 58.8 KB/step packed (159.5 KB raw), record 3.58 ms, scrub 1/100/300 steps 1.5/56/161 ms;
  save/load of 300 steps 740/678 ms.
- Stress: 10,000 steps on a 99.5k-vertex mesh, all jumps bit-exact, 25.8 MiB of history, ~0.95 ms per
  commit flat across the whole run.
- Not measured (scope cut to 100k/500k): 1M vertices and the "6,000 steps at 1M <= 1.8 GB" target.
  At 500k the packed size per 2% stroke is 58.8 KB.
- Profiling fix (once, as flagged): commit cost grew from ~1 ms to ~2.6 ms over 10,000 steps because cap
  and tier checks rescanned every entry on each commit. Made incremental (full rescan every 64 commits):
  now flat at ~0.95 ms; stress test 36 s -> 28 s.

### Agents used
- Haiku: R1 and R2 research, README, PARITY audit (4 completed). Two early haiku website/CI agents were
  stopped by the plan change before producing anything.
- Sonnet: website, build/publish/Pages workflows (2).
- Opus: storage engine, restore engine, journal/persistence core, scrubber UI, History Menu,
  prefs/sidebar/filtering, warnings, save/load glue, Movie Mode, Morph History, Join/Unmerge,
  Separate/Duplicate, stress/benchmark/install tests (13).
- The main session wrote the shared types, registry, global timeline, mesh I/O, runtime/recorder,
  operators/keymaps, props, add-on init, test harness and runner, and integrated everything.

### Beta 1.0.0-beta.1 (2026-10-14, second job)
- Version `1.0.0-beta.1` is in blender_manifest.toml: both the Blender 4.2 and 5.2 extension validators
  accept it (validate + build exit 0). Tagline ends with "(beta)".
- Add-on beta labelling: "BETA" in the Undo History sidebar panel header and in Add-on Preferences, with
  "Beta: please report bugs on GitHub" and a "Report a bug" button (wm.url_open to the issue chooser).
  Test: tests/test_bl_beta.py. Suite: 16 files / 125 tests green on bpy 4.2.23 and 5.2.2.
- The repo's default branch is `claude/lucid-brahmagupta-9gsdsn` (it was the first branch pushed to the
  empty repo), so Pages, workflows and releases already point at the branch with the code. No `main`.
- **Permission walls (not worked around):**
  - `POST /repos/Min3scon/Hindsight/pages {"build_type":"workflow"}` -> HTTP 403 "Access to this GitHub
    API path is not permitted through this proxy." You need to click: **Settings > Pages > Build and
    deployment > Source: GitHub Actions**, then **Actions > pages > Run workflow** (or push any commit).
  - `POST /repos/Min3scon/Hindsight/releases` -> HTTP 403 "Creating, editing, or deleting releases is not
    permitted for this session type." Git tag pushes are refused too (HTTP 403). You need to click:
    **Releases > Draft a new release > Choose a tag: type `v1.0.0-beta.1` > Create new tag on publish >
    Target: `claude/lucid-brahmagupta-9gsdsn` > Title "Hindsight 1.0.0 beta 1" > paste
    `.github/release-notes/v1.0.0-beta.1.md` > tick "Set as a pre-release" > Publish release.** Creating
    the tag starts `.github/workflows/release.yml`, which builds `hindsight.zip`, attaches it, keeps it a
    pre-release and uses the notes file. If the zip doesn't appear within a few minutes, attach
    `docs/download/hindsight.zip` by hand (Edit release > drag the file in).
- Pages got enabled during this job (has_pages became true) together with a new
  `.github/workflows/static.yml` ("Deploy static content to Pages") that uploaded the whole repo root,
  which has no index.html and raced with the existing `pages` workflow (same concurrency group). Fixed by
  keeping `static.yml` as the single deploy workflow: it now rebuilds `docs/download/hindsight.zip`,
  publishes only `docs/`, and also runs on `v*` tags. The duplicate `pages.yml` was removed.
