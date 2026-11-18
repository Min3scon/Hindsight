"""Only lighting that renders counts as the scene's own (review S3-2).

A light in a collection excluded from the view layer, disabled in renders (itself or through a
parent collection), with no energy, or only in a view layer the render doesn't use, lights nothing:
Movie Mode adds its lamps. A light that does render, or a world with an image or a sky, is the
scene's own lighting: Movie Mode adds none. A plain colour world (the default) doesn't count.
(Checked against Cycles renders on Blender 5.3 while writing this: a sun lamp in a child of a
collection disabled in renders, or in an excluded collection, or only in an unused view layer,
left the image as dark as no lamp at all.)
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bpy

import tests.harness as H
from hindsight.bl import camrig, runtime

C = bpy.context
LIGHTS = {"key", "fill", "rim"}


def setup():
    H.fresh()
    ob = H.make_mesh(300, "Model")
    H.enter(ob, "SCULPT")
    H.fake_stroke(ob, 1)
    assert H.commit(ob)
    runtime.set_global(C, True)
    sc = C.scene
    parent = bpy.data.collections.new("Old lights")
    sc.collection.children.link(parent)
    child = bpy.data.collections.new("Inner")
    parent.children.link(child)
    lamp = bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "POINT"))
    child.objects.link(lamp)
    sc.hindsight.movie_mode = True
    return sc, parent, child, lamp


def lights_added(sc):
    camrig.ensure(sc)
    got = set(camrig.rig(sc)) & LIGHTS
    assert got in (set(), LIGHTS), got
    assert bool(got) != camrig.scene_has_own_light(sc)
    return bool(got)


def lc(vl, *names):
    c = vl.layer_collection
    for n in names:
        c = c.children[n]
    return c


def test_collections():
    sc, parent, child, lamp = setup()
    vl = C.view_layer
    assert not lights_added(sc), "a lamp that renders"
    lc(vl, "Old lights").exclude = True
    assert lights_added(sc), "excluded from the view layer"
    lc(vl, "Old lights").exclude = False
    lc(vl, "Old lights", "Inner").exclude = True
    assert lights_added(sc), "its own collection excluded"
    lc(vl, "Old lights", "Inner").exclude = False
    assert not lights_added(sc)
    parent.hide_render = True
    assert lights_added(sc), "a parent collection disabled in renders"
    sc.collection.objects.link(lamp)                       # also in the scene's own collection
    assert not lights_added(sc), "renders through another collection"
    sc.collection.objects.unlink(lamp)
    assert lights_added(sc)
    parent.hide_render = False
    child.hide_render = True
    assert lights_added(sc), "its own collection disabled in renders"
    child.hide_render = False
    lamp.hide_render = True
    assert lights_added(sc), "the object disabled in renders"
    lamp.hide_render = False
    lamp.data.energy = 0.0
    assert lights_added(sc), "no energy"
    lamp.data.energy = 10.0
    lamp.data.color = (0.0, 0.0, 0.0)
    assert lights_added(sc), "black"
    lamp.data.color = (1.0, 1.0, 1.0)
    lc(vl, "Old lights").hide_viewport = True              # hidden in the viewport still renders
    lamp.hide_set(True)
    assert not lights_added(sc)


def test_view_layers():
    sc, parent, child, lamp = setup()
    first = C.view_layer
    second = sc.view_layers.new("Second")
    lc(second, "Old lights").exclude = True
    assert not lights_added(sc), "renders in the first layer"
    lc(first, "Old lights").exclude = True
    assert lights_added(sc), "excluded from both"
    lc(first, "Old lights").exclude = False
    first.use = False
    assert lights_added(sc), "only in a layer the render doesn't use"
    lc(second, "Old lights").exclude = False
    assert not lights_added(sc)


def _world(sc):
    w = bpy.data.worlds.new("W")
    sc.world = w
    if bpy.app.version < (5, 0, 0):
        w.use_nodes = True
    return w


def test_world():
    sc, parent, child, lamp = setup()
    parent.hide_render = True                             # no lamp renders
    assert lights_added(sc), "no world"
    w = _world(sc)
    nt = w.node_tree
    bg = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1.0)
    assert lights_added(sc), "a plain colour world doesn't count"
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    assert lights_added(sc), "an Environment Texture without an image"
    env.image = bpy.data.images.new("HDRI", 8, 4, float_buffer=True)
    assert not lights_added(sc), "an HDRI world lights the scene"
    env.mute = True
    assert lights_added(sc), "muted"
    nt.nodes.remove(env)
    sky = nt.nodes.new("ShaderNodeTexSky")
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    assert not lights_added(sc), "a sky world lights the scene"
    bg.inputs["Strength"].default_value = 0.0
    assert lights_added(sc), "a sky at strength 0 lights nothing"
    bg.inputs["Strength"].default_value = 1.0
    assert not lights_added(sc)
    sc.world = None
    assert lights_added(sc)


def test_review_repro():
    """r9_excluded_light.py: both cases used to add no lamps."""
    sc, parent, child, lamp = setup()
    lc(C.view_layer, "Old lights").exclude = True
    sc.hindsight.movie_mode = False
    sc.hindsight.movie_mode = True
    assert set(camrig.rig(sc)) & LIGHTS == LIGHTS
    lc(C.view_layer, "Old lights").exclude = False
    parent.hide_render = True
    camrig.ensure(sc)
    assert set(camrig.rig(sc)) & LIGHTS == LIGHTS


for f in [test_collections, test_view_layers, test_world, test_review_repro]:
    H.run(f)
H.finish()
