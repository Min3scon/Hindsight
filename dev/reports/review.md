> 2026-11-18. An agent's report as delivered, unchanged below this line. Review report only: the findings are fixed or listed in DEVELOPMENT.md (section d).

# Hostile review: Hindsight changes e6a8248..HEAD (2026-11-18)

Scripts in this folder; run with `/home/user/venvs/bpy53/bin/python <script>` (all also run on bpy42).
Counts: S1 2 (one new, one older but in scope of question 6), S2 1, S3 2, S4 3.
Checked and found sound (no finding): runtime._groups/_position cache (fuzzed 150 random ops: strokes,
scrubs, collection filtering, delete earlier, step cap; r7_groups_cache_fuzz.py, 0 mismatches; join/unmerge
and goto never change entries without a new seq), meshio.fingerprint equals the old digest (r3, 4.2 and 5.3),
TOPO `before` aliasing (r10: every position exact after topology change + strokes + scrubbing), movie._SHOWN
skip logic, store.enforce_caps cursor rule, API names on 4.2/5.0/5.1/5.2/5.3 (r4, r8).

## S1-1 Crash recovery: a DROP larger than the saved cursor leaves a history that doesn't match the mesh
- core/persist.py:384-397 (replay, DROP), with bl/runtime.py:752-786 (_journal_drop / _enforce).
- Case: file saved with a history at cursor c (e.g. scrubbed back to 2 of 5, or simply 5 of 5). Later the
  step cap or the memory limit drops n > c oldest steps (live cursor is higher, so D64 allows it; the memory
  limit also does it to a history other than the one being recorded). Crash, reopen, Interrupted session
  recovery: replay drop_oldest(n) clamps the cursor to 0, so the history says the mesh is the state before
  the first kept step, while the .blend holds the state of the save. check_outside_changes doesn't notice
  (fingerprint is that of the save). Scrubbing applies deltas to the wrong base: shapes that never existed.
  - r1_replay_drop_past_saved.py: saved [1..5] cursor 5, six more strokes with cap 5 -> recovered [7..11]
    cursor 0 (= state after 6), file mesh = state after 5; live mesh at that cursor != saved mesh.
  - r1b_replay_scrubbed_back.py case A: saved at cursor 2, scrub to 5, one stroke with cap 3 -> recovered
    [4,5,6] cursor 0; mesh at that cursor != file mesh.
  (Before today it was wrong differently: [1..5,11].)
- Fix: in replay keep a per-history `skew`; on DROP drop only min(n, h.cursor) entries and add the rest to
  skew; add skew to later STEP `at` and TRUNC `keep`. The recovered history is longer than the cap but
  contains the saved state; the next _enforce trims it. (Or: stop replaying that history and add a load note.)

## S1-2 (older code, same question) Crash recovery after scrubbing back past the save and recording
- core/persist.py:365-383 (STEP/TRUNC keep the saved cursor, only clamped).
- Case: save at 5/5, scrub back to 2, sculpt (TRUNC keep=2, STEP at 2), crash, recover: entries [1,2,6]
  (= live) but cursor 2 while the file mesh is the state after 5. r1b case B: mesh at recovered cursor !=
  file mesh. `recovered_cursor` is written but never used.
- Fix: same mechanism: when a TRUNC keep is below the history's (saved) cursor, the saved state is no longer
  on the history: stop replaying that history (keep what came before) and say so in load_notes.

## S2-1 Movie Mode camera crops models with modifiers (Mirror, Multires, Subdivision, shape keys, Armature)
- bl/camrig.py:268-289 (_measure reads `ob.data.attributes["position"]`, the base mesh).
- Case: half-model sculpt with a Mirror modifier (the usual symmetric workflow): rendered model spans
  camera-view x -0.13..0.72; the mirrored half is cut off (r2_camrig_modifiers_rename.py (a)). Multires
  displacement the same.
- Fix: also merge the extents of the 8 corners of `ob.bound_box` (evaluated, includes modifiers) at the
  current transform; or measure `ob.evaluated_get(depsgraph)` positions for the current shape.

## S3-1 The user's camera isn't given back if it was renamed while Movie Mode was on
- bl/camrig.py:122-143 (movie_prev_camera stores a name).
- Case: scene camera "MyCam", Movie Mode on, rename it "Renamed", Movie Mode off -> scene.camera is None
  (r2 (b)); the user's renders then fail with "no camera".
- Fix: remember the object itself (PointerProperty(type=bpy.types.Object) on scene.hindsight), name only as
  fallback.

## S3-2 A light that doesn't render counts as "the scene has its own light"
- bl/camrig.py:55-59 (scene_has_own_light checks ob.hide_render only).
- Case: the user's old lamp sits in a collection excluded from the view layer, or a collection disabled for
  renders: no rig lights are added and the timelapse renders unlit (r9_excluded_light.py: [] both cases).
- Fix: iterate `view_layer.objects` (or depsgraph object instances) and use `ob.visible_get()`-style
  checks plus collection hide_render, e.g. lights in `depsgraph.object_instances` of a render depsgraph;
  minimal: `ob.name in view_layer.objects and not ob.hide_render and all parents' collections renderable`.

## S4-1 scrubber._DIGITS is never pruned (bl/scrubber.py:366)
Keyed by region pointer; regions come and go (areas split/closed, file loads) and pointers get reused, so a
stale digit count can shift the hit test by a few px until the next draw. Tiny leak. Fix: clear on load_post
or keep only the last N.

## S4-2 log.error_once has no cap (bl/log.py:41) - suspected, not reproduced
Keys include str(e) (runtime.tick: ("camrig_tick", str(e)), ("datarec_tick", str(e)), ...): an error whose
text varies (an object name, a number) adds a key and prints a traceback on every tick, forever. Fix: cap
the set but on overflow stop logging (or key on type(e) + site only).

## S4-3 Memory limit: full rescan on every stroke when nothing more can be dropped - suspected, not reproduced
bl/runtime.py:762-790 + core/store.py:205-224. With D64 a history scrubbed back keeps its steps at/after the
cursor; when everything droppable is gone and the estimate is still over the limit, `over_limit` stays true
and every commit does the full pass (what D65 removed). Fix: remember the total after a pass that couldn't
reach the target and skip full passes until the total grows past it.

## Out of scope / not reproduced
- Vertex Paint on 5.3 (D71): headless mode_set adds the colour layer at once on 5.2 and 5.3 (r6_vpaint53.py),
  so the late-layer path can't be exercised here. Code reads fine.
- Morph on 5.3 (D75): brushes default to use_unified_size=True on 5.3 (r5_morph53.py); with a brush that has
  it off, the tool header still edits the unified size, which then has no effect (S4, not listed above).
