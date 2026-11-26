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
    from hindsight.bl import datarec, guard, idmap, persistence
    datarec.forget()          # module state from an earlier test must not leak into this one
    idmap.forget()
    persistence._PROTECT.update(sidecar=False, embed=False)     # what loading a file resets (no handler ran)
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


def stored_text(path):
    """{key: value} of the history Text in the .blend at `path`, as it is on disk (groups as dicts): since format 5,
    Blender lets go of the parts once the file is written. A copy is read: Blender won't load the open file."""
    from hindsight.bl import persistence
    tmp = os.path.join(tempfile.mkdtemp(), "stored.blend")
    shutil.copyfile(path, tmp)
    known = set(bpy.data.libraries)
    out = {}
    try:
        with bpy.data.libraries.load(tmp) as (src, dst):
            dst.texts = [persistence.TEXT_NAME] if persistence.TEXT_NAME in src.texts else []
        for t in dst.texts:
            out = {k: (t[k].to_dict() if hasattr(t[k], "to_dict") else t[k]) for k in t.keys()}
            bpy.data.texts.remove(t)
    finally:
        for lib in [lib for lib in bpy.data.libraries if lib not in known]:
            bpy.data.libraries.remove(lib)
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
    return out


def stored_registry(path):
    """The registry the .blend at `path` holds on disk, read from its format 5 parts."""
    from hindsight.bl import persistence
    from hindsight.core import persist
    g = stored_text(path)[persistence.PARTS_PROP]
    return persist.registry_from_format5(g["index"], g.get("index_copy"), lambda uid, i: g.get(uid, {}).get(str(i)))


def saved_steps(reg):
    """A registry as a save keeps it: its settings, and each history's members, cursor, morph target and steps,
    each step as (bytes, CRC-32, meta). A step recorded since the last save has no bytes yet: (None, None, meta)."""
    from hindsight.core import persist
    out = {"settings": (reg.next_seq, reg.use_global, reg.preserve_modes, dict(reg.by_object), set(reg.disabled),
                        set(reg.everything))}
    for uid, h in reg.histories.items():
        out[uid] = (list(h.members), h.cursor, h.morph_target,
                    [(*e.blob_crc(), persist._meta_for_json(e.meta)) for e in h.entries])
    return out


def save_format4(path, **kw):
    """Saves `path` with its history in format 4, as builds before format 5 wrote it (the bytes and the save id on
    the Text, Blender keeping its copy): for the tests of reading formats 1 to 4, which is unchanged."""
    from hindsight.bl import persistence
    from hindsight.core import persist

    @bpy.app.handlers.persistent
    def as_format4(*_a):
        text = bpy.data.texts.get(persistence.TEXT_NAME)
        if text is None or persistence.PARTS_PROP not in text:
            return
        reg = runtime.reg()
        for key in (persistence.DATA_PROP, persistence.PARTS_PROP):
            del text[key]
        text[persistence.DATA_BIN_PROP] = persist.parts_to_bytes(persist.registry_parts(reg))
        text[persistence.SAVE_ID_PROP] = reg.save_id
    bpy.app.handlers.save_pre.append(as_format4)
    try:
        return bpy.ops.wm.save_as_mainfile(filepath=path, **kw)
    finally:
        bpy.app.handlers.save_pre.remove(as_format4)


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
