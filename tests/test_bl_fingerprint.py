import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""R060: mesh fingerprints are xxh3-128 (the engine's Hasher128), written "x3:" + 32 hex digits.

The digest input is exactly what blake2b was fed before (the counts, then each recorded array in key
order with its name and dtype), so only the hash function changed. A fingerprint stored without a tag
(files and crash journals of builds before R060) is compared by hashing the mesh with blake2b again: a
mesh is never reported as changed because the algorithm changed. A tag this build doesn't know is taken
as unchanged. Opening files with such values: tests/test_bl_t1_outside.py; crash recovery with them:
tests/test_bl_recovery.py; Hasher128 against known values: tests/test_core_hash.py.

Run: blender -b --factory-startup --python tests/test_bl_fingerprint.py (or tests/run_tests.py)."""
import hashlib
import re

import bpy
import numpy as np

import tests.harness as H
from hindsight.bl import meshio, runtime
from hindsight.core.native import ENGINE

X3_FORM = re.compile(r"x3:[0-9a-f]{32}")
OLD_FORM = re.compile(r"[0-9a-f]{32}")


def _mesh_with_extras(name="Fp"):
    ob = H.make_mesh(3000, name=name)
    n = len(ob.data.vertices)
    vg = ob.vertex_groups.new(name="G")
    vg.add(list(range(0, n, 3)), 0.5, "REPLACE")
    ob.shape_key_add(name="Basis")
    sk = ob.shape_key_add(name="Moved")
    for v in sk.data[:50]:
        v.co.x += 0.1
    return ob


def _flip_low_bit(me, i, axis=0):
    """Flip the lowest bit of one coordinate of vertex i (the smallest change a float can have)."""
    a = me.attributes["position"]
    co = np.empty(len(a.data) * 3, np.float32)
    a.data.foreach_get("vector", co)
    co.view(np.uint32)[3 * i + axis] ^= 1
    a.data.foreach_set("vector", co)
    me.update()


def test_new_fingerprints_are_tagged_xxh3():
    H.fresh()
    ob = _mesh_with_extras()
    for fp in (meshio.fingerprint(ob.data), meshio.fingerprint(ob.data, ob), runtime.mesh_fingerprint(ob)):
        assert X3_FORM.fullmatch(fp), fp
    for fp in (meshio.fingerprint(ob.data, None, "blake2b"), runtime.mesh_fingerprint(ob, "blake2b")):
        assert OLD_FORM.fullmatch(fp), fp
    # what a save writes for an object with a history
    clay = H.make_mesh(800, name="Clay")
    H.enter(clay, "SCULPT")
    H.fake_stroke(clay, 3)
    assert H.commit(clay) is not None
    H.enter(clay, "OBJECT")
    recs = [r for r in runtime.saved_prints().values() if isinstance(r, dict) and "mesh" in r]
    assert len(recs) == 1 and X3_FORM.fullmatch(recs[0]["mesh"]), recs
    assert recs[0]["mesh"] == runtime.mesh_fingerprint(clay)
    try:
        meshio.fingerprint(ob.data, None, "md5")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown algorithm must not give a value")


def test_equal_meshes_equal_values_one_bit_differs():
    H.fresh()
    a = _mesh_with_extras("P")
    b = a.copy()                    # an equal mesh of its own (two primitives can number their edges apart)
    b.data = a.data.copy()
    b.name = "Q"
    bpy.context.scene.collection.objects.link(b)
    assert a.data != b.data and a.data.shape_keys != b.data.shape_keys
    for with_ob in (False, True):
        for algo in ("x3", "blake2b"):
            fa = meshio.fingerprint(a.data, a if with_ob else None, algo)
            assert fa == meshio.fingerprint(a.data, a if with_ob else None, algo), "the same mesh twice"
            assert fa == meshio.fingerprint(b.data, b if with_ob else None, algo), "an equal mesh"
    before = {algo: meshio.fingerprint(a.data, a, algo) for algo in ("x3", "blake2b")}
    last = len(a.data.vertices) - 1
    for i, axis in ((0, 0), (last, 2), (1234, 1)):
        _flip_low_bit(a.data, i, axis)
        for algo in ("x3", "blake2b"):
            assert meshio.fingerprint(a.data, a, algo) != before[algo], (i, axis, algo)
            assert meshio.fingerprint(a.data, a, algo) != meshio.fingerprint(b.data, b, algo), (i, axis, algo)
        _flip_low_bit(a.data, i, axis)
        for algo in ("x3", "blake2b"):
            assert meshio.fingerprint(a.data, a, algo) == before[algo], "flipped back: the same value again"
    # one bit of a vertex group weight (an extra) too
    w = a.vertex_groups["G"].weight(3)
    a.vertex_groups["G"].add([3], float(np.nextafter(np.float32(w), np.float32(0))), "REPLACE")
    assert meshio.fingerprint(a.data, a) != before["x3"] and meshio.fingerprint(a.data) == meshio.fingerprint(b.data)


def test_digest_input_is_what_blake2b_was_fed():
    """fingerprint(..., "blake2b") is the value of builds before R060 (hashing the snapshot's arrays as
    their code did), and "x3" is xxh3-128 of the same bytes, with the tag."""
    H.fresh()
    ob = _mesh_with_extras()
    me = ob.data
    for with_ob in (None, ob):
        snap = meshio.read_snapshot(me, ob=with_ob, extras=with_ob is not None)
        stream = [repr(sorted(snap.counts.items())).encode()]
        for key in sorted(snap.arrays):
            a = np.ascontiguousarray(snap.arrays[key])
            stream.append(key.encode() + b"\0" + str(a.dtype).encode() + b"\0")
            stream.append(bytes(a.view(np.uint8)))
        old = hashlib.blake2b(digest_size=16)
        new = ENGINE.Hasher128()
        for p in stream:
            old.update(p)
            new.update(p)
        assert meshio.fingerprint(me, with_ob, "blake2b") == old.hexdigest(), with_ob is not None
        assert meshio.fingerprint(me, with_ob) == "x3:" + new.hexdigest(), with_ob is not None
        assert meshio.fingerprint(me, with_ob) == meshio.fingerprint(me, with_ob, "x3")


def test_a_stored_value_is_compared_the_way_it_was_made():
    H.fresh()
    ob = _mesh_with_extras()
    old = runtime.mesh_fingerprint(ob, "blake2b")
    new = runtime.mesh_fingerprint(ob)
    assert meshio.print_algo(old) == "blake2b" and meshio.print_algo(new) == "x3"
    for unknown in ("zz:" + "0" * 32, "x4:" + new[3:], "blake3:" + "1" * 64, None, 7, b"x3:00"):
        assert meshio.print_algo(unknown) is None, unknown
    assert not runtime.mesh_changed(ob, old) and not runtime.mesh_changed(ob, new)
    assert runtime.mesh_changed(ob, new[:-1] + ("0" if new[-1] != "0" else "1")), "another x3 value"
    assert runtime.mesh_changed(ob, old[:-1] + ("0" if old[-1] != "0" else "1")), "another blake2b value"
    assert runtime.mesh_changed(ob, new[3:]), "xxh3 digits without the tag are read as blake2b: no match"
    assert runtime.mesh_changed(ob, "x3:" + old), "blake2b digits with the x3 tag: no match"
    # Edit Mode: read from a copy, both forms, the same values as in Object Mode (before the change below:
    # positions moved outside the shape keys are reconciled by Blender when Edit Mode is entered)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        assert (runtime.mesh_fingerprint(ob), runtime.mesh_fingerprint(ob, "blake2b")) == (new, old)
        assert not runtime.mesh_changed(ob, new) and not runtime.mesh_changed(ob, old)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    assert (runtime.mesh_fingerprint(ob), runtime.mesh_fingerprint(ob, "blake2b")) == (new, old)
    ob.data.vertices[0].co.x += 1.0
    ob.data.update()
    assert runtime.mesh_changed(ob, old) and runtime.mesh_changed(ob, new)
    for unknown in ("zz:" + "0" * 32, "x4:" + new[3:], None):
        assert not runtime.mesh_changed(ob, unknown), ("a tag this build doesn't know is never a change", unknown)


if __name__ == "__main__":
    for t in (test_new_fingerprints_are_tagged_xxh3, test_equal_meshes_equal_values_one_bit_differs,
              test_digest_input_is_what_blake2b_was_fed, test_a_stored_value_is_compared_the_way_it_was_made):
        H.run(t)
    H.finish()
