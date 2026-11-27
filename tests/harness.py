"""Headless test helpers. Import after inserting the repo root into sys.path."""
import os
import shutil
import sys
import tempfile

# Every temporary file of this test process (the crash journals of its sessions, saved test files,
# history folders) goes into one folder that finish() removes. Headless Blender never unregisters the
# add-on on exit, so the journals stayed behind: 19 GB of them and of test folders filled the disk in
# one day of runs. Processes a test starts inherit the folder (TMPDIR) and leave it to their parent.
# HS_KEEP_TMP=1 keeps it.
_OWN_TMP = None
if not os.environ.get("HS_TEST_TMP_OWNER"):
    _OWN_TMP = tempfile.mkdtemp(prefix="hs_test_")
    os.environ["TMPDIR"] = _OWN_TMP
    os.environ["HS_TEST_TMP_OWNER"] = str(os.getpid())
    tempfile.tempdir = _OWN_TMP

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests import runner  # noqa: E402
import hindsight  # noqa: E402
from hindsight.bl import runtime, meshio  # noqa: E402
from hindsight.bl import prefs

_registered = [False]


def fresh(register=True):
    """Factory settings, empty scene. register=False leaves Hindsight off (the replay oracle runs
    the same operations without it)."""
    if _registered[0]:
        hindsight.unregister()
        _registered[0] = False
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if not register:
        return
    hindsight.register()
    _registered[0] = True
    runtime.set_registry(runtime.Registry())
    runtime.reg().disabled.clear()
    from hindsight.bl import datarec, guard, idmap
    datarec.forget()          # module state from an earlier test must not leak into this one
    idmap.forget()
    guard.reset()
    prefs.FORCE["record_scope"] = None
    prefs.FORCE["record_kinds"] = None


def make_mesh(n_verts_approx=2000, name="Obj", location=(0, 0, 0)):
    seg = max(8, int((n_verts_approx / 2) ** 0.5 * 1.41))
    rings = max(4, n_verts_approx // seg)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=seg, ring_count=rings, location=location)
    ob = bpy.context.active_object
    ob.name = name
    return ob


def enter(obj, mode):
    vl = bpy.context.view_layer
    if vl.objects.active is not None and vl.objects.active.mode != "OBJECT" and vl.objects.active != obj:
        bpy.ops.object.mode_set(mode="OBJECT")
    vl.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode=mode)
    runtime.check_mode_transition()


def fake_stroke(obj, seed, n=50, channel="position"):
    """Change some elements of one channel, as a brush/tool would."""
    rng = np.random.default_rng(seed)
    me = obj.data
    if channel == "position":
        a = me.attributes["position"]
        co = np.empty(len(a.data) * 3, np.float32)
        a.data.foreach_get("vector", co)
        idx = rng.choice(len(a.data), size=min(n, len(a.data)), replace=False)
        co.reshape(-1, 3)[idx] += rng.normal(0, 0.01, (len(idx), 3)).astype(np.float32)
        a.data.foreach_set("vector", co)
    elif channel == "mask":
        a = me.attributes.get(".sculpt_mask") or me.attributes.new(".sculpt_mask", "FLOAT", "POINT")
        v = np.empty(len(a.data), np.float32)
        a.data.foreach_get("value", v)
        idx = rng.choice(len(a.data), size=min(n, len(a.data)), replace=False)
        v[idx] = rng.random(len(idx)).astype(np.float32)
        a.data.foreach_set("value", v)
    elif channel == "fset":
        a = me.attributes.get(".sculpt_face_set") or me.attributes.new(".sculpt_face_set", "INT", "FACE")
        v = np.empty(len(a.data), np.int32)
        a.data.foreach_get("value", v)
        idx = rng.choice(len(a.data), size=min(n, len(a.data)), replace=False)
        v[idx] = rng.integers(1, 50, len(idx)).astype(np.int32)
        a.data.foreach_set("value", v)
    elif channel == "hide":
        for nm, dom in ((".hide_vert", "POINT"), (".hide_poly", "FACE")):
            a = me.attributes.get(nm) or me.attributes.new(nm, "BOOLEAN", dom)
            v = np.empty(len(a.data), bool)
            a.data.foreach_get("value", v)
            idx = rng.choice(len(a.data), size=min(n, len(a.data)), replace=False)
            v[idx] = ~v[idx]
            a.data.foreach_set("value", v)
    elif channel in ("color", "bytecolor"):
        nm = "Col" if channel == "color" else "BCol"
        typ = "FLOAT_COLOR" if channel == "color" else "BYTE_COLOR"
        a = me.color_attributes.get(nm) or me.color_attributes.new(nm, typ, "POINT")
        prop = "color" if typ == "FLOAT_COLOR" else "color_srgb"
        v = np.empty(len(a.data) * 4, np.float32)
        a.data.foreach_get(prop, v)
        idx = rng.choice(len(a.data), size=min(n, len(a.data)), replace=False)
        newc = rng.integers(0, 256, (len(idx), 4)).astype(np.float32) / 255
        v.reshape(-1, 4)[idx] = newc
        a.data.foreach_set(prop, v)
    else:
        raise ValueError(channel)
    me.update()


def commit(obj, **kw):
    return runtime.commit(obj, **kw)


def snapshot(obj):
    snap = meshio.read_snapshot(obj.data, sculpt_only=False)
    return {k: v.copy() for k, v in snap.arrays.items()}


def assert_same(a, b, msg=""):
    ka = set(a)
    kb = set(b)
    assert ka == kb, f"{msg} channel sets differ: {sorted(ka ^ kb)}"
    for k in ka:
        x, y = a[k], b[k]
        assert x.dtype == y.dtype and x.shape == y.shape, f"{msg} {k} shape/dtype"
        assert x.view(np.uint8).tobytes() == y.view(np.uint8).tobytes(), f"{msg} {k} differs"


def _cleanup():
    if _OWN_TMP and not os.environ.get("HS_KEEP_TMP"):
        shutil.rmtree(_OWN_TMP, ignore_errors=True)
        # Blender's own temporary folder (made at start-up, before this module): exiting with os._exit
        # skips Blender's clean-up of it
        bt = getattr(bpy.app, "tempdir", "") or ""
        if os.path.basename(os.path.normpath(bt)).startswith("blender_"):
            shutil.rmtree(bt, ignore_errors=True)


runner.on_finish.append(_cleanup)
run, finish = runner.run, runner.finish
