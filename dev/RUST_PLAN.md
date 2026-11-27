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
`part_salvage`. `codec.py`, `crc.py`, `delta.py` and `step.py` are facades. The engine is required: no Python path
ships, `native.py` raises `EngineMissing`, six abi3 wheels are in the manifest.

Still Python, and needing no Blender API:

| Where | Lines | What |
|---|---|---|
| `core/persist.py` | 1,013 | registry document, formats 1 to 4 reading, history folder reading, format 5 save and open, journal replay |
| `core/framing.py` | 613 | Movie Mode framing, besides `extents_into` |
| `core/store.py` | 403 | packer thread, tiers, caps |
| `core/history.py` | 374 | `MeshState`, `History` (cursor, goto) |
| `core/types.py` | 330 | `Step`, `ChannelDelta`, `Snapshot`, `DataChange`, `Slice`, `Entry`, keys |
| `core/journal.py` | 273 | crash journal framing, atomic writes |
| `core/globaltl.py` | 222 | global timeline, jump planning |
| `core/registry.py`, `morph.py`, `topopack.py` | 193 | registry, morph targets, topology packing |
| `bl/datarec.py` | part | comparing recorded values (`_diff`, `_data_diff`, `_jump_matches`) |
| `bl/runtime.py` | part | undo matching, the kept global groups |
| `bl/scrubber.py` | part | the slider's layout, shapes and hit test |
| `bl/persistence.py` | part | the journal writer thread |
| `bl/multires.py`, `bl/morph.py`, `bl/camrig.py` | part | block hashing, morph targets, camera math |

The importer is gone (#72), so nothing of it moves.

## Why the building waits for the owner's merge

The core files are nearly the same on every open branch, but #10 and #23 (#105, #117) and #107 (#108) change
`persist.py` and `registry.py` (#23 also `framing.py`), and the Blender layer differs a lot (#13, #23 and #110 add 584, 1,162 and 738
lines under `hindsight/`). A move built before the merge would port Python that the merge then changes, and its
oracle would be the wrong code: written twice, which the owner's rule forbids. An integration branch that merges
the whole stack would be the owner's merge in all but name. So the first move (#129) is blocked by the owner
merging the stack, in the order of the pull requests (on #24).

## Rules for every move

1. **The oracle.** The Python that moves goes to `tests/reference/` (by `git mv`, unchanged); the module under
   `hindsight/` keeps its public names and signatures and holds no algorithm. `tests/test_core_reference.py` keeps
   failing if anything under `hindsight/` imports a reference module.
2. **Equivalence, test first.** Before the engine code: property tests (hypothesis), the corpus of real files from
   formats 1 to 5 (#129) and hostile input (cut short, flipped bytes, huge lengths, wrong types) compare engine and
   reference bit for bit: results, bytes written, exception class and message. Hostile input never crashes the
   engine or allocates past the bounds in `dev/RUST_DESIGN.md` ("Memory accounting").
3. **Measured, as `OPTIMISATION_LOG.md` says.** The scenarios a move touches (named in its sub-issue) are run before
   and after with `bench/ab.py` (alternating processes, at least 3 rounds, usually 5), conditions stated: sizes,
   Blender version, machine. Target: none slower or bigger beyond the A/B's noise, held memory not higher. A move
   is not a round (it is not aimed at speed); its numbers go in its PR and the table below. A move slower beyond
   noise that profiling within the move can't fix goes to the owner with its numbers on its sub-issue, never merged
   slower silently. No number is ever invented.
4. **One engine version per move.** API and the crate's minor version up by one, the line in `native.py` that
   says what the API added, six wheels rebuilt.
5. **As every ticket.** GIL and error rules of `dev/RUST_DESIGN.md`; full suites on Blender 4.2, 5.0, 5.1, 5.2 and
   5.3 before pushing; commit subjects end with `[skip ci]`; a draft PR into `claude/lucid-brahmagupta-9gsdsn`.
   A sub-issue that turns out bigger than one helper's context is split again before work starts.

## The moves

| Move | Sub-issue | What moves | Blocked by | Measured by |
|---|---|---|---|---|
| 24.1 | #129 | nothing: baseline numbers, the corpus, the equivalence helper | the stack merged | full `bench/suite.py`, `held_once_mem_f5.py` |
| 24.2 | #130 | `core/journal.py`, the journal writer thread | #129 | `commit`, `oneoff/open_journal_ab.py` |
| 24.3 | #131 | `core/types.py`, `core/topopack.py` | #129 | `commit`, `jumps`, `editmode`, held memory |
| 24.4 | #132 | `core/history.py` | #131 | `jumps`, `commit` |
| 24.5 | #133 | `core/store.py` | #132 | `commit`, `idle`, `packer_bench.py` |
| 24.6 | #134 | `core/registry.py`, formats 1 to 4 and the history folder in `persist.py` | #132 | `saveload` (open), `oneoff/regload_ab.py` |
| 24.7 | #135 | format 5 in `persist.py` | #134 | `saveload`, `oneoff/embed_ab.py`, `held_once_mem_f5.py` |
| 24.8 | #136 | journal replay in `persist.py` | #130, #135 | `oneoff/replay_ab.py`, `open_journal_ab.py` |
| 24.9 | #137 | `core/globaltl.py`, the kept groups in `bl/runtime.py` | #132 | `draw`, `jumps`, `oneoff/groups_ab.py` |
| 24.10 | #138 | `core/framing.py`, `core/morph.py`, math in `bl/morph.py`, `bl/camrig.py` | #132 | `movie`, `oneoff/frame_walk.py`, `morph_*.py` |
| 24.11 | #139 | comparisons in `bl/datarec.py` | #131 | `datarec`, `oneoff/same_values_ab.py` |
| 24.12 | #140 | undo matching (`bl/runtime.py`), slider shapes (`bl/scrubber.py`), `bl/multires.py` blocks | #132 | `commit`, `editmode`, `draw` |
| 24.13 | #141 | nothing: the list below made final, the engine required everywhere | #130 to #140 | every scenario, before (24.1) and after |

24.2, 24.3 and, after 24.4, 24.5 to 24.12 do not depend on each other; one helper still works at a time, in the
order of the numbers. Before and after numbers per scenario are added here by each move.

## What stays in Python

First version; #141 makes it final. Each stays because it needs Blender's Python API, or is glue that only calls
Blender or the engine.

- `hindsight/__init__.py`, `bl/reg.py`, `bl/ops_core.py`, `bl/ui_panel.py`, `bl/ui_menu.py`, `bl/header.py`,
  `bl/props.py`, `bl/slider_prop.py`, `bl/prefs.py`: registration, operators, panels, menus, properties and
  preferences are Blender types.
- `bl/meshio.py`: mesh arrays are read and written with `foreach_get` and `foreach_set`.
- `bl/adapters/`, the reading in `bl/datarec.py`: RNA reads and writes of every recorded datablock.
- `bl/runtime.py` (besides #137 and #140): depsgraph and undo handlers, timers, mode changes, and the commit that
  reads Blender and calls the engine.
- `bl/persistence.py` (besides #130): load and save handlers, the history's bytes in the .blend, file paths.
- `bl/idmap.py`: the uids live in ID properties.
- `bl/camrig.py`, `bl/movie.py` (besides #138): camera and light objects, frame handlers.
- `bl/scrubber.py` (besides #140): drawing with `gpu` and `blf`, region events.
- `bl/multires.py` (besides #140): `bpy.data.libraries.write` and `load`.
- `bl/joinsep.py`, `bl/dupsep.py`, `bl/filtering.py`, `bl/cleanup.py`, `bl/selfcheck.py`, `bl/warnings.py`:
  hooks and checks on Blender's objects and operators.
- `bl/guard.py`, `bl/log.py`, `bl/beta.py`, `bl/ctx.py`, `bl/rigmark.py`: glue around Blender's callbacks.
- The facades `core/codec.py`, `crc.py`, `delta.py`, `step.py`, `native.py` and those the moves leave: they check
  Python arguments and call the engine.
