> 2026-11-18. An agent's report as delivered, unchanged below this line. Report only: it changed no code (analysis and design).

# LIMITS-B report: "Not recorded in this version", audit and recording design

Agent LIMITS-B. Audit and design only: no repository file was changed, nothing was committed.
Worktree `/home/user/Hindsight/.claude/worktrees/agent-acaba07647b1798d6` at 66fcdf8. <!-- precheck: allow -->
Everything below was measured headless with the `bpy` modules in /home/user/venvs (4.2.23, 5.0.1, <!-- precheck: allow -->
5.1.2, 5.2.2, 5.3.0). There is no Blender binary, no window and no GPU here: no real brush stroke,
no UI drag, no screenshot was possible.

Folder S/agents/LIMITS-B/ (S = /tmp/claude-0/-home-user-Hindsight/4a339202-445d-5357-a8ee-013fb190a1d0/scratchpad): <!-- precheck: allow -->

| File | What |
|---|---|
| probe_limits.py | 80 cases: makes each change with Record > Everything on (every object and the scene recorded, every data kind on, `FORCE_SCOPE`/`FORCE_KINDS` like the tier 1 tests), runs `runtime._tick_once()` and `datarec.finish_operation(label)` (what the timer does when an operator ends), leaves the mode if any, then jumps the global timeline back and forward and compares an independent read of the data. Two controls (tier 1 move, Edit Mode step) must pass. `PROBE_DEBUG=1` prints the values that differ, `PROBE_STEPS=1` the recorded steps. |
| out42.txt, out50.txt, out51.txt, out52.txt, out53.txt | Its output on each Blender. Command: `/home/user/venvs/bpyNN/bin/python probe_limits.py <worktree>` | <!-- precheck: allow -->
| compare.py | `python3 compare.py` prints the rows that differ between versions; `compare.py all` the whole table. |
| gncheck.py, gn3.py, gn4.py, gn5.py | Where Geometry Nodes modifier inputs live per version. |
| seamcheck.py, seamcheck2.py, apicheck.py | Seam attributes, embedded node tree pointers, list reorder operators, custom normals storage, `modifiers.move`. Output in api42/50/53.txt, seam42/53.txt. |
| depsgraph_probe.py | Which IDs a depsgraph update names for each kind of change (depsgraph42/53.txt). |
| bench_texpaint.py, bytecheck.py | Image pixel read/write, tile diff, lossless 8-bit path (bench42/53.txt). |
| bench_capture.py | Capture cost of the planned adapters with Hindsight's own `struct_values` (capture53.txt). |
| bench_weights.py | Cost of `meshio.read_extras` (weights, shape keys) at 100k vertices (weights42/53.txt). |

## 1. The parked tier 2 code

COVERAGE.md says "code parked on branch tier2" for proc.modifiers, proc.nodetrees, proc.materials.
HANDOFF.md line 287 names it: local branch `tier2`, worktree `.tmp/wt3b`, commit 2ac7e45, and
`.tmp/tier2_wip.patch`. None of it exists any more:

- `git branch -a`: no `tier2` (only `claude/lucid-brahmagupta-9gsdsn`, its remote and the agent worktree branches).
- `git ls-remote origin`: HEAD, `claude/lucid-brahmagupta-9gsdsn`, `refs/pull/1/head` (f0d94ef), `refs/pull/2/head` (fbf5f83), tag `v1.0.0-beta.2`. Both pull heads are ordinary commits already in this history.
- `git cat-file -t 2ac7e45`: "Not a valid object name".
- `git stash list`: empty.
- `git fsck --lost-found`: two dangling commits (f9e08b8 "WIP on claude/lucid-brahmagupta-9gsdsn" and 1da6f89 "memwork-check-13930", both from today, store/meshio/persist work) and three dangling trees whose adapter files match the current ones. No tier 2 code.
- `git log --all -G "class [A-Za-z]*(Modifier|Material|NodeTree|Node)[A-Za-z]*Adapter"`: no match in any commit.
- `/home/user/Hindsight/.tmp` holds only `migrate`, `old_builds`, `results`; no `wt3b`, no patch. `find / -name tier2_wip.patch`: nothing. <!-- precheck: allow -->

Verdict: the tier 2 code is lost. Only its description survives (TEST_AUDIT.md line 112: "object
adapter: stack, settings, GN inputs, slots; node tree adapter: nodes, links, values, ramps, curves,
zone items, group interface; derived tracking of materials and groups; node editor ids"). D14 and
D15 (fixes said to be on that branch) were ported since: `datarec._mesh_step` records Object Mode
mesh changes and `adapters.animated_paths` skips animated values. COVERAGE.md's note should change
to "not written (earlier code lost)". Proposed text in section 9.

## 2. Verdicts

"Step" is the number of steps at the change and at leaving the mode. "Back" and "Fwd": the global
timeline moved back past the change, then forward again, gives exactly the earlier, then the later
value. The result is the same on 4.2, 5.0, 5.1, 5.2 and 5.3 unless the last column says otherwise.
Evidence for every row: probe_limits.py, out<version>.txt.

| Item | Sub-case | How the change was made | Step | Back | Fwd | Recorded today | Version notes |
|---|---|---|---|---|---|---|---|
| control | move a mesh object | transform.translate | 1 | yes | yes | yes | |
| control | Edit Mode translate | transform.translate | 1 | yes | yes | yes | |
| 1 Modifiers | add | object.modifier_add | 0 | | | no | |
| 1 | remove | object.modifier_remove | 0 | | | no | |
| 1 | reorder | object.modifier_move_to_index | 0 | | | no | |
| 1 | rename | property (UI field) | 0 | | | no | |
| 1 | change a setting (levels) | property | 0 | | | no | |
| 1 | show in viewport | property | 0 | | | no | |
| 1 | apply | object.modifier_apply | 1 | NO | yes | mesh only: the step is a topology step; going back gives the old mesh without the modifier (the modifier is lost) | |
| 1 | delete an object with a Geometry Nodes modifier, bring it back | object.delete, global timeline | 1 | 5.2, 5.3: NO | yes | the object comes back, its GN input values don't on 5.2+ | 4.2 to 5.1: yes |
| 2 Geometry Nodes | modifier input value | property | 0 | | | no | 4.2 to 5.1 ID property `m["Socket_1"]`; 5.2+ RNA `m.properties.inputs.Socket_1.value` |
| 2 | add a node to the group | nodes.new | 0 | | | no | |
| 2 | link nodes | links.new | 0 | | | no | |
| 2 | node socket value | default_value | 0 | | | no | |
| 3 Materials | add a slot | object.material_slot_add | 0 | | | no | |
| 3 | remove a slot | object.material_slot_remove | 0 | | | no | |
| 3 | change a slot's material | property | 0 | | | no | |
| 3 | material settings (roughness, colour) | property | 0 | | | no | |
| 3 | node tree: socket value | default_value | 0 | | | no | |
| 3 | node tree: add a node and link | nodes.new, links.new | 0 | | | no | |
| 3 | assign faces to a slot | Edit Mode, object.material_slot_assign | 1 | yes | yes | yes: `material_index` is a mesh channel | |
| 4 Weight paint | stroke in Weight Paint mode | weights set as a stroke would (brushes crash headless) | 0, then 1 at leaving the mode | yes | yes | yes, but one step per visit to Weight Paint mode, labelled with the operator that left the mode | |
| 4 | Weight Paint operator (Smooth) | object.vertex_group_smooth in Weight Paint mode | 0, then 1 at leaving | yes | yes | as above | |
| 4 | weights changed in Object Mode | script | 1 | yes | yes | yes | |
| 4 | assign in Edit Mode | object.vertex_group_assign | 1 | yes | yes | yes | |
| 4 | add a vertex group | object.vertex_group_add | 1 | NO | yes | a step is made, going back leaves the group | |
| 4 | remove a vertex group | object.vertex_group_remove | 1 | NO | yes | going back doesn't bring the group or its weights back | |
| 4 | rename a vertex group | property | 1 | NO | yes | going back leaves the new name, weights end up on no group | |
| 4 | lock a vertex group | property | 0 | | | no | |
| 5 UVs | unwrap | Edit Mode, uv.unwrap | 1 | yes | yes | yes | |
| 5 | smart UV project | Edit Mode, uv.smart_project | 1 | yes | yes | yes | |
| 5 | UVs changed in Object Mode | script (UV editor result) | 1 | yes | yes | yes | |
| 5 | mark seams | Edit Mode, mesh.mark_seam | 1 | yes | yes | yes | 4.2 `.uv_seam`, 5.x `uv_seam` attribute |
| 5 | mark sharp | Edit Mode, mesh.mark_sharp | 1 | yes | yes | yes | |
| 5 | edge crease | Edit Mode, transform.edge_crease | 1 | yes | yes | yes | |
| 5 | seams changed in Object Mode | script | 1 | yes | yes | yes | |
| 5 | add a UV map | mesh.uv_texture_add | 1 | 5.1+: NO | 4.2, 5.0: NO | data yes; which map is active and which renders isn't recorded | 4.2, 5.0: back yes, forward NO |
| 5 | remove a UV map | mesh.uv_texture_remove | 1 | NO | yes | the map comes back, not as the active one | |
| 5 | rename a UV map | property | 1 | 5.1+: NO | yes | 5.1+: the old name comes back but the map is no longer active or rendered | 4.2, 5.0: yes |
| 5 | set the active / render map | property | 0 | | | no | |
| 5 | custom normals | Edit Mode, mesh.set_normals_from_faces | 0 | | | no | |
| 5 | custom normals in Object Mode | normals_split_custom_set_from_vertices | 1 | NO | yes | only the sharp edges Blender marks with it; the normals stay | 5.0+: `custom_normal` CORNER INT16_2D, a type Hindsight doesn't read; 4.2: not an attribute at all |
| 6 Texture paint | stroke in Texture Paint mode | pixels set as a stroke would | 0 | | | no | |
| 6 | image changed in Object Mode | script, bake | 0 | | | no | |
| 7 Animation | insert a keyframe | keyframe_insert | 1 | NO | yes | a step is made (the property becomes animated and leaves the recorded values), going back changes nothing | |
| 7 | move a keyframe | graph editor values | 0 | | | no | |
| 7 | change interpolation | property | 0 | | | no | |
| 7 | delete a keyframe | keyframe_delete (anim.keyframe_delete_v3d needs a 3D view) | 0 | | | no | |
| 7 | push down to NLA | API | 0 | | | no | |
| 7 | changing the frame | scene.frame_set | 0 | | | correctly not a step | |
| 8 Shape keys | add | object.shape_key_add | 1 | NO | yes | positions yes (channel `sk:002:Key 2`), the key itself stays when going back | |
| 8 | remove | object.shape_key_remove | 1 | NO | yes | the key doesn't come back | |
| 8 | rename | property | 1 | NO | yes | name doesn't come back | |
| 8 | value slider | property | 0 | | | no | |
| 8 | settings (mute, slider max) | property | 0 | | | no | |
| 8 | edit a key's positions in Edit Mode | transform.translate | 1 | yes | yes | yes | |
| 8 | positions changed in Object Mode | script | 1 | yes | yes | yes | |
| 9 Armatures | move the armature object | transform.translate | 1 | yes | yes | yes (object transform, tier 1) | |
| 9 | pose a bone | Pose Mode, transform.translate | 0 | | | no | |
| 9 | pose bone values | property in Pose Mode | 0 | | | no | |
| 9 | move edit bones | armature Edit Mode, transform.translate | 0 | | | no | |
| 9 | bone setting (deform) | property | 0 | | | no | |
| 10 Other types | curve, light, camera: move the object | transform.translate | 1 | yes | yes | yes (tier 1) | |
| 10 | add a light, delete a camera | object.light_add, object.delete | 1 | yes | yes | yes (tier 1, while the data still exists) | |
| 10 | curve bevel depth | property | 0 | | | no | |
| 10 | curve control points | curve Edit Mode, transform.translate | 0 | | | no | |
| 10 | text body, text size | property | 0 | | | no | |
| 10 | light power and colour | property | 0 | | | no | |
| 10 | camera focal length | property | 0 | | | no | |
| 10 | empty display size | property | 0 | | | no | |
| 11 Scene | render resolution, frame range, render engine, world colour, unit scale, view transform | property | 0 | | | no | |

Per item:

1. Modifiers: true. Nothing about the stack is recorded. Two defects follow from it in tier 1 rows
   that are claimed `partial` today: D-a and D-b below.
2. Geometry Nodes inputs and node trees: true.
3. Materials: true, except which faces use which slot (the `material_index` channel), which is part
   of Edit Mode steps.
4. Weight paint: partly false. Weights are recorded (Edit Mode steps, Object Mode changes, and one
   step per visit to Weight Paint mode). Adding, removing, renaming and locking vertex groups are not,
   and three of these make a step that going back doesn't undo (D-c).
5. UVs: mostly false. UV coordinates, seams, sharp edges and creases are recorded and come back
   exactly. Not recorded: which UV map is active and which renders, and custom normals (D-d).
6. Texture paint: true.
7. Animation: true. Inserting the first key on a recorded property makes an empty step (D-e).
8. Shape keys: partly false. Positions are recorded; the list (add, remove, rename, order), values
   and settings are not, and add, remove and rename make steps that going back doesn't undo (D-c).
9. Armatures: true for bones and poses. The armature object's own transform is tier 1 (recorded).
10. Other object types: true for their data. Object-level things (transform, parent, visibility,
    add and delete) are tier 1 and recorded for every object type.
11. Scene settings: true.

None of the partly recorded parts may be claimed on the site: their COVERAGE rows haven't passed
any gate.

### Defects found in rows the site already claims (tier 0 and 1)

- D-a (obj.addremove, Blender 5.2 and 5.3): an object deleted and brought back by the global history
  loses its Geometry Nodes input values. `adapters._id_props(m)` reads `m.keys()`, which raises
  TypeError on 5.2+ because inputs moved from ID properties to RNA
  (`m.properties.inputs.<identifier>.value`, `.type`, `.attribute_name`; gn5.py). The TypeError is
  caught, so the body records nothing and `_set_id_props` writes nothing. COVERAGE claims "mesh and
  empty objects come back with their settings, constraints, modifiers". Probe case "delete an object
  with a GN modifier": NO on 5.2.2 and 5.3.0, yes on 4.2, 5.0, 5.1. Belongs to whoever owns
  adapters.py; the fix is the `gn_inputs()` helper in section 4.2, used by `object_body` and
  `_recreate_object`.
- D-b (sculpt.topology / obj.editsteps): going back over Apply Modifier gives the mesh from before
  Apply without the modifier, so the modifier's effect is lost. Fixed by the modifier adapter (the
  Apply step then holds both the topology snapshot and the stack change). Until then a sidebar note
  is owed (gate item 11).
- D-c (obj.editsteps, D43): adding, removing or renaming a vertex group or a shape key, and adding,
  removing or renaming a UV map, make a step whose going back leaves the list as it is now. COVERAGE
  says "adding or removing a vertex group or a shape key isn't recorded yet" but not that a step is
  made, and says nothing about renames or UV maps.
- D-d: custom normals on 5.0+ live in `custom_normal` (CORNER, INT16_2D). `meshio._recordable` drops
  it (INT16_2D isn't in `CHANNEL_TYPES`), so going back over Set Custom Normals restores the sharp
  edges Blender marked and keeps the new normals: a state that never existed.
- D-e (obj.transforms): the first keyframe on a recorded property (location) makes a step: the
  property disappears from what the RNA adapter reads (`animated_paths`), which `_diff` records as a
  change to MISSING. Going back writes nothing (MISSING is skipped). A position on the timeline that
  does nothing.
- D-f (Weight Paint mode): strokes aren't steps. `datarec.commit` only reads the mesh of an object in
  Object Mode (`_mesh_step`), and it discards `_MESH_DIRTY` for an object in another mode. Headless,
  the whole visit became one step when the probe left the mode. In a real window the visit is caught
  only if leaving the mode sends a geometry update; if it doesn't, the weights fall into the next
  Object Mode operation's step. Needs a real window to confirm.

## 3. What Blender sends and allows (measured)

Depsgraph updates (depsgraph42/53.txt, same on 4.2 and 5.3):

| Change | Update names |
|---|---|
| modifier setting, vertex group lock, UV map active index, shape key value | Object, Mesh, Key |
| modifier add, vertex group rename | Object, Mesh, Key, Scene, other objects |
| material slot material | Material, its node tree, Object, Mesh |
| material roughness, node socket value (material in use) | Material, node tree, Object, Mesh |
| node added (material in use) | only the embedded ShaderNodeTree (its pointer equals `material.node_tree.as_pointer()`, seamcheck2.py) |
| a material not used by any object | NO UPDATE |
| image pixels | NO UPDATE |
| light energy | Light, Object (5.3 also the light's node tree) |
| pose bone location | Armature data, Object |
| keyframe insert | Action, Object, Mesh, Key, Scene |
| render resolution, frame_end | Scene |
| world colour | World (5.3 also its node tree) |

So: datarec.on_update today only marks a datablock dirty when its own uid is updated. A node added
to a material reaches the object only through an operator ending (finish_operation compares every
tracked datablock). A UI field edit (settle path) on a node tree, material, image, light or world is
missed unless the seam maps those IDs to the recorded object (section 5, `watch`). Image pixels never
send an update: texture paint must be read when a paint stroke ends.

API facts (apicheck.py, seamcheck2.py, gn*.py):
- `ObjectModifiers.move(from, to)` exists on 4.2 to 5.3.
- No API reorders vertex groups or shape keys; `object.vertex_group_move` and `object.shape_key_move`
  run headless under `temp_override(object=ob, active_object=ob)` on 4.2 and 5.3.
- Geometry Nodes inputs: ID properties on the modifier up to 5.1 (`Socket_N`, `Socket_N_use_attribute`,
  `Socket_N_attribute_name`); from 5.2, `m.properties.inputs.Socket_N` with `value`, `type`,
  `attribute_name`, ID properties refused.
- Custom normals: 4.2 not an attribute (no exact read or write: `normals_split_custom_set` re-encodes);
  5.0+ `custom_normal` INT16_2D, `foreach_get/foreach_set("value")` on int16 works.
- `Material.use_nodes` warns on 5.3 ("expected to be removed in Blender 6.0").
- Grease Pencil: 4.2 has both GPENCIL (legacy) and GREASEPENCIL; 5.0+ only GREASEPENCIL (v3).
- Operators that ran headless: modifier_add/remove/move_to_index/apply, material_slot_add/remove/assign,
  vertex_group_add/remove/assign/smooth, vertex_group_move, shape_key_add/remove/move, uv_texture_add/
  remove, uv.unwrap, uv.smart_project, mark_seam, mark_sharp, transform.edge_crease,
  set_normals_from_faces, transform.translate in Pose Mode and armature and curve Edit Mode,
  object.light_add/camera_add/text_add/armature_add/delete, curve.primitive_bezier_curve_add.
  Not headless: anim.keyframe_delete_v3d (poll needs a 3D view), every paint brush (crash, known).

Costs (capture53.txt, weights42/53.txt, bench42/53.txt; min/median of 20 or of 5):

| What | 4.2 | 5.3 |
|---|---|---|
| 10 modifiers, struct_values each | | 0.06 ms |
| 60-node shader tree: nodes, socket values, links | | 1.35 ms |
| material settings | | 0.014 ms |
| 20 shape keys' settings | | 0.07 ms |
| 64-bone pose, per-bone attributes / foreach_get matrix_basis / struct_values per bone | | 0.08 / 0.005 / 2.8 ms |
| light, camera, curve data, scene, render settings | | 0.01 to 0.025 ms each |
| `meshio.read_extras`, 100k vertices, 3 groups | 70 ms | 111 ms |
| the same with 21 shape keys | 177 ms | 308 ms |
| 2K byte image: get / set / banded to uint8 / changed tiles | 16 / 27 / 19 / 5 ms | 10 / 36 / 19 / 5 ms |
| 2K float image: get / set / changed tiles | 10 / 11 / 17 ms | 10 / 12 / 12 ms |
| 4K byte image: get / set / changed tiles | 53 / 101 / 29 ms | 45 / 86 / 37 ms |
| 4K float image: get / set / changed tiles | 27 / 26 / 64 ms | 30 / 26 / 55 ms |

Texture paint memory per image (bench): a float32 read buffer (the only way to read `image.pixels`
is the whole image as float32: 64 MiB at 2K, 256 MiB at 4K) plus a baseline (8-bit images as uint8:
16 MiB at 2K, 64 MiB at 4K; float images as float32: 64 / 256 MiB). An 8-bit image read as float,
converted with `(x * 255 + 0.5)` to uint8 and written back as `u8 / 255` gives back the same floats
bit for bit on 4.2 and 5.3 (bytecheck.py), so uint8 is a lossless record of 8-bit images. A
synthetic stroke over 6% of a 2K image touched 67 of 1024 tiles of 64x64: 2.1 MiB raw (before and
after, 8-bit), 8.4 MiB for float; zlib level 1 shrank it to 0.02 / 0.14 MiB, but that stroke was flat
colour, so real brush strokes will compress far less; plan with the raw size. The unbanded
`np.round(x * 255)` conversion cost 52 to 203 ms and pushed VmHWM up 384 MiB (2K) and 1.2 GiB (4K)
through temporaries; the banded conversion (64 rows at a time, one reused band buffer) is 19 ms at
2K and adds one band of memory.

## 4. Design per item

Common rules for every adapter below:
- Opt-in: nothing runs unless Preferences > Record is Everything and the object has Record everything
  on (`datarec.tracked_uids()` is empty otherwise, so the cost when off is one empty-set check per
  update and per tick, as today). Each family has its own data kind (section 5), so it can be switched
  off; a kind that is off is not read, and `_diff` already treats a value that isn't read as no change.
- Values are JSON values through `struct_values` / `set_struct_values` and `normalise` / `same`, so
  floats are exact and ID pointers are Hindsight ids (`idmap.ref_of`, `idmap.find`), which survive
  renames; a reference to a deleted datablock goes through `_note_ref` / `MISSING_REFS` (D37) like
  constraint targets.
- Animated or driven values: each adapter passes `animated_paths(id)` of the datablock that owns the
  value (Object, Key, Material, node tree, Light, Camera, Scene, World each have their own
  `animation_data`) and records them as `ANIMATED`, as constraints do now.
- Records: paths are split so a change records only what changed: a list layout path ("modifiers")
  holding `[[type, name], ...]` and one path per item ("mod:<name>") holding its values. Today's
  constraint record stores the whole stack per change; new adapters must not.
- Derived datablocks (materials, node groups, shape Key, images, actions, armature, light, camera,
  curve data) are recorded in the history of the recorded object that uses them, as paths of that
  object (`@<uid>:<path>`), with the owner rule of shared meshes (D39): the first recorded user by
  name owns it while it uses it, other users record only their own object data and a note says
  which object records it. ADAPTERS.md says such datablocks get their own history. I recommend the
  owner rule instead: per-object scrubbing then undoes a material or texture change made while
  working on that object, it needs no change to the history engine, and the global timeline orders
  everything as now. This is a decision for the owner before work starts (proposed DECISIONS text in
  section 9).
- Write order inside one jump: data records are written before mesh channels going forward and after
  them going back (core/history.py `_cross`). So list adapters (vertex groups, shape keys, UV maps)
  create a group, key or map before the mesh channel writes its values, and remove it after.

### 4.1 Mesh data lists: vertex groups, shape keys, UV maps, custom normals; weight paint per stroke
Rows paint.weights, paint.uvs, anim.shapekeys. Module `adapters_meshdata.py`, adapter id `meshdata`,
Object of type MESH. Highest value, smallest effort: the data is already recorded (mesh channels
`vg:`, `sk:`, UV attributes, seams, sharp edges, creases), only the lists and their settings are not,
and D-c makes wrong states today.

Capture (all cheap, under 0.1 ms):
- `vgroups`: `[[name, lock_weight], ...]` in order; `vgroup_active`: `vertex_groups.active_index`.
- `uvmaps`: `[[name, active, active_render], ...]` in `uv_layers` order (data stays in the channel).
- `colors`: `[active_color_name, default_color_name]` (Blender 5.0 forgets them, see meshio.write_channel).
- `shapekeys`: `None` without a Key, else `{"use_relative", "reference": name, "eval_time"}`;
  `sk:<name>`: `[value, mute, slider_min, slider_max, relative_key name, vertex_group, interpolation,
  lock_shape]` per key block (value as `ANIMATED` when the Key animates it); `sk_order`: names in
  order; `shape_active`: `active_shape_key_index`; `show_only_shape_key`, `use_shape_key_edit_mode`.
- `custom_normals` (5.0+ only): nothing here; the channel is the mesh recorder's (below).

Write:
- vertex groups: remove the groups not wanted (`vertex_groups.remove`), add missing ones
  (`vertex_groups.new(name=...)`), rename by matching the recorded order where only the name differs
  (one group renamed: same position, other name), set `lock_weight`, fix order with
  `object.vertex_group_move` under `temp_override(object=ob, active_object=ob)` (headless OK), then
  `active_index`. The weights come after, from the `vg:` channels (`meshio.write_extras`, by name).
- shape keys: missing key blocks `ob.shape_key_add(name, from_mix=False)`, removed ones
  `ob.shape_key_remove(kb)` (all removed: `ob.shape_key_clear()`); a rename renames; order with
  `object.shape_key_move`; then settings (relative_key by name after all exist). Positions come from
  the `sk:NNN:name` channels. Note: those channel keys include the index, so reordering keys
  renames every channel after it (remove plus add in the mesh step); exact, but large. Keep the key
  format (old histories use it).
- UV maps: create missing ones with `uv_layers.new(name=..., do_init=False)` only if the channel write
  hasn't (a FLOAT2 CORNER attribute is a UV map since 3.5), rename, set `active_index`, `active` and
  `active_render` (5.3 has `uv_layers.active_render`; on 4.2 use the map's `active_render`).

Weight paint per stroke (D-f): today's per-visit step stays the fallback. In `datarec.commit`, the
seam adds `MESH_STEP_MODES` (default `{"OBJECT"}`); this module adds `"WEIGHT_PAINT"` when its kind
`weight_paint` is on, so the operator that ends (`PAINT_OT_weight_paint`, Smooth, Levels, Gradient)
gets its own step with the mesh compared as in Object Mode (in Weight Paint mode the mesh is the real
mesh, no edit layers). Cost per stroke is `read_extras`: 70 to 111 ms at 100k vertices with 3
groups, 177 to 308 ms with 21 shape keys too. So: above `WP_STEPS_MAX = 150_000` vertices one step
per visit, as Edit Mode does above 300k, and the module passes `extras="vg"` so a Weight Paint step
reads only the groups (needs the small `runtime._observe` parameter, section 5). Auto Normalize and
mirror change other groups too, which is why every group is read, not only the active one.

Custom normals (5.0+): add `"INT16_2D": (np.int16, 2, "value")` to `core/types.CHANNEL_TYPES`; the
Rust engine's diff and apply must accept int16 (its dtype table is in `native/`); a V2 build from before format 5 that
meets a delta with that type fails with KeyError (CorruptStep), so a history with such a delta is
written as format 5 (section 5). 4.2 and 4.x: custom normals can't be read or written exactly
through Python; record nothing, show a note on objects with `has_custom_normals`.

Forward and backward are exact by construction: lists from records, values from channels. Risks:
`vertex_group_move` and `shape_key_move` are operators (a jump from a handler must defer them like
topology writes, `IN_HANDLER`); a group used by a modifier (Armature, Mask) by name follows a rename.

### 4.2 Modifiers and Geometry Nodes inputs
Rows proc.modifiers. Module `adapters_modifiers.py`, adapter id `modifiers`, Object (any type with
`modifiers`).

Capture: `modifiers`: `[[type, name], ...]`; `mod:<name>`: `{"values": struct_values(m, SKIP, anim=...),
"gn": gn_inputs(m), "nested": {...}}`. SKIP is `_UI_PROPS` minus `show_in_editmode` and `show_on_cage`
(they change the result in Edit Mode; today they are skipped as UI state) plus `execution_time`,
`persistent_uid`, `is_override_data`, the `open_*_panel` flags, `show_group_selector`. Nested structs
that matter: Bevel `custom_profile` (points), Hook (`center`, `matrix_inverse`, vertex indices through
`vertex_indices_set`), Warp/Displace texture refs (refs only). `gn_inputs(m)`:
- 4.2 to 5.1: the modifier's ID properties (`m.keys()`, the existing `_id_props` but keeping the
  `_use_attribute` and `_attribute_name` keys), ID values as refs.
- 5.2+: for each input identifier in `m.node_group.interface.items_tree` with `in_out == "INPUT"`:
  `getattr(m.properties.inputs, ident)` → `[value, type, attribute_name]` (ID values as refs).
  Pick by `hasattr(m, "properties") and hasattr(m.properties, "inputs")`, not by version.
  This helper also fixes D-a when `object_body` and `_recreate_object` use it.

Write: remove modifiers not in the layout, add missing ones (`modifiers.new(name, type)` then
`m.name = name`), reorder with `modifiers.move(i, j)` (never remove-all: that would drop bind data
and caches of the modifiers that stay), then values per changed `mod:` path with `set_struct_values`
(the `node_group` ref first, then GN inputs: ID properties or `properties.inputs.<id>.value/type/
attribute_name`). Apply (`object.modifier_apply`) is already one step with a TOPO snapshot from
`_mesh_step`; with this adapter the same step also holds the stack change, so going back gives
the old mesh and the modifier (D-b).

Not restorable, with a sidebar note and a COVERAGE limit: physics modifiers (Cloth, Soft Body, Fluid,
Dynamic Paint, Collision, Ocean caches) and Particle System (made through `object.particle_system_add`,
not `modifiers.new`): the layout is recorded, re-adding one is refused and noted. Multires: adding
it pauses sculpt recording already; its subdivision data can't be recreated, so Multires is left out
of the adapter (note). Surface Deform, Mesh Deform, Laplacian Deform, Corrective Smooth bind data:
`is_bound` comes back False after a remove and re-add; note "bind again". Modifiers on curves,
lattices, Grease Pencil (5.x uses the same `modifiers`): handled by the same code, tested with the
types package.

Depsgraph: every modifier change updates the Object, so the settle path works without `watch`.

### 4.3 Materials and node trees
Rows proc.materials, proc.nodetrees. Modules `adapters_materials.py` (slots, material settings) and
`adapters_nodes.py` (node tree capture and write, used by materials, by Geometry Nodes groups, and
later by the world), one owner.

Object part, adapter `matslots`: `slots`: `[[link, material ref], ...]` and `active_material_index`.
Slot count lives on the mesh (`me.materials`) for DATA slots: write appends or pops `me.materials`
to the recorded count, sets `slot.link`, then `slot.material` (refs; a deleted material: MISSING_REFS
note). Face assignment stays the `material_index` channel (already recorded, probe row).

Material part, adapter `material`, paths `@<material uid>:...` on the owner object:
`mat`: `struct_values(material, SKIP)` (SKIP: preview, `use_preview_world`, `paint_active_slot`,
`texture_paint_images`, `texture_paint_slots`, and `use_nodes` on 5.x where it is deprecated);
`gp`: `struct_values(material.grease_pencil)` when present; `tree`: the node tree (below).

Node tree capture (`adapters_nodes.capture_tree(nt)` → dict of paths):
- `nodes`: `[[name, bl_idname, parent name], ...]` sorted by name.
- `node:<name>`: `{"props": struct_values(node, NODE_SKIP), "in": [[identifier, default_value, hide,
  enabled?], ...] for every input with a default value (linked or not: the value comes back when the
  link goes), "out": [[identifier, default_value]] (Value and RGB nodes keep their value on output 0),
  "ramp": color_ramp elements [[position, color, alpha]] plus interpolation/color_mode, "curves":
  mapping curves [[points [[x, y, handle_type]], ...]] plus clip/extend, "items": zone and menu
  items (repeat_items, simulation state_items, foreach items, capture_items, bake_items, menu
  enum_items: [[name, socket_type]]), "location": [x, y], "width", "label", "mute", "hide"}`.
  NODE_SKIP: `select`, `show_options`, `show_preview`, `show_texture`, `dimensions`, `is_active_output`
  (derived), `internal_links`.
- `links`: `[[from node, from socket identifier, to node, to socket identifier, is_muted,
  multi_input_sort_id], ...]` sorted; the order inside a multi-input socket (Join Geometry) is the
  sort id.
- `interface` (groups): `items_tree` as `[[item_type, in_out, socket_type, name, identifier, parent
  panel, default_value, min_value, max_value, subtype, hide_value, ...]]`.
Node location is a user edit Blender's own undo records, so moving a node is a step; it can be
switched off with the kind if users find it noisy (open question for the owner).

Node tree write (`write_tree(nt, values, changed paths)`): nodes not wanted removed; missing ones
`nt.nodes.new(bl_idname)`, renamed to the recorded name (a clash: rename the holder first, like
`_give_name`); zone pairs (`GeometryNodeRepeatInput.pair_with_output(output)`, simulation, foreach)
paired after both exist; interface items rebuilt in order before node values (group input sockets
depend on them); then values; links last: if the recorded list differs, remove all links and add
them again in recorded order (multi-input order follows creation order). Sockets by identifier,
never by index.

Geometry Nodes groups: adapter `nodegroup` records every node group used by the owner's NODES
modifiers, recursively through Group nodes, as `@<group uid>:...`. Shared groups: owner rule.

Watching: the seam's `watch(idb)` returns, for an object, its materials and their embedded node
trees, and node groups reached from its modifiers and from those trees; datarec marks the object
dirty when an update names any of those pointers (a node added sends only the tree's update, see
section 3). Cost of a 60-node tree capture 1.35 ms; a tree is only read when its pointer was updated
or an operator ended with the object in the operation's dirty set.

Hard parts: node types with special structs (Image Texture node: `image` ref and `image_user`;
Script node; Group node `node_tree` ref; frame nodes; reroute; custom node add-ons: record their
struct_values, never fail the adapter on an unknown type, note it). Embedded trees of compositor
(5.x: a node group) and world reuse the same code.

### 4.4 Texture paint
Row paint.texture. Module `adapters_paint.py`, adapter `image`, paths `@<image uid>:...` on the owner
object (the object being painted).

What is recorded: for each image used by the owner's materials (Image Texture nodes; in Single Image
paint mode `tool_settings.image_paint.canvas` too), source GENERATED, FILE or packed, not UDIM
(`len(image.tiles) > 1`: note "UDIM images aren't recorded"), not movies or sequences:
- `img:size`: `[width, height, is_float, channels]`; `img:t:<ty>_<tx>`: one 64x64 tile as a string
  `"z1:" + base64(zlib(bytes))`, 8-bit images as uint8 RGBA, float images as float32 bit patterns.
  A string keeps the record readable by the current JSON record format (a list of 16k numbers per
  tile would be 8 MB of JSON per stroke).

When: at the end of every operator while the owner is in Texture Paint mode (`PAINT_OT_image_paint`
is modal, so `datarec._operation_running()` holds the commit until the stroke ends), and on an
operator end in any mode for images whose `is_dirty` became True since the last read (bake, fill,
Image editor paint). Never per depsgraph update (images send none).

Capture cost and memory: per stroke one full read (`foreach_get` into a reused float32 buffer: 10 ms
at 2K, 30 to 53 ms at 4K), banded conversion to uint8 for 8-bit images (19 ms at 2K), tile compare
against the baseline (5 to 64 ms), compression of the changed tiles. Baseline per image: 16 MiB
(2K 8-bit) to 256 MiB (4K float); the float32 read buffer (64 or 256 MiB) is allocated on entering
Texture Paint mode and freed when leaving. Per stroke a 4K 8-bit image costs about 150 to 250 ms
after the stroke on this machine, measured parts summed; weak machines more. Record: own memory cap
(`texture_memory_mb`, default 512, counted in the global limit), oldest image steps dropped first.
A 4K float image on an 8 GB machine needs 512 MiB just to watch it: the kind defaults to off,
and a note says how much memory an image costs before it is recorded.

Write: patch the recorded tiles into the float32 buffer of the image as it is now (read it once),
`foreach_set` the whole image (27 to 101 ms), `image.update()`; 8-bit tiles as `u8 / 255`
(lossless, bytecheck.py). Size changes (Image > Resize) are a whole-image record. Blender's own image
undo (64x64 tiles too) is independent: after a Hindsight jump the jump is pushed onto Blender's undo
as for other data.

Not possible from Python: reading only the tiles the brush touched (Blender's partial update
tracking isn't exposed), so every stroke reads the whole image. If the owner accepts, a later Rust
helper could compare and compress tiles off the main thread.

### 4.5 Animation
Rows anim.keys, anim.nla. Module `adapters_anim.py`, adapters `animdata` (Object and any ID with
`animation_data` the owner records: Key, Material, node trees, Light, Camera, Scene, World) and
`action` (the Action datablock, `@<uid>:` on the owner).

`animdata` capture: `anim`: `None` or `{"action": ref, "slot": slot identifier (4.4+), "blend_type",
"influence", "extrapolation", "use_nla", "use_tweak_mode"}`; `nla`: `[[track name, mute, solo, lock,
[[strip name, action ref, frame_start, frame_end, action_frame_start, action_frame_end, scale,
repeat, blend_type, extrapolation, influence, use_reverse, mute], ...]], ...]`; `drivers`:
`[[data_path, array_index, type, expression, variables [[name, type, targets [[id ref, data_path,
transform_type, ...]]]]], ...]`.

`action` capture, per F-curve path `fc:<data_path>[<index>]`: `{"group", "extrapolation", "mute",
"hide"?, "keys": {"co": [...], "hl": [...], "hr": [...], "interp": [...], "hlt": [...], "hrt": [...],
"easing": [...], "type": [...], "back", "amplitude", "period"}}` read with
`keyframe_points.foreach_get` (float lists exact in JSON). F-curve modifiers: struct_values per
modifier. 4.2 to 4.3: `action.fcurves`; 4.4+: layered actions, read through
`bpy_extras.anim_utils.action_get_channelbag_for_slot` (already used by `_fcurve_paths`), one path set
per slot. Write: create the F-curve if missing (`channelbag.fcurves.new` / `action.fcurves.new`),
`keyframe_points.clear()`, `add(n)`, set handle types first, then `co`, handles, the rest with
`foreach_set`, then `fcurve.update()` only if it doesn't move auto handles (test: auto handles are
derived from co, so writing them back and calling update gives the same values; must be proved per
version).

Rules: scrubbing never changes `frame_current` or playback (ADAPTERS.md tier 4); `frame_set` is not a
step (probe row: correctly not one today). D-e is fixed by recording the RNA value disappearing and
the animation appearing in one step: the seam's `_diff` must not record "value became animated" as a
change on its own when the animdata adapter recorded the animation in the same commit; simplest is
that the RNA adapter records `ANIMATED` for an animated path (as constraints do) instead of leaving
the path out, so the change reads `[1,2,3] → ANIMATED` and going back writes the number.

Cost: one F-curve of 1000 keys is about 10k floats; diff only F-curves whose data changed (compare
the `co` lists first). Actions update in the depsgraph (section 3).

### 4.6 Shape key values and settings
In 4.1 (`sk:<name>`) with the Key's own `animated_paths`: values driven by drivers or keyed are
recorded as ANIMATED, never as a change. Shape key animation is the animation package's.

### 4.7 Armatures
Row anim.armature. Module `adapters_rig.py`, adapters `pose` (Object of type ARMATURE) and `bones`
(the Armature data, `@<uid>:` on the owner).

`pose`: `pb:<bone>`: `[location, rotation_mode, rotation_quaternion, rotation_euler,
rotation_axis_angle, scale, custom props]`, plus bone constraints with the constraint writer of
adapters.py (`_write_constraints` on `pose.bones[name]`), paths animated by the action recorded as
ANIMATED. Readable in Object and Pose Mode. Cheap: 0.08 ms for 64 bones with plain attributes
(`struct_values` per bone is 2.8 ms; use the explicit attribute list). Write: rotation mode first
(DEPENDENTS), then values; in Pose Mode directly.

`bones`: `bones`: `[[name, parent, use_connect], ...]`; `bone:<name>`: `[head_local, tail_local,
matrix_local, envelope and B-Bone settings, use_deform, inherit flags, collections]`;
`collections`: bone collections `[[name, parent, is_visible, ...]]`. Readable only outside armature
Edit Mode (in Edit Mode `bones` is stale), so capture is blocked in EDIT (seam: `readable()`), and
the armature Edit Mode visit is one step at leaving, like Edit Mode on meshes without step by step.
Write needs armature Edit Mode: `mode_set(EDIT)` under `suspended()`, outside handlers (defer with a
timer like `_write_state` does for topology), set `edit_bones` (create, delete, parent, head, tail,
then roll from `matrix_local` via `EditBone.matrix`), back to the earlier mode. Unknown: whether
head/tail/roll come back bit-exact through `EditBone.matrix` (roll is derived from the matrix): must
be proved on every version before the row may be more than `partial`; if not exact, record roll
from the edit bone at Edit Mode exit through a small `datarec` hook that reads `edit_bones` before
the mode switch (Blender gives no "before mode exit" handler: needs the mode-exit operator check in
the tick, risky).

### 4.8 Other object types
Rows types.lights, types.geometry. Module `adapters_types.py`.
- Empties: add `empty_display_type`, `empty_display_size`, `empty_image_offset`,
  `use_empty_image_alpha`, `empty_image_depth`, `empty_image_side`, `show_empty_image_*` to a new
  Object row list in this module's own adapter `objdisplay` (ALLOWLIST stays adapters.py's), plus
  other display settings of every object (`display_type`, `show_wire`, `show_in_front`, `color`).
- Lights, cameras, light probes, speakers: adapter `objdata`, `@<data uid>:data`:
  `struct_values(ob.data, SKIP)` plus nested `camera.dof`, `camera.background_images` refs,
  light node tree via adapters_nodes. Data shared by linked duplicates: owner rule. 0.01 ms.
- Curves and surfaces: settings as above; geometry `splines`: per spline `[type, use_cyclic_u,
  order, resolution, material_index, ...]` and points with `foreach_get` on `bezier_points`
  (`co`, `handle_left`, `handle_right`, types, `radius`, `tilt`, `weight_softbody`) or `points`
  (`co` with w); stored as float lists per spline path `spl:<i>`. Edit Mode holds an edit copy:
  capture blocked in EDIT, one step per visit at leaving.
- Text: `body`, `body_format` (per-character flags and material index), size and other settings;
  Edit Mode blocked as above.
- Lattice: `points_u/v/w`, `points.foreach_get("co_deform")`; metaball: `elements` struct_values.
- Bringing these objects back after deletion (obj.addremove "other types only while their data still
  exists"): the body gains the data record, and `_recreate_object` makes the data from it
  (`bpy.data.lights.new`, `cameras.new`, `curves.new` + splines). That function is adapters.py's: a
  patch for the seam owner, written by this package.
- Grease Pencil v3, hair Curves, point clouds, volumes: not in this plan. Their data is attribute
  based (Grease Pencil v3 drawings have `attributes`), so a later package could reuse the channel
  machinery per drawing; 4.2 also has legacy GPENCIL. COVERAGE keeps them `not covered` with a note.

### 4.9 Scene settings
Row scene.settings. Module `adapters_scene.py`, adapter `scenesettings` on the Scene (the scene is
already a recorded datablock when Record the scene is on).
Paths: `scene`: `struct_values(scene, SKIP)` with SKIP `frame_current`, `frame_current_final`,
`frame_float`, `frame_preview_*`, `cursor`, `tool_settings`, `sequence_editor`, `keying_sets*`,
`is_nla_tweakmode`, `use_preview_range` (open question); `render`, `unit`, `view`, `display`
(`view_settings`, `display_settings`, `sequencer_colorspace_settings`), `eevee`, `cycles` (add-on
pointer: `struct_values(scene.cycles)` when present), `camera` and `world` refs; `viewlayers`:
`[[name, use, samples, use_pass_*...]]`; `world:@<uid>`: World settings plus its node tree through
adapters_nodes. Collection settings missing from tier 1 (colour tag, render switch, instance offset;
COVERAGE obj.collections limit) belong here too: `coll:<uid>`.
Depsgraph sends Scene and World updates (section 3); a render setting edit is caught by the settle path.

## 5. The shared seam (one agent, before the others start)

Files the seam agent owns and changes: `hindsight/bl/adapters.py`, `hindsight/bl/datarec.py`,
`hindsight/bl/prefs.py` (kind items), `hindsight/bl/ui_panel.py` (kind toggles),
`hindsight/bl/warnings.py` (adapter notes), `hindsight/bl/runtime.py` (two small hooks),
`hindsight/core/persist.py` (format 5), `hindsight/core/history.py` (nothing unless the owner picks
own histories), `tests/test_bl_seam.py`, `tests/families.py` (shared test helpers), COVERAGE text
proposals. After it lands, every other package only adds files.

### 5.1 Adapter contract (adapters.py)

```python
class Adapter:
    id = ""                  # short, unique, stable: it is stored in every record
    id_types: tuple = ()     # bl_rna identifiers handled ("Object", "Scene", ...)
    kinds: tuple = ()        # data kind ids from KINDS this adapter records (one or more)
    rows: tuple = ()         # COVERAGE.md row ids this adapter implements
    format = 4               # registry format a history needs once it holds a record of this adapter
    order = 50               # write order within one jump, lower first (lists before values)
    budget_ms = 2.0
    label = "Change"         # step label when no operator names the change (settle path)

    def handles(self, idb) -> bool                       # as now
    def capture(self, idb) -> dict                       # as now; derived data as "@<uid>:<path>"
    def write(self, idb, values: dict, recorded=None)    # as now
    def changed(self, path, old, new) -> bool            # as now
    def kind_of(self, path) -> str | None                # NEW: which kind a path belongs to
    def watch(self, idb) -> list                         # NEW: other IDs whose updates may change capture()
    def readable(self, idb) -> bool                      # NEW: False while the data is owned by a mode
                                                         #      (curve, text, armature Edit Mode)
    def notes(self, idb) -> list                         # NEW: [(level, text)] limits for the sidebar
    def derived(self, idb) -> list                       # NEW: IDs this adapter records as "@<uid>:" paths


def register(adapter: Adapter) -> None:
    """Add an adapter. Refuses (ValueError) a duplicate id or a kind not in KINDS."""

FAMILIES = ("adapters_meshdata", "adapters_modifiers", "adapters_materials", "adapters_nodes",
            "adapters_paint", "adapters_anim", "adapters_rig", "adapters_types", "adapters_scene")

def load_families() -> list:
    """Import each family module that exists (each calls register() at import). A module that
    isn't written yet is skipped; one that raises is switched off through guard
    ("adapter:<module>") with one log line. Returns the loaded module names."""
```

`ADAPTERS` stays the dict `{id: adapter}` and the three existing adapters register through
`register()`. `kind_of(adapter_id, path)` delegates to the adapter (`rna`, `object`, `scene` keep
their current answers). `hindsight/__init__.register()` calls `adapters.load_families()` before the
preferences class is registered (it is in `hindsight/__init__.py`, a seam-owned edit).

### 5.2 Kinds (prefs.py, ui_panel.py)
`KINDS` gets every planned kind now, appended in this exact order after the nine that exist (an
ENUM_FLAG stores a bit per position, at most 32 items; this makes 22):
`modifiers` "Modifiers", `materials` "Materials", `node_trees` "Node trees", `vertex_groups`
"Vertex groups", `uv_maps` "UV maps", `shape_keys` "Shape keys", `weight_paint` "Weight Paint step
by step", `texture_paint` "Texture paint", `animation` "Animation", `pose` "Poses", `bones`
"Bones", `object_data` "Lights, cameras and curves", `scene_settings` "Scene settings".
`prefs._KIND_ITEMS` is built from `adapters.KINDS` (not a second list). Default: all on except
`texture_paint` (memory). Users upgrading keep their saved bits, so new kinds would be off for them:
a hidden `kinds_version` IntProperty; on register, if it is lower than 2, the new defaults are OR-ed
in and it is set to 2. The Advanced panel draws one `layout.prop_enum(prefs, "record_kinds", K)` per
kind whose adapter is loaded; kinds of families not written yet aren't shown.

### 5.3 Recording (datarec.py)
- `_diff`: skip keys whose adapter isn't in `ADAPTERS` (a newer build's records) and keys of an
  adapter whose `readable(idb)` is False now (not a removal), as it does for kinds that are off.
- Watch map: `_WATCH: dict[int, set[str]]` (ID pointer → owner uids), rebuilt for a uid in
  `baseline()` and after each commit of it from `a.watch(idb)` of its adapters (only kinds that are
  on). `on_update`: an update whose original's pointer is in `_WATCH` marks those owners dirty. Cleared
  in `forget()`. Unit test: a node added to a material (only the tree is updated) dirties the object.
- Derived ownership: `_DERIVED_OWNER: dict[str, str]` (derived uid → owner uid), computed in
  `refresh_mesh_users()` from `a.derived(ob)` with the D39 rule; an adapter's `capture()` leaves out
  `@<uid>:` paths for derived datablocks it doesn't own (`datarec.owns(owner_uid, derived_uid)`).
- `MESH_STEP_MODES = {"OBJECT"}`: `commit` compares the mesh of an object in one of these modes
  (`_mesh_step`), instead of only Object Mode; `_MESH_DIRTY` is kept, not discarded, for an object in
  another mode. Families may add a mode at import (`WEIGHT_PAINT`).
- Labels: on the settle path, a step whose records come from one adapter takes `adapter.label`
  ("Modifier", "Material", "Shape Key", ...); with an operator the operator's label wins, as now.
- After Blender's undo (`on_undo`): unchanged; every adapter is part of `capture()`.

### 5.4 Runtime hooks (runtime.py)
- `_observe(..., extras="all")`: `extras="vg"` reads only vertex group channels (Weight Paint steps).
- `extras_on(obj)` unchanged.

### 5.5 File format (persist.py)
- Each history's JSON gets `"adapters": [ids]` (adapters with records in it), kept up to date on push
  and on load (from step metas: commit writes `extra["adapters"]` on DATA steps, which is in the meta).
- `FORMAT_VERSION` becomes 5. A registry is written as format 4 when every history's adapters are in
  `{"rna", "object", "scene"}` and no delta uses a channel type the pre-format-5 V2 build lacks; else as 5. V1 and the pre-format-5 V2 build
  refuse format 5 with `NewerFormatError` ("history format 5 is newer than this Hindsight
  understands"): nothing guessed, nothing overwritten. That is better than what that build would do with
  unknown records in format 4: load them, write nothing for them on a jump, then record their
  disappearance as a change on the next operation (`_diff` sees MISSING on the Blender side and
  `kind_of` gives None, so `kind_on("")` is True).
- Steps keep blob version 4: records are JSON, new adapters only add records; no new header flag (a
  flag the pre-format-5 V2 build doesn't know makes its header length check fail and drops the history from there).
- Tests: format 4 file still written when only tier 1 adapters were used; format 5 file refused by
  the pre-format-5 V2 code in `S/baseline` (checked out at 66fcdf8) and by `.tmp/old_builds` with the message;
  this build reads format 4 and 5.

### 5.6 Notes and COVERAGE (warnings.py, tools)
`everything_notes(context, obj)` appends `a.notes(obj)` of every loaded adapter that handles the
object. Each adapter's `rows` lists its COVERAGE ids; `tests/test_bl_seam.py` checks that every
COVERAGE row with Recording `opt-in` has a loaded adapter naming it, and that every adapter's rows
exist in COVERAGE.md. COVERAGE "Recording" changes from `-` to `opt-in` only when its package lands;
Status stays `not covered` until the gate.

### 5.7 Seam tests (`tests/test_bl_seam.py`, `tests/families.py`)
A dummy adapter registered in the test: register, duplicate refused, kind list matches prefs items
in order, unknown adapter records ignored by `_diff` and `write`, `readable()` False is not a
removal, watch map dirties the owner, derived owner follows D39, `MESH_STEP_MODES`, format 4 vs 5
selection, labels. `tests/families.py`: `run_case()` from probe_limits.py turned into a helper
(`record(change, mode)` → steps made, `back_forward()` → exact both ways), and `oracle_*` readers
each package extends in its own test file. Size: about 250 lines of code, 300 of tests, 8 to 12 hours.

## 6. Work packages (parallel after the seam)

| Package | Owns (new files only) | Rows | Value to a sculptor | Size | Depends on |
|---|---|---|---|---|---|
| P0 Seam | section 5 files | all | enabler | 250 + 300 lines, 8-12 h | none |
| P1 Mesh data | `hindsight/bl/adapters_meshdata.py`, `tests/test_bl_t3_meshdata.py`, `tests/test_bl_t3_meshdata_prop.py`; patch for `core/types.py` + `native/` INT16_2D | paint.weights, paint.uvs, anim.shapekeys | highest: fixes D-c, D-d, D-f | 400 + 600 lines, 18-26 h | P0 |
| P2 Modifiers | `adapters_modifiers.py`, `tests/test_bl_t2_modifiers.py`, `..._prop.py`; patch for `adapters.object_body` (D-a) | proc.modifiers | high: fixes D-a, D-b | 350 + 500, 16-24 h | P0 |
| P3 Materials and nodes | `adapters_materials.py`, `adapters_nodes.py`, `tests/test_bl_t2_materials.py`, `tests/test_bl_t2_nodes.py`, `..._prop.py` | proc.materials, proc.nodetrees | high | 800 + 800, 32-45 h | P0 |
| P4 Texture paint | `adapters_paint.py`, `tests/test_bl_t3_texture.py`, `bench/texpaint.py` | paint.texture | medium-high, heavy | 450 + 400, 22-30 h | P0; uses P3's image lookup only through its own code (iterates Image Texture nodes itself) |
| P5 Animation | `adapters_anim.py`, `tests/test_bl_t4_anim.py`, `..._prop.py` | anim.keys, anim.nla | medium | 600 + 600, 30-40 h | P0; fixes D-e together with the seam's ANIMATED rule |
| P6 Rigs | `adapters_rig.py`, `tests/test_bl_t4_rig.py` | anim.armature | medium | 500 + 500, 25-35 h | P0, P5 for animated pose paths (works without) |
| P7 Other types | `adapters_types.py`, `tests/test_bl_t5_types.py`; patch for `_recreate_object` | types.lights, types.geometry (not GP, hair, volumes) | lower | 450 + 500, 20-30 h | P0; light node trees use P3 (optional) |
| P8 Scene | `adapters_scene.py`, `tests/test_bl_t6_scene.py` | scene.settings | lowest | 250 + 300, 10-16 h | P0; world node tree uses P3 |

Order if fewer agents than packages: P0, then P1 and P2, then P3, then P4, P5, P6, P7, P8. A
package that needs another's module imports it lazily inside functions and works without it (world
settings without the world's node tree), so no package waits for another.

Rule per package (COMMON.md): no edit to another package's file or a seam file; changes they need
there go in their report as a unified diff for the seam owner, who applies them after review.

## 7. Test plan (headless, per package, on 4.2, 5.0, 5.1, 5.2, 5.3)

Every package copies the tier 1 shape (tests/t1_gate_lib.py, t1_ops.py, oracle.py):
1. Real operators where they run headless (list in section 3); a property set where the user edits a
   field; data set through the API where Blender's tool can't run headless (brushes), said so in the
   test's docstring as harness.fake_stroke does.
2. Exactness both ways at every position, against an independent oracle written in the test file
   (never the adapter's capture): `vertex_weights`, shape key blocks with settings, UV maps with
   flags, modifier stacks with GN inputs (both API eras), node trees (nodes, links with sort ids,
   values, ramps, curves, interface), images (bytes hash per tile), F-curves (keyframe arrays),
   poses and bones, light/camera/curve data, scene settings.
3. Replay oracle: the same operations without Hindsight, compared with the states recorded.
4. Exactly one step per operation (operator-end path) and per settled field edit (settle path, with
   `datarec.SETTLE` and fake time); no step for frame changes, selection, node selection, UI-only
   properties.
5. Undo: `datarec.on_undo` emulation as test_bl_t1_real_undo.py does; mode switches (Weight Paint,
   Texture Paint, Pose, armature/curve/text Edit Mode).
6. Save and reload: sidecar, embed, both; rename; append; Shift+D and Alt+D for shared data (owner
   rule); format 4 vs 5 written as expected; the pre-format-5 V2 build refuses format 5 (S/baseline).
7. Hypothesis: 2,000 sequences per adapter mixing its operations with tier 1 ones; corpus in
   `tests/corpus/<package>/`.
8. Mutation testing of each adapter's diff and write code (as TEST_AUDIT describes for tier 1).
9. Fault injection: the adapter raising in capture, diff, write disables only itself (guard),
   history intact, sculpting fine.
10. Cost: capture time per adapter on small, medium and heavy scenes against `budget_ms`
    (tests/test_bl_adapters.py style), memory per step, zero work with Record set to Sculpting only
    (count calls with a spy), texture memory cap enforced.
11. Every limit in section 4 shows its note (warnings) and has a COVERAGE line.

## 8. What still needs a real Blender window before the site may claim any of this

- Real brush strokes: Weight Paint (one step per stroke, Auto Normalize, mirror), Texture Paint
  (stroke end detection through `PAINT_OT_image_paint`, partial image updates, the cost on a weak
  machine with 2K and 4K images), Vertex Paint unaffected.
- Field drags in the Properties editor and the N panel (settle path labels, one step per drag),
  node editor link drags, node moves, Ctrl+Shift click previews (must not be steps), the Image editor.
- `anim.keyframe_delete_v3d`, Graph Editor transforms, Dope Sheet, NLA editor operators (need their
  editors).
- Whether leaving Weight Paint mode really ends the visit as one step today (D-f).
- Blender's undo (Ctrl+Z, Ctrl+Shift+Z) mixed with scrubbing across these modes, Global Undo off.
- Screenshots of every new sidebar note and Advanced toggle, looked at (gate item 12).
- COVERAGE requires passing runs on 5.0.1, 5.1.1 and 5.1.2 for the gate; this sandbox has 5.0.1,
  5.1.2, 5.2.2, 5.3.0 and 4.2.23, not 5.1.1.

## 9. Proposed text (for the owners of these files; not applied)

COVERAGE.md, Notes column of proc.modifiers, proc.nodetrees, proc.materials: replace "code parked on
branch tier2" with "not written (the earlier tier 2 code was lost: no branch, commit or patch is
left)".

COVERAGE.md, obj.editsteps Limits, replace "but adding or removing a vertex group or a shape key
isn't recorded yet" with: "but adding, removing or renaming a vertex group, a shape key or a UV map
isn't recorded yet: it makes a step, and going back over it leaves the list as it is now".

COVERAGE.md, obj.addremove Limits, add: "on Blender 5.2 and later, Geometry Nodes input values of a
deleted object don't come back".

COVERAGE.md, sculpt.topology Limits, add: "going back over Apply Modifier gives the mesh from before
it, without the modifier".

DEFECTS.md, new rows D-a to D-f as written in section 2 (severity proposals: D-a S2, D-b S2, D-c S2,
D-d S3, D-e S3, D-f S3).

site/pages/limitations.html, "Not recorded in this version": every line stays true as a claim
because no row has passed a gate. More exact wording that still says "isn't recorded" (run
`python3 tools/check_copy.py` on it):
- "Modifiers aren't recorded. Going back over Apply Modifier gives the mesh from before Apply,
  without the modifier."
- "Vertex groups and shape keys you add, remove or rename aren't recorded, and Weight Paint strokes
  aren't recorded one by one."
- "Which UV map is active isn't recorded, and custom normals aren't recorded."
(Weights, UV coordinates, seams, sharp edges, creases and shape key positions are part of Edit Mode
steps today, but the site may say so only after their rows pass the gate.)

DECISIONS.md, proposed: "Derived datablocks (materials, node groups, images, actions, shape keys,
armature, light, camera and curve data) are recorded in the history of the recorded object that
uses them, with the shared-mesh owner rule (D39), not in histories of their own as ADAPTERS.md first
planned: per-object scrubbing then undoes what was changed while working on that object, and the
history engine needs no change." (Owner decides before P3 starts.)

## 10. Risks

- Files written by these packages need format 5, which V1 and the pre-format-5 V2 build refuse: anyone going back
  to an older build loses access to that file's history (the file itself opens).
- Node trees: hundreds of node types, add-on nodes, zone pairing, interface panels, version changes
  between 4.2 and 5.3 (compositor became a node group in 5.0, `use_nodes` deprecated). Largest risk
  of a write that doesn't give back the exact tree; the oracle must compare everything.
- Texture paint memory and time on weak machines; off by default for that reason.
- Armature bones may not be exact through `EditBone.matrix` (roll); then the row stays partial.
- `vertex_group_move` and `shape_key_move` are operators inside a jump: never from a handler, and
  one call per position moved (slow for long lists).
- Shape key channel names include the index (`sk:NNN:name`): reordering keys rewrites every later
  channel; correct but large steps.
- Upgrading users get new kinds only through the `kinds_version` migration; wrong code there switches
  recording on or off without the user knowing.
- Weight Paint per stroke at 100k vertices costs 70 to 110 ms after each stroke (Python loop over
  vertex groups; no bulk API).
- Everything in this report was run headless; nothing was seen in a window.
