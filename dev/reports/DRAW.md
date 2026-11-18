> 2026-11-18. An agent's report as delivered, unchanged below this line. Its work is merged into the default branch.

# DRAW report: redraw cost of the slider, sidebar, header, menu, warnings and filtering

Branch: worktree `agent-a79aa8a5e8c63e2d4` (commits 1b79737, c6ef432, 694e2e2, d658f9f on top of 66fcdf8).
No GPU and no Blender binary here: gpu and blf were replaced by recorders, so only the Python side is
measured and verified. Nothing was looked at in a real window.

## Method
- `S/agents/DRAW/draw_bench.py ROOT` (bpy53): registers the add-on with a real AddonPreferences entry,
  records real steps (`fake_stroke` + `runtime.commit`, packer drained before timing), builds a fake
  3D View context (1574x954 window region, toolbar 56 px, sidebar 320 px), stubs `gpu`, `gpu_extras`,
  `blf` with call counters, then times (7 batches, min) and counts Python calls (cProfile) per:
  - viewport redraw = gizmo group poll + slider draw handler + warnings overlay + header button
  - panel redraw = main panel draw + IntProperty getter + Warnings poll/draw + Movie/Filter polls
  - Steps sub-panel, Memory sub-panel, header popover, hover (`test_select` per mouse move)
  - drag: one mouse move over a 5,000 step history = gizmo modal (pos, throttle, goto, tag) + the
    viewport redraw that follows; `runtime.goto` replaced by a cursor move (UI share only)
  - slider_prop timer: CPU over 60 simulated seconds (300 calls)
  Sizes 10 / 1,000 / 10,000 steps; 1 and 50 objects (steps split over objects); per-object and global
  history; 50 objects again with Collection Filtering (10 collections, 3 blacklisted).
- A/B: `S/agents/DRAW/ab.py 5 run_full` alternates baseline (S/baseline) and this worktree, 5 rounds
  each; `summarize.py run_full` prints min over rounds and (median of the per-round mins).
  Full table: `S/agents/DRAW/summary_full.txt`. Raw JSON: `S/agents/DRAW/run_full/`.

## Bottlenecks found (profile evidence)
1. Slider batches keyed on (layout, pos, length, morph, hover): every handle move rebuilt all 11
   batches (`batch_for_shader` 11 per drag move), and every stroke rebuilt them too (pos and len change
   even though the handle stays at the right end).
2. `layout()` recomputed on every draw and every hover `test_select`.
3. `import` statements inside per-redraw functions (`_runtime`, `_prefs`, ui_panel `_rt`, `rigmark`,
   `datarec`): `importlib._handle_fromlist` was 1,800 to 4,800 calls per 200 redraws.
4. Warnings overlay ran the full `collect()` (large mesh, shared mesh, shape keys, modifiers,
   materials, and with Record everything `everything_notes`: animated paths, every constraint's RNA
   properties, name checks, missing refs) on every viewport redraw in Sculpt/Vertex Paint/Edit Mode,
   only to keep the ERROR lines.
5. `filtering.object_in_collection` walked `obj.users_collection` first (Blender scans every
   collection in the file for that, about 6 us each) before the `all_objects` lookup that decides
   anyway. With global history `runtime._visible_uids` calls it for every object, 3 times per viewport
   redraw: 3.9 ms per redraw at 50 objects with a blacklist.
6. Warnings sub-panel: poll ran every check, draw ran them all again.
7. `wrap()` ran `textwrap.wrap` on the same texts every redraw.
8. Not mine (RUNTIME): `scrub_info` is called 2 times per viewport redraw (gizmo poll, draw handler)
   plus once by the header and twice by the sidebar (draw_main and the IntProperty getter). In
   global mode with 50 objects one call costs about 200 us (`_visible_uids` walks every object), so
   a viewport redraw is about 600 us regardless of what the UI does. `step_list` builds a label for
   every step (O(n)) to show 25 rows; `memory_stats` sums `nbytes()` over every entry (O(n), 2.3 ms
   at 10,000 steps) on every Memory sub-panel redraw.

## What changed (my files)
- `hindsight/bl/scrubber.py`
  - Geometry split into groups by what each part depends on: track (bg, dot ring; layout only), core
    (dot centre; hover on dot), fill (handle x), handle (handle x, hover on bar), tick (morph x). Each
    group's batches are kept (16 entries max per cache, emptied when full). A draw "plan" (batch,
    colour) list in draw order is kept per (layout, handle x, morph x, hover, colours). Keys use the
    handle's x, not (pos, len), so a stroke at the end of the history builds nothing.
  - Arc cos/sin tables kept per (segments, a0, a1); vertices are the same floats (same expressions).
  - `layout()` results kept by (region size, UI scale, insets, digits, the 4 layout prefs).
  - Sibling modules looked up once.
  - S4-1: `_DIGITS` is LRU-bounded (16 regions; the least recently drawn goes first) and emptied by a
    persistent `load_post` handler (registered in `register()`, removed in `unregister()`), together
    with every other cache and the hover state.
- `hindsight/bl/warnings.py`: `_errors()` (the three ERROR checks, shared by `collect()`) and
  `error_texts()`; the overlay uses `error_texts()`. `collect()` output unchanged.
- `hindsight/bl/filtering.py`: `object_in_collection` drops the `users_collection` walk;
  `all_objects` already holds the direct members. Same answers (tested against the old code).
- `hindsight/bl/ui_panel.py`: modules imported once (`_mod`), `guard.failed()` once per draw, the
  top package from `sys.modules`, `wrap()` lines kept (64 max), Warnings sub-panel draw takes the
  list its poll just made (once, only for the same object and region pointers).
- `hindsight/bl/slider_prop.py`: runtime imported once.
- Not changed: header.py, ui_menu.py, prefs.py (no measurable win: header and menu are thin; caching
  the AddonPreferences struct would be unsafe across preference reloads, a freed non-ID struct can
  crash).

## Before and after (A/B, 5 rounds alternating, min over rounds, us per call)
| scenario | viewport redraw | panel redraw | hover test_select |
|---|---|---|---|
| 10 steps, 1 obj, per object | 50.9 -> 36.5 | 55.5 -> 51.6 | 6.7 -> 4.3 |
| 1,000 steps, 1 obj, per object | 55.6 -> 39.5 | 60.0 -> 56.4 | 6.7 -> 4.2 |
| 10,000 steps, 1 obj, per object | 76.8 -> 37.7 | 62.6 -> 54.0 | 7.0 -> 4.3 |
| 10,000 steps, 1 obj, global | 85.3 -> 66.9 | 118.0 -> 81.9 | 6.4 -> 4.4 |
| 10,000 steps, 50 obj, per object | 59.5 -> 37.6 | 63.4 -> 55.0 | 6.4 -> 4.3 |
| 10,000 steps, 50 obj, global | 565 -> 606 (noise, runtime-bound) | 569 -> 626 (noise) | 6.8 -> 4.6 |
| 10,000 steps, 50 obj, per object, filtered | 139.5 -> 56.6 | 162.5 -> 105.2 | 6.8 -> 4.4 |
| 10,000 steps, 50 obj, global, filtered | 3,943 -> 1,247 | 4,240 -> 1,312 | 6.8 -> 4.4 |

Python calls per viewport redraw: 389 -> 314 (per object), 500 -> 425 (global, 1 obj),
9,305 -> 7,655 (global, 50 obj filtered). Batches built per redraw with nothing changed: 0 -> 0;
per stroke at the end of the history: 11 -> 0; per drag move: 11 -> 5.

Drag over 5,000 steps, UI share per mouse move: 217 -> 95 us (min), 282 -> 197 (median), Python
calls 1,582 -> 425, `batch_for_shader` 11 -> 5.

Timer (slider_prop, 0.2 s), CPU per 60 s idle: 1.9 -> 1.7 ms (1 obj); 57 -> 54 to 89 ms (global,
50 obj, noise: it is all `scrub_info`); 424 -> 129 ms (global, 50 obj, filtered). It keeps firing
when nothing is visible: it must, the sidebar and popover read the range it sets, and skipping it
would show a wrong range. Its cost is `scrub_info` (RUNTIME).

Steps/Memory sub-panels and popover at 10,000 steps: unchanged within noise from my side (O(n) in
runtime.step_list / memory_stats; see patches).

Memory: the caches are bounded (16 plans, 16 per batch group, 16 layouts, 16 digit entries, 64
wrapped texts, a handful of arc tables). Not measured with tracemalloc: the bound is by count and each
entry is a few KB of vertex lists at most.

## Tests
- New `tests/test_bl_draw_scrubber.py`: every gpu/blf call of the slider draw (each vertex, colour,
  blend, text, position) equals a verbatim copy of the 66fcdf8 drawing code over 5 window layouts x 46
  states (empty, locked, global, hover dot/bar, morph tick, colours, sizes, UI scale 1/1.5/2, 3 to 5
  digit counters), cold and warm caches, two orders; geometry equal to the old function; batch
  counts (nothing rebuilt on an unchanged redraw, 0 on a stroke at the end, 5 on a drag); caches
  bounded; caches follow colour, height, UI scale, width, digits; S4-1 regression (100 regions
  -> at most 16 kept, an active region never dropped, file load empties it and a region at a reused
  pointer hit-tests like a fresh one). Run against the baseline code: the draw equality passes
  (the copy is faithful), S4-1 and batch tests fail as expected.
- New `tests/test_bl_draw_panels.py`: overlay errors equal collect's ERROR lines (dyntopo, multires,
  global undo, mixed); overlay never runs `everything_notes`; `wrap` output equals `textwrap` for
  widths, scales, icons, units, cold and warm; Warnings sub-panel poll+draw checks once, draw without
  fresh poll or for another object checks again. Fails on baseline.
- New `tests/test_bl_draw_filtering.py`: `is_excluded` and `object_in_collection` equal the old code
  over 3 modes x 22 lists x 11 objects (nested 3 deep, a collection under two parents, objects in
  several or no collections, empty rows); old/new speed at 50 objects (about 1,100 -> 200 us).
- Existing: test_bl_scrubber, test_bl_panels, test_bl_warnings, test_bl_menu pass unchanged.
- Full suite on bpy53: SUITE53. My area on bpy42: SUITE42.
- `python3 tools/check_copy.py`: 0 problems.

## Tried, not kept
- Sharing one `scrub_info` result between the gizmo poll and the draw handler: no cheap token says
  nothing changed between them (the poll also runs during event handling, before a timer or
  operator can change the history in the same loop iteration). Left to RUNTIME's own caching.
- Drawing the handle as one cached batch moved with `gpu.matrix.translate`: vertices would be
  computed on the GPU in float32 instead of in Python then rounded; can't prove pixel equality here.
- Caching `get_prefs()`'s AddonPreferences struct: unsafe across preference reloads.
- Skipping the 0.2 s range timer when no sidebar is open: the popover reads the same range.

## Risks and limits
- No GPU: the gpu/blf calls are proven identical to before, not seen on screen. Batches now live
  longer (up to about 16 x 5 groups kept); they were already kept before (17 sets).
- Warnings sub-panel memo relies on Blender calling poll right before draw in the same layout pass
  (it does in `ed_panel_draw`/`ED_region_panels_layout_ex`); a draw without a matching poll result
  checks again, so the worst case is the old behaviour.
- Overlay behaviour change in an error path only: an exception in a non-error check (e.g. in
  `everything_notes`) used to switch the overlay off through `collect()`; now the overlay doesn't run
  those checks, so it stays on.
- `scrubber.register()` now adds a persistent `load_post` handler; `unregister()` removes it.

## Patches for files I don't own (hindsight/bl/runtime.py, owner RUNTIME)
Measured on a scratch tree = this worktree + both patches, 3 alternating rounds, 10,000 steps / 50
objects (`S/agents/DRAW/summary_rt.txt`): Steps sub-panel 1,774 -> 147 us (global), 14.9 -> 8.4
(per object); popover 2,460 -> 519 us (global); viewport redraw 1,868 -> 1,167 us (global filtered),
39.6 -> 35.0 (per object); timer 56 -> 44 ms/min (global). Files: `S/agents/DRAW/rtpatch/step_list.patch`
and `S/agents/DRAW/rtpatch/imports.patch`.

1. step_list builds only the shown rows (same output):
```diff
STEPLIST
```
2. Modules imported once in the per-object, per-redraw helpers (note: runtime then imports filtering,
rigmark and warnings at load; none imports runtime at module level, checked):
```diff
IMPORTS
```
3. Not a patch, a finding: `memory_stats()` sums `nbytes()` over every entry of every history on
each Memory sub-panel redraw: 2.3 ms at 10,000 steps (50 objects), 0.7 ms at 10,000 (1 object). A
running total kept by the registry (add on commit, adjust on pack/drop) would make it O(objects).
4. `scrub_info` per call: about 10 us per object mode, 200 us global at 50 objects (450 us filtered
before my filtering change). It is called 2x per viewport redraw by the slider (poll + draw), 1x by
the header, 2x by the sidebar, 300x a minute by the range timer. A cached result invalidated by the
registry's change counter plus scene/active object/filter state would remove most of the global cost.

## Proposed text
- DEFECTS.md: "S4-1 fixed: the on-screen slider's per-region digit counts (`scrubber._DIGITS`) are
  bounded (16, least recently drawn dropped first) and emptied on file load, so a reused region
  pointer can't shift the hit test. Regression: tests/test_bl_draw_scrubber.py
  test_digits_bounded_and_forgotten_on_load."
- PERF_LOG.md: "Redraw cost (Python side, gpu/blf stubbed, bpy 5.3, A/B 5 rounds): viewport redraw
  51-77 -> 37-40 us per object, 85 -> 67 us global; 3.9 -> 1.2 ms global with Collection Filtering at
  50 objects; drag mouse move UI share over 5,000 steps 217 -> 95 us, batches built 11 -> 5; a stroke
  at the end of the history builds no batch (was 11). Remaining cost is runtime.scrub_info
  (about 600 us per redraw in global mode at 50 objects)."
- CHANGELOG.md: "Faster redraws: the on-screen slider keeps its drawing between redraws and rebuilds
  only the handle while you drag; the viewport warnings and Collection Filtering do less work on each
  redraw."
