# Handoff

Status is updated at each checkpoint.

## Checkpoint 1 (P1 done, ~12:12 UTC)
- Core engine (storage, restore, journal/persist), recorder/runtime, scrubber, History Menu, prefs,
  sidebar, filtering, warnings: implemented, 85 headless tests passing (`python3 tests/run_tests.py`).
- In flight: bl/persistence.py (save/load glue), Movie Mode, Morph History, Join/Unmerge,
  Separate/Duplicate, stress test + benchmarks + install test, website (docs/), README.

## How to resume
- Tests: `python3 tests/run_tests.py [pattern]` (uses /home/user/venvs/bpy42 if present, else $HINDSIGHT_PY).
- Build: `python3 scripts/build_zip.py` -> dist/hindsight.zip and docs/download/hindsight.zip.
- Contracts: INTERFACES.md. Parity status: PARITY.md. Guesses and known issues: DECISIONS.md.
