# Research notes

Phase 0 research by two agents (R1: Blender API, R2: packaging/compression/CI). Reference product pages were blocked by the environment's network policy; see DECISIONS.md.

# Hindsight R1: Blender 4.2 Python API notes

Legend: "verified" = checked by running bpy 4.2.23 headless (`$BPY42_VENV`), by reading the bundled 4.2 scripts, or by grepping the 4.2 binary. "(UNVERIFIED)" = not confirmed locally. "web" = from search-result snippets only. docs.blender.org and developer.blender.org are blocked by the egress proxy (403) in this session, so those pages were not read directly. Headless bpy has no UI, so GPU drawing, msgbus callbacks and sculpt strokes could not be exercised.

## 1. Detecting sculpt strokes and mode changes

- Handlers exist in 4.2 (verified): `depsgraph_update_pre/post`, `undo_pre/post`, `redo_pre/post`, `load_pre/post`, `save_pre/post`, `frame_change_pre/post`. `bpy.app.handlers.persistent` exists (verified).
- Load handlers must be decorated `@bpy.app.handlers.persistent` to survive file loads (bundled `addons_core/bl_pkg/__init__.py:407` does this).
- `depsgraph_update_post(scene, depsgraph)`: iterate `depsgraph.updates`; each entry has `.id`, `.is_updated_geometry`, `.is_updated_transform` (verified).
- Verified headless: a Python write to a `.sculpt_face_set` value and a write to `vertices[0].co` both produced `('Cube', is_updated_geometry=True)`.
- A sculpt stroke should produce many geometry updates, not one at stroke end (UNVERIFIED, no UI here). Treat them as a debounced signal, for example "no geometry update for ~150 ms" via a timer.
- `undo_post` fires on undo/redo execution, not on each sculpt undo push (UNVERIFIED for sculpt).
- `bpy.context.window_manager.operators` is documented as "Operator registry" (verified). It is empty in background (verified). Reading `operators[-1].bl_idname` in the UI is UNVERIFIED and unreliable for modal strokes, so do not depend on it.
- Operator idnames (all verified in `dir(bpy.ops.*)` on 4.2):
  - Brush stroke: `sculpt.brush_stroke` (Sculpt keymap, LEFTMOUSE PRESS, `blender_default.py` ~line 5578)
  - Mesh filter `sculpt.mesh_filter`; color filter `sculpt.color_filter`; cloth `sculpt.cloth_filter`
  - Mask: `sculpt.mask_filter`, `sculpt.mask_init`, `sculpt.mask_from_cavity`, `sculpt.mask_by_color`; paint-side `paint.mask_flood_fill`, `paint.mask_box_gesture`, `paint.mask_lasso_gesture`, `paint.mask_line_gesture`, `paint.mask_polyline_gesture`
  - Face sets: `sculpt.face_sets_create`, `sculpt.face_sets_init`, `sculpt.face_set_edit`, `sculpt.face_set_change_visibility`, `sculpt.face_sets_randomize_colors`, `sculpt.face_set_{box,lasso,line,polyline}_gesture`
  - Hide/show: `paint.hide_show`, `paint.hide_show_all`, `paint.hide_show_masked`, `paint.hide_show_{lasso,line,polyline}_gesture` (under `paint`, not `sculpt`)
  - Voxel remesh: `object.voxel_remesh` (not under sculpt in 4.2); `object.quadriflow_remesh`
  - Trim: `sculpt.trim_box_gesture`, `trim_lasso_gesture`, `trim_line_gesture`, `trim_polyline_gesture`
  - Vertex paint stroke: `paint.vertex_paint`; toggle `paint.vertex_paint_toggle`; colour ops `paint.vertex_color_*`
  - Mode and dyntopo: `sculpt.sculptmode_toggle`, `object.mode_set`, `sculpt.dynamic_topology_toggle`
- msgbus: `bpy.msgbus.subscribe_rna(key, owner, args, notify, options={'PERSISTENT'})`. Key is an RNA property instance, a Struct type, or `(StructType, "prop")` (docstring, verified). Bundled example: `addons_core/copy_global_transform.py:1024` uses `(bpy.types.ToolSettings, "use_keyframe_insert_auto")`.
- msgbus subscribers are cleared on file load (4.2 docstring, verified). Re-register in a `load_post` handler. Unsubscribe with `bpy.msgbus.clear_by_owner(owner)`.
- msgbus did not fire in my headless test of `Object.mode` changes (verified no callbacks; background has no notifier loop). Web: msgbus delivers nothing under `--background`, and callbacks are deferred until operators finish and fire at most once per property per update cycle (web, 5.0 docs snippet; UNVERIFIED for 4.2).
- Web forum reports: subscribing to the `Sculpt.brush` data-block got no callbacks (UNVERIFIED). Mode-change callbacks reportedly fired only for the active object with multi-selection (UNVERIFIED). Forum suggests `(bpy.types.LayerObjects, "active")` for active-object changes (UNVERIFIED; the type exists, verified).
- Timers: `bpy.app.timers.register(fn, first_interval=0, persistent=False)`. Return `None` to stop, or a float to reschedule (verified docstring). Good fallback for polling `obj.mode` and `view_layer.objects.active`.

## 2. Mesh data access in sculpt mode

- Mode entry works headless: `bpy.ops.object.mode_set(mode='SCULPT')` returns FINISHED and sets `obj.mode == 'SCULPT'`. `bpy.ops.sculpt.sculptmode_toggle()` switches back to OBJECT (verified).
- Headless timing on a 32,514-vertex UV sphere: enter SCULPT 17 ms, exit to OBJECT 0.3 ms (verified). UI timings are likely higher (UNVERIFIED).
- Positions: `me.vertices.foreach_get('co', buf)` with a float32 buffer of length 3N works (verified). `me.attributes['position'].data.foreach_get('vector', buf)` also works (verified). Use the attribute path in hot loops; `me.vertices[i].co` is a per-element Python loop.
- Python write in sculpt mode: set `vertices[0].co.x`, toggle to OBJECT, and the value persisted (verified, one case). Whether sculpt's PBVH and viewport show external writes mid-session is UNVERIFIED.
- Plan after `foreach_set`: call `me.update()` (verified callable) and `obj.update_tag()`. If the viewport is stale, rebuild by toggling mode (`mode_set` OBJECT then SCULPT). The viewport part is UNVERIFIED.
- Dyntopo: the mesh is converted to BMesh and written back on exit (UNVERIFIED, general knowledge). Reads during a stroke may lag. Check `obj.use_dynamic_topology_sculpting` first.
- Mask: `.sculpt_mask`, POINT, FLOAT (verified: created with `attributes.new`, present in binary). Helpers in `modules/bpy_types.py` (~lines 58-68): `me.vertex_paint_mask`, `vertex_paint_mask_ensure()`, `vertex_paint_mask_remove()` (verified; get returns None when absent, ensure creates POINT/FLOAT, remove deletes). Behaviour in 3.x and 5.x is UNVERIFIED.
- Face sets: `.sculpt_face_set`, FACE, INT (verified creation; string present in binary). Sign or hidden-set semantics are UNVERIFIED.
- Hidden: `.hide_vert` POINT BOOLEAN and `.hide_poly` FACE BOOLEAN (verified creation; strings in binary). `.hide_edge` is UNVERIFIED (not tested).
- Colour: `me.color_attributes.new(name, 'FLOAT_COLOR', 'POINT')` and `new(name, 'BYTE_COLOR', 'CORNER')` both work (verified). `foreach_get('color', float32 buf of 4N)` works on FLOAT_COLOR (verified). BYTE_COLOR element dtype is UNVERIFIED.
- `mesh.attributes` lists internal dot-prefixed attributes such as `.select_vert`, `.corner_vert` (verified). Filter them out when reporting to users.
- Create/remove: `me.attributes.new(name, data_type, domain)`. `name in me.attributes` works (verified). Removal is exercised by `vertex_paint_mask_remove`.
- dtype map: FLOAT->`'value'` float32; FLOAT_VECTOR->`'vector'` float32 (3 per item); FLOAT_COLOR->`'color'` float32 (4 per item); INT->`'value'` int32; BOOLEAN->`'value'` (numpy bool dtype UNVERIFIED).
- Face-set and other sculpt operators need a 3D view context. `bpy.ops.sculpt.face_sets_init(mode='LOOSE_PARTS')` raises "poll() failed, context is incorrect" in bpy (verified). Write attributes directly in background tests.
- Undo replaces the Mesh datablock: after `bpy.ops.ed.undo()` the old `me` reference raises `ReferenceError` (verified). Re-fetch `bpy.data.objects[name].data` after every undo/redo.

## 3. Native undo in sculpt mode

- Settings: `bpy.context.preferences.edit.use_global_undo` (default True) and `.undo_steps` (default 32), both verified. Docstring: global undo keeps a full in-memory copy of the file (verified). UI location: UNVERIFIED (the brief says Preferences > System; check the 4.2 UI).
- Memfile steps: `bpy.ops.ed.undo_push(message=...)` returns FINISHED headless (verified). Test: push, set vertex z 1.0 to 3.0, push, `ed.undo()`, and z returned to 1.0 (verified).
- Sculpt steps: strokes push their own C-side sculpt undo steps (web: developer.blender.org commits on sculpt undo and face-set undo; current behaviour UNVERIFIED). Python writes inside sculpt mode are not automatically undo-pushed (UNVERIFIED). Practical: after your own writes, call `bpy.ops.ed.undo_push(message=...)` and confirm the interaction with sculpt steps in the UI.
- Undo keys: `ed.undo` on Ctrl+Z, `ed.redo` on Ctrl+Shift+Z, `ed.undo_history` on Ctrl+Alt+Z are in the "Screen" keymap (`km_screen`, space EMPTY, region WINDOW, `blender_default.py` line 809-831; verified). The "Sculpt" keymap (`km_sculpt`, ~line 5571) has no undo items (verified).
- Undo handlers: `bpy.app.handlers.undo_pre/undo_post`, `redo_pre/redo_post` exist (verified). Use them to refresh the Hindsight cache after undo/redo.
- Mac: `KeyMapItem.oskey` exists (verified). The default keymap uses `"ctrl": True` with no separate mac branch; the only `oskey` string is a modifier helper at line 261 (verified). Whether macOS maps Ctrl to Cmd in keymap matching is UNVERIFIED. Test on mac before adding an `oskey` duplicate.
- Add-on keymap: `wm.keyconfigs.addon.keymaps.new(name=..., space_type=..., region_type=...)` then `km.keymap_items.new(idname, 'Z', 'PRESS', ctrl=True)` (pattern in bundled `addons_core/pose_library/keymaps.py:14-25`). Cleanup in `modules/bpy/utils/__init__.py:1182` checks both `keyconfigs.default` and `keyconfigs.addon`. Never write to `keyconfigs.default`.
- To take over Ctrl+Z, bind in the add-on "Screen" keymap, where `ed.undo` lives. Whether add-on items beat default items is UNVERIFIED.

## 4. Custom viewport drawing and input

- `bpy.types.SpaceView3D.draw_handler_add(callback, args, 'WINDOW', 'POST_PIXEL')`; arguments are positional-only (verified docstring). Remove with `bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')` (verified; bundled `templates_py/operator_modal_draw.py:42,46`).
- Shaders valid in 4.2 (verified: binary strings, plus bundled use): `'UNIFORM_COLOR'` (5 uses in bundled scripts), `'POLYLINE_UNIFORM_COLOR'`, `'FLAT_COLOR'`, `'IMAGE'`.
- Removed: `'2D_UNIFORM_COLOR'`, `'3D_UNIFORM_COLOR'`, `'2D_SMOOTH_COLOR'` are absent from the 4.2 binary (grep count 0, verified). Web: a 2022 master commit removed the C-side 2D uniform shader; exact release UNVERIFIED (3.x/4.0 era). Use `UNIFORM_COLOR`.
- Pattern from the bundled template (verified, lines 18-27):
  ```python
  shader = gpu.shader.from_builtin('UNIFORM_COLOR')
  batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": pts})  # gpu_extras.batch
  gpu.state.blend_set('ALPHA')
  shader.uniform_float("color", (r, g, b, a))
  batch.draw(shader)
  gpu.state.blend_set('NONE')
  ```
- Build batches once per geometry change and reuse them in the draw callback (general practice; not measured).
- GPU drawing is unavailable in background: `gpu.shader.from_builtin` raises `SystemError: GPU functions for drawing are not available in background mode` (verified). Shader code can only be tested in a UI session.
- blf: `blf.size(fontid, size)` takes no dpi argument in 4.2 (verified docstring). `blf.position(font_id, x, y, 0)` and `blf.draw` as in the template. For UI scale, `context.preferences.system.ui_scale` exists and is documented as a size multiplier (verified property; 0.0 in background, UNVERIFIED in UI).
- Redraw: `context.area.tag_redraw()` (template line 36).
- Mouse: `event.mouse_region_x` and `event.mouse_region_y` exist (verified). They are region-local, matching POST_PIXEL coordinates (standard; UNVERIFIED in UI).
- Modal: `context.window_manager.modal_handler_add(self)`, then return `{'RUNNING_MODAL'}` (template lines 61-62, verified). Return `{'PASS_THROUGH'}` to let other handlers see the event; `{'FINISHED'}` or `{'CANCELLED'}` ends the modal (template lines 43, 47).
- Click-inside-widget pattern (UNVERIFIED in UI): a keymap item in the "3D View" keymap for LEFTMOUSE PRESS calls your operator. Its `invoke()` tests `event.mouse_region_x/y` against the slider rect. Outside the rect, return `PASS_THROUGH`. Inside, start the modal drag and return `RUNNING_MODAL`. Note `sculpt.brush_stroke` also binds LEFTMOUSE PRESS in the Sculpt keymap (verified), so keymap priority matters and is UNVERIFIED.
- Menu from code: `bpy.ops.wm.call_menu(name="MY_MT_menu")` (verified signature, "Open a predefined menu").

## 5. Custom sculpt tool and brush (4.2 vs 4.3+)

- `bpy.utils.register_tool(tool_cls, after=None, separator=False, group=False)` and `bpy.utils.unregister_tool` exist (verified). The class is a `bpy.types.WorkSpaceTool` subclass (verified). Class attributes: `bl_space_type`, `bl_context_mode`, `bl_idname`, `bl_label`, `bl_description`, `bl_icon`, `bl_keymap` (the bundled `startup/bl_ui/space_toolsystem_common.py` uses `bl_keymap`; `bl_context_mode` name is UNVERIFIED by grep). RNA exposes it as `WorkSpaceTool.mode` (verified).
- 4.2 brush operators (verified `dir(bpy.ops.brush)`): `add`, `add_gpencil`, `curve_preset`, `reset`, `scale_size`, `sculpt_curves_falloff_preset`, `stencil_control`, `stencil_fit_image_aspect`, `stencil_reset_transform`. No `asset_activate`.
- Version 4.3+ (web, 4.3 API snippet; UNVERIFIED locally): brushes become assets. `bpy.ops.brush.asset_activate(asset_library_type, asset_library_identifier, relative_asset_identifier)` makes a brush asset the active sculpt and paint tool. `use_toggle` appears only in 5.x reference pages. Save operator is `asset_save` in some pages and `asset_update` in others (unresolved).
- Guard by version: `'asset_activate' in dir(bpy.ops.brush)`. `hasattr(bpy.ops.brush, 'asset_activate')` returned True on 4.2 even though the operator is missing (verified). Use `dir()`.
- Raycast: `bpy.context.scene.ray_cast` exists on the instance (verified; signature UNVERIFIED, believed to be `(depsgraph, origin, direction, distance=...)`). Screen-to-ray: `bpy_extras.view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)` and `region_2d_to_vector_3d` (verified exist). `mathutils.bvhtree.BVHTree.FromObject` exists (verified; signature UNVERIFIED).
- Radius and strength: `tool_settings.unified_paint_settings` has `size`, `use_unified_size`, `strength` (verified). `tool_settings.sculpt` exists (verified). Pattern: `ups = ts.unified_paint_settings; size = ups.size if ups.use_unified_size else ts.sculpt.brush.size` (`brush.size` UNVERIFIED for 4.3+).

## 6. Object switching and modes

- Alt+Q: `object.transfer_mode` bound to Alt+Q PRESS (verified, `blender_default.py` line 4969). Docstring: switches the active object and assigns the same mode to the one under the cursor. Option `use_flash_on_transfer` (verified).
- Other operators (verified to exist): `object.join` (Ctrl+J), `object.duplicate_move` (Shift+D), `object.duplicate_move_linked` (Alt+D), `mesh.separate(type='SELECTED')` (P in Edit mode), `mesh.paint_mask_extract(mask_threshold=0.5, add_boundary_loop=True, smooth_iterations=4, apply_shrinkwrap=True, add_solidify=True)`. Default key bindings for the first four are standard, not all re-checked.
- Active-object change: forum suggests msgbus on `(bpy.types.LayerObjects, "active")` (UNVERIFIED). Fallback: timer polling `context.view_layer.objects.active`.
- Intercept pattern (UNVERIFIED in UI): register your own operator, bind it in `wm.keyconfigs.addon` on the same key. In `invoke`, show `context.window_manager.invoke_confirm(self, event)`, and on confirm call the original (`bpy.ops.object.join()`, etc.). Calling the original keeps its behaviour intact. Blender's add-on-vs-default precedence is UNVERIFIED.
- Disabling a default item: setting `kmi.active = False` on the default keymap item is the common approach (UNVERIFIED here). Wrapping is safer.
- `bpy.ops.object.mode_set(mode=...)` returns FINISHED when an active object exists (verified). In a bad context it raises RuntimeError, as with the face-set case in section 2.

## 7. Dyntopo, Multires, shape keys

- `Object.use_dynamic_topology_sculpting` (bool, per object) exists in 4.2 (verified). Toggle: `sculpt.dynamic_topology_toggle()` (verified). The bundled UI reads it via `context.sculpt_object` (`startup/bl_ui/space_view3d_toolbar.py:994`).
- Multires: `Modifier.type == 'MULTIRES'` is in the 4.2 enum (verified). `MultiresModifier` properties include `levels`, `sculpt_levels`, `render_levels`, `total_levels`, `quality`, `use_sculpt_base_mesh` (verified).
- Shape keys: `me.shape_keys` exists on Mesh (verified). It is `None` when absent (verified). Key blocks via `me.shape_keys.key_blocks` (UNVERIFIED).
- Detection: `any(m.type == 'MULTIRES' for m in obj.modifiers)`, `obj.use_dynamic_topology_sculpting`, `obj.data.shape_keys is not None`.
- Design risk (UNVERIFIED): dyntopo changes vertex count and attribute layout during a session, so per-stroke index deltas recorded earlier may be invalid. Multires sculpting edits displacement levels rather than the base mesh (UNVERIFIED). Both need a plan in Hindsight's delta format.
- Global undo path: `preferences.edit.use_global_undo` (verified property). UI location UNVERIFIED (see section 3).

## Version notes (4.3-5.x)

- 4.3: brushes become assets; `bpy.ops.brush.asset_activate` added (web).
- 5.0+: msgbus callbacks are deferred until operators finish and fire once per property per update cycle (web, 5.0 docs snippet).
- 5.x: `use_toggle` on `brush.asset_activate` (web).
- No 4.3-5.x change was checked for gpu, blf, mesh attribute names, or the handler list. Treat those as 4.2-only until checked.

## Gotchas

1. Official docs and developer pages are blocked (proxy 403) in this session. Verified items come from the local 4.2.23 binary, bundled scripts, and headless runs. Web items are search snippets. Re-check msgbus timing and brush-asset API against docs.blender.org before relying on them.
2. `ReferenceError` after undo/redo: re-fetch `bpy.data.objects[name].data` after every undo, redo and mode change.
3. `hasattr(bpy.ops.brush, 'asset_activate')` is True on 4.2 although the operator is missing. Use `in dir(...)`.
4. GPU drawing, msgbus callbacks and sculpt strokes do not run in background. Test shaders, msgbus and stroke handlers in a UI session.
5. Use `UNIFORM_COLOR`; `2D_UNIFORM_COLOR` is absent from 4.2. `blf.size(fontid, size)` takes no dpi argument.
6. Face-set, trim, hide and brush-stroke operators fail with "context is incorrect" outside a 3D view. Write attributes directly instead.
7. msgbus subscriptions are dropped on file load and fire only after operators finish. Re-subscribe in a `@persistent` `load_post` handler.
8. Depsgraph handlers run on every update, including your own writes. Guard against re-entry, and do not write datablocks inside `depsgraph_update_post` without a flag.
9. Python writes in sculpt mode persisted through a mode exit (one test). The viewport and PBVH may still lag, so call `mesh.update()` and rebuild by toggling mode if needed.
10. Ctrl+Z lives in the "Screen" keymap, not "Sculpt". Put overrides in `wm.keyconfigs.addon` and remove them on unregister; never edit `keyconfigs.default`.

# R2: Blender extensions, threading, compression, CI

Verified against the bpy 4.2.23 source (`blender_ext.py`, `bl_extension_cli.py`, `addon_utils.py`) and by running real builds. Anything not checked is marked (UNVERIFIED).

## 1. Blender extensions: manifest, CLI, bundling

### blender_manifest.toml (4.2 rules, enforced by the strict validator)

```toml
schema_version = "1.0.0"                  # semver
id = "hindsight"                          # Python identifier: no "-", no "__", no leading/trailing "_"
version = "0.1.0"                         # semver X.Y.Z
name = "Hindsight"
tagline = "Per-stroke undo history for sculpting"
maintainer = "Min3scon"                   # non-empty; format not validated
type = "add-on"                           # 4.2 accepts only "add-on" or "theme"
website = "https://min3scon.github.io/Hindsight/"   # optional
tags = ["Sculpt"]                         # optional; checked against Blender's add-on list (below)
blender_version_min = "4.2.0"             # strict: exactly three numbers
license = ["SPDX:GPL-3.0-or-later"]       # non-empty list; "SPDX:" prefix per docs (UNVERIFIED, not validated)
copyright = ["2026 Min3scon"]             # optional; "YYYY" or "YYYY-YYYY", a space, then a name
platforms = ["linux-x64", "windows-x64", "macos-arm64"]   # optional list
wheels = ["./wheels/lz4-4.4.5-cp311-cp311-win_amd64.whl"] # optional; must end ".whl", forward slashes

[permissions]                             # must be a table
files = "Store sculpt history in the user data folder"    # keys: files, network, clipboard, camera, microphone

[build]
paths_exclude_pattern = ["__pycache__/", ".*", "/research/", "/docs/", "*.blend"]
# OR paths = [...]  (both together is an error, verified)
```

- `tagline` and every `[permissions]` reason: max 64 characters (65 rejected, verified), no leading/trailing whitespace, no control characters, last character must be alphanumeric or `)`, `]`, `}`. A trailing period is rejected (verified).
- `[build].paths`: relative, forward slashes, no `..`, unique case-insensitively, must not name the manifest.
- `[build.generated]` is reserved; the build tool writes it (see zip contents).
- Platform strings: the names the tool derives from wheels are `linux-x64`, `linux-arm64`, `windows-x64`, `windows-arm64`, `macos-x64`, `macos-arm64`. The validator does not check values (UNVERIFIED whether extensions.blender.org does).
- Valid add-on tags (`_bpy_internal/extensions/tags.py`): 3D View, Add Curve, Add Mesh, Animation, Bake, Camera, Compositing, Development, Game Engine, Geometry Nodes, Grease Pencil, Import-Export, Lighting, Material, Mesh, Modeling, Node, Object, Paint, Physics, Pipeline, Render, Rigging, Scene, Sculpt, Sequencer, System, Text Editor, Tracking, UV, User Interface. ("All" is auto-added for legacy add-ons; avoid it.)

### CLI

- Blender: `blender --command extension build --source-dir addon/ --output-filepath dist/hindsight.zip`
  - `--output-dir DIR` gives the default name `<id>-<version>.zip`. Setting both `--output-dir` and `--output-filepath` is an error (verified).
  - `--split-platforms` requires `platforms` and writes `<id>-<version>-<platform>.zip`, e.g. `hindsight-0.1.0-linux_x64.zip`.
  - Blender's handler (`bl_extension_cli.cli_extension_handler`) runs `blender_ext.main(args_internal=False)` with Blender's built-in tag list as the default `--valid-tags`.
- `blender --command extension validate SRC_DIR_OR_ZIP`: strict manifest check; add-ons need `__init__.py` at the root (directory or zip). It does not check the `[build]` section.
- `blender_ext.py` is stdlib-only (imports `tomllib`), so it runs under plain Python 3.11+ without Blender. Verified with 3.11.17 and 3.13.16:

```bash
BLENDER_EXT=$BPY42_VENV/lib/python3.11/site-packages/bpy/4.2/scripts/addons_core/bl_pkg/cli/blender_ext.py
/usr/bin/python3.11 "$BLENDER_EXT" build --source-dir addon/ --output-filepath dist/hindsight.zip \
  --valid-tags $BPY42_VENV/lib/python3.11/site-packages/bpy/4.2/scripts/modules/_bpy_internal/extensions/tags.py
/usr/bin/python3.11 "$BLENDER_EXT" validate addon/
```

- Plain Python skips tag checks unless `--valid-tags <tags.py>` is given (its default is empty). Verified both ways.
- Exact Blender-equivalent without the GUI binary (verified): `$BPY42_VENV/bin/python -I` with `import bpy; from bl_pkg.bl_extension_cli import cli_extension_handler; sys.exit(cli_extension_handler(args))`.

### Zip contents (verified)

- Zip root = contents of the source dir. `blender_manifest.toml` is at the root. Deflate level 9; `.whl`, `.zip`, `.gz` and similar files are stored uncompressed.
- Default excludes apply only when there is no `[build]` section: `__pycache__/`, `.*`, `/*.zip`. With no `[build]`, `research/` and `stray.blend` were included.
- Gotcha: a custom `paths_exclude_pattern` replaces the defaults. My test with a custom list and no `__pycache__/` or `.*` entry shipped `__pycache__/a.pyc` and `.hidden`. Always list them explicitly.
- Split builds append this block to the manifest in each zip, and include only that platform's wheels:

```toml
# BEGIN GENERATED CONTENT.
# This must not be included in source manifests.
[build.generated]
platforms = ["linux-x64"]
wheels = ["./wheels/lz4-4.4.5-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl"]
# END GENERATED CONTENT.
```

### Bundling wheels

- Real filenames from `pip download` for the versions in the bench venv (lz4 4.4.5, zstandard 0.25.0). Use cp311 for Blender 4.2 to 5.0 and cp313 for 5.1+ (per the task brief).
  - `lz4-4.4.5-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl`
  - `zstandard-0.25.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`
  - `lz4-4.4.5-cp311-cp311-win_amd64.whl`, `zstandard-0.25.0-cp311-cp311-win_amd64.whl`
  - `lz4-4.4.5-cp311-cp311-macosx_11_0_arm64.whl`, `zstandard-0.25.0-cp311-cp311-macosx_11_0_arm64.whl`
- Mapping (verified with `blender_platforms_from_wheel_platform`): `manylinux*`/`musllinux*` to `linux-x64`, `win_amd64` to `windows-x64`, `macosx_*_arm64` to `macos-arm64`.
- Split zips built from these: linux-x64 6.9 MB, windows-x64 0.6 MB, macos-arm64 0.85 MB.
- Blender unpacks wheels into a shared `site-packages` (under the user extensions `.local/lib/python3.11/site-packages`) and adds it to `sys.path` (`addon_utils.py`, `wheel_manager.py`). So import them absolutely: `import lz4.frame`, `import zstandard`. The directory is shared by all extensions, so version clashes are possible.
- Your own pure-Python modules use relative imports. The package loads as `bl_ext.<repo_module>.<id>`, and `__package__` holds that string (example `bl_ext.user_default.hindsight`, UNVERIFIED). Do not use `sys.path` hacks.
- `bl_info` is ignored for extensions. `addon_utils` deletes it and builds one from the manifest. Do not rely on it.
- User data:

```python
import bpy
DATA_DIR = bpy.utils.extension_path_user(__package__, path="", create=True)  # <config>/extensions/.user/<repo>/<id>/
```

  Pass `__package__`, not `__name__`. A non-extension package raises ValueError (per `addon_utils._extension_module_name_decompose` source, not run).

## 2. Thread safety

- bpy is main-thread only: `bpy.data`, `bpy.context`, `bpy.ops`, any RNA struct access (`obj.data.vertices`, `mesh.update()`), `bmesh` on a live mesh, `bpy.app.handlers`.
- Safe off the main thread: numpy on plain arrays, `zlib`/`lz4`/`zstandard` on bytes, file I/O, hashing, diffing.
- Move data across on the main thread:

```python
# main thread, at stroke begin/end
buf = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
mesh.vertices.foreach_get("co", buf)     # plain numpy copy from here on
```

- Hand-back pattern:

```python
import queue, bpy
from concurrent.futures import ThreadPoolExecutor
_done = queue.SimpleQueue()               # thread-safe FIFO
_pool = ThreadPoolExecutor(max_workers=1) # one worker keeps order

def _work(job):                           # worker thread: NO bpy here
    _done.put(encode_stroke(job))         # numpy / codec / IO only

def _drain():                             # main thread (timer)
    while True:
        try:
            item = _done.get_nowait()
        except queue.Empty:
            break
        apply_result(item)                # bpy allowed here
    return 0.1 if _running else None      # float = reschedule; None = unregister

def start():
    bpy.app.timers.register(_drain, first_interval=0.1, persistent=True)
```

  - Submit with `_pool.submit(_work, job)`. On unregister, call `_pool.shutdown(wait=True)` and drain the queue once more.
  - `bpy.app.timers` callbacks run on the main thread (Blender's own `bl_extension_notify.py` uses the same call; `first_interval=` confirmed in source).
- GIL release, probed with one 1-second run per codec (noisy, single run). Ratio = main-thread pure-Python loop throughput with a worker running, divided by baseline:
  - Pure-Python control: 0.43 (GIL held, as expected).
  - zstd L3: 1.03 (released). zlib L6: 0.81 and numpy `sqrt` on 20M floats: 0.93 (consistent with release).
  - lz4.frame L1: 0.65 (inconclusive; UNVERIFIED).
  - Documented behaviour: zlib and numpy ufunc loops release the GIL (general knowledge, not re-checked for each function). Fancy indexing and object arrays do not.

## 3. Compression benchmark

- Script: `research/compress_bench.py`. Run with `$BENCH_VENV/bin/python research/compress_bench.py` (about 60 s). Venv: Python 3.13.16, numpy 2.5.4, lz4 4.4.5, zstandard 0.25.0, x86_64 Linux, seed 1234. Every round trip is asserted.
- Data: 1,000,000 vertices on a jittered lat/long grid with row-major indices. Stroke: the 5,000 vertices nearest a 60-degree great-circle path. Their indices form 115 consecutive runs (median gap 1, max gap 997). Offsets about 1e-3 radial. Deltas are XOR of uint32 bit patterns.
- Payloads (bytes): a, b, c, bc = 80,000 per stroke; d = 12,000,000; e = 1,000,000.
  - a: raw indices + XOR. b: delta-encoded indices + XOR. c: raw indices + byte-shuffled XOR. bc: delta indices + byte-shuffled XOR (extra). d: full f32 positions. e: dense uint8 mask delta.

### Compression ratio (raw / compressed, higher is better)

| codec | a raw idx + XOR | b delta idx + XOR | c raw idx + shuffled XOR | bc delta idx + shuffled XOR | d full f32 positions | e uint8 mask |
|---|---|---|---|---|---|---|
| zlib L1 | 1.63 | 1.89 | 1.88 | 2.26 | 1.15 | 96.28 |
| zlib L6 | 1.61 | 1.88 | 1.93 | **2.35** | 1.16 | 146.52 |
| lz4.frame default | 1.00 | 1.32 | 1.42 | 2.17 | 1.00 | 102.79 |
| lz4.frame L9 (HC) | 1.06 | 1.43 | 1.47 | 2.30 | 1.00 | 105.27 |
| zstd L1 | 1.45 | 2.00 | 1.60 | 2.28 | 1.12 | 186.71 |
| zstd L3 | 1.45 | 1.99 | 1.61 | 2.30 | 1.13 | 185.53 |
| zstd L9 | 1.45 | 1.98 | 1.62 | 2.34 | 1.12 | **189.11** |

### Throughput, MB/s (encode / decode, best of 3)

| payload | zlib L1 | zlib L6 | lz4 default | lz4 L9 | zstd L1 | zstd L3 | zstd L9 |
|---|---|---|---|---|---|---|---|
| a raw idx + XOR | 40 / 153 | 10 / 161 | 2101 / 12506 | 49 / 4441 | 645 / 880 | 554 / 819 | 512 / 846 |
| b delta idx + XOR | 42 / 170 | 10 / 207 | 1595 / 11482 | 48 / 4361 | 611 / 1397 | 256 / 1256 | 73 / 1005 |
| c raw idx + shuffled XOR | 69 / 240 | 30 / 244 | 2563 / 4583 | 19 / 5411 | 676 / 1110 | 670 / 996 | 134 / 1176 |
| bc delta idx + shuffled XOR | 104 / 339 | 51 / 345 | 2534 / 4189 | 18 / 4977 | 731 / 3103 | 705 / 2591 | 117 / 4029 |
| d full f32 positions | 28 / 129 | 17 / 135 | 1793 / 6277 | 30 / 4419 | 632 / 945 | 454 / 696 | 462 / 901 |
| e uint8 mask | 566 / 1727 | 276 / 1075 | 10338 / 6619 | 113 / 6224 | 6737 / 15119 | 3722 / 15164 | 687 / 12398 |

Compressed bytes for bc (80,000 raw): zlib L6 34,103; zstd L9 34,156; lz4 L9 34,825; zstd L3 34,849; zstd L1 35,094; lz4 default 36,934.

**Recommendation:** zstd level 3, delta-encode the sorted indices (`np.diff`), and byte-shuffle the XOR deltas (bc). That gives ratio 2.30 at about 700 MB/s encode and 2.6 GB/s decode. zlib L6 is 2% smaller but about 14x slower to encode, and zstd L9 is 2% smaller at 6x slower encode. Dropping the byte-shuffle (b) costs 15% more bytes; dropping the index delta (c) costs 43% more; dropping both (a) costs 58% more.

Caveats:
- The mesh is synthetic. Gains from delta-encoding depend on how well index order follows space. Check real Blender meshes.
- Per-stroke payloads are only 80 KB, so throughput numbers are noisy (single run).
- Payload (a) is byte-identical across zstd levels 1, 3 and 9. Likely cause: almost no matches, so all levels emit the same literal block (UNVERIFIED).
- Full positions (d) compress to about 1.1x. Store full snapshots only as occasional keyframes, never per stroke.
- The dense uint8 mask (e) costs about 5.4 KB with zstd L3, so a dense mask is fine.

## 4. GitHub Actions

- Sandbox limits: `download.blender.org` was unreachable (curl code 000) and the GitHub API returned 403. The Blender URL, the tarball name pattern, and all action versions below are UNVERIFIED. Pin versions after checking them in a real run.
- Blender 4.2 LTS tarball pattern (UNVERIFIED): `https://download.blender.org/release/Blender4.2/blender-4.2.23-linux-x64.tar.xz`.
- Plan B (verified locally): `pip install bpy==4.2.*` on Python 3.11 (the bpy42 venv here is bpy 4.2.23 from pip), then run `blender_ext.py build` as above. This avoids the tarball and any GUI-library dependencies on the runner.

`.github/workflows/release.yml`:

```yaml
name: release
on:
  push:
    tags: ['v*']
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - name: Check tag matches manifest version
        run: test "${GITHUB_REF_NAME#v}" = "$(python3 -c 'import tomllib,sys;print(tomllib.load(open("addon/blender_manifest.toml","rb"))["version"])')"
      - name: Fetch Blender 4.2 LTS
        run: |
          curl -fsSL -o "$RUNNER_TEMP/blender.tar.xz" \
            https://download.blender.org/release/Blender4.2/blender-4.2.23-linux-x64.tar.xz
          mkdir -p "$RUNNER_TEMP/blender"
          tar -xJf "$RUNNER_TEMP/blender.tar.xz" -C "$RUNNER_TEMP/blender" --strip-components=1
      - name: Build
        run: $RUNNER_TEMP/blender/blender --command extension build --source-dir addon/ --output-filepath dist/hindsight.zip
      - name: Validate
        run: $RUNNER_TEMP/blender/blender --command extension validate dist/hindsight.zip
      - name: Publish release
        uses: softprops/action-gh-release@v2
        with:
          files: dist/hindsight.zip
```

- Replace `addon/` with the folder that holds `blender_manifest.toml`.
- Blender's Linux build may need system libraries to run headless on the runner (UNVERIFIED). If so, install them with apt or use plan B.
- `softprops/action-gh-release@v2` with `files:` attaches the asset under its filename (`hindsight.zip`). When triggered by a tag, it uses the tag as the release name.

`.github/workflows/pages.yml`:

```yaml
name: pages
on:
  push:
    branches: [main]          # default branch assumed (UNVERIFIED)
    paths: ['site/**']
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/upload-pages-artifact@v3
        with:
          path: site
      - id: deployment
        uses: actions/deploy-pages@v4
```

- Repo setting: Settings > Pages > Build and deployment > Source: "GitHub Actions". Without this, `deploy-pages` fails.
- The Pages URL `https://min3scon.github.io/Hindsight/` means the site is served under `/Hindsight/`. Use relative links in `site/`.
- `releases/latest/download/hindsight.zip` redirects to the asset of the most recent release marked Latest. Drafts and prereleases are skipped, so a `v0.2.0-beta` tag will not win. The asset name must match exactly.
- Keep the latest release non-prerelease, or the stable download link will point at an older version.
