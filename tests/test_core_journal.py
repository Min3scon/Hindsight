import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Tests for hindsight.core.journal and hindsight.core.persist (pure Python, no bpy).

Uses the real core.step / core.history / core.registry when importable; otherwise (or with
HS_STANDINS=1 / --standins) installs simple stand-ins so this layer is testable on its own.
"""
import base64
import json
import pickle
import shutil
import tempfile
import traceback
import types

import numpy as np

import hindsight.core as core_pkg
from hindsight.core.types import (
    ChannelDelta, Snapshot, Step, Entry, DELTA, TOPO,
    POSITION_KEY, MASK_KEY, HIDE_VERT_KEY, FACE_SET_KEY,
    EDGE_VERTS_KEY, CORNER_VERT_KEY, CORNER_EDGE_KEY, FACE_OFFSETS_KEY,
)

FORCE_STANDINS = os.environ.get("HS_STANDINS") == "1" or "--standins" in sys.argv
STANDINS = []


# ---------------------------------------------------------------------------
# stand-ins (fallback only)
# ---------------------------------------------------------------------------
def _standin_step_module():
    m = types.ModuleType("hindsight.core.step")

    def step_to_bytes(step, codec="auto"):
        return b"PKL0" + pickle.dumps(step, protocol=pickle.HIGHEST_PROTOCOL)

    def step_from_bytes(b):
        assert bytes(b[:4]) == b"PKL0"
        return pickle.loads(bytes(b[4:]))

    m.step_to_bytes = step_to_bytes
    m.step_from_bytes = step_from_bytes
    return m


def _standin_history_module():
    m = types.ModuleType("hindsight.core.history")

    class History:
        def __init__(self, uid, members=None):
            self.uid = uid
            self.members = list(members) if members else []
            self.entries = []
            self.cursor = 0
            self.enabled = True
            self.morph_target = None

        def __len__(self):
            return len(self.entries)

        def push(self, step):
            dropped = self.entries[self.cursor:]
            self.entries = self.entries[:self.cursor] + [Entry(step)]
            self.cursor = len(self.entries)
            if self.morph_target is not None and self.morph_target > self.cursor:
                self.morph_target = None
            return dropped

        def drop_oldest(self, n):
            n = max(0, min(n, len(self.entries)))
            self.entries = self.entries[n:]
            self.cursor = max(0, self.cursor - n)
            if self.morph_target is not None:
                self.morph_target = None if self.morph_target - n < 0 else self.morph_target - n
            return n

    m.History = History
    return m


def _standin_registry_module():
    m = types.ModuleType("hindsight.core.registry")

    class Registry:
        def __init__(self):
            self.histories = {}
            self.by_object = {}
            self.next_seq = 1
            self.use_global = False
            self.preserve_modes = True
            self.disabled = set()

        def new_seq(self):
            s = self.next_seq
            self.next_seq += 1
            return s

        def history_for(self, obj_uid):
            hid = self.by_object.get(obj_uid)
            return self.histories.get(hid) if hid else None

        def all(self):
            return list(self.histories.values())

    m.Registry = Registry
    return m


def _install(name, factory, check):
    full = "hindsight.core." + name
    if not FORCE_STANDINS:
        try:
            mod = __import__(full, fromlist=["_"])
            check(mod)
            return
        except Exception:
            sys.modules.pop(full, None)
    mod = factory()
    sys.modules[full] = mod
    setattr(core_pkg, name, mod)
    STANDINS.append(name)


def _check_step(mod):
    assert callable(mod.step_to_bytes) and callable(mod.step_from_bytes)
    s = _delta_step(1, "probe", np.random.default_rng(0))
    assert _steps_equal(s, mod.step_from_bytes(mod.step_to_bytes(s, "auto")))


def _check_history(mod):
    h = mod.History("x", ["x"])
    assert h.entries == [] and h.cursor == 0 and h.enabled and h.morph_target is None


def _check_registry(mod):
    r = mod.Registry()
    assert isinstance(r.histories, dict) and isinstance(r.by_object, dict)


# ---------------------------------------------------------------------------
# test data
# ---------------------------------------------------------------------------
def _delta_step(seq, target, rng, nv=500):
    k = 40
    idx = np.unique(rng.integers(0, nv, size=k).astype(np.uint32))
    pos_xor = rng.integers(0, 2 ** 32, size=(len(idx), 3), dtype=np.uint64).astype(np.uint32)
    midx = np.unique(rng.integers(0, nv, size=k).astype(np.uint32))
    mask_xor = rng.random(len(midx), dtype=np.float32).view(np.uint32).reshape(-1, 1).copy()
    hidx = np.unique(rng.integers(0, nv, size=10).astype(np.uint32))
    hide_xor = np.ones((len(hidx), 1), dtype=np.uint8)
    deltas = [
        ChannelDelta(POSITION_KEY, idx, pos_xor, (True, True)),
        ChannelDelta(MASK_KEY, midx, mask_xor, (False, True)),
        ChannelDelta(HIDE_VERT_KEY, hidx, hide_xor, (True, True)),
    ]
    return Step(seq=seq, label="Stroke %d" % seq, mode="SCULPT", target=target,
                time=1000.0 + seq * 0.25, kind=DELTA, deltas=deltas, extra={"brush": "Draw"})


def _snapshot(rng, nv, nf):
    ne = nf * 2
    nl = nf * 4
    pos = rng.normal(size=nv * 3).astype(np.float32)
    pos[:3] = [-0.0, np.inf, 1e-40]
    arrays = {
        POSITION_KEY: pos,
        EDGE_VERTS_KEY: rng.integers(0, nv, size=ne * 2).astype(np.int32),
        CORNER_VERT_KEY: rng.integers(0, nv, size=nl).astype(np.int32),
        CORNER_EDGE_KEY: rng.integers(0, ne, size=nl).astype(np.int32),
        FACE_OFFSETS_KEY: (np.arange(nf + 1) * 4).astype(np.int32),
        MASK_KEY: rng.random(nv, dtype=np.float32),
        FACE_SET_KEY: rng.integers(0, 5, size=nf).astype(np.int32),
        HIDE_VERT_KEY: rng.random(nv) > 0.5,
    }
    return Snapshot({"POINT": nv, "EDGE": ne, "FACE": nf, "CORNER": nl}, arrays)


def _topo_step(seq, target, rng):
    return Step(seq=seq, label="Voxel Remesh", mode="SCULPT", target=target, time=2000.0 + seq,
                kind=TOPO, deltas=[], before=_snapshot(rng, 300, 120), after=_snapshot(rng, 420, 200),
                extra={})


def _arr_eq(a, b):
    return (a.dtype == b.dtype and a.shape == b.shape
            and np.ascontiguousarray(a).tobytes() == np.ascontiguousarray(b).tobytes())


def _snap_eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    if dict(a.counts) != dict(b.counts) or set(a.arrays) != set(b.arrays):
        return False
    return all(_arr_eq(np.asarray(a.arrays[k]).ravel(), np.asarray(b.arrays[k]).ravel()) for k in a.arrays)


def _meta_core(m):
    return {k: m.get(k) for k in ("seq", "label", "mode", "target", "time", "kind")}


def _steps_equal(a, b):
    if _meta_core(a.meta()) != _meta_core(b.meta()):
        return False
    if len(a.deltas) != len(b.deltas):
        return False
    da = sorted(a.deltas, key=lambda d: d.key)
    db = sorted(b.deltas, key=lambda d: d.key)
    for x, y in zip(da, db):
        if x.key != y.key or tuple(x.exists) != tuple(y.exists):
            return False
        if not (_arr_eq(x.idx, y.idx) and _arr_eq(x.xor, y.xor)):
            return False
    return _snap_eq(a.before, b.before) and _snap_eq(a.after, b.after)


def _build_registry():
    from hindsight.core.history import History
    from hindsight.core.registry import Registry
    rng = np.random.default_rng(42)
    reg = Registry()
    originals = {}
    h1 = History("H1", ["objA"])
    s = [_delta_step(1, "objA", rng), _delta_step(2, "objA", rng), _topo_step(4, "objA", rng),
         _delta_step(5, "objA", rng)]
    h1.entries = [Entry(st) for st in s]
    h1.cursor = 3
    h1.enabled = False
    h1.morph_target = 1
    originals["H1"] = s
    h2 = History("H2", ["objB", "objC"])
    s2 = [_delta_step(3, "objB", rng), _topo_step(6, "objB", rng)]
    h2.entries = [Entry(st) for st in s2]
    h2.entries[0].pack("auto")           # one packed entry, one raw
    h2.cursor = 2
    h2.enabled = True
    h2.morph_target = None
    originals["H2"] = s2
    reg.histories = {"H1": h1, "H2": h2}
    reg.by_object = {"objA": "H1", "objB": "H2", "objC": "H2"}
    reg.next_seq = 7
    reg.use_global = True
    reg.preserve_modes = False
    if hasattr(reg, "disabled"):
        reg.disabled = {"objZ"}
    return reg, originals


def _assert_reg_matches(reg, originals, src):
    assert set(reg.histories) == set(src.histories)
    assert reg.by_object == src.by_object
    assert reg.next_seq == src.next_seq
    assert reg.use_global == src.use_global and reg.preserve_modes == src.preserve_modes
    if hasattr(src, "disabled"):
        assert set(reg.disabled) == set(src.disabled)
    for uid, h0 in src.histories.items():
        h = reg.histories[uid]
        assert h.uid == uid
        assert list(h.members) == list(h0.members)
        assert h.cursor == h0.cursor, (h.cursor, h0.cursor)
        assert h.enabled == h0.enabled
        assert h.morph_target == h0.morph_target
        assert len(h.entries) == len(originals[uid])
        for e, st in zip(h.entries, originals[uid]):
            assert e.is_packed()
            assert _meta_core(e.meta) == _meta_core(st.meta())
            assert _steps_equal(e.load(), st), "step not bit-exact after load"


def _tmp():
    return tempfile.mkdtemp(prefix="hs_journal_test_")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_append_read_roundtrip():
    from hindsight.core.journal import Journal, MAGIC, RTYPES
    d = _tmp()
    try:
        path = os.path.join(d, "sub", "dir", "session.hsj")
        j = Journal(path)
        assert j.size() == 0
        recs = [
            ("META", {"hist": "H1", "members": ["a"], "enabled": True, "morph_target": None, "objects": {}}, b""),
            ("STEP", {"hist": "H1", "at": 0}, os.urandom(1000)),
            ("CURSOR", {"hist": "H1", "cursor": 1}, b""),
            ("TRUNC", {"hist": "H1", "keep": 0}, b""),
            ("DROP", {"hist": "H1", "n": 2}, b""),
            ("RESET", {"hist": "H1"}, b""),
            ("SAVE", {"blend": "/x/ünicode.blend"}, b""),
            ("STEP", {"hist": "H2", "at": 3}, bytes(range(256)) * 50),
        ]
        offsets = [j.append(*r) for r in recs]
        assert offsets[0] == 0 and offsets == sorted(offsets) and len(set(offsets)) == len(offsets)
        j.flush()
        assert j.size() == os.path.getsize(path)
        assert j.read() == recs
        assert j.read(upto=offsets[3]) == recs[:3]
        assert j.read(upto=offsets[3] + 5) == recs[:3]
        j.close()
        with open(path, "rb") as fh:
            raw = fh.read(4)
        assert raw == MAGIC and set(RTYPES) == {"STEP", "CURSOR", "TRUNC", "DROP", "RESET", "META", "SAVE"}
        j2 = Journal(path)
        assert j2.size() == os.path.getsize(path)
        assert j2.read() == recs
        prev = j2.size()
        off = j2.append("CURSOR", {"hist": "H2", "cursor": 0})
        assert off == prev
        assert j2.read()[-1] == ("CURSOR", {"hist": "H2", "cursor": 0}, b"")
        j2.truncate(offsets[2])
        assert j2.size() == offsets[2] == os.path.getsize(path)
        assert j2.read() == recs[:2]
        j2.append("RESET", {"hist": "H1"})
        assert j2.read() == recs[:2] + [("RESET", {"hist": "H1"}, b"")]
        j2.close()
        j2.close()  # idempotent
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_crash_tolerance():
    from hindsight.core.journal import Journal
    d = _tmp()
    try:
        path = os.path.join(d, "j.hsj")
        recs = [("STEP", {"hist": "H", "at": i}, os.urandom(300 + i)) for i in range(5)]

        def fresh():
            if os.path.exists(path):
                os.remove(path)
            j = Journal(path)
            offs = [j.append(*r) for r in recs]
            j.close()
            return offs, os.path.getsize(path)

        # 1) truncated mid-record (payload part and inside the fixed header)
        for cut in ("payload", "header", "crc"):
            offs, full = fresh()
            if cut == "payload":
                size = offs[3] + 40
            elif cut == "header":
                size = offs[3] + 5
            else:
                size = offs[4] - 2
            os.truncate(path, size)
            j = Journal(path)
            assert j.read() == recs[:3], cut
            assert j.size() == offs[3] and os.path.getsize(path) == offs[3], cut
            j.append("CURSOR", {"hist": "H", "cursor": 2})
            j.close()
            j = Journal(path)
            assert j.read() == recs[:3] + [("CURSOR", {"hist": "H", "cursor": 2}, b"")], cut
            j.close()

        # 2) garbage tail
        offs, full = fresh()
        with open(path, "ab") as fh:
            fh.write(b"HSJ1" + os.urandom(77))
        j = Journal(path)
        assert j.read() == recs and j.size() == full and os.path.getsize(path) == full
        j.append("RESET", {"hist": "H"})
        assert j.read() == recs + [("RESET", {"hist": "H"}, b"")]
        j.close()

        # 3) flipped byte in a payload, in a header JSON, in the magic -> stops before that record
        for where in ("payload", "json", "magic"):
            offs, full = fresh()
            pos = {"payload": offs[2] + 17 + 30 + 50, "json": offs[2] + 17 + 2, "magic": offs[2]}[where]
            with open(path, "r+b") as fh:
                fh.seek(pos)
                b = fh.read(1)
                fh.seek(pos)
                fh.write(bytes([b[0] ^ 0x40]))
            # reading through a fresh reader (no truncation yet) stops before record 2
            j = Journal(path)
            assert j.read() == recs[:2], where
            assert j.size() == offs[2], where
            j.append("SAVE", {"blend": "x.blend"})
            assert j.read() == recs[:2] + [("SAVE", {"blend": "x.blend"}, b"")], where
            j.close()

        # 4) corruption appearing under a live journal: read() still stops silently
        offs, full = fresh()
        j = Journal(path)
        with open(path, "r+b") as fh:
            fh.seek(offs[1] + 20)
            fh.write(b"\xff\xff\xff\xff")
        assert j.read() == recs[:1]
        j.close()

        # 5) empty / non-journal file
        with open(path, "wb") as fh:
            fh.write(b"not a journal at all")
        j = Journal(path)
        assert j.read() == [] and j.size() == 0 and os.path.getsize(path) == 0
        j.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_atomic_write():
    from hindsight.core.journal import atomic_write, atomic_write_json
    d = _tmp()
    try:
        p = os.path.join(d, "out", "data.bin")
        atomic_write(p, b"first")
        with open(p, "rb") as fh:
            assert fh.read() == b"first"
        atomic_write(p, b"second version" * 1000)
        with open(p, "rb") as fh:
            assert fh.read() == b"second version" * 1000
        assert os.listdir(os.path.dirname(p)) == ["data.bin"]
        # a failing write leaves the old file untouched and no tmp file behind
        try:
            atomic_write(p, object())
        except TypeError:
            pass
        else:
            raise AssertionError("expected TypeError")
        with open(p, "rb") as fh:
            assert fh.read() == b"second version" * 1000
        assert os.listdir(os.path.dirname(p)) == ["data.bin"]
        pj = os.path.join(d, "out", "index.json")
        atomic_write_json(pj, {"version": 1, "blend": "a.blend", "x": [1, 2]})
        with open(pj) as fh:
            assert json.load(fh) == {"version": 1, "blend": "a.blend", "x": [1, 2]}
        assert sorted(os.listdir(os.path.dirname(p))) == ["data.bin", "index.json"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_registry_roundtrip():
    from hindsight.core.persist import registry_to_bytes, registry_from_bytes
    reg, originals = _build_registry()
    b = registry_to_bytes(reg)
    assert isinstance(b, bytes) and b[:4] == b"HSR1"
    reg2 = registry_from_bytes(b)
    _assert_reg_matches(reg2, originals, reg)
    # serialising again gives the same registry (blobs are reused as-is)
    reg3 = registry_from_bytes(registry_to_bytes(reg2))
    _assert_reg_matches(reg3, originals, reg)
    # all codecs accepted
    for codec in ("none", "zlib", "auto"):
        _assert_reg_matches(registry_from_bytes(registry_to_bytes(reg, codec)), originals, reg)
    # empty registry
    from hindsight.core.registry import Registry
    empty = registry_from_bytes(registry_to_bytes(Registry()))
    assert empty.histories == {} and empty.by_object == {}
    # garbage -> ValueError
    for bad in (b"", b"XXXX\x00\x00\x00\x00", b[: len(b) - 10]):
        try:
            registry_from_bytes(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")


def test_sidecar():
    from hindsight.core.persist import save_sidecar, load_sidecar, sidecar_dir, sidecar_missing
    d = _tmp()
    try:
        blend = os.path.join(d, "my scene.blend")
        assert sidecar_dir(blend) == os.path.join(d, "my scene_hindsight")
        assert load_sidecar(blend) is None
        assert sidecar_missing(blend, True) is True
        assert sidecar_missing(blend, False) is False
        reg, originals = _build_registry()
        save_sidecar(reg, blend)
        folder = sidecar_dir(blend)
        assert sorted(os.listdir(folder)) == ["history.hsr", "index.json"]
        with open(os.path.join(folder, "index.json")) as fh:
            idx = json.load(fh)
        assert idx["version"] == 1 and idx["blend"] == "my scene.blend" and isinstance(idx["saved_at"], float)
        assert sidecar_missing(blend, True) is False
        _assert_reg_matches(load_sidecar(blend), originals, reg)
        # save again (overwrite) -> still exactly two files
        reg.histories["H1"].cursor = 4
        save_sidecar(reg, blend)
        assert sorted(os.listdir(folder)) == ["history.hsr", "index.json"]
        assert load_sidecar(blend).histories["H1"].cursor == 4
        # folder without history.hsr -> None
        os.remove(os.path.join(folder, "history.hsr"))
        assert load_sidecar(blend) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rename_blend_without_folder():
    from hindsight.core.persist import save_sidecar, load_sidecar, sidecar_missing
    d = _tmp()
    try:
        a = os.path.join(d, "a.blend")
        b = os.path.join(d, "b.blend")
        with open(a, "wb") as fh:
            fh.write(b"BLENDER")
        reg, _ = _build_registry()
        save_sidecar(reg, a)
        os.rename(a, b)
        assert sidecar_missing(b, True) is True
        assert load_sidecar(b) is None
        assert sidecar_missing(a, True) is False   # old folder still there under the old name
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_replay_after_save():
    from hindsight.core.journal import Journal
    from hindsight.core.persist import save_sidecar, load_sidecar, replay
    from hindsight.core.history import History
    from hindsight.core.registry import Registry
    from hindsight.core.step import step_to_bytes
    d = _tmp()
    try:
        blend = os.path.join(d, "scene.blend")
        jpath = os.path.join(d, "scene_hindsight", "session.hsj")
        rng = np.random.default_rng(7)
        steps = [_delta_step(i + 1, "objA", rng) for i in range(5)]
        reg = Registry()
        h = History("H1", ["objA"])
        reg.histories["H1"] = h
        reg.by_object["objA"] = "H1"
        j = Journal(jpath)
        j.append("META", {"hist": "H1", "members": ["objA"], "enabled": True, "morph_target": None,
                          "objects": {"objA": "Sphere"}})
        for st in steps:
            j.append("STEP", {"hist": "H1", "at": len(h.entries)}, step_to_bytes(st))
            h.entries.append(Entry(st))
            h.cursor = len(h.entries)
            reg.next_seq = st.seq + 1
        j.append("CURSOR", {"hist": "H1", "cursor": 5})
        save_sidecar(reg, blend)
        j.append("SAVE", {"blend": blend})
        # ---- after the save: 3 more strokes (one with a TOPO step), truncate, cursor move
        tail = [_delta_step(6, "objA", rng), _topo_step(7, "objA", rng), _delta_step(8, "objA", rng)]
        for i, st in enumerate(tail):
            j.append("STEP", {"hist": "H1", "at": 5 + i}, step_to_bytes(st))
        j.append("TRUNC", {"hist": "H1", "keep": 7})
        j.append("CURSOR", {"hist": "H1", "cursor": 6})
        j.flush()
        # crash: half-written record at the tail
        off = j.append("STEP", {"hist": "H1", "at": 7}, step_to_bytes(_delta_step(9, "objA", rng)))
        j.flush()
        j._fh.close(); j._fh = None   # simulate process death (no clean close)
        os.truncate(jpath, off + 30)

        # "Last Saved"
        last = load_sidecar(blend)
        hl = last.histories["H1"]
        assert len(hl.entries) == 5 and hl.cursor == 5
        assert all(_steps_equal(e.load(), s) for e, s in zip(hl.entries, steps))

        # "Interrupted Session"
        rec = load_sidecar(blend)
        records = Journal(jpath).read()
        assert records[-1] == ("CURSOR", {"hist": "H1", "cursor": 6}, b"")
        n = replay(rec, records)
        assert n == 5, n
        hr = rec.histories["H1"]
        assert len(hr.entries) == 7, len(hr.entries)
        expect = steps + tail[:2]
        assert all(_steps_equal(e.load(), s) for e, s in zip(hr.entries, expect))
        assert hr.cursor == 5, "cursor must stay at the saved value"
        assert hr.recovered_cursor == 6
        assert rec.next_seq > 8
        assert rec.by_object == {"objA": "H1"}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_replay_semantics():
    from hindsight.core.persist import replay
    from hindsight.core.registry import Registry
    from hindsight.core.step import step_to_bytes
    rng = np.random.default_rng(3)
    st = [_delta_step(i + 1, "objA", rng) for i in range(6)]
    recs = [
        ("META", {"hist": "H1", "members": ["objA", "objB"], "enabled": True, "morph_target": None, "objects": {}}, b""),
    ]
    recs += [("STEP", {"hist": "H1", "at": i}, step_to_bytes(st[i])) for i in range(4)]
    recs += [("STEP", {"hist": "H1", "at": 2}, step_to_bytes(st[4]))]      # undo x2 then stroke -> truncates
    reg = Registry()
    assert replay(reg, list(recs)) == 6          # no SAVE -> everything applied
    h = reg.histories["H1"]
    assert [e.meta["seq"] for e in h.entries] == [1, 2, 5]
    assert h.cursor == 0                          # new history: cursor untouched (0)
    assert list(h.members) == ["objA", "objB"]
    assert reg.by_object == {"objA": "H1", "objB": "H1"}
    assert reg.next_seq == 6

    # DROP decrements cursor, RESET clears, META updates flags, STEP for an unknown history creates it
    h.cursor = 3
    h.morph_target = 2
    more = [
        ("DROP", {"hist": "H1", "n": 2}, b""),
        ("META", {"hist": "H1", "members": ["objA", "objB"], "enabled": False, "morph_target": 1, "objects": {}}, b""),
        ("STEP", {"hist": "H9", "at": 0}, step_to_bytes(_delta_step(40, "objQ", rng))),
    ]
    assert replay(reg, more) == 3
    assert [e.meta["seq"] for e in h.entries] == [5]
    assert h.cursor == 1 and h.enabled is False and h.morph_target == 1
    h9 = reg.histories["H9"]
    assert list(h9.members) == ["objQ"] and reg.by_object["objQ"] == "H9" and len(h9.entries) == 1
    assert reg.next_seq == 41
    assert replay(reg, [("RESET", {"hist": "H1"}, b""), ("SAVE", {"blend": "x"}, b"")]) == 0  # after last SAVE: nothing
    assert len(h.entries) == 1
    assert replay(reg, [("SAVE", {"blend": "x"}, b""), ("RESET", {"hist": "H1"}, b"")]) == 1
    assert h.entries == [] and h.cursor == 0 and h.morph_target is None


def test_embed_base64():
    from hindsight.core.persist import registry_to_bytes, registry_from_bytes
    reg, originals = _build_registry()
    s = base64.b64encode(registry_to_bytes(reg)).decode("ascii")
    assert isinstance(s, str)
    reg2 = registry_from_bytes(base64.b64decode(s.encode("ascii")))
    _assert_reg_matches(reg2, originals, reg)


TESTS = [
    test_append_read_roundtrip,
    test_crash_tolerance,
    test_atomic_write,
    test_registry_roundtrip,
    test_sidecar,
    test_rename_blend_without_folder,
    test_replay_after_save,
    test_replay_semantics,
    test_embed_base64,
]


def main():
    _install("step", _standin_step_module, _check_step)
    _install("history", _standin_history_module, _check_history)
    _install("registry", _standin_registry_module, _check_registry)
    print("INFO stand-ins in use: %s" % (", ".join(STANDINS) if STANDINS else "none (real modules)"))
    failed = 0
    for t in TESTS:
        try:
            t()
            print("PASS %s" % t.__name__)
        except Exception:
            failed += 1
            print("FAIL %s" % t.__name__)
            traceback.print_exc()
    sys.stdout.flush()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
