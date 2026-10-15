# Test audit

Internal. Written 2026-10-14 when the owner's test guarantee arrived (see HANDOFF.md, "Test
guarantee"). An inventory of what exists, measured against the 12-point tier gate. Blunt on purpose:
"code exists" never counts as covered, and sculpt tests never count for other rows.

Gate codes used below:
OPS real operators (bpy.ops with context overrides or the event harness) · EXACT every step both ways
· ORACLE independent snapshot code plus a replay oracle · STEPS exactly one step per operation,
counted · UNDO Ctrl+Z / Ctrl+Shift+Z mixed with scrubbing, mode switches, Global Undo on and off, undo
steps default/min/max · RELOAD sidecar, embed, both, rename, copy-paste, append, link, Shift+D, Alt+D,
older formats · PROP Hypothesis, 2,000+ sequences per adapter · MUT mutation testing of the adapter's
diff and apply code · FAULT fault injection per adapter and corrupt data · PERF cost and memory on
small, medium and heavy scenes against budgets, 30-minute soak · WARN messages for limits, tested ·
VISUAL screenshots of the row's UI, looked at.

"UI" means the real-interface harness (tests_ui, real Blender with a window, real events). "Headless"
means `blender -b` (tests/). Both run on 5.0.1, 5.1.1 and 5.1.2.

## Summary

| Tier | Rows | Code written | Rows meeting the full gate |
|---|---|---|---|
| 0 Sculpting (existing rows) | 6 | yes | 0 (closest: vertex positions; missing ORACLE, PROP for the recorder, UNDO matrix, RELOAD variants, soak) |
| 1 Objects | 6 | yes (D14 gap: Object Mode mesh changes not recorded) | 0 |
| 2 Modifiers, node trees, materials | 3 | partly, parked on branch `tier2`, untested | 0 |
| 3 Painting | 3 | no | 0 |
| 4 Animation | 4 | no | 0 |
| 5 Other object types | 2 | no | 0 |
| 6 Scene settings | 1 | no | 0 |

Infrastructure missing for the gate (all tiers):
- **Independent oracle.** Every UI scenario hashes meshes with Hindsight's own `meshio.read_snapshot`
  (s_round_c, s_round_e, s_regress_native_undo, s_round_b_features). Object scenarios use small
  hand-written readers (s_adapters `xf`, s_tier1 `obj_state`), which are independent but narrow.
  No replay oracle anywhere.
- **Hypothesis** is not installed in Blender's Python. The closest thing is seeded random sequences
  (test_core_history: 500 random jumps over one history; test_bl_tier1 test_random_mix).
- **Mutation testing** covers only hindsight/core (28 mutants). Nothing for hindsight/bl adapters,
  datarec or the mesh recorder's observe/apply.
- **Fault isolation per adapter** doesn't exist: a failing adapter trips the guard for the whole
  recorder ("recorder" feature), which stops sculpt recording too.
- **Undo matrix**: Global Undo off is tested only for transforms (s_adapters); undo steps are never
  set to the minimum or maximum; Undo History menu and File > Revert are untested.
- **RELOAD variants**: sidecar/embed/both are tested headless for sculpt histories only
  (test_bl_persistence); rename, copy-paste into another file, append and link are untested for
  every row.
- **Soak**: none (test_bl_stress makes 10,000 sculpt steps headless; no time-based soak, no checks
  for threads, timers or file handles).
- **Evidence**: runners print text; there is no machine-readable result, no row tags on tests, no
  TEST_EVIDENCE.md, no MANUAL_TESTS.md.
- **Test models**: make_test_models.py has size ladders (10k to 2M), shapes, scene_many (40 objects,
  join pair), data_layers, awkward. Missing: table and robot from primitives, parented hierarchy,
  modifier-heavy object, big Geometry Nodes graph, shader tree, painted weights and textures, rig
  with NLA, every other object type, heavy versions of each.

## Tier 0: sculpting (recorded by default)

| Row | Tests (headless) | Tests (UI) | Gate items present | Missing |
|---|---|---|---|---|
| Sculpt: vertex positions | test_bl_recorder (first_stroke_starts_history, all_channels_scrub_exact, step_back_forward_ops, branch_silent_and_confirm, native_undo_sync_and_f9_replace, cap_and_memory_eviction, stroke_end_debounce, ...), test_core_history, test_core_codec, test_core_store, test_core_format, test_core_migration, test_bl_stress | round_c (mouse and operator strokes, every jump bit-exact, 120 jumps on a long history), regress_stroke_split, regress_native_undo, ui_tour, round_b, round_d, round_e (owner copies), round_f (files across builds) | OPS, EXACT (bit-exact), STEPS (one stroke one step, quick and slow strokes), MUT (core only), FAULT (fail_inject, damaged files in round_d and test_core_format), PERF (PERF_LOG, bench), WARN (Multires, Dyntopo, 2M vertices), VISUAL (ui_tour, regress_slider_visible) | ORACLE (hashes use meshio; no replay), PROP (no Hypothesis), UNDO (no Global Undo off, no undo-steps min/max, no Undo History menu, no Revert), RELOAD (rename, copy-paste, append, link untested), MUT of the recorder (runtime._observe, write paths), soak |
| Mask, face sets, hidden vertices and faces | test_bl_recorder all_channels_scrub_exact | round_c (mask flood fill, face sets, hide) | OPS, EXACT, STEPS | as above |
| Colour attributes (Vertex Paint, sculpt paint) | test_bl_recorder vertex_paint_starts_history | none with a real paint stroke | headless EXACT | OPS (no real Vertex Paint stroke in the UI), STEPS, UNDO, RELOAD, ORACLE, PROP |
| Topology changes (remesh, decimate) | test_core_history topo_vertex_count, test_bl_recorder topology_snapshot | round_c only if its random ops include one (they don't: strokes, mask, face sets, hide, filters) | headless EXACT | OPS (no real remesh in the UI), STEPS, UNDO, RELOAD, ORACLE |
| Edit Mode, one step per visit | test_bl_recorder edit_mode_grouping, test_bl_regressions edit_mode_alone_starts_no_history | none | headless | OPS, EXACT in the UI, STEPS, UNDO |
| Join, separate, duplicate (histories) | test_bl_join (5), test_bl_dupsep (7, incl. Alt+D warning) | round_b_features (Ctrl+J, scrub back past the join, Separate) | OPS (UI join), EXACT (headless), WARN (linked duplicate) | ORACLE, UNDO, RELOAD variants, PROP, soak |

Morph History brush and Movie Mode are features, not data rows: test_bl_morph (9), test_bl_movie
(5), round_b_features. D13 (Morph History broken on Blender 5) was missed by headless tests and found
by the UI.

## Tier 1: objects (opt-in)

| Row | Tests (headless) | Tests (UI) | Gate items present | Missing |
|---|---|---|---|---|
| Transforms (location, rotation with mode, scale, deltas) | test_bl_adapters (records_transforms_and_plays_back, packed_and_saved, capture_budget, animation_is_not_recorded, awkward_values_round_trip, rotation_mode_switches), test_bl_tier1 random_mix | s_adapters (G, R 45, S 1.5, typed location: one step each, exact jumps, Ctrl+Z/Ctrl+Shift+Z of a jump, Global Undo off), s_tier1 (G) | OPS (keyboard), EXACT, STEPS (4 ops), UNDO (partial), PERF (capture budget only) | gizmo drags, N panel fields, delta transforms in the UI, snapping, pivot changes, multi-object transforms; replay oracle; undo steps min/max; RELOAD variants; PROP; MUT; FAULT; soak; VISUAL |
| Parenting | test_bl_tier1 test_parenting | s_tier1 (Ctrl+P, Object) | OPS (Ctrl+P), EXACT, STEPS (one op) | Alt+P with and without keep transform, parent to bone, UNDO, RELOAD, PROP, MUT, FAULT, PERF, VISUAL |
| Constraints | test_bl_tier1 test_constraints | s_tier1 (add via operator) | OPS (add), EXACT, STEPS (one op) | remove and edit in the UI, reorder, **animated constraint settings are recorded as user steps (found while designing Tier 2, D15)**, UNDO, RELOAD, PROP, MUT, FAULT, PERF, WARN (list settings not recorded: no message in the add-on) |
| Collections, visibility | test_bl_tier1 test_collections_visibility_names, test_kinds_switch_off_and_on | s_tier1 (H, Alt+H; **"move to a new collection" pokes data, not an operator**) | OPS (H, Alt+H), EXACT | M (move to collection), link to collection, outliner toggles for viewport/render/select, UNDO, RELOAD, PROP, MUT, FAULT |
| Adding, deleting, duplicating, renaming objects | test_bl_tier1 test_objects_added_deleted, test_deleted_object_back_after_reload | s_tier1 (Shift+D, X, Shift+A cube via operator, global slider) | OPS (partial), STEPS (3 ops), EXACT (names only in the UI) | every primitive type, Alt+D, F2 rename in the UI, join and separate with the scene history, parenting and collections kept across creation and deletion on the global timeline, UNDO, RELOAD variants, PROP, MUT, FAULT, PERF, VISUAL |
| Edit Mode step by step | test_bl_tier1 test_edit_mode_steps, test_edit_mode_default_unchanged, test_edit_step_cost | s_tier1 (G, E, Ctrl+Z, Ctrl+Shift+Z, slider jump, leaving Edit Mode adds no step) | OPS (G, E), EXACT, STEPS (2 ops), UNDO (Ctrl+Z, Ctrl+Shift+Z), PERF (cost test) | bevel, loop cut, inset, delete, merge, subdivide, knife, rotate, scale, fill, dissolve, bridge; selection-only changes making no step; Global Undo off; RELOAD; PROP; MUT; FAULT; VISUAL |

Defects found by this audit, before any new test:
- **D14** (S2): on a recorded object, Object Mode operations that change the mesh (Apply > Scale,
  Apply Modifier, Set Origin, Shade Smooth) aren't recorded. Scrubbing back past Ctrl+A > Scale puts
  the old scale back on the already scaled mesh. Fix written (on the `tier2` branch), to be ported
  with a regression test.
- **D15** (S3): animated or driven constraint settings are read as user changes, so changing the
  frame records steps. Fix written (on the `tier2` branch), to be ported with a regression test.

### Tier 1 update (2026-10-15)

The gate machinery from the plan below exists and runs on all three Blenders: tests/oracle.py
(independent oracle) and the replay oracle (tests/t1_gate_lib.py), test_bl_t1_gate (every row's
operations, every position both ways, the global timeline, Blender's undo emulated), test_bl_t1_prop
(Hypothesis, 2,000 sequences, corpus kept), tools/mutation_bl.py (39 mutants), test_bl_t1_faults
(per-adapter fault isolation), test_bl_t1_corrupt, test_bl_t1_reload and test_bl_t1_migrate (every
reload variant and older formats), test_bl_t1_warn, test_bl_t1_perf (budgets), test_bl_t1_jumps
(Blender's undo of jumps), test_bl_t1_added (objects that come and go), and the real-interface
scenario s_t1_gate (every Tier 1 operation by keyboard and mouse, Ctrl+Z / Ctrl+Shift+Z mixed with
scrubbing and across Edit Mode, Global Undo off, undo steps at their minimum and maximum) plus
s_soak (per adapter and mixed). The gaps listed in the table above are closed by those tests;
TEST_EVIDENCE.md is the record of what passed where.

Defects found by the gate work: D14 to D39 (DEFECTS.md, no D21), among them S1: D17, D27 and S2:
D14, D18, D20, D24, D26, D28, D32, D35, D36, D38, D39. None open.

## Tiers 2 to 6

| Tier | Rows | Code | Tests |
|---|---|---|---|
| 2 | Modifiers and Geometry Nodes inputs; node trees; materials | written on branch `tier2` (object adapter: stack, settings, GN inputs, slots; node tree adapter: nodes, links, values, ramps, curves, zone items, group interface; derived tracking of materials and groups; node editor ids), never run | none |
| 3 | weight paint and vertex groups; UVs, seams, sharp, creases, custom normals; texture paint | none | none |
| 4 | keyframes and F-curves; shape keys; armatures and pose bones; NLA and frame range | none | none |
| 5 | curves, text, metaballs, lattices, Grease Pencil; lights, cameras, empties | none | none |
| 6 | scene, world, render settings, collection structure | none | none |

## Cross-feature and system tests

| Required | Exists |
|---|---|
| Mixed sessions on one object (sculpt, edit, modifier, GN, weights, transform, keys), scrub, save, reopen, scrub | no |
| Same on multi-object scenes with parenting and collections | no |
| Scrubbing mixed with Ctrl+Z, Ctrl+Shift+Z, Edit > Undo History, File > Revert | Ctrl+Z / Ctrl+Shift+Z only (ui_tour, regress_native_undo, s_adapters, s_tier1) |
| Copy, paste, link, append, library overrides, missing linked data | no |
| Memory limit across all datablocks with eviction | sculpt only (test_bl_recorder cap_and_memory_eviction, round_d) |
| Per-datablock opt-out, "sculpt only" vs "everything", nothing recorded until enabled | test_bl_adapters test_scope_and_opt_in (headless) |
| Format migration (beta 1 and current beta files) | sculpt histories: test_core_migration, round_f; data records: test_core_records (format only) |
| Old sculpt suite green after every shared change | yes, run at each gate so far |

## Required by the test guarantee (owner's list, kept here so it isn't lost)

- Tier 1: G, R, S by keyboard, gizmo and typed values; N panel fields; delta transforms; snapping;
  pivot changes; multi-object transforms. Add every primitive type, delete, duplicate (Shift+D,
  Alt+D with the warning), rename, join and separate with unmerge, parent and unparent (with and
  without keep transform), constraints add, remove, edit, collection link, unlink and move,
  visibility toggles (viewport, render, select). Edit Mode steps: extrude, bevel, loop cut, inset,
  delete, merge, subdivide, knife, grab, rotate, scale, fill, dissolve, bridge; selection-only changes
  make no step; topology snapshots with positions delta-encoded between them. Global timeline across
  creation and deletion of several objects with parenting and collections intact.
- Tier 2: Mirror, Array, Bevel, Subdivision, Solidify, Boolean, Decimate, Displace, Shrinkwrap,
  Armature, Simple Deform: add, remove, reorder, edit, enable/disable, apply, apply then scrub. GN
  modifier inputs; group edits (nodes, links, socket values, moves, labels, frames, interface);
  shader trees; groups used in several places. Material slots, settings, linked vs object slots.
- Tier 3: weight strokes; vertex groups create, assign, remove, rename, lock, mirror; UV move, pin,
  unwrap, seams; colour attributes; seams, sharp, creases, custom normals. Texture paint as tile deltas
  on several image sizes, tile boundaries, stroke undo, reload from disk, packed and external images,
  its own memory cap (holds, shown in the UI, eviction works and is reported).
- Tier 4: keyframe insert, move, delete, handles, interpolation, F-curve modifiers, shape key values
  and data, armature edit and pose bones, NLA strips, frame range. Hard rule, tested: scrubbing never
  changes the frame, playback, selection or mode (unless Preserve Modes), including with playback
  running, an animated scene, and while the timeline is scrubbed.
- Tier 5: curves (points, handles, bevel, extrude), text (body, font settings), metaballs, lattices,
  Grease Pencil (layers, strokes), lights, cameras, empties; generic adapter allowlist tested row by
  row.
- Tier 6: scene, world, render settings, collection structure: only what the generic adapter covers
  cheaply, each with a test.

## Tier 0 gate plan (next after Tier 1)

The sculpt rows get the same machinery Tier 1 has, sculpt tests counting only for sculpt rows:
- OPS: real operators. Headless: mask flood fill and filters, face set operators, hide, Vertex Paint
  fills, voxel remesh, decimate, Edit Mode visits, join and separate. Real interface: brush strokes
  by mouse (round_c), a real Vertex Paint stroke and a real remesh (both missing so far).
- ORACLE: tests/oracle.py's mesh reader at every position, and a replay of the same operations in a
  fresh scene without Hindsight.
- STEPS: one stroke one step, quick and slow, through the real timer (operator end and settle).
- UNDO: a real-interface scenario like s_t1_gate (Ctrl+Z and Ctrl+Shift+Z after strokes, mixed with
  scrubbing, across Sculpt / Vertex Paint / Edit Mode, Global Undo off, undo steps min and max, Edit >
  Undo History, File > Revert).
- RELOAD: sidecar, embed, both, rename, copy-paste, append and link, Shift+D and Alt+D, older formats,
  for sculpt histories (a test_bl_t0_reload like test_bl_t1_reload).
- PROP: Hypothesis over strokes on several objects, masks, face sets, hiding, colours, remesh, Edit
  Mode visits, joins and separates, jumps and emulated Blender undo; every position exact.
- MUT: the recorder (runtime._observe, _apply_to_state, _write_state, commit) in tools/mutation_bl.py.
- FAULT, FAULT.CORRUPT: the recorder's guard, damaged files (round_d, test_core_format) tagged.
- PERF, SOAK: per-stroke cost on small, medium and heavy meshes against budgets; s_soak with a sculpt
  focus.
- WARN, VISUAL: Multires, Dyntopo, 2M vertices, the Alt+D warning; ui_tour shots, looked at.

## Plan

1. Evidence tooling: machine-readable results from every runner, row and gate tags on tests,
   `tools/make_evidence.py`, TEST_EVIDENCE.md, MANUAL_TESTS.md; COVERAGE.md statuses reset to the
   gate (`supported`, `partial`, `not covered`).
2. Shared gate machinery: independent oracle module (tests/oracle.py), replay oracle, Hypothesis in a
   sandboxed library folder, adapter mutation testing inside Blender, per-adapter fault isolation,
   undo-matrix and reload-variant helpers, soak runner, test models per row.
3. Gate sculpting's rows (tier 0), then Tier 1 (with D14 and D15), hostile review, full regression
   pass. Then Tier 2 from the parked branch, and so on.
