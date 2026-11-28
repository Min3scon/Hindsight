# Rust moves (#24): the plan

Owner, 2026-11-22: everything in Hindsight that does not need Blender's Python API is rewritten in Rust, after the
build tickets and before add-on testing (#15) and all optimisation. This plan splits that into moves, one sub-issue
and one pull request each. It builds on `dev/RUST_DESIGN.md` (modules, ownership, GIL rules, errors, memory
accounting) and replaces two of its conclusions: "What should not move" (the values in `Step.extra` and
`DataChange`) and "M3 revisited" (History kept in Python because a native one did not pay). The owner has since
named the history and its store, so they move, measured.

## Where it starts (`v2/11-format5`, engine 0.14.0, API 13)

Already in the engine: the delta work (diff, apply, pack, unpack, runs of steps), the codecs, the step container
(formats 1 to 4, recompress), crc32, the mesh fingerprint hasher, the data recorder's strict `same_values` (R070),
one Morph History dab (R083), Movie Mode `extents_into` (R085), and format 5's index part, step parts and
`part_salvage`. `codec.py` and `crc.py` are facades; `delta.py` and `step.py` are too, apart from the rows below. The
engine is required: no Python path ships, `native.py` raises `EngineMissing`, six abi3 wheels are in the manifest.

Still Python, and needing no Blender API:

| Where | Lines | What |
|---|---|---|
| `core/persist.py` | 1,013 | registry document, formats 1 to 4 reading and the format 4 writer, history folder reading, format 5 save and open, journal replay |
| `core/framing.py` | 613 | Movie Mode framing, besides `extents_into` |
| `core/store.py` | 403 | packer thread, tiers, caps |
| `core/history.py` | 374 | `MeshState`, `History` (cursor, goto) |
| `core/types.py` | 330 | `Step`, `ChannelDelta`, `Snapshot`, `DataChange`, `Slice`, `Entry`, keys (`Settings` stays) |
| `core/journal.py` | 273 | crash journal framing, atomic writes |
| `core/globaltl.py` | 222 | global timeline, jump planning |
| `core/registry.py`, `morph.py`, `topopack.py` | 193 | registry, morph targets, topology packing |
| `core/step.py` | part | the JSON of `extra` and the records (`_dumps`, `_enc_value`, `_dec_value`, `records_to_bytes`, `records_from_bytes`, `_extra`, the header dict of `read_header`), building the `Step` in `step_from_bytes`, `same_step` |
| `core/delta.py` | part | `as_xor`, `same_change` (undo matching) |
| `bl/datarec.py` | part | comparing recorded values (`_same_values`, `_diff`, `_data_diff`) |
| `bl/runtime.py` | part | undo matching, the kept global groups |
| `bl/scrubber.py` | part | the slider's layout, shapes and hit test |
| `bl/persistence.py` | part | the journal writer thread |
| `bl/multires.py`, `bl/morph.py`, `bl/camrig.py` | part | block hashing, morph targets, the matrix math of `_bone_scale` |

`DECISIONS.md` (the Ponytail audit) parks about 60 lines of dead code in `delta.py`, `step.py` and `types.py` until
the Rust work on those files: the move that turns each file into a facade deletes its dead code. The importer is
gone (#72), so nothing of it moves.

## Why the building waits for the owner's merge

The core files are nearly the same on every open branch, but #10 and #23 (#105, #117) and #107 (#108) change
`persist.py` and `registry.py` (#23 also `framing.py`), and the Blender layer differs a lot (#13, #23 and #110 add 584, 1,162 and 738
lines under `hindsight/`). A move built before the merge would port Python that the merge then changes, and its
oracle would be the wrong code: written twice, which the owner's rule forbids. An integration branch that merges
the whole stack would be the owner's merge in all but name. So the first move (#129) is blocked by the owner
merging the stack, in the order of the pull requests (on #24).

## Rules for every move

1. **The oracle.** The Python that moves goes to `tests/reference/` (by `git mv`, unchanged but for its imports);
   the module under `hindsight/` keeps its public names and signatures and holds no algorithm. A reference imports
   the reference copy of a moved module, never its facade, so nothing is checked against itself: the move that
   turns a module into a facade switches every reference importing it to the copy, in the same PR. 24.3 (#148) switched
   the references of `core/types.py`, `step.py`, `topopack.py`, `codec.py` and `crc.py` (`tests/reference/step.py`,
   `delta.py`, `types.py`, `framing_extents.py`, `topopack.py`, and `v1_persist.py`'s `Entry`, `step_from_bytes` and
   `SETTINGS`); still to switch: `v1_persist.py`'s `history.History` and `registry.Registry` (`:224`, `:225`,
   `:455`; 24.4 and 24.6, since `tests/test_core_framing_crc.py:424` takes it as the oracle of
   `persist.registry_from_bytes`) and `framing_extents.py:10`'s `history.MeshState` (24.4). `tests/test_core_reference.py`
   fails if a reference imports a facade (its `FACADES`, which each move adds to) and keeps failing if anything under `hindsight/` imports a reference module.
2. **Equivalence, test first.** Before the engine code: property tests (hypothesis), the corpus of real files from
   formats 1 to 5 (#129) and hostile input (cut short, flipped bytes, huge lengths, wrong types) compare engine and
   reference bit for bit: results, bytes written, exception class and message. Hostile input never crashes the
   engine or allocates past the bounds in `dev/RUST_DESIGN.md` ("Memory accounting").
3. **Measured, as `OPTIMISATION_LOG.md` says.** The scenarios a move touches (named in its sub-issue) are run before
   and after with `bench/ab.py` (alternating processes, at least 3 rounds, usually 5), conditions stated: sizes,
   Blender version, machine. Target: none slower or bigger beyond the A/B's noise, held memory not higher. Each ratio
   is quoted with the spread `ab.py` printed next to it. A move is not a round (it is not aimed at speed); its
   numbers go in its PR and the table below. A move slower beyond noise that profiling within the move can't fix
   goes to the owner with its numbers on its sub-issue, never merged slower silently. Losses within noise can add
   up (`commit` is a target of five moves, `jumps` of three), so 24.13 runs one `bench/ab.py` of 24.1's commit
   against the last one, with the same target and the same rule (owner point 4). No number is ever invented.
4. **One engine version per move.** API and the crate's minor version up by one, the line in `native.py` that
   says what the API added, six wheels rebuilt.
5. **As every ticket.** GIL and error rules of `dev/RUST_DESIGN.md`; full suites on Blender 4.2, 5.0, 5.1, 5.2 and
   5.3 before pushing; commit subjects end with `[skip ci]`; a draft PR into `claude/lucid-brahmagupta-9gsdsn`.
   A sub-issue that turns out bigger than one helper's context is split again before work starts.

## The moves

| Move | Sub-issue | What moves | Blocked by | Measured by | Before (24.1: min, A/A spread) |
|---|---|---|---|---|---|
| 24.1 | #129 | nothing: baseline numbers and the commit they were measured at, the corpus, the equivalence helper, four references renamed | the stack merged | full `bench/suite.py`, `held_once_mem_f5.py` | this: `bench/results/baseline_8dbb008.md` |
| 24.2 | #130, split: 24.2a #152, 24.2b #153 | `core/journal.py` with a test-only fault hook (a), the journal writer thread (b, blocked by a) | #129, #131 | `commit`, `oneoff/open_journal_ab.py` | `commit_100k` 0.6423 ms (38%) |
| 24.3 | #131, split: 24.3a #146, 24.3b #147, 24.3c #148 | `core/types.py` (not `Settings`), `core/topopack.py`, the step data's JSON and `same_step` in `core/step.py`; in three parts, each blocked by the one before: the JSON (a), the engine classes with `step_from_bytes` and `same_step` (b), `topopack.py`, the hand-off, the dead code and the references switch (c) | #129 | `commit`, `jumps`, `editmode`, held memory | `commit_100k` 0.6423 ms (38%); `jump_3000_back_100k` 56.98 ms (18%); `edit_step_100k` 40.98 ms (11%); held after open 270.8 MB, save peak 348.3 MB (working set) |
| 24.4 | #132 | `core/history.py` | #131 | `jumps`, `commit` | `jump_3000_back_100k` 56.98 ms (18%); `commit_100k` 0.6423 ms (38%) |
| 24.5 | #133 | `core/store.py` | #132 | `commit`, `idle`, `packer_bench.py` | `commit_100k` 0.6423 ms (38%); `idle_60s_50obj` 13.01 ms (26%) |
| 24.6 | #134 | `core/registry.py`, formats 1 to 4 and the history folder in `persist.py`; the format 4 writer to `tests/reference/` | #132 | `saveload` (open), `oneoff/regload_ab.py` | `open_embed_100k_6000` 3079 ms (34%) |
| 24.7 | #135 | format 5 in `persist.py` | #134 | `saveload`, `oneoff/embed_ab.py`, `held_once_mem_f5.py` | `save_embed_100k_6000` 952.1 ms (18%); `open_embed_100k_6000` 3079 ms (34%); held after open 270.8 MB, save peak 348.3 MB (working set) |
| 24.8 | #136 | journal replay in `persist.py` | #130, #135 | `oneoff/replay_ab.py`, `open_journal_ab.py` | its one-offs only, run by the move |
| 24.9 | #137 | `core/globaltl.py`, the kept groups in `bl/runtime.py` | #132 | `draw`, `jumps`, `oneoff/groups_ab.py` | `redraw_viewport_50obj_global` 0.0906 ms (15%); `jump_3000_back_100k` 56.98 ms (18%) |
| 24.10 | #138 | `core/framing.py`, `core/morph.py`, math in `bl/morph.py`, the matrix math of `_bone_scale` in `bl/camrig.py` | #132 | `movie`, `oneoff/frame_walk.py`, `morph_*.py` | `movie_addon_ms_per_frame_100k` 0.4932 ms (11%) |
| 24.11 | #139 | comparisons in `bl/datarec.py` (not `_jump_matches`) | #131 | `datarec`, `oneoff/same_values_ab.py` | `dr_finish_move_500` 61.29 ms (14%); `dr_on_update_500` 0.1796 ms (15%) |
| 24.12 | #140 | undo matching (`bl/runtime.py`, `same_change` and `as_xor` in `core/delta.py`), slider shapes (`bl/scrubber.py`), `bl/multires.py` blocks | #132 | `commit`, `editmode`, `draw` | `commit_100k` 0.6423 ms (38%); `edit_step_100k` 40.98 ms (11%); `redraw_viewport_50obj_object` 0.0492 ms (7%) |
| 24.13 | #141 | nothing: the list below made final, the engine required everywhere | #130 to #140 | every scenario, one `bench/ab.py` of 24.1's commit against 24.13's | all of `bench/results/baseline_8dbb008.md` |

**Measured.** 24.3a (#146; `bench/ab.py`, 5 alternating rounds, run twice, 5.3.0 module, this Windows PC, 62332d0 against 4ae6ef2): `commit_100k` 0.6335 ms, median ratio 0.95 (spread 13% / 3%; again 1.02, 4% / 17%); `jump_3000_back_100k` 56.9 ms, 0.99 (3% / 4%; again 0.99, 1% / 16%); every `commit` and `jumps` median ratio 0.95 to 1.02, inside the A/A band; the JSON over the corpus's steps 0.52 (writing), 0.43 (reading) and 0.68 (`read_header`) of before (`OPTIMISATION_LOG.md`, "Rust moves"). 24.2a (#152; the same conditions, b98173e (0.17.0) against 04e811a (0.18.0)): `commit_100k` 0.6369 ms, best and median ratio 0.98, 0.98 (spread 17% / 4%; again 0.97, 0.95, 20% / 4%); all four of `commit` 0.91 to 1.00 (medians 0.92 to 0.99); `oneoff/open_journal_ab.py` (3,000 strokes, 5 alternating rounds, run twice) 265.2 ms, 0.96, 0.99 (13% / 11%; again 0.97, 0.97, 10% / 3%): none slower.

One helper works at a time, in this order: 24.1, 24.3, 24.2, then 24.4 to 24.13 by number (24.2 encodes steps the
engine holds, so it waits for 24.3: owner point 1). Before and after numbers per scenario are added here by each
move. A function the 24.13 scan finds with no owner becomes a new move, blocking 24.13, before 24.13 closes. The
blockers are GitHub issue dependencies, repeated in each sub-issue's "Blocked by"; 24.1's (the owner's merge) is text
only.

## Owner points (provisional)

The review of #142 asked for design choices. These are the answers the moves follow until the owner says otherwise.

1. **Threads and the step's Python values (24.2, 24.3, 24.5).** Both helper threads encode steps: the journal
   writer runs the payload of `bl/runtime.py:210` (`step_to_bytes`, called at `bl/persistence.py:131`) and the
   packer calls `step_to_bytes` at `core/store.py:151`. That encoding includes Python JSON of `extra` and the
   records, and a Rust thread never touches a Python object (GIL rule 3). So the JSON moves into the engine in 24.3
   and runs on the main thread when a step is handed to a thread (the journal payload, `Packer.submit`); the
   threads get bytes and engine-owned arrays only. The bytes equal `_dumps` and `records_to_bytes` byte for byte,
   floats included. The runtime sets `extra` and the records before a step is pushed (`bl/runtime.py:993`,
   `bl/datarec.py:934`); a test checks that a step's bytes don't change after hand-off. This puts the JSON, not the
   delta encoding, back on the main thread that R079 took the whole record off. If `commit` then loses beyond
   noise, the fallback is a thread that takes the GIL for the JSON only, an exception to rule 3 for the owner.
2. **The journal's write faults (24.2).** Five tests reach into the Python journal: `os.fsync` patched in
   `tests/test_core_saveload_storage.py:75` and `:87` (EIO is reported, EINVAL tolerated), `j._fh` swapped for a
   failing file at `:129` and `:175` (ENOSPC inside a record keeps the records around it, a failed flush is cut
   back), `j._fh` closed at `tests/test_core_journal.py:739` (a crash). The engine gets a test-only fault hook,
   compiled in and off unless a test sets it: fail the nth write, flush or fsync with a given errno, or drop the
   file without a clean close. The five tests assert the same things through it, against the engine and the
   reference.
3. **The format 4 writer (24.6).** `registry_to_bytes`, `registry_parts` and `_hist_steps` (`core/persist.py:105`
   to `:174`) leave `hindsight/` for `tests/reference/format4_writer.py`, with its own copies of `_encode_raw`,
   `_hist_doc` and `_registry_doc` (they stay for format 5, 24.7). No engine writer: V2 writes only format 5
   (`dev/FORMAT5_V1.md`), and the writer's users are tests and benchmarks that make format 4 files
   (`tests/harness.py:200`, `tests/test_bl_audit.py:175`, `tests/test_core_saveload_storage.py:37`,
   `tests/fixtures/make_legacy_fixtures.py:141`, `bench/scenarios/oneoff/regload_ab.py:22`, `rawsave_ab.py`,
   `bench/mem_speed_core.py`), which import it from there.
4. **Losses that add up (24.1, 24.13).** 24.1 records the commit it measured. 24.13 runs `bench/ab.py --a <a
   checkout of that commit> --b <the last commit>` over every scenario of `bench/suite.py`, with a move's target
   (within noise, held memory not higher) and a move's rule: slower beyond noise goes to the owner with the numbers.

5. **CPython's messages (24.3a, 24.6; #144's review, point 6).** The engine keeps CPython's json and codec
   messages word for word, as #144 recommended: its fast path reads exact types only, and anything else and every
   error run the reference's Python with the stdlib json from inside the engine (24.3a: `native/src/stepjson.rs`),
   so the result and the exception are the reference's, per Python version (on 3.11 the engine call's own count
   against the recursion limit is given back for the fallback, so a `RecursionError` hits where it did). The cost is
   on the error path only: over the corpus's steps no stdlib json runs. If the owner lets the messages differ, the
   fallback can go and `tests/equiv.py` compares those functions' exceptions by class only.

## What stays in Python

First version; #141 makes it final. Each stays because it needs Blender's Python API, or is glue that only calls
Blender or the engine.

- `hindsight/__init__.py`, `bl/__init__.py`, `bl/reg.py`, `bl/ops_core.py`, `bl/ui_panel.py`, `bl/ui_menu.py`,
  `bl/header.py`, `bl/props.py`, `bl/slider_prop.py`, `bl/prefs.py`: registration, operators, panels, menus,
  properties and preferences are Blender types.
- `Settings` in `core/types.py`: a dataclass, because `bl/prefs.py:37` and `bl/selfcheck.py:54` call
  `dataclasses.fields` on it.
- `bl/meshio.py`: mesh arrays are read and written with `foreach_get` and `foreach_set`.
- `bl/adapters/`, the reading in `bl/datarec.py`: RNA reads and writes of every recorded datablock. `_jump_matches`
  too: it reads each history's cursor and calls `find_object` and `mesh_fingerprint` (its `_data_diff` moves, #139).
- `bl/runtime.py` (besides #137 and #140): depsgraph and undo handlers, timers, mode changes, and the commit that
  reads Blender and calls the engine.
- `bl/persistence.py` (besides #130): load and save handlers, the history's bytes in the .blend, file paths.
- `bl/idmap.py`: the uids live in ID properties.
- `bl/camrig.py`, `bl/movie.py` (besides #138): camera and light objects, frame handlers. `_bone_scale` reads the
  Armature modifier and its pose bones (its matrix math moves), `_basis_matrix` builds a `mathutils.Matrix`.
- `bl/morph.py` (besides #138): the brush operator, its tool, the hit and radius under the cursor, the start and
  end of a stroke.
- `bl/scrubber.py` (besides #140): drawing with `gpu` and `blf`, region events, `region_insets` (it walks
  `area.regions`).
- `bl/multires.py` (besides #140): `bpy.data.libraries.write` and `load`.
- `bl/joinsep.py`, `bl/dupsep.py`, `bl/filtering.py`, `bl/cleanup.py`, `bl/selfcheck.py`, `bl/warnings.py`:
  hooks and checks on Blender's objects and operators.
- `bl/guard.py`, `bl/log.py`, `bl/beta.py`, `bl/ctx.py`, `bl/rigmark.py`: glue around Blender's callbacks.
- The facades `core/__init__.py`, `codec.py`, `crc.py`, `delta.py`, `step.py`, `native.py` and those the moves
  leave: they check Python arguments and call the engine.
