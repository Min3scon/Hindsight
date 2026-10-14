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
| Object transforms: location, rotation (with its mode), scale, delta transforms | opt-in | 1.0.0-beta.2 | test_bl_adapters, s_adapters | animated or driven properties aren't recorded | 0.03 ms per object to read | object transforms; location; rotation; scale |
| Parenting | planned | | | | | parenting; parent |
| Constraints | planned | | | | | constraints; constraint |
| Collections an object is in, visibility | planned | | | | | collection membership; visibility |
| Adding, deleting, duplicating, renaming objects (scene level) | planned | | | | | adding objects; deleting objects; renaming |
| Edit Mode step by step | planned | | | | | edit mode step by step |
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
