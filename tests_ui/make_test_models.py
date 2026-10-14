"""Generate the test models into test_models/ (git-ignored). Runs inside Blender:

    python tests_ui/hs_ui.py models          (wraps: blender -b --factory-startup --python this -- OUT)

Every model is made from scratch here, so screenshots of them are safe to publish.
Files:
  size_1k/10k/100k/500k/1m/2m.blend   one head-like blob each (vertex counts within ~2%)
  shapes.blend      blob, torus, subdivided cube, grid, subdivided Suzanne, multi-part character
  scene_many.blend  40 objects in nested collections, a linked-duplicate pair, a join pair, an
                    object with two loose parts to separate, 12 "sculpted" objects
  data_layers.blend mask, face sets, hidden faces, float and byte colour layers, an Edit Mode edit
  awkward.blend     Multires, subsurf, mirror, armature, displace, shape keys, vertex groups,
                    materials, animation, parenting, rotated and non-uniformly scaled, non-manifold
                    and loose geometry, a 1-vertex mesh, an empty mesh
"""
import math
import os
import sys
import time

import bpy
import numpy as np

OUT = os.path.abspath(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else os.path.abspath("test_models")
os.makedirs(OUT, exist_ok=True)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def mesh_from_arrays(name, verts, quads, tris=None):
    """Fast mesh creation with foreach_set (no Python lists of tuples)."""
    me = bpy.data.meshes.new(name)
    verts = np.asarray(verts, np.float32).reshape(-1, 3)
    quads = np.asarray(quads, np.int32).reshape(-1, 4)
    tris = np.zeros((0, 3), np.int32) if tris is None else np.asarray(tris, np.int32).reshape(-1, 3)
    me.vertices.add(len(verts))
    me.vertices.foreach_set("co", verts.ravel())
    nl = quads.size + tris.size
    me.loops.add(nl)
    me.loops.foreach_set("vertex_index", np.concatenate([quads.ravel(), tris.ravel()]).astype(np.int32))
    me.polygons.add(len(quads) + len(tris))
    starts = np.concatenate([np.arange(len(quads)) * 4, quads.size + np.arange(len(tris)) * 3]).astype(np.int32)
    me.polygons.foreach_set("loop_start", starts)   # Blender 4+ derives face sizes from the starts
    me.update(calc_edges=True)
    me.validate(clean_customdata=False)
    try:
        me.shade_smooth()
    except Exception:
        for p in me.polygons:
            p.use_smooth = True
    return me


def link(me, name=None, coll=None, location=(0, 0, 0)):
    ob = bpy.data.objects.new(name or me.name, me)
    (coll or bpy.context.scene.collection).objects.link(ob)
    ob.location = location
    return ob


def head_blob(target_verts, name="Blob", seed=0):
    """A smooth, slightly asymmetric head-like shape: a UV sphere with low-frequency bumps."""
    rings = max(6, int(round(math.sqrt(target_verts / 2.0))))
    seg = max(8, int(round((target_verts - 2) / (rings - 1))))
    th = np.linspace(0, np.pi, rings + 1)[1:-1]           # latitudes without the poles
    ph = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    T, P = np.meshgrid(th, ph, indexing="ij")
    rng = np.random.default_rng(seed)
    a = rng.uniform(0.04, 0.09, 4)
    r = (1.0 + a[0] * np.sin(3 * T) * np.cos(2 * P) + a[1] * np.cos(5 * T)
         + a[2] * np.sin(2 * P + 1.0) * np.sin(T) ** 2)
    x = r * np.sin(T) * np.cos(P) * 0.92
    y = r * np.sin(T) * np.sin(P) * 1.05 + 0.18 * np.sin(T) ** 4 * (np.cos(P) > 0)   # a soft brow
    z = r * np.cos(T) * 1.18
    body = np.stack([x, y, z], -1).reshape(-1, 3)
    verts = np.concatenate([[[0, 0, 1.18 * (1 + a[1])]], body, [[0, 0, -1.18 * (1 - a[1] * 0.0)]]]).astype(np.float32)
    quads = []
    idx = lambda i, j: 1 + i * seg + (j % seg)
    i = np.arange(rings - 2)[:, None]
    j = np.arange(seg)[None, :]
    q = np.stack([idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)], -1).reshape(-1, 4)
    top = np.stack([np.zeros(seg, int), idx(0, np.arange(seg) + 1), idx(0, np.arange(seg))], -1)
    last = len(verts) - 1
    bot = np.stack([np.full(seg, last), idx(rings - 2, np.arange(seg)), idx(rings - 2, np.arange(seg) + 1)], -1)
    return mesh_from_arrays(name, verts, q, np.concatenate([top, bot]))


def save(name):
    path = os.path.join(OUT, name)
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=False)
    print(f"wrote {name} ({os.path.getsize(path) / 2**20:.1f} MB)")


def apply_subsurf(ob, levels):
    m = ob.modifiers.new("Subdivision", 'SUBSURF')
    m.levels = levels
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.modifier_apply(modifier=m.name)


def select_only(ob):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)


# ------------------------------------------------------------------------------------ sizes
SIZES = (("1k", 1_000), ("10k", 10_000), ("100k", 100_000), ("500k", 500_000), ("1m", 1_000_000),
         ("2m", 2_000_000))


def make_sizes():
    for tag, n in SIZES:
        reset()
        t = time.time()
        me = head_blob(n, f"Blob_{tag}", seed=len(tag))
        link(me, f"Blob_{tag}")
        print(f"  {tag}: {len(me.vertices)} verts in {time.time() - t:.1f} s")
        save(f"size_{tag}.blend")


# ------------------------------------------------------------------------------------ shapes
def make_shapes():
    reset()
    link(head_blob(20_000, "Blob"), "Blob", location=(-6, 0, 0))
    bpy.ops.mesh.primitive_torus_add(major_segments=96, minor_segments=48, location=(-3, 0, 0))
    bpy.context.active_object.name = "Torus"
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
    cube = bpy.context.active_object
    cube.name = "Cube_subdiv"
    apply_subsurf(cube, 4)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=160, y_subdivisions=160, size=2.5, location=(3, 0, 0))
    bpy.context.active_object.name = "Grid"
    bpy.ops.mesh.primitive_monkey_add(location=(6, 0, 0))
    monkey = bpy.context.active_object
    monkey.name = "Suzanne_subdiv"
    apply_subsurf(monkey, 2)
    # multi-part character as separate objects
    char = bpy.data.collections.new("Character")
    bpy.context.scene.collection.children.link(char)
    parts = [("Char_Head", (0, 0, 2.6), 0.55, 4000), ("Char_Torso", (0, 0, 1.6), 0.8, 8000),
             ("Char_Arm_L", (-1.0, 0, 1.7), 0.25, 2000), ("Char_Arm_R", (1.0, 0, 1.7), 0.25, 2000),
             ("Char_Leg_L", (-0.35, 0, 0.5), 0.3, 2500), ("Char_Leg_R", (0.35, 0, 0.5), 0.3, 2500)]
    for i, (name, loc, s, n) in enumerate(parts):
        ob = link(head_blob(n, name, seed=10 + i), name, coll=char, location=(loc[0], loc[1] - 5, loc[2]))
        ob.scale = (s, s, s * (2.2 if "Arm" in name or "Leg" in name else 1.0))
    save("shapes.blend")


# ------------------------------------------------------------------------------------ scene
def make_scene_many():
    reset()
    root = bpy.context.scene.collection
    groups = []
    for gi in range(5):
        c = bpy.data.collections.new(f"Group_{gi}")
        root.children.link(c)
        groups.append(c)
        sub = bpy.data.collections.new(f"Group_{gi}_inner")
        c.children.link(sub)
        groups.append(sub)
    rng = np.random.default_rng(3)
    for k in range(40):
        me = head_blob(1500 + 100 * (k % 7), f"Obj_{k:02d}", seed=100 + k)
        link(me, f"Obj_{k:02d}", coll=groups[k % len(groups)],
             location=(float(rng.uniform(-12, 12)), float(rng.uniform(-12, 12)), 0))
    # sculpted-looking objects (12)
    sculpted = bpy.data.collections.new("Sculpted")
    root.children.link(sculpted)
    for k in range(12):
        me = head_blob(6000, f"Sculpted_{k:02d}", seed=200 + k)
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        for _ in range(8):
            c = co[rng.integers(len(co))]
            d = np.linalg.norm(co - c, axis=1)
            w = np.clip(1 - d / 0.4, 0, 1) ** 2
            co += (co / np.linalg.norm(co, axis=1, keepdims=True)) * (w[:, None] * rng.uniform(-0.12, 0.15))
        me.vertices.foreach_set("co", co.ravel())
        me.update()
        link(me, f"Sculpted_{k:02d}", coll=sculpted, location=(-15 + k * 2.6, 16, 0))
    # linked duplicate pair (shared mesh)
    shared = head_blob(3000, "SharedMesh", seed=300)
    link(shared, "Linked_A", location=(0, -16, 0))
    link(shared, "Linked_B", location=(3, -16, 0))
    # join pair and an object with two loose parts
    link(head_blob(2000, "JoinMe_1", seed=301), "JoinMe_1", location=(8, -16, 0))
    link(head_blob(2000, "JoinMe_2", seed=302), "JoinMe_2", location=(10.5, -16, 0))
    a = head_blob(1500, "tmpA", seed=303)
    b = head_blob(1500, "tmpB", seed=304)
    oa = link(a, "SeparateMe", location=(14, -16, 0))
    ob_ = link(b, "tmpB", location=(16, -16, 0))
    select_only(oa)
    ob_.select_set(True)
    bpy.ops.object.join()
    save("scene_many.blend")


# ------------------------------------------------------------------------------------ data
def make_data_layers():
    reset()
    me = head_blob(12_000, "Layers", seed=400)
    ob = link(me, "Layers")
    n, f = len(me.vertices), len(me.polygons)
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", co)
    z = co.reshape(-1, 3)[:, 2]
    mask = me.attributes.new(".sculpt_mask", 'FLOAT', 'POINT')
    mask.data.foreach_set("value", np.clip((z + 0.2) * 1.5, 0, 1).astype(np.float32))
    fs = me.attributes.new(".sculpt_face_set", 'INT', 'FACE')
    fs.data.foreach_set("value", (np.arange(f) // max(1, f // 6) + 1).astype(np.int32))
    hide = me.attributes.new(".hide_poly", 'BOOLEAN', 'FACE')
    hv = np.zeros(f, bool)
    hv[: f // 20] = True
    hide.data.foreach_set("value", hv)
    col = me.color_attributes.new("PaintFloat", 'FLOAT_COLOR', 'POINT')
    rgba = np.stack([np.clip(z, 0, 1), np.full(n, 0.4), 1 - np.clip(z, 0, 1), np.ones(n)], -1).astype(np.float32)
    col.data.foreach_set("color", rgba.ravel())
    bcol = me.color_attributes.new("PaintByte", 'BYTE_COLOR', 'CORNER')
    nl = len(me.loops)
    bcol.data.foreach_set("color", np.tile([0.8, 0.3, 0.2, 1.0], nl).astype(np.float32))
    # an Edit Mode edit: extrude a few faces
    select_only(ob)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    for p in me.polygons[f // 2: f // 2 + 12]:
        p.select = True
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.extrude_region_shrink_fatten(TRANSFORM_OT_shrink_fatten={"value": 0.15})
    bpy.ops.object.mode_set(mode='OBJECT')
    save("data_layers.blend")


# ------------------------------------------------------------------------------------ awkward
def make_awkward():
    reset()
    sc = bpy.context.scene.collection
    x = [0.0]

    def place():
        x[0] += 3.0
        return (x[0], 0, 0)

    ob = link(head_blob(3000, "Multires", seed=500), "Multires", location=place())
    m = ob.modifiers.new("Multires", 'MULTIRES')
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.multires_subdivide(modifier="Multires", mode='CATMULL_CLARK')
    ob = link(head_blob(3000, "Mod_Subsurf", seed=501), "Mod_Subsurf", location=place())
    ob.modifiers.new("Subdivision", 'SUBSURF').levels = 1
    ob = link(head_blob(3000, "Mod_Mirror", seed=502), "Mod_Mirror", location=place())
    ob.modifiers.new("Mirror", 'MIRROR')
    ob = link(head_blob(3000, "Mod_Displace", seed=503), "Mod_Displace", location=place())
    d = ob.modifiers.new("Displace", 'DISPLACE')
    d.texture = bpy.data.textures.new("Noise", 'CLOUDS')
    d.strength = 0.1
    arm_data = bpy.data.armatures.new("Rig")
    arm = bpy.data.objects.new("Rig", arm_data)
    sc.objects.link(arm)
    ob = link(head_blob(3000, "Mod_Armature", seed=504), "Mod_Armature", location=place())
    arm.location = ob.location
    ob.modifiers.new("Armature", 'ARMATURE').object = arm
    ob = link(head_blob(3000, "ShapeKeys", seed=505), "ShapeKeys", location=place())
    ob.shape_key_add(name="Basis")
    k = ob.shape_key_add(name="Puff")
    co = np.empty(len(ob.data.vertices) * 3, np.float32)
    k.data.foreach_get("co", co)
    k.data.foreach_set("co", (co * 1.1).astype(np.float32))
    k.value = 0.5
    ob = link(head_blob(3000, "VertexGroups", seed=506), "VertexGroups", location=place())
    vg = ob.vertex_groups.new(name="Weights")
    vg.add(list(range(0, len(ob.data.vertices), 3)), 0.7, 'REPLACE')
    ob = link(head_blob(3000, "Materials", seed=507), "Materials", location=place())
    for nm in ("Skin", "Stone"):
        ob.data.materials.append(bpy.data.materials.new(nm))
    ob = link(head_blob(3000, "Animated", seed=508), "Animated", location=place())
    ob.keyframe_insert("location", frame=1)
    ob.location.z += 2
    ob.keyframe_insert("location", frame=40)
    parent = bpy.data.objects.new("ParentEmpty", None)
    sc.objects.link(parent)
    parent.location = place()
    ob = link(head_blob(3000, "Parented", seed=509), "Parented")
    ob.parent = parent
    ob = link(head_blob(3000, "RotatedScaled", seed=510), "RotatedScaled", location=place())
    ob.rotation_euler = (0.3, 0.8, 0.5)
    ob.scale = (1.0, 2.0, 0.5)
    # non-manifold + loose geometry
    me = head_blob(2000, "NonManifold", seed=511)
    ob = link(me, "NonManifold", location=place())
    select_only(ob)
    bpy.ops.object.mode_set(mode='EDIT')
    import bmesh
    bm = bmesh.from_edit_mesh(me)
    bmesh.ops.delete(bm, geom=list(bm.faces)[:40], context='FACES_ONLY')   # open hole
    v1 = bm.verts.new((0, 0, 2.0))
    v2 = bm.verts.new((0.3, 0, 2.2))
    bm.edges.new((v1, v2))                                                 # loose edge
    bm.verts.new((0, 0, 2.6))                                              # loose vertex
    bmesh.update_edit_mesh(me)
    bpy.ops.object.mode_set(mode='OBJECT')
    one = bpy.data.meshes.new("OneVertex")
    one.vertices.add(1)
    link(one, "OneVertex", location=place())
    link(bpy.data.meshes.new("EmptyMesh"), "EmptyMesh", location=place())
    save("awkward.blend")


if __name__ == "__main__":
    what = sys.argv[sys.argv.index("--") + 2:] if "--" in sys.argv else []
    t0 = time.time()
    for name, fn in (("sizes", make_sizes), ("shapes", make_shapes), ("scene", make_scene_many),
                     ("data", make_data_layers), ("awkward", make_awkward)):
        if what and name not in what:
            continue
        print("==", name)
        fn()
    print(f"done in {time.time() - t0:.0f} s -> {OUT}")
