"""R057: mesh fingerprints are xxh3-128 (the engine's Hasher128), written "x3:" + 32 hex digits.

The digest input is exactly what blake2b was fed (the counts, then each recorded array in key order with
its name and dtype), so only the hash function changed. A fingerprint stored without a tag (files and
journals of older builds) is compared by hashing the mesh with blake2b again: a mesh is never reported as
changed without Hindsight because the algorithm changed. A tag this build doesn't know is taken as
unchanged.

Run: blender -b --factory-startup --python tests/test_bl_fingerprint.py (or tests/run_tests.py)."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hashlib
import shutil
import tempfile

import bpy
import numpy as np

import hindsight
import tests.harness as H
from tests import t1_gate_lib as L
from tests import t1_ops as T
from hindsight.bl import datarec, meshio, runtime
from hindsight.core.native import ENGINE
from hindsight.core.types import SETTINGS

C = bpy.context
_TMP = []


def tmpdir():
    d = tempfile.mkdtemp(prefix="hs_fp_")
    _TMP.append(d)
    return d


def test_hasher_vectors_and_streaming():
    """xxh3-128 (seed 0) known values, and any split of the input into pieces gives the same digest."""
    def x3(*parts):
        h = ENGINE.Hasher128()
        for p in parts:
            h.update(p)
        return h.hexdigest()
    assert x3() == "99aa06d3014798d86001c324468d497f"
    assert x3(b"a") == "a96faf705af16834e6c632b61e964e1f"
    assert x3(b"abc") == "06b05ab6733a618578af5f94892f3950"
    rng = np.random.default_rng(3)
    data = rng.bytes(700_000)
    want = x3(data)
    for cuts in ((1,), (240,), (241, 1000), (65535,), (65536, 65537), (100_000, 300_000, 400_000)):
        pos, parts = 0, []
        for c in cuts:
            parts.append(data[pos:c]); pos = c
        parts.append(data[pos:])
        assert x3(*parts) == want, cuts
    # the buffer kinds, and the GIL-released path (a bytes of at least 64 KB) against the held one (bytearray)
    assert x3(bytearray(data)) == want and x3(memoryview(data)) == want
    assert x3(np.frombuffer(data, np.uint8)) == want and x3(np.frombuffer(data, np.uint8).reshape(7000, 100)) == want
    for bad in ("text", 3, None, memoryview(data)[::2]):
        try:
            ENGINE.Hasher128().update(bad)
        except (TypeError, BufferError, ValueError):
            continue
        raise AssertionError(bad)


def _mesh_with_extras():
    H.fresh()
    ob = H.make_mesh(3000, name="Fp")
    vg = ob.vertex_groups.new(name="G")
    vg.add(list(range(0, 3000, 3)), 0.5, "REPLACE")
    ob.shape_key_add(name="Basis")
    sk = ob.shape_key_add(name="Moved")
    for v in sk.data[:50]:
        v.co.x += 0.1
    return ob


def test_digest_input_is_what_blake2b_was_fed():
    """fingerprint(..., "blake2b") is the old value (hashing the snapshot's arrays by hand, as the pre-R057
    code did), and "x3" is xxh3-128 of the same bytes, with the tag."""
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
            old.update(p); new.update(p)
        assert meshio.fingerprint(me, with_ob, "blake2b") == old.hexdigest(), with_ob is not None
        assert meshio.fingerprint(me, with_ob) == "x3:" + new.hexdigest(), with_ob is not None
        assert meshio.fingerprint(me, with_ob) == meshio.fingerprint(me, with_ob, "x3")
    a = meshio.fingerprint(me, ob)
    me.vertices[5].co.z += 0.25
    me.update()
    assert meshio.fingerprint(me, ob) != a and meshio.fingerprint(me, ob, "blake2b") != meshio.fingerprint(me, None, "blake2b")


def test_a_stored_value_is_compared_the_way_it_was_made():
    ob = _mesh_with_extras()
    old = runtime.mesh_fingerprint(ob, "blake2b")
    new = runtime.mesh_fingerprint(ob)
    assert not old.startswith("x3:") and new.startswith("x3:") and len(new) == 3 + 32 and len(old) == 32
    assert meshio.print_algo(old) == "blake2b" and meshio.print_algo(new) == "x3"
    assert meshio.print_algo("x9:abcd") is None and meshio.print_algo(None) is None and meshio.print_algo(7) is None
    assert not runtime.mesh_changed(ob, old) and not runtime.mesh_changed(ob, new)
    assert not runtime.mesh_changed(ob, "x9:0123"), "a tag this build doesn't know is not a change"
    ob.data.vertices[0].co.x += 1.0
    ob.data.update()
    assert runtime.mesh_changed(ob, old) and runtime.mesh_changed(ob, new)
    # Edit Mode: read from a copy, both forms
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        assert runtime.mesh_fingerprint(ob, "blake2b") == runtime.mesh_fingerprint(ob, "blake2b")
        assert runtime.mesh_fingerprint(ob).startswith("x3:")
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def setup():
    L.fresh_scene()
    L.track_all()
    runtime.reg().preserve_modes = False


def op(o):
    o.run()
    L.tick_like(o.label)


def save_with_old_prints(path):
    """Save as a build of before R057 did: every fingerprint blake2b, untagged."""
    real = runtime.mesh_fingerprint
    runtime.mesh_fingerprint = lambda ob, algo="x3": real(ob, "blake2b")
    try:
        SETTINGS.save_mode = "SIDECAR"
        T.select()
        bpy.ops.wm.save_as_mainfile(filepath=path)
        saved = dict(runtime.reg().saved or {})
    finally:
        runtime.mesh_fingerprint = real
    prints = [r["mesh"] for r in saved.values() if isinstance(r, dict) and "mesh" in r]
    assert prints and all(not p.startswith("x3:") for p in prints), prints
    return saved


def test_a_file_with_blake2b_fingerprints_opens_without_the_alarm():
    setup()
    for o in (T.translate("A", (1, 0, 0)), T.edit_ops("A", 2)[0], T.leave_edit("A")):
        op(o)
    sculpt = H.make_mesh(800, name="Clay")
    H.enter(sculpt, "SCULPT")
    H.fake_stroke(sculpt, 3)
    H.commit(sculpt)
    H.enter(sculpt, "OBJECT")
    before = {h.uid: len(h.entries) for h in runtime.reg().histories.values()}
    path = os.path.join(tmpdir(), "old_prints.blend")
    save_with_old_prints(path)
    bpy.ops.wm.open_mainfile(filepath=path)
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    after = {h.uid: len(h.entries) for h in runtime.reg().histories.values()}
    assert after == before, (before, after)
    assert not any("wasn't running" in t or runtime.OUTSIDE_MESH_TEXT.format(name="A") == t
                   for _s, t in runtime.load_notes), runtime.load_notes
    # saved again by this build: the values are tagged now, and reopening is quiet too
    bpy.ops.wm.save_as_mainfile(filepath=path)
    assert all(str(r.get("mesh", "x3:")).startswith("x3:") for r in runtime.reg().saved.values() if isinstance(r, dict))
    bpy.ops.wm.open_mainfile(filepath=path)
    assert {h.uid: len(h.entries) for h in runtime.reg().histories.values()} == before
    assert not any("wasn't running" in t for _s, t in runtime.load_notes), runtime.load_notes


def test_a_change_without_hindsight_is_still_found_against_a_blake2b_fingerprint():
    """The old value still tells a changed mesh (the alarm, or the backup copy bridging it) as before."""
    setup()
    for o in (T.translate("A", (1, 0, 0)), T.edit_ops("A", 2)[0], T.leave_edit("A")):
        op(o)
    path = os.path.join(tmpdir(), "old_prints_changed.blend")
    save_with_old_prints(path)
    hindsight.unregister()
    H._registered[0] = False
    a = bpy.data.objects["A"]
    a.data.vertices[0].co.x += 1.0
    a.data.update()
    bpy.ops.wm.save_as_mainfile(filepath=path)
    if os.path.isfile(path + "1"):
        os.remove(path + "1")
    hindsight.register()
    H._registered[0] = True
    bpy.ops.wm.open_mainfile(filepath=path)
    datarec.FORCE_SCOPE[0] = "EVERYTHING"
    assert runtime.history_of(bpy.data.objects["A"]) is None, "a history that can't match the mesh is left out"
    assert any(runtime.OUTSIDE_MESH_TEXT.format(name="A") == t for _s, t in runtime.load_notes), runtime.load_notes


def test_recovery_replays_a_reset_with_old_prints():
    """A journal RESET record written before R057 carries blake2b prints: replayed, they land in the saved
    values untagged and are compared that way."""
    from hindsight.core import persist
    from hindsight.core.registry import Registry
    reg = Registry()
    reg.saved = {"u1": {"mesh": "0" * 32}}
    h = persist.History("H1", ["u1"]) if hasattr(persist, "History") else None
    from hindsight.core.history import History
    h = History("H1", ["u1"])
    reg.histories = {"H1": h}
    reg.by_object = {"u1": "H1"}
    persist.replay(reg, [("RESET", {"hist": "H1", "mesh": {"u1": "f" * 32, "u2": "x3:" + "1" * 32}}, b"")])
    assert reg.saved["u1"]["mesh"] == "f" * 32 and reg.saved["u2"]["mesh"].startswith("x3:"), reg.saved


TESTS = (test_hasher_vectors_and_streaming, test_digest_input_is_what_blake2b_was_fed,
         test_a_stored_value_is_compared_the_way_it_was_made, test_a_file_with_blake2b_fingerprints_opens_without_the_alarm,
         test_a_change_without_hindsight_is_still_found_against_a_blake2b_fingerprint,
         test_recovery_replays_a_reset_with_old_prints)

if __name__ == "__main__":
    try:
        for t in TESTS:
            H.run(t)
        H.finish()
    finally:
        for d in _TMP:
            shutil.rmtree(d, ignore_errors=True)
