# Adapters (v2 scope): design

Internal. How Hindsight records more than sculpting without changing how sculpting behaves.
Status per data type lives in COVERAGE.md; this file is the architecture.

## Model

- A **datablock** is any Blender ID Hindsight records (object, mesh, material, node tree, world,
  scene, collection, action, armature, camera, light, curve, ...). Each one it records gets a stable
  id in the custom property `_hindsight_uid` (16 hex characters). Copies made by Blender (duplicate,
  append) carry the property along, so a datablock whose uid is already in use by another datablock
  gets a fresh one the first time Hindsight sees the clash.
- A **history** belongs to one datablock (objects keep theirs; materials, node trees, worlds and the
  scene get their own). Join still merges object histories. The global timeline orders every step of
  every history by sequence number, as it does now.
- A **step** is one user operation. It holds records for one or more datablocks of one history:
  - mesh records: the existing channel deltas (DELTA) and snapshots (TOPO, JOIN), unchanged;
  - **data records** (new, kind DATA): `DataChange(adapter, target, changes)` where `changes` maps a
    path inside the target to `(old, new)` values. Values are JSON values (numbers, strings, lists,
    booleans, None); floats come from Blender's float32 or double properties and round-trip exactly.
    Larger arrays (keyframe points, pose bones, texture tiles) use packed numeric records like the
    mesh channels, with their own adapter id.
- **Playback** applies records to a per-target baseline (`DataState`: path -> value) in memory and
  writes only the paths that changed to Blender once at the end of a jump, like the mesh path.

## The adapter contract

Each adapter has a short id (`"xform"`, `"rna"`, `"material"`, ...), the ID types it handles, and:

- `capture(id) -> dict` read the tracked values (path -> value). Must stay under its time budget for
  the test models (enforced by tests/test_bl_adapters.py).
- `diff(old, new) -> changes | None` pure Python, no bpy (in core), exact.
- `write(id, values)` set the given paths on the datablock; never touches other data.
- `cost(changes) -> int` bytes, for the memory limit.
- `paths(id)` the paths it tracks on that datablock (for the generic adapter: from the allowlist).

Adapter one is the existing mesh recorder (sculpt and paint channels, topology snapshots): wrapped,
not rewritten, so sculpting behaves exactly as before.

The generic **RNA adapter** records properties listed in an allowlist (type, property names), only
for datablocks Hindsight records, and compares a cheap hash of their values first so unchanged
datablocks cost one hash.

## When a step ends

One step per user operation:

1. A tracked modal operator ends (sculpt strokes, transform G/R/S, knife, ...): commit at once (the
   existing modal check, extended).
2. The newest registered operator changes (`window_manager.operators`): an operator finished.
3. Otherwise changes settle for 0.3 s (property drags in the UI, scripts, drivers): commit.

All records from one boundary go into one step per history; a change spanning several histories
(link a material to several objects) becomes one step in each, with consecutive sequence numbers so
the global timeline keeps them together.

Changes Hindsight makes itself (playback) are never recorded.

## Blender's own undo

- After a jump that wrote non-mesh data, Hindsight pushes an undo step ("Hindsight jump") so that
  Ctrl+Z in Object Mode undoes the jump instead of jumping somewhere unrelated.
- After Blender's undo or redo, every tracked datablock is re-read. If the result matches a
  neighbouring recorded state, the cursor moves there (as for meshes today); otherwise the baseline
  is refreshed and nothing is recorded.
- Global Undo off (Preferences > System): Blender's undo then does little outside Edit Mode;
  Hindsight still records and plays back. Both settings are tested.
- The sculpt-mode rules stay as they are (no native fallback after a write, put-back).

## Scope and opt-in

- Preference **Record**: `Sculpting only` (default, exactly the current behaviour) or `Everything`.
- With `Everything`, other data is recorded only for objects (and their materials) the user turned
  on with **Record everything on this object** in the Undo History tab, plus the scene if **Record the
  scene** is on. Nothing else is recorded until enabled.
- Per-data-type toggles in an Advanced sub-panel; per-datablock pause like the existing per-object one.

## Storage

- Step blob format 3 gains a records section; file format 4. Files from formats 1 to 3 load as they
  are (no DATA steps in them).
- Memory: data records count toward the same global limit; per-history step cap as before.

## Tiers (each gated: tests pass, COVERAGE.md rows filled, committed and pushed)

1. Objects: transforms (location, rotation in its mode, scale, delta transforms), parent and parent
   inverse, constraints (add, remove, reorder, settings), collections an object is in, visibility
   flags; add, delete, duplicate, rename; Edit Mode step by step; global timeline with creation and
   deletion.
2. Modifiers (stack and settings, Geometry Nodes inputs), node trees (nodes, links, values),
   materials (slots, settings).
3. Weight paint and vertex groups, UV maps, colour attributes, seams, sharp edges, creases, custom
   normals; texture paint as tile deltas with its own memory cap.
4. Keyframes and F-curves, shape keys, armatures and pose bones, NLA basics, frame range. Scrubbing
   never changes the current frame or playback state.
5. Curves, text, metaballs, lattices, Grease Pencil, lights, cameras, empties.
6. Scene, world, render settings, collection structure.
