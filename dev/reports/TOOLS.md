> 2026-11-18. An agent's report as delivered, unchanged below this line. Report only: it changed no code (analysis and design).

# TOOLS report: analysis pass on Hindsight (worktree agent-a653b731066213a73, base 66fcdf8)

The harness refused to let me write `S/agents/TOOLS/report.md`, so this full report is returned here. No repository file was changed: `git status` is empty after all runs. Raw outputs are in `/tmp/claude-0/-home-user-Hindsight/4a339202-445d-5357-a8ee-013fb190a1d0/scratchpad/agents/TOOLS/` (`ruff_full.txt`, `semgrep_registry.txt`, `jscpd.txt`, `codeql/results.sarif`, `clippy.txt`, `js_knip.txt`, `js_biome.txt`, and others). The bpy repro is `.../agents/TOOLS/repro_constraints.py`.

## Real findings to act on

| file:line | what | tool | area |
|---|---|---|---|
| `hindsight/bl/adapters.py:484` | `_write_constraints` zips `ob.constraints` with `want`. If `constraints.new` fails for one entry (caught at 480-482), the list is shorter and every later constraint gets the wrong values. Reproduced with bpy 5.3: constraint `C` received `B`'s values. Reached only when the running Blender cannot create a saved constraint type. Suggested fix (not applied): collect `(c, w)` pairs in the creation loop and iterate those. | ruff B905, read, bpy repro | datarec (adapters) |
| `hindsight/core/journal.py:118` | `flush(fsync=True)` swallows `OSError` from `os.fsync`, so a failed fsync is reported as durable. Design decision to make, not a proven bug. | CodeQL py/empty-except, read | persistence |
| `hindsight/core/delta.py:323-324` | Dead branch: `if nr == 1:` comes after the early `return` for `nr == 1` (about lines 306-309), so it is never true and its `else` always runs. Removing it changes nothing. | CodeQL py/redundant-comparison and py/unreachable-statement, read | core |
| `hindsight/core/delta.py:268` | `_IDX_ENC` dict is never used. | CodeQL py/unused-global-variable, grep | core |
| `hindsight/core/history.py:181` | `kind = step.kind` is never used. | ruff F841, CodeQL, read | core |
| `hindsight/bl/joinsep.py:342` | `invoke` calls `_members(context)` and uses neither result (`_a` and `others` unused). Wasted call; I did not check whether `_members` has side effects. | ruff RUF059, CodeQL, read | runtime (join) |
| `hindsight/bl/header.py:10`, `hindsight/bl/meshio.py:8-10`, `hindsight/bl/runtime.py:1449` | Unused imports: `draw_help`; `MASK_KEY`, `FACE_SET_KEY`, `HIDE_VERT_KEY`, `HIDE_EDGE_KEY`, `HIDE_POLY_KEY`; `adapters`. Dead, harmless. | ruff F401, CodeQL py/unused-import | UI, datarec, runtime |
| `hindsight/bl/joinsep.py:370-391` and `hindsight/bl/ops_core.py:142-163` | Identical register/unregister and keymap boilerplate in two modules. Low value; one helper would do. | jscpd, read | runtime |
| `native/src/lib.rs:367, 380, 606` | Index loop `for c in 0..w` used to index `tags`. Micro-perf nit, not measured. | clippy | native |
| `site/package.json:9-10` (website, report only) | `@fontsource-variable/fraunces` and `@fontsource-variable/inter` are not referenced anywhere (fonts are committed as woff2). `tests/site/lighthouse.js` imports `chrome-launcher`, which is not declared. | knip, depcheck | site (not in the list) |

Nothing else from tools 1 to 5 is a real bug or real performance issue in `hindsight/` or `native/`. Most output is noise (counts below).

## 1. ruff

- Command: `ruff check hindsight native tests tools bench --select E,F,W,B,PERF,SIM,C4,UP,RUF,PLE,PLW --output-format concise`
- Version: ruff 0.17.5 (from the reused `S/toolkit/venv`). Exit status 1 (violations found).
- Total: 4058 findings. By directory: tests 1808, hindsight 1297, bench 479, tools 474. `native` is Rust and has no ruff findings.
- Top rules: E501 3267 (line length), E702 142, E402 89, E401 66, SIM105 63, RUF059 59, B023 54, RUF012 31, B905 31, RUF005 29, F401 29, PLW0603 22, PLW0108 22, F841 14, PERF401 12, E741 12.
- hindsight/ by rule: E501 1125, SIM105 60, RUF012 30, PLW0603 18, SIM102 7, PERF401 7, F401 7, RUF005 6, B905 6, UP045 5, SIM118 4, PLW2901 3, RUF046 3, and 12 smaller.

Triage (code read for each):
- B905 zip without strict (hindsight, 6): `adapters.py:132`, `adapters.py:718`, `runtime.py:783`, `history.py:171`, `step.py:410` are safe. Lengths are checked first (132, 410), a generator yields one item per entry (history 171), or both lists come from one source (runtime 783, adapters 718). `adapters.py:484` is real (above).
- RUF012 mutable class attribute (hindsight, 30): all 30 are Blender `bl_options = {...}` sets, which Blender reads and never mutates. Noise.
- PLW2901 loop variable overwritten (`adapters.py:362, 369`, `persist.py:362`): intentional reuse within one iteration. Noise.
- PERF401 (7: `cleanup.py:55`, `meshio.py:55`, `runtime.py:1334`, `selfcheck.py:52, 170`, `globaltl.py:88, 90`): per-item appends on small lists, not hot. Noise.
- PLW0603 global statements (18) and PLW0602 at `scrubber.py:702`: Blender module-state pattern. Noise.
- SIM115 open without context manager (`journal.py:90, 149`): the handle lives for the journal's lifetime and is closed in `close()` or `__del__`. Noise.
- Real dead code (F401, F841, RUF059): in the table above.
- tests/tools/bench (not triaged in depth): 53 B023 closures in loops (e.g. `tests/run_tests.py:108`, called in the same iteration: safe). `test_bl_camrig.py:195` and `test_bl_dupsep.py:153` assign objects that go unused; the tests still assert, so harmless. `tests/oracle.py:151` has a duplicate set item (B033). Noise.

## 2. semgrep

- Command: `semgrep --config p/python --config p/security-audit hindsight --metrics=off`
- Version: 1.186.0. Exit status 2.
- Not run: the registry is blocked. The error is CONNECT to `semgrep.dev` answered `403 Forbidden` by the egress proxy, which `/__agentproxy/status` records as a policy denial. I did not retry. The pip package ships no rules (only a schema file), so there was nothing local to run. No findings.

## 3. jscpd

- Command: `jscpd hindsight --min-lines 8 --min-tokens 70 --reporters console`
- Version: 4.3.0. Exit status 0.
- Result: 1 clone, 21 duplicated lines, 139 tokens (0.18% of lines). 42 files analyzed of 45 `.py` files; I did not check which 3 were skipped.
- Pair: `hindsight/bl/joinsep.py [370:63 - 391:17]` and `hindsight/bl/ops_core.py [142:87 - 163:17]`. Keymap and register/unregister boilerplate. Read: real duplicate, low value.

## 4. CodeQL

- Version: CodeQL CLI 2.27.4. The bundle `codeql-bundle-linux64.tar.gz` downloaded from the github.com release on the first try (HTTP 200, about 696 MB) and was extracted under `S/agents/TOOLS/codeql/bundle`.
- Commands: `codeql database create .../codeql/db --language=python --source-root <worktree>/hindsight` (exit 0, 45 files), then `codeql database analyze ... codeql/python-queries:codeql-suites/python-security-and-quality.qls --format=sarifv2.1.0` (exit 0).
- Result: 148 results.
  - py/empty-except: 103 (97 in `bl/` and `__init__.py`, 6 in `core/`). I read 8: `journal.py:118` (in the table), `journal.py:138` (scan read error returns a partial list; a truncated tail is the designed case), `persist.py:250` (fine), `datarec.py:196` (safe lookup), `journal.py:174, 198, 208` (close and atomic-write cleanup: fine). The rest were not triaged individually.
  - py/cyclic-import: 34. Nearly all are lazy imports inside functions (e.g. `core/types.py:240-254`), deliberate to break cycles. Architecture note, not a bug.
  - py/unused-local-variable: 3 (`joinsep.py:342` twice, `history.py:181`). Dead code, in the table.
  - py/unused-import: 2 entries (`header.py:10`, `meshio.py:8` with 4 names). Dead code, in the table.
  - py/unused-global-variable: 1 (`delta.py:268`). Dead code, in the table.
  - py/redundant-comparison and py/unreachable-statement: 1 each (`delta.py:323-324`). In the table.
  - py/weak-sensitive-data-hashing: 1 (`bl/persistence.py:71`, SHA1 over the session key). False positive: the hash only names the journal file from a path; no secret, no security use.
  - py/not-named-self: `bl/morph.py:322`, `draw_settings(context, layout, tool)`. Not a bug: Blender calls it through `getattr(tool_cls, "draw_settings", None)` and then as a plain function (checked in the bpy 5.3 package: `scripts/modules/bpy/utils/__init__.py:1120` and `scripts/startup/bl_ui/space_toolsystem_common.py:807`).
  - py/overly-complex-delete: `core/journal.py:170` (`__del__`). Style.

## 5. Rust (clippy)

- Command: `CARGO_TARGET_DIR=.../agents/TOOLS/target cargo clippy --locked --manifest-path native/Cargo.toml --all-targets -- -W clippy::all -W clippy::perf`
- Versions: cargo 1.97.0, clippy 0.1.97. Exit status 0.
- Result: 9 warnings in the lib (9 duplicates in the lib-test target), all in `native/src/lib.rs`:
  - 4 x "useless conversion to the same type: `pyo3::PyErr`" at lines 194, 272, 416, 650. The spans point at `#[pyfunction]` signatures, so the conversion comes from PyO3's macro expansion. Noise.
  - 1 x "very complex type" at 416. Style.
  - 1 x "manually reimplementing `div_ceil`" at 551 (`(span as u128 + 7) / 8` becomes `.div_ceil(8)`, same value). Style.
  - 3 x "loop variable `c` used to index `tags`" at 367, 380, 606. The loop runs over `0..w` and `tags` is `split_at_mut(w)`, so indexing is in bounds. Micro-perf nit only, not measured.
- No correctness-class warnings.

## 6. JavaScript (website build scripts only, report only)

Scope: `site/render_assets.js`, `site/assets/site.js`, `site/assets/demo.js`, with `tests/site/*.js` copied in as context. All runs used a copy at `.../agents/TOOLS/jsite/`, so the worktree was not touched. `site/build.py` is Python and outside this tool set.

- **knip** 6.45.1 (exit 1). "Unused files": all 5 JS files, because no entry points were configured, so this is meaningless. Unused devDependencies: the two fontsource packages (real, see table) and `lighthouse` (false: used by `tests/site/lighthouse.js`, which knip did not treat as an entry).
- **depcheck** 1.4.7. Unused devDependencies: the two fontsource packages (real). Missing dependency: `chrome-launcher` used by `tests/site/lighthouse.js` (real, undeclared; possibly transitive from `lighthouse`).
- **madge** 8.0.0 (`--circular`): no circular dependency among the 6 files.
- **unimported** 1.31.0: needed entry points (`.unimportedrc.json` in the copy). Result: 0 unimported files, 0 unused dependencies. Its 3 unresolved imports (`playwright`, `chrome-launcher`, `lighthouse/...`) are a gap in my copy, which lacks the root `devDependencies`, not findings. Exit status not captured.
- **biome** 2.6.8 (`biome lint`, exit 1): 91 errors, 60 warnings, 25 infos on 3 files. By rule: `noInnerDeclarations` 90 (`var` inside `if` blocks; hoisting is intended: style); `useArrowFunction` 53; `useTemplate` 23; `noRedundantUseStrict` 3; `useOptionalChain` 3; `useNodejsImportProtocol` 2; `noAssignInExpressions` 1 (`demo.js:53`); `noUnusedVariables` 1 (`demo.js:313`, an unused binding in `positionsAt`; I did not check which name). Only the last two are worth a look, and only slightly.
- **ts-prune**: not applicable, no TypeScript files in `site/` or `tests/site/`.

## 7. Not run

- SonarQube: needs a Java server and a database; not set up here.
- sourcery: needs an account login.
- gemini-cli, aider, OpenHands, continue, cline, plandex, mentat, gpt-pilot, refact: AI coding assistants that need a model-provider key and write code themselves. Not analysis tools.
- serena: an MCP server for editors.
- everything-claude-code: a collection of agent settings, not a tool.
- rtk: a token-saving proxy for agent output.
- RefactoringGuru: a catalogue of refactorings; used as reading material, not run.

## Limits

- No Blender GUI or GPU. The only Blender run is the headless bpy 5.3 repro of the constraint bug.
- semgrep produced no output (registry denied, no local rules).
- The 3 unresolved JS imports in the unimported run are my install gap, not findings.
- The fsync, dead-code and duplicate items are read-only claims from reading the code; I did not run them.

Branch: none (no commits). Repo state: unchanged at 66fcdf8.