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

## Repository settings you need to change
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

## Known issues
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

## Benchmarks (real, measured; see bench/RESULTS.md)
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

## Agents used
- Haiku: R1 and R2 research, README, PARITY audit (4 completed). Two early haiku website/CI agents were
  stopped by the plan change before producing anything.
- Sonnet: website, build/publish/Pages workflows (2).
- Opus: storage engine, restore engine, journal/persistence core, scrubber UI, History Menu,
  prefs/sidebar/filtering, warnings, save/load glue, Movie Mode, Morph History, Join/Unmerge,
  Separate/Duplicate, stress/benchmark/install tests (13).
- The main session wrote the shared types, registry, global timeline, mesh I/O, runtime/recorder,
  operators/keymaps, props, add-on init, test harness and runner, and integrated everything.

## Beta 1.0.0-beta.1 (2026-10-14, second job)
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
