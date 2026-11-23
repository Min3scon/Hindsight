# Format 5: how a step's encoding carries a version

Design for #42, part of #7 ("Design the history's new layout inside the .blend"). It builds on
`dev/FORMAT5_LAYOUT.md` (#41: the index part and the step parts) and keeps to the owner's givens in `DECISIONS.md`
("History inside the .blend", 2026-11-22): every step is kept, exactly, and the size target is about 2 bytes per vertex
a step moves, which needs better compression later. This doc says how that compression can be added without changing
the format again, and what a build does with a step whose encoding it doesn't know.

## New words

- **Encoding**: how one step's bytes are laid out and compressed, named by its **encoding number**. Today there are
  three: 1, 3 and 4.
- **Step prefix**: the first 11 bytes of every step written since format 3. It is the same for every encoding from 3
  on, and it says which encoding the rest of the bytes use.
- **Unknown step**: a step whose bytes are intact but whose encoding this build has no decoder for.

## Where the encoding lives

In the step's own bytes, at a fixed place. The step prefix is the start of today's format 3 step, unchanged:

```
byte   0        4                      8                 10          11
       "HSS3"   u32 CRC-32 of byte 8   u16 length of     u8          everything after: the encoding's own
                to the end             the header        encoding    (today: kind, flags, seq, time, label,
                                       from byte 10      number      ... then the compressed body)
```

Every later encoding keeps these 11 bytes as they are and is free in everything after them. `"HSS3"` now names the
step container (since format 3), not an encoding. The step part's entry for a step (its length, CRC-32 and meta, #41)
has no encoding field: the step's bytes alone say how to read them.

### How a reader picks its decoder

1. The step's CRC-32 in its step part must match its bytes; otherwise the step is damaged (#43, in
   `dev/FORMAT5_DAMAGE.md`).
2. Its first 4 bytes:
   - `HSS1`: encoding 1.
   - `HSS3`: the CRC-32 in the prefix must match bytes 8 to the end, or the step is damaged. The encoding number is
     then byte 10.
   - anything else: not a step; damaged.
3. An encoding number the build knows goes to its decoder. Any other is an unknown step (below).

The prefix CRC covers byte 10, so a changed encoding byte is caught as damage and never taken for a newer encoding.
Damage and an unknown encoding are told apart from the step's bytes alone, so the same rule works for a step outside
any step part: in the crash journal (#44), or held in memory.

When each check runs: at open and at a journal replay, the step part's CRC-32 (#43) or the journal record's (#44)
already vouches for every byte of the step, so the reader takes the encoding from the first 4 bytes and byte 10 without
the prefix CRC. That is how unknown steps are known at open. A step that passes its part's CRC-32 but starts with
neither `HSS1` nor `HSS3` was written wrong and counts as damaged at open. The prefix CRC is checked when a jump decodes
the step, as `dev/FORMAT5_DAMAGE.md` and `dev/FORMAT5_OPENING.md` say.

An encoding number names the whole recipe, fixed on the day it ships: every codec, delta record coding, index coding
and predictor tag it may use. A build that knows encoding 3 can decode every encoding 3 step. A new codec, record
coding or tag comes with a new encoding number, never inside an old one; so an unknown codec id inside a known encoding
(today the engine's `unknown codec id`) can only mean damage, and is read as damage.

Numbers 5 to 254 are free for later encodings. **255 is never given out**: tests use it to make an unknown step.

## Today's steps are encodings 1, 3 and 4

| First 4 bytes | Byte 10 | Encoding | Which steps | Read by |
|---|---|---|---|---|
| `HSS1` | (JSON header) | 1 | every step of a format 1 or 2 history (beta 1, beta 2 work in progress); no longer written | `decode_v1`: JSON header, XOR delta records `HD`, compressed body |
| `HSS3` | 3 | 3 | format 3 and 4 histories: steps without data records | `decode_v3`: binary header, `HE` delta records (wrapping difference, predictors, byte planes), compressed body |
| `HSS3` | 4 | 4 | format 4 histories: steps with data records (adapters other than the mesh recorder) | `decode_v3`, with the data records section |
| `HSS3` | 5 to 254 | later | written by later builds | the build's decoder for it, or carried unread |

(Decoders in `native/src/step.rs`.) Packed steps and steps written quickly (the crash journal and saving, header flag
8: values stored as they are) are the same encodings; the flag marks a step written with its recipe's quick settings,
not another encoding. Encodings 3 and 4 already fit the step prefix, so **no step needs re-encoding to fit**.

A V1 history moves in (#45) step by step as bytes: a format 1 or 2 history's steps are encoding 1, a format 3 one's
are encoding 3 (plus encoding 1 steps the packer hadn't re-packed yet), a format 4 one's are encodings 3 and 4 (and
1). Each step's bytes are copied into a step part as they are; no step is decoded or re-encoded at the move-in.

Re-encoding is not chosen beyond what V1 already does. The packer keeps doing what it does today while the user is
idle: it re-packs encoding 1 steps, steps written quickly and steps not yet at the strong codec setting into encoding
3 or 4 at that setting, and keeps the new bytes only if they decode to the same step (`same_step`). Each such swap changes its step part at the
next save, which #41 already counts.

## A step whose encoding this build doesn't know

This happens when a later V2 build, writing encoding 5, saved the file and an earlier V2 build opens it. The step is
kept untouched and saved back as it was, the user is told, and it is never misread:

- **Opening**: the step is kept as its bytes and never decoded. Its meta comes from its step part (#41), so the
  history still lists it with its label. It is not counted as damaged, and the history is not cut at it (V1's load
  cuts a history at a damaged step, `hindsight/core/persist.py`).
- **Telling the user**: one note per object at open, in the style of the damaged-step notes in
  `hindsight/bl/persistence.py`, saying how many of its steps were saved by a newer Hindsight, that they are kept as
  they are, and that updating Hindsight is needed to go back past them.
- **Jumps**: every step undoes and redoes itself, so the cursor moves only between the nearest unknown steps on each
  side of it. A jump, a scrub, a global timeline jump, Movie Mode or a time lapse that would cross one stops at it
  and says why; a morph target beyond one can't be set.
- **Recording**: goes on as usual while no unknown step lies after the cursor (new steps go after the cursor and
  nothing is cut). If one does (the file was saved with the cursor before it), recording would cut it, so that history
  is **held**: all of it is kept and saved back as it was, it is not recorded into, and its note says so.
- **The packer**: never re-packs an unknown step.
- **Saving**: the step's bytes go back into a step part unchanged, with the CRC-32 they came with; a full step part
  holding it is handed over as it is (#41).
- **Remove Hindsight data**: removes it with everything else, as the user asked.

**Today** the engine reads both cases as damage. Measured on this branch (bpy 5.3.0, a throwaway script, not merged):
a format 3 step with byte 10 set to 5 or 255 and its prefix CRC fixed gives `CorruptStep: step blob: bad header` from
both `step_from_bytes` and `recompress`; with its first 4 bytes set to `HSS9`, `CorruptStep: step blob: not a
Hindsight step (wrong header)`. The packer survives that (a failed re-pack leaves the step as it is,
`hindsight/core/store.py`), but a jump that reaches the step fails with the damaged-step error. So the build needs, in the engine, an
encoding check that answers "unknown encoding" apart from `CorruptStep`; today's `step.version_of` (which answers 3
for both encodings 3 and 4) gives way to the rule above.

## Adding an encoding later

1. Take the next free number (5 first). Write its encoder and decoder in the engine (`native/src/step.rs`), after the
   step prefix, and add the number to the decoders the reader knows. Nothing else changes: no new format number, and
   no change to the index part or the step parts.
2. In that encoding's ticket, decide and measure: which steps get it (new packed steps; the quick form or not);
   whether the packer moves older steps to it (each moved step changes its step part at the next save, so moving a
   whole history rewrites all its step parts once); and its size on real brush strokes
   (`tests_ui/scenarios/s_corpus.py`) against the target. Each change aimed at size or speed is a measured round in
   `OPTIMISATION_LOG.md`.
3. Its pull request says that earlier V2 builds will carry its steps unread (the section above): that is the cost of
   writing a new encoding.

The tests it then needs:

- **Bit-exact round trips**: for every kind of step (delta, topology, join, data), every header flag (before, after,
  snapshots, data records, written quickly), empty and one-element deltas, every value bit pattern (NaN payloads, -0,
  denormals, infinities) and the largest step allowed, encoding then decoding gives the same step bit for bit, in the
  engine and through `hindsight/core/step.py`.
- **Old encodings still read**: the fixtures already there keep passing unchanged (`tests/fixtures/beta1_format1.hsr`,
  `beta2wip_format2.hsr`, the real beta 2 files in `tests/fixtures/beta2`, and the 46-step corpus
  `step_corpus_base.zip` read by `tests/test_core_reference.py`), and a corpus of the new encoding's steps is frozen
  into `tests/fixtures` the day it ships, so the encoding after it is checked against it.
- **Re-packing**: the packer moving a step of each older encoding to the new one keeps it bit for bit, and leaves
  damaged and unknown steps alone.
- **Unknown and damaged**: a step of encoding 255 with a correct prefix CRC opens as an unknown step (the history
  opens whole, the note shows, a jump stops at it, a save gives back the same SHA-256, nothing is reported damaged);
  the same step with one byte changed is reported damaged.
- The full suites on Blender 4.2, 5.0, 5.1, 5.2 and 5.3.

## Alternatives considered

- **The encoding in the step part's entry for each step** (beside its length, CRC-32 and meta): a step also lives
  outside step parts (the crash journal, memory), so its bytes would no longer say how to read them; the packer would
  have to change two places for every swap, which could disagree; and encodings 1, 3 and 4 already carry it in their
  bytes. Lost.
- **One encoding per step part, per history, or per file (the format number)**: the packer re-packs one step at a
  time, and a moved-in V1 history mixes encodings 1, 3 and 4, so a whole part or history would be re-encoded to
  change one step. Per file, every new encoding would be a new format, which is what #7 is meant to avoid. Lost.
- **A new first 4 bytes per encoding (`HSS5`, `HSS6`, ...)**: a reader couldn't tell a newer encoding from bytes that
  aren't a step at all, and without a checksum at a fixed place it couldn't tell damage from an encoding it doesn't
  know. The step prefix gives both. Lost.
- **Self-describing steps** (each step lists its stages by name, such as "difference, zigzag, byte planes, zstd", so
  new combinations need no new number): more bytes in every step, against the 2 bytes per vertex target, and a reader
  still can't decode a stage it doesn't know, so unknown steps remain. One number per recipe is smaller and is
  tested as one corpus. Lost.
- **Dropping the prefix CRC** (the step part already holds a CRC-32 per step): it saves 4 bytes a step (0.4 MB at
  100,000 steps, arithmetic), but encodings 3 and 4 would no longer fit the prefix, and a step in the crash journal
  or in memory couldn't be told damaged from unknown. Lost.
- **Re-encoding every V1 step into the newest encoding at the move-in** (#45): every step would be decoded and
  encoded again at once (slow at 100,000 steps, and one more chance to break exactness) for a size gain the packer
  already gets step by step while the user is idle, each step checked with `same_step`. A move-in without re-encoding
  is a byte copy. Lost.
- **An unknown step read as damaged** (today's engine): the load would cut the history at it, against "every step is
  kept", and the user would be told their history is damaged when it isn't. Lost.
- **A history with any unknown step held whole** (as V1 holds a format 5 history): the user loses the steps the build
  can read, and recording stops even when nothing would be cut. Kept only for the one case where recording would cut
  an unknown step.
- **A file with any unknown step refused whole**: the same, for every history in the file. Lost.

## What the build ticket does with this

- The engine: an encoding check from the step prefix, decoders for 1, 3 and 4, and an "unknown encoding" answer apart
  from `CorruptStep`.
- Opening and saving: unknown steps carried as bytes with their meta from the step part; the notes; jumps stopping
  at unknown steps; the held history.
- The tests listed above, with encoding 255 for the unknown step.
