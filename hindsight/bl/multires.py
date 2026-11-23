"""Multires: one exact step per Sculpt Mode visit on an object with a Multires modifier.

Sculpting a Multires mesh changes its displacement layers (CD_MDISPS), which are not attributes and
which a stroke can't diff. So the step is a snapshot of the whole mesh datablock: bpy.data.libraries
.write puts the mesh (displacements included, and its shape keys) into a .blend file, which is read
back as bytes and compressed with the engine's codec. Going back or forward loads that file again
(libraries.load) and user_remap puts the loaded mesh on the object.

The step is a TOPO step (kind TOPO, extra {"multires": 1}) whose before and after snapshots each hold
one int8 channel "multires:blob|POINT|INT8": the compressed bytes. Nothing else in a history of such
an object is recorded (Multires objects have no per-stroke steps: the pause that was there before
stays for everything but this).

Preference "Record Multires" (off by default until a real-window check). Off: recording pauses on a
Multires object as before.
"""
import contextlib
import os
import tempfile

import bpy
import numpy as np

from ..core import codec
from ..core.types import TOPO, Snapshot, Step, make_key

MR_KEY = make_key("multires:blob", "POINT", "INT8")
FORCE = [None]            # tests: True or False when the preferences aren't registered
_BASE: dict = {}          # object uid -> (raw blob digest, compressed blob array) at the start of the visit
_LEVEL: dict = {}         # object uid -> (levels, sculpt_levels, render_levels) seen while sculpting
_PENDING: dict = {}       # uid -> time the visit began; its baseline is read again at the first tick after SETTLE
                          # seconds: Sculpt Mode adds its own layers to the mesh after its first update, which is
                          # not a change of the sculpt
SETTLE = [0.25]           # (tests set 0: no timer runs there)


def enabled() -> bool:
    if FORCE[0] is not None:
        return FORCE[0]
    try:
        from .prefs import get_prefs
        p = get_prefs()
        return p is not None and bool(getattr(p, "record_multires", False))
    except Exception:
        return False


def modifier(obj):
    for m in getattr(obj, "modifiers", ()):
        if m.type == "MULTIRES":
            return m
    return None


def active(obj) -> bool:
    """Multires recording is on and obj has a Multires modifier."""
    return enabled() and getattr(obj, "type", None) == "MESH" and modifier(obj) is not None


def _tmp_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".blend", prefix="hindsight_mr_")
    os.close(fd)
    return path


def snapshot_bytes(me) -> bytes:
    """The mesh datablock as the bytes of a .blend file."""
    path = _tmp_path()
    try:
        bpy.data.libraries.write(path, {me}, fake_user=False)
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def pack(raw: bytes) -> np.ndarray:
    """Compressed snapshot as the int8 array a Snapshot holds."""
    return np.frombuffer(codec.compress(raw, "zstd"), dtype=np.int8).copy()


def unpack(arr) -> bytes:
    return codec.decompress(np.asarray(arr, dtype=np.int8).tobytes())


def _snap(obj, arr) -> Snapshot:
    me = obj.data
    return Snapshot({"POINT": len(me.vertices), "EDGE": len(me.edges), "FACE": len(me.polygons),
                     "CORNER": len(me.loops)}, {MR_KEY: arr})


def restore(obj, arr) -> None:
    """Put the snapshot's mesh on the object: libraries.load, then user_remap. The object keeps its
    mesh's name, its materials (the load brings copies of them: they are put back and dropped) and its
    modifiers. Not in Sculpt Mode (the caller leaves it first)."""
    raw = unpack(arr)
    old = obj.data
    name = old.name
    mats = list(old.materials)
    before = {c: set(getattr(bpy.data, c)) for c in ("materials", "images", "meshes", "shape_keys")}
    path = _tmp_path()
    try:
        with open(path, "wb") as fh:
            fh.write(raw)
        with bpy.data.libraries.load(path) as (src, dst):
            dst.meshes = list(src.meshes)
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    new = dst.meshes[0]
    for i, m in enumerate(mats):                  # the load made copies of the materials: use the originals
        if i < len(new.materials):
            new.materials[i] = m
    old.user_remap(new)
    bpy.data.meshes.remove(old)
    new.name = name
    for cat in ("materials", "images", "shape_keys"):
        coll = getattr(bpy.data, cat)
        for idb in [x for x in coll if x not in before[cat] and x.users == 0]:
            coll.remove(idb)


_OWN_IDS = (b"ME\x00\x00", b"KE\x00\x00")    # the mesh and its shape keys; DATA blocks follow their owner


def blend_blocks(raw: bytes) -> list:
    """(offset, code, length) of each block of a .blend file written by Blender 4.2 to 5.x: the old
    header ("BLENDER-v402", 12 bytes, 24-byte block heads) or the new one ("BLENDER17-01v0500", 17
    bytes, 32-byte block heads). [] if the bytes aren't read that way."""
    import struct
    try:
        if raw[:7] != b"BLENDER":
            return []
        if raw[7:9] == b"17":
            pos, head = 17, struct.Struct("<4siQqq")            # code, SDNA index, old address, length, count
            length = lambda t: t[3]
        elif raw[7:8] == b"-" and raw[8:9] == b"v":
            pos, head = 12, struct.Struct("<4siQii")            # code, length, old address, SDNA index, count
            length = lambda t: t[1]
        else:
            return []
        out = []
        while pos + head.size <= len(raw):
            t = head.unpack_from(raw, pos)
            n = length(t)
            if n < 0 or pos + head.size + n > len(raw):
                return []
            out.append((pos, t[0], head.size + n))
            if t[0] == b"ENDB":
                return out
            pos += head.size + n
        return []
    except Exception:
        return []


def hash_of(raw: bytes) -> bytes:
    """Digest of the mesh as written: its own blocks only (the mesh and its shape keys, each with the
    DATA blocks that follow it). D150: Blender 5.0 to 5.2 write a material slightly differently each
    time (one byte of its data), so a digest of the whole file made a visit that changed nothing a step
    whenever the mesh had a material. Bytes that aren't read as blocks: a digest of every byte."""
    import hashlib
    h = hashlib.blake2b(digest_size=16)
    blocks = blend_blocks(raw)
    if not blocks:
        h.update(raw)
        return h.digest()
    own = False
    mv = memoryview(raw)
    for pos, code, n in blocks:
        if code != b"DATA":
            own = code in _OWN_IDS
        if own:
            h.update(mv[pos:pos + n])
    return h.digest()


def begin_visit(obj) -> None:
    """Sculpt Mode starts on a Multires object: remember the mesh as it is."""
    raw = snapshot_bytes(obj.data)
    from . import runtime
    uid = runtime.obj_uid(obj, create=True)
    _BASE[uid] = (hash_of(raw), pack(raw))
    m = modifier(obj)
    _LEVEL[uid] = (m.levels, m.sculpt_levels, m.render_levels)
    _PENDING[uid] = __import__("time").time()


_ENDING: set = set()      # uids whose Sculpt Mode just ended: the step is taken at the next flush, when the
                          # mesh has settled (inside the mode change Blender hasn't written sculpt data back)


def leave(obj) -> None:
    from . import runtime
    uid = runtime.obj_uid(obj)
    if uid is not None and uid in _BASE:
        _ENDING.add(uid)


def flush() -> None:
    """Take the step of every visit that ended (timer tick, before a jump, before a save)."""
    if not _ENDING:
        return
    from . import runtime
    for uid in list(_ENDING):
        _ENDING.discard(uid)
        ob = runtime.find_object(uid)
        if ob is not None and ob.mode != "SCULPT" and active(ob):
            end_visit(ob)


def end_visit(obj, label: str = "Multires Sculpt"):
    """The visit ends (Sculpt Mode left, or the level changed): one step if the mesh differs from the
    start of the visit. Returns the Step or None."""
    from . import runtime
    uid = runtime.obj_uid(obj)
    base = _BASE.get(uid)
    if uid is None or base is None:
        return None
    raw = snapshot_bytes(obj.data)
    digest = hash_of(raw)
    if digest == base[0]:
        return None
    after = pack(raw)
    step = Step(seq=0, label=label, mode="SCULPT", target=uid, time=__import__("time").time(), kind=TOPO,
                before=_snap(obj, base[1]), after=_snap(obj, after), extra={"multires": 1})
    st = runtime._STATES.get(uid)
    if st is None or not st.counts:
        runtime.ensure_state(obj, refresh=True)
    r = runtime.commit(obj, label=label, mode="SCULPT", _mr_step=step)
    _BASE[uid] = (digest, after)
    return r


def tick(obj) -> None:
    """While sculpting: a change of the Multires levels ends the visit's step and starts the next."""
    from . import runtime
    uid = runtime.obj_uid(obj)
    m = modifier(obj)
    if uid is None or m is None or uid not in _LEVEL:
        return
    if uid in _PENDING and uid in runtime._STROKE_SEEN:
        # D143: a stroke came before the re-read: it belongs to the visit, so the baseline read on
        # entering Sculpt Mode stays (Sculpt Mode's own layers are in the step then, which is harmless)
        del _PENDING[uid]
    elif uid in _PENDING and __import__("time").time() - _PENDING[uid] >= SETTLE[0] and not runtime.stroke_running():
        del _PENDING[uid]
        raw = snapshot_bytes(obj.data)
        _BASE[uid] = (hash_of(raw), pack(raw))
    now = (m.levels, m.sculpt_levels, m.render_levels)
    if now != _LEVEL[uid]:
        end_visit(obj)
        begin_visit(obj)
