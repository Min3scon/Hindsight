# Format 5: how a damaged part is found and what survives it

Design for #43, part of #7 ("Design the history's new layout inside the .blend"). It builds on
`dev/FORMAT5_LAYOUT.md` (#41: the index part and the step parts) and `dev/FORMAT5_ENCODINGS.md` (#42: the step
prefix), and keeps to the owner's givens in `DECISIONS.md` ("History inside the .blend", 2026-11-22): every step is
kept, exactly. Every number below was measured ("How it was measured" at the end), or is marked as an estimate.

## New words

- **Damaged**: a part or a step whose bytes don't match the CRC-32 written with them, or that is missing or cut short.
- **Reachable steps**: the steps of a history that a jump can reach from the saved cursor without crossing a damaged
  step. Every step undoes and redoes itself, so no jump can cross a damaged one.

## What this adds to #41's layout

1. The index part is kept twice, as `index` and `index_copy`: the same bytes, each ending with a CRC-32 of the bytes
   before it. Every save writes both.
2. For each step part, the index part also says where its steps begin, beside its number of steps, its length and its
   CRC-32.
3. A step part's header is two pieces, each with its own CRC-32: the **step table** (the first step's number, the
   number of steps, and each step's length and CRC-32) and the **metas**. The steps follow, back to back.

Nothing here changes what a step looks like (#42) or what a save rebuilds (#41). The second copy of the index part
costs tens of KB per save at most (estimate: about 60 bytes per step part, so 60 KB for a 4 GB history in 1,024 step
parts).

## How each part is checked when the file opens

In this order, while #41's opening reads the parts one by one:

| Part | Check | Cost |
|---|---|---|
| The stub (`hindsight_data`) and `sidecar` | not read: V2 knows a format 5 file by its `hindsight_parts` group | none |
| The index part | its CRC-32; `index_copy` is read only when `index` fails | tens of KB: not measurable |
| Each step part | it is there, and its length and CRC-32 match the index part's | 0.028 to 0.035 s per 256 MB (engine `crc32`) |
| A step part that failed | the CRC-32 of its step table and of its metas, then each step's CRC-32 against the table | one more pass over that part: under 1 ms per 4 MiB |
| A step part whose step table failed | its steps are found by their prefix (below) | 0.13 to 0.14 s per 256 MB: about 2 ms per 4 MiB |

So an undamaged file costs one CRC-32 pass over its history, as today: V1's load already takes a CRC-32 of every step
(`registry_from_bytes` in `hindsight/core/persist.py`). With the engine's `crc32` that is about 0.03 s per 256 MB,
against 0.20 to 0.22 s with Python's `zlib.crc32`. More work happens only in a part that failed. A part or a history
group the index part doesn't list is not read, and not saved again.

Two checks are left for later, as today. A step's own prefix CRC (#42) is checked by the engine when a jump decodes the
step. A step that passes every CRC-32 but can't be decoded was written wrong rather than damaged in the file; a jump
that reaches it stops with an error.

**Finding steps without the step table.** Every step since format 3 starts with `HSS3` and a CRC-32 of the rest of its
bytes (#42), and a step part's steps lie back to back from where the index part says they begin to the part's end. So
a step starts at an `HSS3` whose CRC-32 matches up to the next such start, or up to the part's end. Steps are numbered
forward from the beginning until the first gap and backward from the end until the last gap; the index part gives
the part's number of steps, so with one gap its steps are exactly the missing ones, and with more than one every step
between the first gap and the last counts as damaged. Steps of encoding 1 (formats 1 and 2, #45) have no CRC-32 of
their own, so a step part of them whose step table is damaged is lost whole.

A step's meta comes from the metas, or, when they are damaged, from the step's own bytes, as today's load does for a
step without a meta. An unknown step (#42) whose meta is lost is listed as a step saved by a newer Hindsight.

## What is lost and what is kept

| Damaged | Lost | Kept |
|---|---|---|
| The stub or `sidecar` | nothing: V2 doesn't read them | everything |
| One copy of the index part | nothing | everything, from the other copy |
| Both copies | the use of every history in the file: their cursors, members and settings are only there | every step part, untouched and set aside; the objects as they were saved |
| A step part's metas | nothing: each meta is read from its step | everything |
| A step part's step table | the steps that can't be found by their prefix | every step found by its prefix |
| A step part missing, or cut short | its steps, or those after the cut | the steps before the cut |
| A step | that step | the others |

Wherever steps are damaged, the steps beyond them, seen from the saved cursor, go too: the history keeps its reachable
steps, its cursor moves with them, and its morph target is kept only when it lies among them. Other histories are not
touched: each has step parts of its own.

Why this is the least that can be lost:

- **A damaged step** can't be repaired: a CRC-32 tells that bytes changed, not which ones or what they were. A step
  beyond it, seen from the cursor, can be reached only by applying it, because the object in the file is as it was at
  the saved cursor and every step undoes and redoes itself. So the reachable steps are all that any jump can ever
  reach. This is V1's rule today (`registry_from_bytes`).
- **The index part** alone holds the cursors and the members, so without it no step part can be tied to an object or
  applied from the right place (a step applied from the wrong cursor changes the object wrongly). Its second copy means
  two separate damages are needed to lose it; when both are damaged nothing can be used safely, but nothing is thrown
  away either.
- **Everything else** is held a second time elsewhere: the stub is fixed, each meta is in its step, and each step's
  place is found from its prefix. Damage there costs nothing, or only steps that are damaged themselves.

A history keeps no steps only when the step just before its saved cursor and the step just after it are both damaged,
or one is and there is no step on the other side (the cursor at the end and the last step damaged, say): no step can
be reached then. The
object is still as it was saved, and recording goes on from there.

## What the user sees

Today's notes (`hindsight/bl/persistence.py`), one per object and in the same words. What changes is how much they
report lost:

| Damage | Today (V1 and the main-branch build) | Format 5 |
|---|---|---|
| A step | the history keeps its reachable steps, and `DAMAGED_TEXT` ("3 damaged steps of 'Suzanne' could not be loaded. The 40 steps before them are fine"), `DAMAGED_PART_TEXT` or `DAMAGED_ALL_TEXT` says so | the same |
| A step's length, or the history cut short | every later step in the file, in every later history: the registry reads the lengths in a row and stops at a bad one | only the steps of that step part that can't be found by their prefix, or that come after the cut |
| The step list (metas, CRC-32s) | it sits in the registry's one header, so every history: `UNREADABLE_EMBED_TEXT` ("The history stored in this .blend could not be read, so it was not loaded. It is kept in the file in case it can be recovered") | nothing lost, no note |
| The registry header, or the index part | every history: `UNREADABLE_EMBED_TEXT` | one copy: nothing lost, no note; both: `UNREADABLE_EMBED_TEXT`, which then holds word for word |

Damage that loses nothing gets a line in the log, not a note: the user has nothing to do, and the next save writes the
part again.

## What the next save does

Nothing is written before the user saves: a damaged file that is opened and closed without saving stays as it was.

| Damaged | The next save | Why |
|---|---|---|
| The stub or `sidecar` | writes it again | so older builds keep saying that a newer Hindsight saved the file; it is 80 characters |
| One copy of the index part | writes both, as at every save | |
| Both copies | the `hindsight_parts` group is renamed `hindsight_parts_unreadable` at open and saved byte for byte in every save until Remove Hindsight data; new steps start new histories | its steps may all be intact with only their map lost, which a later repair could rebuild; today keeps an unreadable embedded history the same way (`hindsight_data_unreadable`) |
| Only a step part's metas or step table, every step found | rebuilds that step part | once, so the next open is clean; it is one part, under 4 MiB |
| Steps | drops them, with the steps that could be reached only through them, and saves the reachable steps as the history | no build can repair a damaged step or reach past it, so keeping them would carry bytes nobody can use into every later save and show the note at every open |

For the last row the save rebuilds the step parts that hold the first and the last reachable step and drops the step
parts wholly outside them, as when the step cap drops a history's oldest steps and a new recording cuts its newest.
The build settles whether the step parts in between are reused untouched or rebuilt once: that depends on how a step
part numbers its first step after the oldest steps go, which the step cap needs settled too.

Unknown steps (#42) are not damage: they are kept and saved as they are.

## Alternatives considered

- **One index part**: one damaged byte would cost every history in the file, as a damaged registry header does
  today. Lost to a second copy, for tens of KB per save at most.
- **A head part in each history's group** (its cursor, members and step parts) instead of a second copy: a damaged
  head still costs its whole history, the registry's settings still need an index part, and it is more parts and more
  code. Lost.
- **Rebuilding a history from its step parts when both index copies are damaged**: the cursors and members are only in
  the index part, and a step applied from the wrong cursor changes the object wrongly. Lost; the parts are set aside.
- **Checking a step part only when a jump first needs it**: the reachable steps have to be known at open, where the
  damage lies against the cursor, or the user finds out midway through a session when a jump stops. Checking at open
  costs 0.03 s per 256 MB. Lost.
- **Two copies of each step table** instead of finding steps by their prefix: at least 8 bytes per step (a length and
  a CRC-32) in every step part of every file, where the prefix search costs nothing until a step table is damaged.
  Lost.
- **Error-correcting bytes in each part**, to repair damage and not only find it: more bytes in every file and every
  save for a rare case, against the size target in `DECISIONS.md`. Lost.
- **Keeping damaged and unreachable steps in the file**: no build could use them (see "What the next save does").
  Lost.
- **Getting past a damaged step with a topology step**, which keeps the whole mesh: it puts back only the mesh, so the
  object's other data (its transform, modifiers, data steps) would be wrong, and every step must come back exactly.
  Lost.
- **Refusing a whole history on any damage**, as V1 refuses all of them when its header is damaged: one byte would
  cost everything. Lost.

## What the build ticket does with this

- Write and read the two index copies, each step part's step table and metas with their CRC-32s, and where each step
  part's steps begin.
- At open: the checks in the order above, the search by prefix for a damaged step table, today's cut to the reachable
  steps, and today's notes; `hindsight_parts` set aside when both index copies are damaged.
- At save: the stub written again when it isn't exact, and the rows of "What the next save does".
- Tests, each on a saved file with bytes changed or removed: one per row of "What is lost and what is kept", checking
  the steps kept, the note shown and that the file saved next opens with no note; damage in two histories at once,
  each losing only its own steps; and a damaged file opened and closed without saving, byte for byte the same after.

## How it was measured

A throwaway script, not merged, run in November 2026 on the owner's Windows 10 PC (other test runs going at the same
time, so times are rough), with the Python of the bpy 4.2 test venv (3.11.15) and of the bpy 5.3 one (3.13.4) and the
engine module installed in each (`hindsight_native`, API 10). The history was 64 step parts of 4 MiB (268.4 MB) of
random bytes, made of 64 KiB steps, each with a correct `HSS3` prefix.

- **CRC-32 of every step part**, 5 runs (min / median): the engine's `crc32` 0.028 / 0.035 s on 4.2 and 0.034 /
  0.035 s on 5.3; `zlib.crc32` 0.200 / 0.203 s and 0.214 / 0.221 s.
- **Finding every step by its prefix**, as for a damaged step table, 3 runs: 0.125 / 0.129 s on 4.2 and 0.138 /
  0.139 s on 5.3.
- Per step part, these divided by 64 (arithmetic).
