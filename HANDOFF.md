# Handoff

Read this first. Then DECISIONS.md (top section), TEST_ROUNDS.md, PARITY.md.

## Current job: beta 2 ("broken and sloppy" to polished), started 2026-10-14
Brief: steps 0-6 (PC check, real Blender, root cause, new UI, hardening and test rounds, new website,
ship). Work happens on the owner's Windows PC. Only branch: `claude/lucid-brahmagupta-9gsdsn` (default,
Pages source).

### Status
- [x] Step 0: tools checked. git, gh (logged in as Min3scon), Python 3.13 and 3.11, Node 22, ffmpeg 7.
  Installed: Playwright 1.63.0 (repo devDependency, `npm install`) with Chromium, Firefox, WebKit.
- [x] Step 1: real Blender 5.0.1, 5.1.1 (Steam), 5.1.2 driven with a window through `tests_ui/hs_ui.py`.
- [x] Step 2: root cause found and written to DECISIONS.md (handlers not persistent, slider hidden
  until first step and under the asset shelf, sidebar closed by default).
- [ ] Step 3: new in-Blender UI
- [ ] Step 4: hardening and test rounds A-G (log in TEST_ROUNDS.md)
- [ ] Step 5: website rebuilt from scratch
- [ ] Step 6: ship 1.0.0-beta.2 and verify live

### How to run things
- List Blenders: `python tests_ui/hs_ui.py list`
- Build the zip with real Blender: `python tests_ui/hs_ui.py build` (writes .tmp/build/hindsight.zip)
- Real-UI scenario: `python tests_ui/hs_ui.py run <name> --blender all [--fresh]`
  (scenarios live in tests_ui/scenarios/s_<name>.py; output in .tmp/ui/<name>/<version>/)
- Headless suite inside real Blender: `python tests/run_tests.py --blender all`
- Pre-commit hook (installed in .git/hooks) runs `tools/precommit_check.py`: blocks big files, secrets and
  personal paths. Never write drive-letter paths into public files.

### Safety rules for this job
- Never touch the owner's Blender prefs or add-ons: always go through the sandbox env in hs_ui.py.
- Owner's .blend files: only copies in `my_models_copies/` (git-ignored), deleted at the end.
- Nothing from `.tmp/`, `test_models/`, `my_models_copies/` goes into git.

## Map
- Contracts: INTERFACES.md. Parity: PARITY.md. Decisions and known issues: DECISIONS.md.
- Core (no bpy): hindsight/core. Blender layer: hindsight/bl. Headless tests: tests/. Real-UI tests:
  tests_ui/. Benchmarks: bench/. Website: docs/.
