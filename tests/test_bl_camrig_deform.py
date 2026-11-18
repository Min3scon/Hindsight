"""Movie Mode camera with modifiers that only move vertices (Armature, Lattice, shape keys).

A vertex that is d away from where it is now, somewhere in the history, renders at most
stretch * d away from where it renders now: exact for an Armature on vertex groups (each vertex
moves by a fixed blend of its bones' transforms, so stretch is the largest bone scale), an estimate
for the others (the largest edge stretch now). Before, the bound added the largest displacement of
any vertex to the whole history, which left a posed model at about 40% of the frame.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import math

import bpy

import tests.harness as H
from hindsight.bl import camrig
import test_bl_camrig_modifiers as T

C = bpy.context


def rig_armature(ob, angle=80.0, scale=1.0):
    """A two-bone armature at the origin; the upper half of the mesh follows the upper bone, which
    is turned by `angle` degrees (and scaled by `scale`)."""
    arm = bpy.data.armatures.new("Arm")
    ao = bpy.data.objects.new("Arm", arm)
    C.scene.collection.objects.link(ao)
    C.view_layer.objects.active = ao
    bpy.ops.object.mode_set(mode="EDIT")
    lower = arm.edit_bones.new("Lower")
    lower.head, lower.tail = (0, 0, -1), (0, 0, 0)
    upper = arm.edit_bones.new("Upper")
    upper.head, upper.tail = (0, 0, 0), (0, 0, 1)
    upper.parent = lower
    bpy.ops.object.mode_set(mode="OBJECT")
    ob.vertex_groups.new(name="Upper").add([v.index for v in ob.data.vertices if v.co.z > 0], 1.0, "REPLACE")
    ob.vertex_groups.new(name="Lower").add([v.index for v in ob.data.vertices if v.co.z <= 0], 1.0, "REPLACE")
    ob.modifiers.new("Armature", "ARMATURE").object = ao
    pb = ao.pose.bones["Upper"]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler = (math.radians(angle), 0, 0)
    pb.scale = (scale, scale, scale)
    C.view_layer.objects.active = ob
    return ao


def history(ob, early):
    if early == "shift":                    # the whole model far away from the joint, early on
        H.enter(ob, "SCULPT")
        T.scale_mesh(ob, 1.0, (0.0, 0.0, 5.0))
        assert H.commit(ob) is not None
        T.scale_mesh(ob, 1.0, (0.0, 0.0, -5.0))
        assert H.commit(ob) is not None
        early = "plain"
    return T.make_history(ob, early)


def run(name, setup, min_span, earlies=("plain", "big", "small", "shift")):
    spans = {}
    for early in earlies:
        H.fresh()
        ob = H.make_mesh(1200, "Model")
        setup(ob)
        C.view_layer.update()
        n = history(ob, early)
        spans[early] = T.check_every_step(ob, n, False, f"{name}/{early}", min_span.get(early, 0.0))
    print(name, {k: round(v, 3) for k, v in spans.items()})


def test_armature_holds_and_is_tight():
    run("armature", rig_armature, {"plain": 0.88, "big": 0.85, "small": 0.85, "shift": 0.7})


def test_armature_scaled_bone():
    run("armature scaled bone", lambda ob: rig_armature(ob, 50.0, 1.6), {"plain": 0.85, "big": 0.8, "small": 0.8})


def test_armature_parented():
    """The mesh parented to the armature, both moved and turned: the bound is taken in the mesh's
    own space."""
    def setup(ob):
        ao = rig_armature(ob, 60.0)
        ob.parent = ao
        ao.location = (2.0, -1.0, 0.5)
        ao.rotation_euler = (0.2, 0.4, 0.9)
    run("armature parented", setup, {"plain": 0.85, "big": 0.85, "small": 0.85})


def test_armature_preserve_volume():
    """Preserve Volume blends the bones' turns, not their matrices: the hull of the bones' moves
    doesn't hold it, so only the per-vertex bound (exact: a blended turn moves no point farther)."""
    def setup(ob):
        rig_armature(ob, 70.0)
        ob.modifiers["Armature"].use_deform_preserve_volume = True
    run("armature preserve volume", setup, {"plain": 0.85})


def test_armature_bbones():
    """B-Bones bend along their segments, which their pose matrix doesn't tell: the estimate from
    the edges' stretch is used, and still holds the model."""
    def setup(ob):
        ao = rig_armature(ob, 50.0)
        for b in ao.data.bones:
            b.bbone_segments = 8
        pb = ao.pose.bones["Upper"]
        pb.bbone_curveinx = 0.8
        pb.bbone_curveoutz = -0.6
        pb.bbone_easein = 1.5
        C.view_layer.update()
    run("armature b-bones", setup, {})


def test_lattice():
    def setup(ob):
        lo = bpy.data.objects.new("Lattice", bpy.data.lattices.new("Lattice"))
        C.scene.collection.objects.link(lo)
        lo.scale = (2.5, 2.5, 2.5)
        ob.modifiers.new("Lattice", "LATTICE").object = lo
        C.view_layer.update()
        lo.data.points[0].co_deform.x -= 0.4
        lo.data.points[7].co_deform.z += 0.6
    run("lattice", setup, {"plain": 0.8})


def test_armature_then_subdivision():
    """Not only moving vertices: the general bound (envelope and measured reach) still holds it."""
    def setup(ob):
        rig_armature(ob, 40.0)
        ob.modifiers.new("Subdivision", "SUBSURF").levels = 1
    run("armature + subdivision", setup, {})


if __name__ == "__main__":
    for f in [test_armature_holds_and_is_tight, test_armature_scaled_bone, test_armature_parented,
              test_armature_preserve_volume, test_armature_bbones, test_lattice, test_armature_then_subdivision]:
        H.run(f)
    H.finish()
