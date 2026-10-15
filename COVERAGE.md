# Coverage

What Hindsight records, and how sure we are. This file is the source for what the add-on, the docs,
the site and the release notes may claim. `tools/check_copy.py` fails if the built site names a data
type from a row that isn't `supported` or `partial`, except in a sentence that says it isn't recorded
or is planned.

**Status** comes only from the tier gate (HANDOFF.md, "Test guarantee"), with the evidence in
TEST_EVIDENCE.md, which `tools/make_evidence.py` generates from real test runs and which also checks
this column: a row may be `supported` only when every gate item has a passing run on Blender 5.0.1,
5.1.1 and 5.1.2.
- **supported**: the full gate passed for the whole row.
- **partial**: the gate passed for part of the row; Limits says exactly what works and what doesn't.
- **not covered**: the gate hasn't passed (the code may exist). Notes says why.

**Recording**: `default` (recorded for everyone), `opt-in` (preference Record set to Everything and
"Record everything on this object" switched on), `-` (not written yet).

| ID | Data | Tier | Recording | Status | Since | Limits | Notes | Cost | Site terms |
|---|---|---|---|---|---|---|---|---|---|
| sculpt.positions | Sculpt: vertex positions | 0 | default | not covered | 1.0.0 | Multires and Dyntopo pause recording; an object's history starts at its first stroke, so at earlier positions of the global history it stays as it was then (with any colour layer Vertex Paint added) | tested to the earlier standard (TEST_AUDIT.md); gate open: independent and replay oracle, Hypothesis for the recorder, undo matrix (Global Undo off, undo steps min and max, Undo History, Revert), reload variants (rename, copy-paste, append, link), recorder mutation testing, soak | 7.9 KB per 2% stroke at 100k (PERF_LOG) | vertex positions; sculpt strokes |
| sculpt.masks | Sculpt: mask, face sets, hidden vertices and faces | 0 | default | not covered | 1.0.0 | | tested to the earlier standard; gate open: as sculpt.positions | small | masks; face sets; hidden faces |
| sculpt.colors | Colour attributes (Vertex Paint and sculpt paint) | 0 | default | not covered | 1.0.0 | | gate open: no real Vertex Paint stroke in the UI tests yet; as sculpt.positions | - | colour attributes; vertex paint |
| sculpt.topology | Topology changes (remesh, decimate, ...) | 0 | default | not covered | 1.0.0 | a full copy of the mesh for that step; going back over a topology change keeps vertex groups and shape keys but not their weights and shapes unless Record everything is on for the object | gate open: no real remesh in the UI tests yet; as sculpt.positions | mesh size | topology changes; voxel remesh |
| sculpt.editvisit | Edit Mode, one step per visit | 0 | default | not covered | 1.0.0 | objects that already have a history | gate open: headless only | - | edit mode |
| sculpt.joinsep | Join, separate, duplicate (histories) | 0 | default | not covered | 1.0.0 | a linked duplicate (Alt+D) shares one mesh: a warning says so | gate open: oracle, undo, reload variants, Hypothesis | - | join; separate; duplicate |
| obj.transforms | Object transforms: location, rotation (with its mode), scale, delta transforms | 1 | opt-in | not covered | | animated or driven properties aren't recorded; changes made in a session without Hindsight become one step when the file is opened, but an object whose mesh was changed without Hindsight has its history left out (a message says so) | gate not run (TEST_AUDIT.md) | 0.03 ms per object to read | object transforms; location; rotation; scale |
| obj.parenting | Parenting (parent, parent type and bone, parent inverse) | 1 | opt-in | not covered | | parenting with deform (Armature, Lattice, Curve) also adds a modifier and vertex groups, which aren't recorded yet (a note says so); an object's own history can't bring back a parent that is deleted now (a note says so, also after reopening the file; the global history can) | gate not run | included above | parenting; parent |
| obj.constraints | Constraints (adding, removing, reordering, renaming, settings) | 1 | opt-in | not covered | | settings that are lists (Armature constraint targets) aren't recorded; animated or driven settings aren't recorded; a target that is deleted now can't come back through the object's own history (a note says so; the global history can) | gate not run; D15 open | small | constraints; constraint |
| obj.collections | Collections an object is in, visibility (viewport, render, selectable, hidden with H) | 1 | opt-in | not covered | | collections you make or delete are undone only when the scene is recorded (Record > Objects added and deleted); a collection the global history brings back gets its name and place back, not its other settings (colour tag, render switch, instance offset); hidden (H) is recorded for every view layer; an object excluded by Collection Filtering records nothing, and what changed while it was excluded becomes one step when it isn't any more | gate not run | small | collection membership; visibility |
| obj.addremove | Adding, deleting, duplicating, renaming objects | 1 | opt-in | not covered | | needs "Record > Objects added and deleted"; mesh and empty objects come back with their settings, constraints, modifiers, custom properties, material slots and hidden state, other types only while their data still exists in the session; physics and particle systems don't come back (a note on such objects says so); objects added or duplicated while it is on are recorded fully (Record everything is switched on for them); a mesh shared by linked duplicates (Alt+D) is recorded once, in the history of the object that recorded it first (a note on the others says so); linked objects (from another file) are never recorded; a name another object holds can't be given back (Blender adds .001, a note says so) | gate not run | a copy of the mesh per added or deleted object | adding objects; deleting objects; renaming |
| obj.editsteps | Edit Mode step by step | 1 | opt-in | not covered | | meshes up to 300,000 vertices; above that one step per visit; Edit Mode on a linked duplicate is one step per visit in the history of the object that records the shared mesh; vertex group weights and shape key positions are part of the steps, but adding or removing a vertex group or a shape key isn't recorded yet; weights outside 0 to 1 (Blender's bevel and inset can leave tiny negative ones) come back as 0 to 1 (a note says so) | gate not run; D14 open (Object Mode mesh changes) | 55 ms per operation at 100k vertices | edit mode step by step |
| proc.modifiers | Modifiers and Geometry Nodes inputs | 2 | - | not covered | | | code parked on branch tier2 | - | modifiers; geometry nodes inputs |
| proc.nodetrees | Node trees | 2 | - | not covered | | | code parked on branch tier2 | - | node trees; shader nodes |
| proc.materials | Materials | 2 | - | not covered | | | code parked on branch tier2 | - | materials; material |
| paint.weights | Weight paint and vertex groups | 3 | - | not covered | | | not written | - | weight paint; vertex groups |
| paint.uvs | UV maps, seams, sharp edges, creases, custom normals | 3 | - | not covered | | | not written | - | uv maps; uvs; seams; sharp edges; creases; custom normals |
| paint.texture | Texture paint | 3 | - | not covered | | | not written | - | texture paint |
| anim.keys | Keyframes and F-curves | 4 | - | not covered | | | not written | - | keyframes; f-curves |
| anim.shapekeys | Shape keys | 4 | - | not covered | | | not written | - | shape keys |
| anim.armature | Armatures and pose bones | 4 | - | not covered | | | not written | - | armatures; pose bones |
| anim.nla | NLA, frame range | 4 | - | not covered | | | not written | - | nla; frame range |
| types.geometry | Curves, text, metaballs, lattices, Grease Pencil | 5 | - | not covered | | | not written | - | curve objects; text objects; metaballs; lattices; grease pencil |
| types.lights | Lights, cameras, empties | 5 | - | not covered | | | not written | - | lights; cameras; empties |
| scene.settings | Scene, world, render settings, collection structure | 6 | - | not covered | | | not written | - | world settings; render settings; collection structure |
