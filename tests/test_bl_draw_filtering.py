import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Collection Filtering gives the same answers as before for less: is_excluded runs for every object
on every redraw with global history on, and it used to walk obj.users_collection (Blender scans
every collection in the file for that) before the one lookup that decides."""

import itertools
import time

import bpy

sys.path.insert(0, os.path.dirname(__file__))
import harness as H  # noqa: E402
from hindsight.bl import filtering as F  # noqa: E402

def _old_object_in_collection(obj, coll):
    """filtering.object_in_collection at 66fcdf8."""
    try:
        for uc in obj.users_collection:
            if uc == coll:
                return True
        objs = coll.all_objects
        found = objs.get(obj.name)
        if found is None:
            return False
        if found == obj:
            return True
        return any(o == obj for o in objs)
    except (ReferenceError, AttributeError):
        return False


def _old_is_excluded(obj, scene):
    sh = scene.hindsight
    if sh.filter_mode == 'BLACKLIST':
        return any(_old_object_in_collection(obj, c) for c in F.listed_collections(scene))
    if sh.filter_mode == 'WHITELIST':
        return not any(_old_object_in_collection(obj, c) for c in F.listed_collections(scene))
    return False


def _scene():
    H.fresh()
    sc = bpy.context.scene
    # A > A1 > A11, B, C (C also linked under A1), D empty; objects in one, several or no collection
    A, A1, A11, B, C, D = (bpy.data.collections.new(n) for n in ("A", "A1", "A11", "B", "C", "D"))
    sc.collection.children.link(A)
    A.children.link(A1)
    A1.children.link(A11)
    sc.collection.children.link(B)
    sc.collection.children.link(C)
    A1.children.link(C)
    sc.collection.children.link(D)
    colls = [A, A1, A11, B, C, D]
    obs = []
    homes = [[A], [A1], [A11], [B], [C], [A, B], [A11, C], [sc.collection], [sc.collection, B], [D, A11]]
    for i, home in enumerate(homes):
        me = bpy.data.meshes.new(f"m{i}")
        ob = bpy.data.objects.new(f"Ob{i}", me)
        for c in home:
            c.objects.link(ob)
        obs.append(ob)
    orphan = bpy.data.objects.new("Orphan", bpy.data.meshes.new("orph"))    # in no collection at all
    obs.append(orphan)
    return sc, colls, obs


def test_same_answers_as_before():
    sc, colls, obs = _scene()
    sh = sc.hindsight
    n = 0
    lists = [()] + [c for k in (1, 2) for c in itertools.combinations(colls, k)] + [tuple(colls)]
    for mode in ('NONE', 'BLACKLIST', 'WHITELIST'):
        sh.filter_mode = mode
        for listed in lists:
            sh.filter_collections.clear()
            for c in listed:
                sh.filter_collections.add().collection = c
            sh.filter_collections.add()                   # an empty row is ignored
            for ob in obs:
                assert F.is_excluded(ob, sc) == _old_is_excluded(ob, sc), (mode, [c.name for c in listed], ob.name)
                for c in colls:
                    assert F.object_in_collection(ob, c) == _old_object_in_collection(ob, c), (ob.name, c.name)
                n += 1
    assert n > 700, n
    sh.filter_mode = 'NONE'
    sh.filter_collections.clear()


def test_faster_with_a_list():
    """50 objects in 10 collections, 3 listed: the redraw-time check is several times cheaper."""
    H.fresh()
    sc = bpy.context.scene
    colls = []
    for i in range(10):
        c = bpy.data.collections.new(f"K{i}")
        sc.collection.children.link(c)
        colls.append(c)
    obs = []
    for i in range(50):
        ob = bpy.data.objects.new(f"P{i}", bpy.data.meshes.new(f"p{i}"))
        colls[i % 10].objects.link(ob)
        obs.append(ob)
    sh = sc.hindsight
    for c in colls[:3]:
        sh.filter_collections.add().collection = c
    sh.filter_mode = 'BLACKLIST'

    def best(fn):
        b = 1e9
        for _ in range(5):
            t = time.perf_counter()
            for ob in obs:
                fn(ob, sc)
            b = min(b, time.perf_counter() - t)
        return b
    old, new = best(_old_is_excluded), best(F.is_excluded)
    print(f"  50 objects: {old * 1e6:.0f} us -> {new * 1e6:.0f} us")
    assert new < old / 2, (old, new)


for f in (test_same_answers_as_before, test_faster_with_a_list):
    H.run(f)
H.finish()
