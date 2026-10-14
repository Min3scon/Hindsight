# Test rounds

Internal log. Every round runs in the real Blender interface (with a window) in a throwaway profile,
on every Blender found on the test PC: 5.0.1, 5.1.1 (Steam) and 5.1.2, Windows 10. No Blender 4.2-4.5
is installed, so those versions are not covered here.

| Round | What it covers | Scenarios |
|-------|----------------|-----------|
| A | Registration and lifecycle | install_gui, audit_register, lifecycle |
| B | The UI in every mode on small, large and multi-object models | ui_tour, round_b, regress_slider_visible |
| C | Real recording, bit-exact jumps, long sessions | round_c (10k, 10k with 1,000 steps, 100k, 500k; 100k with 10,000 steps in the final pass), regress_stroke_split, regress_file_load, regress_native_undo |
| D | Awkward models and abuse | round_d, fail_inject |
| E | The owner's own files (copies only) | round_e |
| F | Old versus new builds | round_f.py (1.0.0 and 1.0.0-beta.1 built from git) |
| G | Headless suite inside every Blender | tests/run_tests.py --blender all |

Run a full pass: `python tests_ui/rounds.py --pass N` (add `--big` for the 10,000-step session).
Screenshots of generated models from each pass are in `test_rounds/passN/`. Nothing from Round E
(the owner's files) is ever copied there.

## Found while building the rounds (before pass 1)

Each of these is now a permanent regression test.

| Found in | Problem | Fix | Regression test |
|----------|---------|-----|-----------------|
| Step 2 | Recorder handlers were dropped on every file load, so nothing was recorded after File > Open (the beta 1 "no UI") | `@persistent` handlers that re-install themselves | regress_file_load, test_bl_regressions |
| Step 2 | Slider drawn under the brush asset shelf in Sculpt Mode on Blender 4.3+ | Slider sits above overlapping regions | regress_slider_visible (pixel check), test_bl_regressions |
| Step 2 | Nothing visible before the first stroke | Empty track and hint in Sculpt and Vertex Paint Mode | test_bl_regressions |
| C | Filling a mask and then clearing it was taken for a native undo; the clear was never recorded and later jumps showed the wrong mask | Only a real Blender undo/redo may move the cursor without recording | test_bl_regressions |
| C | Quick strokes (under 0.15 s apart) merged into one step | Commit as soon as the stroke operator ends | regress_stroke_split |
| C | Blender's own undo after a Hindsight jump replayed stale stroke data on top of the mesh and silently dropped 2 later steps | No native fallback after a write; a native undo that lands outside the history is reversed with a message | regress_native_undo |
| D | Multires and Dyntopo objects still said "make a stroke"; every object away from the origin got a transforms note | Paused state shown; note removed | round_d |
| D | A script switching modes mid-stroke crashes Blender 5.1 (`SculptPaintStroke::done`), reproduced with Hindsight not installed | Not Hindsight's bug. Hindsight never jumps (and so never switches mode) while a stroke runs | round_d (keys pressed mid-stroke) |
| E | A slow modifier stack (Geometry Nodes) split one stroke into several steps | Never commit while a stroke operator runs | regress_stroke_split (slow-update case) |
| E | A file saved in Edit Mode got an "Edit Mode" step (and a history) on leaving Edit Mode | Edit Mode alone never starts a history | test_bl_regressions |
| Site | The demo's tool buttons sat inside the sculpt stage, which captured the pointer: Morph and Reset didn't work with a mouse | Stage ignores events from its buttons | tests/site/site_qa.js |

Not bugs, recorded so nobody chases them again:
- Synthetic `bpy.ops.sculpt.brush_stroke` calls need real surface points; `Object.ray_cast` uses a stale
  evaluated mesh in Sculpt Mode, so the rounds use simulated mouse strokes instead.
- Blender builds edges in parallel, so edge and corner order can differ between processes. Cross-build
  comparisons use a scripted session over positions, masks, face sets and colours.
- After a jump in Sculpt Mode the viewport shows the restored shape and strokes land normally (checked
  with screenshots on a 100k mesh).

## Pass 1

Results are filled in below when the pass finishes.

Pass 1 (2026-10-14, 48 minutes): 67 of 72 runs passed on 5.0.1, 5.1.1 and 5.1.2.

- Rounds A, B, C, E and F: every run passed. Round C covered 8 mouse strokes plus 40 operator and tool
  steps with every jump bit-exact on 10k, 100k and 500k meshes, and a 1,000-step session on 10k. Round E
  ran all 8 copies of the owner's files (7 opened in 5.0.1, which is older than the file that didn't).
  Round F: the new build opened files from 1.0.0 and beta 1 in all three save modes (one left mid-rewind)
  bit-exact at every position, beta 1 opened the new files, and the scripted session matched at 11 of 11
  positions on all three builds.
- Round D failed on 5.0.1 and 5.1.1 (43/44): the test still expected the removed "object transforms" note.
  Test fixed during the pass; 5.1.2 ran with the fix and passed 43/43.
- Round G failed on all three (17/19 files): two tests pinned to wording changed in the UI copy pass
  ("Shape Keys", "Global history"). Tests updated to the new strings; 19/19 pass on all three.

No product defects found in pass 1. Under the v2 scope the clean-pass count starts after the last tier.

## Compression check (file format 3), not a counted pass

2026-10-14, 41 minutes: Rounds C, D and F on 5.0.1, 5.1.1 and 5.1.2 with the format 3 build: 51 of
51 runs passed. Round C: every jump bit-exact on 10k, 100k and 500k meshes, the 1,000-step session on
10k, and the stroke-split, file-load and native-undo regressions. Round D: awkward models (43 checks)
and fault injection (13). Round F: the new build opens beta 1 and 1.0.0 files bit-exact at every
position; beta 1 opens the new files without crashing, shows their length and cursor, can't play the
format 3 steps back (it reports "step blob: bad magic"), and a file opened and saved again in beta 1
still plays back bit-exact in the new build. Headless suite: all files on all three Blenders. Under
the v2 scope, counted passes start after the last tier.
