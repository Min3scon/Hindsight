# Rules for every agent on the Hindsight job

This is `agents/COMMON.md` of the 2026-11-18 session, moved into the repository so it survives the
session. Read it in full before doing anything. The orchestrator's brief for a job names your area and
the files you own. The status of the whole project is in `DEVELOPMENT.md`.

Note on paths: the original text used `S`, the session's scratchpad folder
(`/tmp/claude-0/.../scratchpad`). That folder exists only during one session and is deleted with it.
Where a file now lives in the repository this text says so. Where the text still says "scratchpad" it
means a temporary folder of your own session; nothing there is a record.

Hindsight is a free GPL-3.0 Blender extension (4.2 to 5.3): a scrubbable per-object sculpt history.
Python add-on in `hindsight/` (core/ has no bpy; bl/ is the Blender side), Rust engine in `native/`
(PyO3 module `hindsight_native`, Python fallback today), tests in `tests/`, benchmarks in `bench/`.
Read `INTERFACES.md`, `HANDOFF.md` ("Owner rules", "Test guarantee", "Beta 3 job") and the parts of
`DECISIONS.md` and `PERF_LOG.md` about your area before you change anything. Earlier agents' reports
are in `dev/reports/`; the measured optimisation rounds are in `OPTIMISATION_LOG.md`.

## Hard rules
1. You work only inside your own git worktree (your current directory). Never touch the main checkout,
   other worktrees, or the baseline checkout. The baseline is a read-only checkout of commit 66fcdf8,
   the state before the optimisation work of 2026-11-18, used for A/B runs. It was a scratchpad folder
   (`S/baseline`); to make it again: `git worktree add <folder> 66fcdf8`.
2. Never spawn agents. Never push. Never run git commands that rewrite or delete history.
3. Edit only the files your brief says you own. If the right fix needs another file, do NOT edit it:
   put the exact patch (unified diff) in your report under "Patches for files I don't own".
4. Do not edit shared docs (DEFECTS.md, DECISIONS.md, PERF_LOG.md, OPTIMISATION_LOG.md, DEVELOPMENT.md,
   CHANGELOG.md, HANDOFF.md, README.md, COVERAGE.md, METADATA.md, anything in site/ or docs/). Put
   proposed text in your report.
5. Never `pip install` into shared virtual environments. Private tools go in a venv or `--target`
   folder of your own.
6. Tests are never weakened, skipped or deleted. Every bug fixed gets a regression test that fails
   before the fix. If an existing assertion must change because behaviour changed on purpose, say
   exactly which and why in the report.
7. Never fake a result. Report only numbers you measured, with the command. If a change shows no clear
   gain on its own target (well beyond run-to-run noise), revert it and list it under "Tried, not kept".
8. No speed-up by doing less: no feature turned off, no default changed, no quality lowered, no data
   dropped. Recorded history bytes, jump results and mesh results stay bit for bit what they were
   unless your brief says otherwise. Prove it with tests that compare old and new paths.
9. Two attempts per failure, then write it down as a known issue in the report and move on.
10. UI text follows STYLE.md (no dashes as punctuation, short sentences, banned words list). Run
    `python3 tools/check_copy.py` if you touch any user-visible string.
11. Supported Blender: 4.2 (Python 3.11) through 5.3 (Python 3.13). No syntax or API newer than
    3.11 / 4.2 without a guard.

## Running things
- Blender as a Python module: one virtual environment per version, `bpy42` (4.2.23), `bpy50` (5.0.1),
  `bpy51` (5.1.2), `bpy52` (5.2.2), `bpy53` (5.3.0), each with `bin/python`. They lived in
  `/home/user/venvs/` in the 2026-11-18 container, built by hand (`pip install bpy==<version> numpy
  zstandard hypothesis`). `tools/dev_env.sh` is planned to build them; when it exists, use it.
- Tests: `HINDSIGHT_PY=<venv>/bin/python python3 tests/run_tests.py [substring]` (a substring picks
  matching test files). One file directly: `<venv>/bin/python tests/test_x.py`.
  `HINDSIGHT_NATIVE=require` forces the Rust engine, `HINDSIGHT_NATIVE=0` forces Python. Test both.
- Failures that are known and not yours: `test_bl_metadata::test_opens_without_hindsight` (needs a
  Blender binary) and `test_bl_t1_migrate` (needs `.tmp/old/b1.zip`), in the sandbox. On bpy42
  `test_bl_t1_gate::test_gate_parenting` and most of `test_bl_t1_reload`. See DEVELOPMENT.md section e.
- Sculpt brush operators crash headless. Tests simulate strokes: see `tests/harness.py`
  (`H.fresh()`, `H.make_mesh(n)`, `H.enter(ob, "SCULPT")`, `H.fake_stroke(ob, i)`,
  `runtime.commit(ob)`).
- There is no Blender binary and no GPU in the cloud container. Say so wherever it limits what you
  verified.
- The container had 4 cores and was shared with other agents. Run focused tests while you work, at
  most one Blender process at a time, and the whole suite once at the end on bpy53 (plus the files of
  your area on bpy42).

## Measuring (the machine is noisy)
- Compare old against new in alternating runs (baseline against your worktree, A B A B, at least 5
  rounds), or both paths in one process. Report min and median. `bench/mem_speed.py ROOT N` shows the
  pattern.
- Measure memory too (tracemalloc and VmHWM) when you touch a path that allocates.
- Think of weak computers: 4 to 8 GB RAM, 2 to 4 slow cores, no spare CPU while sculpting.

## Handing back
- Commit in your worktree, small commits, subject ends with ` [skip ci]`. End each message with the
  attribution lines your own session instructions give you.
- Your FINAL MESSAGE is the report, starting with the line `# <NAME> report`: what you found (with
  evidence), what you changed and why (files), before and after numbers with the exact method, tests
  run with results, tried and not kept, risks and limits, patches for files you don't own, proposed
  text for DEFECTS.md / PERF_LOG.md / CHANGELOG.md. List every separately measured optimisation as its
  own row (target, before, after, kept or reverted): they go into OPTIMISATION_LOG.md one by one. The
  orchestrator saves the report under `dev/reports/<NAME>.md`.
- Commit early and often: a session limit can stop you at any moment, and only committed work survives.
- Be economical: read what you need, not the whole repository; no long status narration.

## State of the main line when this file was written (2026-11-18 evening)
- The Rust engine is `hindsight_native` 0.2.0 (API 2: diff, apply, pack, unpack, apply_steps). The
  owner has decided Rust is REQUIRED from now on: the Python implementations become test references
  under `tests/reference/` as each part moves to Rust. This has not started (DEVELOPMENT.md section c).
- The approved plan (migration steps R1 to R6, phases 0 to 4, owner decisions) is summarised in
  DEVELOPMENT.md. The plan file itself was outside the repository (`~/.claude/plans/`) and is not kept.
- Audit reports with designs: `dev/reports/LIMITS-A.md` (known limits), `dev/reports/LIMITS-B.md` (what
  isn't recorded, adapter seam, packages), `dev/reports/TOOLS.md` (static analysis),
  `dev/reports/review.md` (hostile review), `dev/reports/ponytail_audit.md`, and the area reports
  `NATIVE.md`, `SAVELOAD.md`, `DRAW.md`, `DATAREC.md`.
- Known failures that are not yours: on bpy42 `test_bl_t1_gate::test_gate_parenting` and most of
  `test_bl_t1_reload`; everywhere `test_bl_metadata::test_opens_without_hindsight` and
  `test_bl_t1_migrate`.

## How the orchestrator runs agents (from the plan)
- At most 4 agents at once, at most 3 of them on the strongest tier. Bursts of 10 hit the 5-hour usage
  limit twice.
- Strongest tier for Rust, data integrity, recovery, recording semantics, concurrency and reviews;
  middle tier for CI workflows, routine tests, docs, benchmark harness; smallest tier for running
  suites and summarising. The orchestrator's own model is not used for agents.
- Each agent owns disjoint files; agents never spawn agents; work is merged only after the
  orchestrator re-runs its tests; work-in-progress commits carry `[skip ci]`.
