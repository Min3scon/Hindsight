# Slop check (ticket #6)

Written 2026-11-22 on `v2/5-remove-website` (09c0c1d), with `/project-triage`. The verdict was set before the
check (salvage and fix in place); this report gives the evidence and the fix list. Nothing in the code was
changed. Sub-tickets #28 to #39 map onto the sections named in each fix ("Area").

## Summary

| Part | Lines | Verdict |
|---|---:|---|
| Add-on core `hindsight/core` (16 modules) | 4,028 | keep; delete dead and test-only code (#5, #6); restructuring waits for #11 and #24 |
| Blender layer `hindsight/bl` (31 modules) + `__init__` | 16,452 + 100 | fix in place (#3, #4, #5, #8 to #12, #14, #15) |
| Rust engine `native/src` (8 files) | 3,721 | keep: no dead code, no real clippy findings |
| Tests `tests/` (127 test files + helpers) | 31,952 | fix in place (#2, #7, #8) |
| Reference oracles `tests/reference` | 2,559 | keep; small dead branches (#4) |
| Window scenarios `tests_ui/` | 6,926 | fix in place (#8) |
| Tools and scripts | 2,699 | keep; two unused scripts (owner, O6) |
| Benchmarks `bench/` | 22,076 (13,225 of them result files) | owner decision (O4); two broken scripts (#13) |
| Root documents (23 `.md`) | 5,331 | one verbatim duplicate (#1); the rest for #39 |

**Verdict: salvage and fix in place.** By the usual signs this is not a sloppy codebase. The core/Blender split
is real (no `bpy` import anywhere in `hindsight/core`). Copy-paste is 0.2% of the add-on (symilar, 6+ lines). The add-on
is pyflakes-clean. There is no commented-out code and no TODO or FIXME. Every test runs and every test checks something. The waste is
small, mechanical and spread out. It comes from three sources:

1. **History left in place.** These are the Python versions of code the engine replaced, the names kept from the days of a
   Python fallback, a feature built ahead of its interface (the importer), and frozen evidence and bench results.
2. **Boilerplate written by hand each time.** There are 36 test runners, 15 copies of the register and unregister loop,
   5 preference readers with test overrides, and 139 `try`/`except`/`pass` blocks.
3. **Two oversized modules and a few very long functions.** These are `adapters.py` (3,953 lines) and `runtime.py`
   (2,478 lines), plus four functions with cyclomatic complexity over 70.

The fixes below save about **1,200 lines of code** (about 575 in the add-on, 2.8% of it, and about 650 in tests,
scenarios and benchmarks), plus **383 lines of duplicated document**. No function is dropped.

## Fixes, ordered by payoff

Each fix fits one helper session. "Lines" means lines removed, net and estimated from the measurements quoted.
"Sure" says how certain the finding is. Every fix keeps behaviour; the full suites prove it. Where a fix changes
text the user sees, it says so.

### 1. Delete the duplicated research notes (docs). Lines: 383. Risk: none. Effort: 5 min. Sure: certain

- `RESEARCH.md` (388 lines) is `research/R1.md` (127) plus `research/R2.md` (256) word for word, with a 2-line intro
  (diff of non-blank lines: 2 lines differ, the intro).
- Fix: delete `research/R1.md` and `research/R2.md`, and keep `research/compress_bench.py`. Nothing links to the two
  files (`git grep research/R1` finds only `RESEARCH.md`). `/simplify`. Area: #39.

### 2. One test runner without bpy for the files that roll their own. Lines: ~250. Risk: low. Effort: 1 session. Sure: high

- 36 of the 127 test files do not use `tests/harness.py`. Each one has its own loop (`ok = True`, `try`, `print("FAIL"…)`,
  `traceback.print_exc()`, `return ok`, `if __name__…`, about 11 lines). Example: `tests/test_core_codec.py`, the end of
  the file. The reason is that `harness.py` imports `bpy` (line 20), so pure-Python core tests can't use it. Nine
  `test_bl_*` files also print their own `PASS`/`FAIL` (`test_bl_panels.py:463-470`, `test_bl_menu.py`,
  `test_bl_scrubber.py`, `test_bl_draw_*.py`, `test_bl_install.py`, `test_bl_warnings.py`, `test_core_format.py`).
- Fix: move `run`/`finish` (`harness.py:147-171`) into a new `tests/runner.py` that doesn't import `bpy`, have
  `harness` re-export them, and change the 36 files to `R.run(fn)` … `R.finish()`. Keep the exact
  `PASS name`/`FAIL name` lines, because `tests/run_tests.py:49-67` counts them. `/simplify`. Area: #34.

### 3. Replace `try: X / except …: pass` with `contextlib.suppress`. Lines: ~250. Risk: low. Effort: 1 session. Sure: high (as a shrink)

- The add-on has 139 of these blocks with no `else`/`finally` (AST count). Each takes 4 lines where 2 would do.
  By file: `adapters.py` 24, `runtime.py` 17, `datarec.py` 8, `selfcheck.py` 8, `morph.py` 7, `scrubber.py` 7 …. Tests,
  tools and benchmarks have 28 more.
- The broad `except Exception` itself (351 in the add-on) is deliberate. Blender must never break across the 4.2 to 5.3
  API changes, and `guard.py` switches a failing feature off, so it is not counted as slop.
- Fix: a mechanical rewrite, the same exception types, `import contextlib` where needed. Leave the blocks inside
  per-element loops of the hot paths alone (`suppress` costs a context manager per pass; check against `bench/suite.py`).
  This overlaps with #20 (shrink); do it here or fold it into #20. `/simplify`. Areas: #29 to #32.

### 4. Remove what is left of the Python fallback. Lines: ~65. Risk: low. Effort: ½ session. Sure: certain

The engine is required. Importing `hindsight.core.native` raises `EngineMissing`, and nothing reads `HINDSIGHT_NATIVE`
(the module's own docstring says so, `native.py:12-14`). Code still acts as if a Python path existed:
- `hindsight/core/native.py:51-53`: `ERROR = None`, `lib = ENGINE`, `AVAILABLE = True` are kept "for callers".
- `hindsight/bl/selfcheck.py:73-81`: `_engine_line` has an "Engine: Python (…)" branch that can never run. When the
  engine is missing, the `except` branch prints "Engine: Python (EngineMissing…)", which is wrong because there is
  no Python engine. **This text is seen by users** (Self-check report). It should read "Engine: missing (…)".
  `tests/test_bl_audit.py:297` checks the old branch.
- Tests with branches that can't run: `test_core_native.py:76-77` and `test_core_native_steps.py:45` (SKIP when not
  loaded). `test_core_native.py:486-517` (`test_switch`) runs three `HINDSIGHT_NATIVE` modes that change nothing.
- `tests/reference/delta.py:97-98, 129-130, 353-354, 476-477`: `lib = None` followed by `if lib is not None:` blocks
  (about 25 dead lines in the oracle).
- CI sets `HINDSIGHT_NATIVE: require` (`.github/workflows/blender.yml:48`, `native.yml:3,40,44`) and prints
  `native.lib.__file__` (`blender.yml:93`, `native.yml:41`). The benchmarks read `native.lib`/`ERROR`/`AVAILABLE`
  (`bench/bench_native.py:7,50-51`, the docstring says "Python path = native.lib set to None", which is wrong).
  More readers: `bench/scenarios/_common.py:172`, `bench/scenarios/facts.py:27-35`, and three one-offs.
- Fix: keep `ENGINE` and `crc32`; point every reader at `ENGINE`; delete the dead branches, the env var and the
  stale docstrings. `/simplify`. Areas: #28, #32, #34, #38.

### 5. Dead code in the add-on. Lines: ~50. Risk: very low. Effort: ½ session. Sure: certain (`git grep -w` finds only the definition)

| Where | What |
|---|---|
| `bl/adapters.py:3950-3953` | `timed_capture()`: never called |
| `bl/datarec.py:597-599` | `_select_only()`: never called (also named in the `finish_operation` docstring, `:688`) |
| `bl/runtime.py:402-407` | `_sync_edit()`: never called |
| `core/globaltl.py:28-35` | `global_order()`: never called (`global_groups` replaced it) |
| `core/types.py:43,45,53,187-188` | `HIDE_EDGE_KEY`, `SHARP_FACE_KEY`, `DOMAINS`, `Step.is_topo()`: never read |
| `core/persist.py:45` | `REG_VERSION = FORMAT_VERSION  # kept for older imports`: nothing imports it |
| `core/persist.py:694-695` | writes `h.recovered_objects`, which nothing reads |
| `core/store.py:70,195` | `saved_bytes`: counted, never read |

Fix: delete them (and the same `REG_VERSION` line in `tests/reference/persist.py:46` only if #6/O5 says so).
`/simplify`. Areas: #28 to #30. This also leaves less for #24 to port.

### 6. Move test-only Python out of the core. Lines: ~55. Risk: low. Effort: ½ session. Sure: high

The repository's own rule (`tests/reference/__init__.py`) is that Python replaced by the engine lives in
`tests/reference/` as an oracle. These pieces were left behind:
- `core/morph.py:66-95`: `falloff()` and `morph_blend()`, the numpy brush replaced by `morph_dab` (R083). Only
  `tests/reference/morph_dab.py` and `tests/test_bl_morph.py:41-47` call them. Move them into the reference.
- `core/delta.py:168-175`: `packed_size()`, a Python header parser that only tests and `reference.pure()` use.
- `core/framing.py:540-552`: `history_extents()`. Only tests use it, as a wrapper of `history_extents_multi`.
  Point the tests at the `_multi` form.
- Parameters marked unused in their own docstrings: `chunk` of `extents` (`framing.py:140,145`) and of
  `extents_multi` (`:264,267`), and `batch` of `history_extents_multi` (`:555,558`). Drop them, and drop
  `chunk=37` in `tests/test_core_framing.py:386`.
- Fix: as above. `/simplify`. Area: #28. Tiny accessors used as test seams (`History.positions_of`,
  `idmap.in_frame`, `persistence.current_journal`, `Packer.pending`) are fine and stay.

### 7. Unused imports and variables in tests and benchmarks, and a pyflakes check. Lines: ~110. Risk: very low. Effort: ½ session. Sure: certain

- pyflakes on `tests tests_ui tools bench` reports 78 unused imports, 42 unused locals and 2 f-strings without
  placeholders (65 in `tests/`, 24 in `bench/scenarios`, 8 in `bench/lab`, 10 in `tests_ui`). The add-on itself is clean,
  apart from Blender's string enum annotations, which are false positives.
- Fix: remove them, and add a pyflakes step to `tools/precommit_check.py`, skipping the `syntax error in forward
  annotation` and `undefined name` reports that Blender's annotations cause, so they don't grow back. No linter is
  set up in the repository today. `/simplify`. Areas: #34 to #38.

### 8. Copy-pasted scenario and test blocks. Lines: ~130. Risk: low to medium. Effort: 1 session. Sure: high

- `tests_ui/scenarios/s_crash_topo.py` (140 lines) is `s_crash.py` (127) with one block changed (`diff`: 87 shared
  8-line windows; only lines 63-74 and the docstring differ). Make it one scenario with a parameter, or a shared
  helper module.
- Repeated blocks inside a single file: `tests/test_bl_t1_outside.py:254` and `:366` (12 windows),
  `tests/test_bl_recovery.py:308` and `:357` (11 windows), and `tests/test_bl_journal_writer.py:46` and `:115`.
  Smaller ones (3 to 6 windows) are in `test_core_store.py:700` / `test_tools_precheck.py:81` and in the `s_dyntopo*` and
  `s_round_f_*` scenarios.
- Fix: extract a helper per pair. The window scenarios need a real window to verify, so run them where #37
  says. `/simplify`. Areas: #36, #37.

### 9. Register and unregister boilerplate in the Blender layer. Lines: ~100. Risk: low to medium. Effort: 1 session. Sure: high

- 15 modules repeat `for c in classes: bpy.utils.register_class(c)` and the reversed unregister with
  `try`/`except` (15 + 15 loops). 43 `register`/`unregister`/`_add_keymaps` functions add up to 421 lines.
- Keymap teardown is copied three times (`joinsep.py:378-384`, `ops_core.py:190-196`, `dupsep.py:247-253`; symilar:
  17 similar lines). `warnings.unregister()` (`warnings.py:377-384`) repeats `_remove_overlay()` (`:356-363`).
- Fix: a small `bl/reg.py` with `register_classes`, `unregister_classes` (keeping the tolerant unregister) and a
  keymap list helper. Each module keeps its own order and handlers. `/simplify`. Areas: #29, #32.

### 10. One way to read a preference with a test override. Lines: ~45. Risk: low to medium. Effort: 1 session. Sure: high

- There are five copies of the same 10-line reader: a module-level box `FORCE_*` that tests set, then `get_prefs()`, then
  `getattr(p, name, default)`, all inside `try`/`except`. They are `runtime.texture_on` (`runtime.py:25-37`),
  `runtime.dyntopo_on` (`:40-55`), `datarec.scope_everything` (`datarec.py:65-74`), `datarec` kinds (`:264-276`) and
  `multires.enabled` (`multires.py:27-41`). Tests set the boxes in 87 places.
- `scrubber._Defaults` (`scrubber.py:34-43`) repeats six defaults of `prefs.py:140-160` (and `INTERFACES.md:292`
  repeats them a third time).
- Fix: `prefs.value(name, default)` plus one `prefs.FORCE` dict, the readers become one line each, the scrubber
  takes its defaults from one table, and the 87 test lines change mechanically. `/simplify`. Areas: #29, #32.

### 11. `runtime.py`'s function-level imports. Lines: ~40. Risk: low. Effort: ½ session. Sure: high

- `runtime.py` has 44 imports inside functions: `from . import datarec` 12 times, `idmap` 8 times, and so on. 42 of them
  are modules that don't import `runtime` at load time: `idmap` imports nothing from `bl`, `datarec`/`adapters`/
  `prefs`/`selfcheck`/`warnings` import `runtime` only inside functions, and `core` never imports `bl`. Only
  `joinsep` and `camrig` (which import `runtime` at the top) need to stay local. Moving the `core.*` imports to the top
  doesn't change the engine-missing path, because `runtime` already imports `core.history`, and through it the
  engine, at load time (`runtime.py:12`, `history.py:17`).
- Fix: move the 42 to the top of the module. The dependencies then show at a glance. `/simplify`. Area: #29.

### 12. Grease Pencil attributes copy the generic attribute code. Lines: ~25. Risk: low. Effort: ¼ session. Sure: certain

- `bl/adapters.py:2390-2399` (`_drawing_record`'s loop) is `_attrs_of` (`:2499-2509`) line for line, and
  `_write_drawing` (`:2410-2427`) contains `_write_attrs` (`:2512-2528`) line for line (only the variable names
  differ).
- Fix: call the two helpers. `test_bl_t1_grease_pencil.py` and `test_bl_t1_hair_points.py` cover both. `/simplify`.
  Area: #30.

### 13. Broken benchmark scripts. Lines: ~100 if deleted. Risk: low. Effort: ¼ session. Sure: certain

- `bench/lab/offset_vs_zigzag.py` (59) uses `delta._bits` and `delta._planes`, which are gone.
  `bench/scenarios/oneoff/stages.py` (38) uses `persist.parts_to_b64`, which is gone (it is in `tests/reference/persist.py`). Neither can run.
- Fix: delete both, or point them at the reference. Settle O4 first. `/simplify`. Area: #38.

### 14. Split `adapters.py` into a package. Lines: 0. Risk: medium. Effort: 1 to 2 sessions. Sure: high (as a readability gain)

- `bl/adapters.py` is 3,953 lines: 172 top-level definitions in 15 sections, one per data kind (materials, node
  groups, modifiers, mesh lists, bones, pose, ID properties, settings, animation, Grease Pencil, hair and points,
  particles, scene, loose data, collections). The sections share only a few helpers (`normalise`, `_bulk`, `_ATTR_KEY`,
  the registry `ADAPTERS`). Tests reach into 7 private names (12 references), so splitting it costs little.
- Fix: `bl/adapters/` with one module per section and an `__init__` that re-exports today's names. No line savings,
  but a reader opens 300 lines instead of 4,000. `/request-refactor-plan`. Area: #30.
- `runtime.py` (2,478 lines, 134 definitions) is the other oversized module. It is **not** proposed for splitting now:
  tests use 20 of its private names 285 times, and #24 moves its pure logic (jump planning, caches, timeline) into the
  engine. Split it when #24 is planned.

### 15. Name the recording guards in the two long commit functions. Lines: ~0. Risk: medium. Effort: 1 session. Sure: medium

- radon puts these at the top for complexity: `core/persist.replay` (278 lines, CC 97), `bl/runtime.commit` (164 lines, CC 78),
  `bl/datarec._commit` (137 lines, CC 75), `bl/adapters.ObjectAdapter.write` (CC 71). The average is B (5.9). The
  first 55 lines of `runtime.commit` (`runtime.py:948-1005`) are about 15 early returns that decide whether to record at
  all, before any step is built.
- Fix: extract that chain into a named predicate (`_why_not_record(obj, mode) -> str | None`) in `runtime.commit`
  and `datarec._commit`. Do it only once #24 has decided what stays in Python. `persist.replay` is **not** a
  fix ticket, because format 5 (#11) and the Rust rewrite (#24) replace it. `/request-refactor-plan`. Areas: #29, #31.

## For the owner to decide

- **O1. The importer (`hindsight/core/importer.py`, 303 lines; tests 228; `IMPORTER.md` 95).** Nothing in the add-on
  imports it. Its Blender side was "staged in `.tmp/staging/`" (`HANDOFF.md:274-276`) and is not in the repo. It is a
  feature built ahead of its interface, not a shipped function. Either keep it for a future ticket or remove it, since
  git keeps it. `tests/test_bl_t1_jumps.py:258` borrows its `snapshot_digest`, which would move into the tests.
- **O2. Gate evidence frozen at beta 2.** `evidence/` (80 files, 3.7 MB), `test_rounds/` (19 files, 1.3 MB) and
  `TEST_EVIDENCE.md` (857 lines) were last changed 2026-10-15, yet `tools/precommit_check.py:127-130` still checks
  `COVERAGE.md` against them with `make_evidence.py --check`. Refresh them, or retire the evidence machinery
  (`make_evidence.py` 376, `gate_spec.py`, `visual_review.py`, `@covers` in 17 test files). Area: #38/#39.
- **O3. check_copy's coverage rule runs on nothing** since the website went (`tools/check_copy.py:128-160` says so;
  `tests/test_tools_coverage.py`). #5 left this for the owner: aim it at README and CHANGELOG (which fail it as
  written today), or delete it.
- **O4. `bench/`.** 13,225 of its 22,076 lines are result files (`bench/results/`, one JSON has 7,000 lines), and 2,085 lines
  are 34 one-off scripts. They are the record behind `OPTIMISATION_LOG.md`. Keep them, or keep only the suite and let git hold
  the rest.
- **O5. Frozen oracle copies.** `tests/reference/persist.py` (701) and `journal.py` (248) are pre-M5 copies of the
  production modules, about 80% the same text (`diff`: 144 of ~700 lines differ). `test_core_framing_crc.py` uses them
  to prove byte equality. Format 5 (#11) changes these formats anyway, so decide there whether they become golden
  byte files.
- **O6. Unused tools.** `tools/net_check.py` (90 lines; it checks the container's network, not Hindsight) and
  `tools/list_ui_strings.py` (25) are referenced from nowhere.

## Noted, not given a fix ticket (a later ticket replaces the code)

- History folder (#8): the sidecar paths in `bl/persistence.py`, `prefs.save_mode` (default `'SIDECAR'`,
  `prefs.py:113`) and the folder clean-up in `bl/cleanup.py`.
- Blender's in-memory copy (#9): the embed paths of `bl/persistence.py`.
- Format 5 (#11): `core/persist.py` (incl. `replay`), `core/journal.py`, `core/step.py`, and in Rust
  `native/src/step.rs` `Head` vs `Layout` (`:307-322` vs `:568-581`, two near-identical header structs).
- Rust rewrite (#24): the rest of `hindsight/core` (thin wrappers such as `core/codec.py`, `core/delta.py`), and the
  restructuring of `runtime.py` and `persist.replay`. Only deletions (#5, #6) are worth doing in the core before #24.

## What is fine (checked, not assumed)

- **Layering.** `hindsight/core` imports no `bpy` and nothing from `bl` (`git grep`). `ui_panel._call("name")` calls the
  runtime by name on purpose, so the panel still draws when the runtime failed to load (`ui_panel.py:54-62`).
- **Duplication.** symilar (6 or more lines, docstrings and imports ignored) finds 42 duplicate lines in 20,580 (0.20%).
  A search with local names normalised finds nothing beyond fixes #8, #9 and #12.
- **Dead code.** vulture at 60% confidence over the add-on, with tests and tools as callers, lists 44 items. All but
  those in fixes #5 and #6 are Blender API hooks (`draw`, `poll`, `draw_select`, `description`, `draw_settings`),
  layout attributes, or names read with `getattr` (the `scrub_*` preferences).
- **Comments.** There is no commented-out code and no TODO, FIXME or HACK. 36 code names quoted in comments and
  docstrings were checked, and all of them exist. `D###` and `R###` tags resolve to `DEFECTS.md` and `OPTIMISATION_LOG.md`.
  The stale ones are listed in fixes #4 and #6 (`native.py`, `selfcheck._engine_line`, `bench_native.py`, the
  "unused, kept for callers" parameters).
- **Tests.** No test function goes unrun. Three discovery styles were checked: explicit lists, `startswith("test_")`
  and the `_tests` decorator in `test_bl_panels.py`. No test lacks a check: an AST walk of each test's call tree
  flagged 9, and all 9 delegate to asserting helpers (`t0_gate_lib.gate`, `T.check_every_step`).
- **Rust.** `cargo clippy --all-targets` finds no dead code. 21 "useless conversion to PyErr" warnings come from
  pyo3's `#[pyfunction]` expansion. The rest are small style suggestions (3 `is_multiple_of`, 3 indexing loops, 2
  complex types, one 8-argument function).
- **Small modules.** `crc`, `ctx`, `beta`, `guard`, `log`, `rigmark`, `slider_prop`, `header`, `registry`, `topopack`:
  each has one job and several users. `guard.py` (60 uses) and `log.py` (99 uses) pay their way.

## Verdict per module

| Module | Verdict | Fixes |
|---|---|---|
| core: `types` `delta` `framing` `morph` `globaltl` `store` `persist` `native` | fix in place | #4 #5 #6 |
| core: `crc` `codec` `step` `history` `registry` `journal` `topopack` | keep (restructuring: #11, #24) | none |
| core: `importer` | owner | O1 |
| bl: `adapters` | fix in place | #3 #5 #12 #14 |
| bl: `runtime` | fix in place | #3 #5 #10 #11 #15 |
| bl: `datarec` | fix in place | #3 #5 #10 #15 |
| bl: `selfcheck` | fix in place | #3 #4 |
| bl: `prefs` `multires` `scrubber` | fix in place | #3 #10 |
| bl: `joinsep` `ops_core` `dupsep` `warnings` `filtering` `ui_menu` `ui_panel` and the other modules that register classes | fix in place | #3 #9 |
| bl: `persistence` `cleanup` | keep (#8, #9 rewrite them) | #3 |
| bl: `camrig` `meshio` `morph` `movie` `idmap` `guard` `log` `props` `header` `slider_prop` `rigmark` `beta` `ctx` | keep | #3 where it applies |
| native: `lib.rs` `codec.rs` `crc.rs` `framing.rs` `hash.rs` `morph.rs` `same.rs` | keep | none |
| native: `step.rs` | keep (#11) | none |
| tests (core and bl), harness | fix in place | #2 #4 #7 #8 |
| tests/reference | keep | #4, O5 |
| tests_ui | fix in place | #7 #8 |
| tools, scripts, CI | keep | #4 #7, O2 O3 O6 |
| bench | owner | #4 #13, O4 |
| root documents | one fix, the rest for #39 | #1, O2 |

## How it was measured

Everything was done in a scratch venv, with nothing installed in the repo.
- **Dead code.** `vulture 2.16` (min confidence 60) was run twice: on `hindsight` alone, and with `tests tests_ui tools
  bench scripts` as callers. The difference between the two runs gives the add-on code that only tests use. Each
  candidate was checked with `git grep -w`.
- **Duplication.** `symilar -d 6 -i --ignore-docstrings --ignore-imports` (pylint 4) found duplicates across files. A
  sliding-window script (6 to 8 normalised lines, with local names folded) found them inside files and across tests.
- **Code quality.** `pyflakes` found unused names and imports. `radon cc` measured complexity. `cargo clippy
  --all-targets -W clippy::all -W dead_code` checked the Rust.
- **AST scripts.** Small scripts counted the try/except/pass blocks, the function-level imports, the test runner styles,
  the test functions that never run or never check, and the imports of names that no longer exist (bench, tests, tools).
