# Handoff

## State at v1.0.0 (~12:20 UTC)
- All tiers (P1, P2, P3) implemented. 15 test files / 123 headless tests pass on bpy 4.2.23 and 5.2.2
  (`python3 tests/run_tests.py`; set `HINDSIGHT_PY` to choose the interpreter).
- Website in docs/ with the zip at docs/download/hindsight.zip (rebuilt by `python3 scripts/build_zip.py`
  and by the pages workflow).
- Needs the repo owner: enable GitHub Pages (Settings > Pages > Source: GitHub Actions).

## What a second round should do
1. Run the manual UI checks listed in DECISIONS.md in a real Blender 4.2 and 5.x session (scrubber drag
   priority over brush strokes, stroke-end detection with real brushes and filters, Sculpt Mode viewport
   refresh after scrubbing, confirmation dialogs, Morph History tool in the toolbar).
2. Capture the three screenshots and replace the placeholders on the website.
3. Measure 1M vertices and the 6,000-step memory target.
4. STRETCH items (Multires, Dyntopo, transforms, shape keys for Morph, multiple named morph targets).

## Map
- Contracts: INTERFACES.md. Parity: PARITY.md. Guesses, known issues, settings: DECISIONS.md.
- Core (no bpy): hindsight/core. Blender layer: hindsight/bl. Tests: tests/. Benchmarks: bench/.

## Beta job (1.0.0-beta.1), in progress
- Done: manifest 1.0.0-beta.1 + "(beta)" tagline; BETA tag + Report a bug in sidebar/prefs
  (bl/beta.py, tests/test_bl_beta.py); issue templates (.github/ISSUE_TEMPLATE); release workflow marks
  `-` tags as pre-releases and uses .github/release-notes/<tag>.md; README beta notice; single Pages
  workflow `.github/workflows/static.yml` (builds zip, publishes docs/).
- Blocked (needs the owner): creating the GitHub Release / pushing tags (HTTP 403). See DECISIONS.md for
  the click-path. Pages is enabled now.
- Website rebuild: docs/index.html + assets/site.* (layout/content), assets/demo.* (live sculpt demo +
  Movie Mode), assets/extras.* (unmerge demo, clips, delta diagram). QA script:
  scratchpad qa/qa.js (serves docs/ under /Hindsight/ on 127.0.0.1:8765).
