# Decisions log

## Timing
- First start: 2026-10-14 11:49:28 UTC. Schedule reset by the follow-up instructions at **11:56:12 UTC**
  (deadline 12:56 UTC).

## Environment / research
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

## Behaviour guesses (please verify against the original)
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

## Scope cuts (per the follow-up instructions)
- STRETCH features dropped. No memory-mapped cold tier, no checkpoints, no dirty-chunk tracking
  (full numpy diff per stroke).

## Manual UI checks (cannot be tested headless)
- Scrubber drawing, drag in Sculpt Mode (clicks must beat brush strokes), dot opens History Menu,
  step counter, locked look in Movie Mode.
- Real brush strokes / filters / mask / face set / hide operators produce exactly one step each;
  F9 tweak of a filter updates the same step.
- Ctrl+Z / Cmd+Z in Sculpt Mode step the Hindsight history.
- Truncation confirmation dialog appears and works.
- Morph History tool appears at the end of the Sculpt toolbar and paints.
- Dyntopo/Multires viewport warning text.
- Viewport refresh after scrubbing in Sculpt Mode.

## Screenshots to capture for the website
1. Scrubber at the bottom of the viewport in Sculpt Mode (with the step counter visible).
2. History Menu open from the dot.
3. Undo History sidebar tab (Movie Mode, Scrubber, Memory, Collection Filtering open).
