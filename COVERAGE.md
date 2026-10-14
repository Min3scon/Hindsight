# Coverage

What Hindsight records, and how sure we are. This file is the source for what the add-on, the docs,
the site and the release notes may claim: `tools/check_copy.py` fails if the built site names a data
type from a row that isn't `supported` or `opt-in`, except in a sentence that says it isn't recorded
or is planned.

Statuses: **supported** (recorded by default), **opt-in** (recorded with the preference Record set to
Everything and "Record everything on this object" switched on), **planned** (not recorded yet).
Costs are measured (see PERF_LOG.md and the tests named); "-" means not measured yet.

| Data | Status | Since | Tests | Limits | Cost | Site terms |
|---|---|---|---|---|---|---|
| Sculpt: vertex positions | supported | 1.0.0 | test_bl_recorder, round_c, regress_* | Multires and Dyntopo pause recording | 7.9 KB per 2% stroke at 100k (PERF_LOG) | vertex positions; sculpt strokes |
| Sculpt: mask, face sets, hidden vertices and faces | supported | 1.0.0 | test_bl_recorder, round_c | | small | masks; face sets; hidden faces |
| Colour attributes (Vertex Paint and sculpt paint) | supported | 1.0.0 | test_bl_recorder, round_b | | - | colour attributes; vertex paint |
| Topology changes (remesh, decimate, ...) | supported | 1.0.0 | test_core_history, round_c | a full copy of the mesh for that step | mesh size | topology changes; voxel remesh |
| Edit Mode, one step per visit | supported | 1.0.0 | test_bl_regressions | objects that already have a history | - | edit mode |
| Join, separate, duplicate (histories) | supported | 1.0.0 | test_bl_join, test_bl_dupsep, round_b_features | | - | join; separate; duplicate |
| Object transforms: location, rotation (with its mode), scale, delta transforms | opt-in | 1.0.0-beta.2 | test_bl_adapters, test_bl_tier1, s_adapters, s_tier1 | animated or driven properties aren't recorded | 0.03 ms per object to read | object transforms; location; rotation; scale |
| Parenting (parent, parent type and bone, parent inverse) | opt-in | 1.0.0-beta.2 | test_bl_tier1, s_tier1 (Ctrl+P) | | included above | parenting; parent |
| Constraints (adding, removing, reordering, renaming, settings) | opt-in | 1.0.0-beta.2 | test_bl_tier1, s_tier1 | settings that are lists (Armature constraint targets) aren't recorded | small | constraints; constraint |
| Collections an object is in, visibility (viewport, render, selectable, hidden with H) | opt-in | 1.0.0-beta.2 | test_bl_tier1, s_tier1 (H, Alt+H) | | small | collection membership; visibility |
| Adding, deleting, duplicating, renaming objects | opt-in | 1.0.0-beta.2 | test_bl_tier1, s_tier1 (Shift+D, X, Shift+A, global slider) | needs "Record adding and deleting objects"; mesh and empty objects come back fully; other types only while their data still exists in the session. A deleted or added object is stored with a copy of its mesh | a copy of the mesh per added or deleted object | adding objects; deleting objects; renaming |
| Edit Mode step by step | opt-in | 1.0.0-beta.2 | test_bl_tier1, s_tier1 (G, E, Ctrl+Z in Edit Mode) | meshes up to 300,000 vertices; above that one step per visit | 55 ms per operation at 100k vertices | edit mode step by step |
| Modifiers and Geometry Nodes inputs | planned | | | | | modifiers; geometry nodes inputs |
| Node trees | planned | | | | | node trees; shader nodes |
| Materials | planned | | | | | materials; material |
| Weight paint and vertex groups | planned | | | | | weight paint; vertex groups |
| UV maps, seams, sharp edges, creases, custom normals | planned | | | | | uv maps; uvs; seams; sharp edges; creases; custom normals |
| Texture paint | planned | | | | | texture paint |
| Keyframes and F-curves | planned | | | | | keyframes; f-curves |
| Shape keys | planned | | | | | shape keys |
| Armatures and pose bones | planned | | | | | armatures; pose bones |
| NLA, frame range | planned | | | | | nla; frame range |
| Curves, text, metaballs, lattices, Grease Pencil | planned | | | | | curve objects; text objects; metaballs; lattices; grease pencil |
| Lights, cameras, empties | planned | | | | | lights; cameras; empties |
| Scene, world, render settings, collection structure | planned | | | | | world settings; render settings; collection structure |
