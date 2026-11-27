"""#129: the corpus of real history files the Rust moves (#24, dev/RUST_PLAN.md rule 2) are checked against, and
the damaged copies made from them.

FILES names each history file in tests/fixtures by where its history is and its format:

- "registry": a history file (`HSR1`, formats 1 to 4), read by `persist.registry_from_bytes`;
- "text": the registry's bytes as base64 in the .blend's history Text (`hindsight_data`, format 4);
- "parts": format 5's index part and step parts in the .blend's history Text (`hindsight_parts`), read by
  `persist.registry_from_format5`;
- "journal": V1's crash journal (`HSJ1` records, format 1 of the journal), read by `journal.Journal`.

The format 5 .blend files of tests/fixtures/older_builds other than v2/ hold v2's parts byte for byte
(test_bl_older_builds), so only v2/ is listed; their history folders, which the older builds wrote, are.
`payload(path, kind)` is what the core reads, `damaged(data, kind)` the hostile copies of it.

MADE names the files this module makes with today's code (#144 review, point 2), the same bytes on every call:
`make(name)` gives a payload as `payload` does. `wrong_types(kind)` gives inputs of the wrong type (rule 2).
"""
import binascii
import os
import shutil
import struct
import tempfile

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

FILES = (  # (path under tests/fixtures, where its history is, format)
    ("beta1_format1.hsr", "registry", 1),
    ("beta2wip_format2.hsr", "registry", 2),
    ("beta2wip_format3.hsr", "registry", 3),
    ("beta2/both/scene_hindsight/history.hsr", "registry", 4),
    ("beta2/crash/scene_hindsight/history.hsr", "registry", 4),
    ("beta2/folder/scene_hindsight/history.hsr", "registry", 4),
    ("older_builds/beta2_both/scene_hindsight/history.hsr", "registry", 4),
    ("older_builds/beta2_folder/scene_hindsight/history.hsr", "registry", 4),
    ("older_builds/beta3_both/scene_hindsight/history.hsr", "registry", 4),
    ("older_builds/beta3_folder/scene_hindsight/history.hsr", "registry", 4),
    ("beta2/both/scene.blend", "text", 4),
    ("beta2/embed/scene.blend", "text", 4),
    ("older_builds/v2/scene.blend", "parts", 5),
    ("beta2/crash/scene.hsj", "journal", 1),
)


MADE = (  # (name, where its history is, format): made by make(name), not kept in tests/fixtures
    ("every_record.hsj", "journal", 1),     # today's Journal, every record type, steps of every kind
    ("big_format5", "parts", 5),            # 6,000 steps, several MB, two histories, more than one step part
)


def payload(path, kind):
    """What the core reads: the bytes of a registry, text or journal; for parts, the `hindsight_parts` group as a
    dict ({"index": bytes, "index_copy": bytes, uid: {"0": bytes, ...}}). text and parts need bpy."""
    full = os.path.join(FIX, path)
    if kind in ("registry", "journal"):
        with open(full, "rb") as fh:
            return fh.read()
    import tests.harness as H
    from hindsight.bl import persistence
    t = H.stored_text(full)
    return binascii.a2b_base64(t[persistence.DATA_PROP]) if kind == "text" else t[persistence.PARTS_PROP]


def header_format(data):
    """The format a registry's header names (formats 1 and 2 called it "version")."""
    import json
    (n,) = struct.unpack_from("<I", data, 4)
    doc = json.loads(bytes(data[8:8 + n]))
    return int(doc.get("format", doc.get("version", 1)) or 1)


def damaged(data, kind):
    """[(name, bytes)]: copies of a registry, text or journal's bytes cut short, with flipped bytes and with a length
    too big. The same bytes on every call. shortcut: parts get cut and flipped bytes only (damaged_parts), since
    format 5's lengths are in its index part's JSON; add a too-big step length there if a move needs it."""
    n = len(data)
    out = [("cut_to_7", data[:7]), ("cut_in_half", data[:n // 2]), ("cut_last_byte", data[:-1])]
    for at in sorted({8, 20, n // 2, n - 5, n - 1}):
        out.append((f"flip_{at}", data[:at] + bytes([data[at] ^ 0xFF]) + data[at + 1:]))
    if kind == "journal":                        # MAGIC(4s) type(u8) header_len(u32) payload_len(u64): the first record
        out.append(("header_len_too_big", data[:5] + struct.pack("<I", 0xFFFFFFFF) + data[9:]))
        out.append(("payload_len_too_big", data[:9] + struct.pack("<Q", 2 ** 63 - 1) + data[17:]))
    else:                                        # b"HSR1" header_len(u32) header, then u64 length + bytes per step
        out.append(("header_len_too_big", data[:4] + struct.pack("<I", 0xFFFFFFFF) + data[8:]))
        at = 8 + struct.unpack_from("<I", data, 4)[0]
        if at + 8 <= n:
            out.append(("step_len_too_big", data[:at] + struct.pack("<Q", 2 ** 63 - 1) + data[at + 8:]))
    return out


def damaged_parts(parts):
    """[(name, parts)]: copies of format 5's parts with the index part, or the first step part, cut short or with a
    flipped byte (see damaged); the index copy flipped or gone, both index parts cut short (no readable index), and the
    first step part missing."""
    uid = next(k for k in parts if k not in ("index", "index_copy"))
    first = min(parts[uid], key=int)
    out = []
    for name, part in (("index", parts["index"]), (f"{uid}/{first}", parts[uid][first])):
        part = bytes(part)
        for what, b in (("cut_in_half", part[:len(part) // 2]),
                        ("flip_middle", part[:len(part) // 2] + bytes([part[len(part) // 2] ^ 0xFF])
                         + part[len(part) // 2 + 1:])):
            copy = {k: (dict(v) if isinstance(v, dict) else v) for k, v in parts.items()}
            if name == "index":
                copy["index"] = b
            else:
                copy[uid][first] = b
            out.append((f"{name}:{what}", copy))

    def edit(name, **parts_set):
        copy = {k: (dict(v) if isinstance(v, dict) else v) for k, v in parts.items()}
        for k, v in parts_set.items():
            if v is None:
                copy.pop(k, None)
            else:
                copy[k] = v
        return (name, copy)

    idx, cp = bytes(parts["index"]), bytes(parts.get("index_copy") or parts["index"])
    out.append(edit("index_copy:flip_middle", index_copy=cp[:len(cp) // 2] + bytes([cp[len(cp) // 2] ^ 0xFF])
                    + cp[len(cp) // 2 + 1:]))
    out.append(edit("index+index_copy:cut_in_half", index=idx[:len(idx) // 2], index_copy=cp[:len(cp) // 2]))
    out.append(edit("index_copy:missing", index_copy=None))
    missing = edit(f"{uid}/{first}:missing")
    missing[1][uid].pop(first)
    out.append(missing)
    return out


def make(name):
    """The payload of MADE's `name`, made with today's code from a fixed seed."""
    return {"every_record.hsj": _every_record, "big_format5": _big_format5}[name]()


def _steps(rng, n, target, seq=1, nv=20000, k=60):
    """n steps of `target` from seq on: strokes (a position delta of k vertices, extra of every JSON kind), and at
    n // 2 a topology step, at n // 3 a data step with records (MISSING on one side)."""
    import numpy as np
    from hindsight.core.types import (CORNER_EDGE_KEY, CORNER_VERT_KEY, DATA, DELTA, EDGE_VERTS_KEY,
                                      FACE_OFFSETS_KEY, MISSING, POSITION_KEY, TOPO, ChannelDelta, DataChange,
                                      Snapshot, Step)

    def snap(nv, nf):
        pos = rng.normal(size=nv * 3).astype(np.float32)
        pos[:3] = [-0.0, np.inf, 1e-40]
        return Snapshot({"POINT": nv, "EDGE": nf * 2, "FACE": nf, "CORNER": nf * 4}, {
            POSITION_KEY: pos, EDGE_VERTS_KEY: rng.integers(0, nv, size=nf * 4).astype(np.int32),
            CORNER_VERT_KEY: rng.integers(0, nv, size=nf * 4).astype(np.int32),
            CORNER_EDGE_KEY: rng.integers(0, nf * 2, size=nf * 4).astype(np.int32),
            FACE_OFFSETS_KEY: (np.arange(nf + 1) * 4).astype(np.int32)})

    out = []
    for i in range(n):
        s, t = seq + i, 1000.0 + (seq + i) * 0.25
        if i == n // 2:
            out.append(Step(seq=s, label="Voxel Remesh", mode="SCULPT", target=target, time=t, kind=TOPO,
                            before=snap(300, 120), after=snap(420, 200)))
        elif i == n // 3:
            out.append(Step(seq=s, label="Move", mode="OBJECT", target=target, time=t, kind=DATA, records=[
                DataChange("xform", target, {"location": ([0.0, -0.0, 1e300], [0.1, 2.5, -3.0])}),
                DataChange("rna", "matE", {"name": (MISSING, "Mat \u00e9\u6f22"), "use_nodes": (True, MISSING)})]))
        else:
            idx = np.unique(rng.integers(0, nv, size=k)).astype(np.uint32)
            vals = rng.integers(0, 2 ** 32, size=(len(idx), 3), dtype=np.uint64).astype(np.uint32)
            out.append(Step(seq=s, label=f"Stroke {s}", mode="SCULPT", target=target, time=t, kind=DELTA,
                            deltas=[ChannelDelta(POSITION_KEY, idx, vals, (True, True))],
                            extra={"brush": "Draw \u00e9", "r": 0.1 * i, "n": i, "ok": i % 2 == 0, "none": None,
                                   "list": [1, 2.5, "x", [None]], "tiny": 5e-324, "big": 2 ** 70}))
    return out


def _every_record():
    """A journal written by today's Journal with every record type, as the runtime writes them (bl/runtime.py,
    bl/datarec.py, bl/joinsep.py, bl/persistence.py), a step of every kind among its STEP records."""
    import numpy as np
    from hindsight.core.journal import Journal
    from hindsight.core.step import step_to_bytes
    from hindsight.core.types import JOURNAL_CODEC
    a, b = "0123456789abcdef", "fedcba9876543210"
    steps = _steps(np.random.default_rng(11), 6, "objA")
    d = tempfile.mkdtemp(prefix="hs_corpus_")
    try:
        path = os.path.join(d, "scene.hsj")
        j = Journal(path)
        j.append("SAVE", {"blend": "C:\\scenes\\scene.blend", "save_id": "a1b2c3d4e5f6", "s": "s1"})
        j.append("META", {"hist": a, "members": ["objA"], "enabled": True, "morph_target": None,
                          "objects": {"objA": "Sphere \u00e9"}})
        j.append("META", {"hist": b, "members": ["objB", "objC"], "enabled": False, "morph_target": 1})
        for at, st in enumerate(steps):
            j.append("STEP", {"hist": a, "at": at}, step_to_bytes(st, JOURNAL_CODEC, 0))
        j.append("CURSOR", {"hist": a, "cursor": 4})
        j.append("TRUNC", {"hist": a, "keep": 5})
        j.append("DROP", {"hist": a, "n": 1})
        j.append("RESET", {"hist": b})
        j.append("RESET", {"hist": a, "at": 2, "mesh": "f1"})
        j.append("META", {"parts": "a1b2c3d4e5f6"})
        j.append("META", {"resync": True, "s": "s1"})
        j.append("META_EVERYTHING", {"unnamed": True})
        j.append("META_EVERYTHING", {"uid": "matE", "on": False})
        j.flush(fsync=False)
        j.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _big_format5():
    """Format 5's parts (as payload gives them) of two histories saved by today's registry_to_format5: 5,990 steps
    of objA (strokes, a topology step, a data step; cursor before the end, a morph target) and 10 of objB and objC."""
    import numpy as np
    from hindsight.core import persist
    from hindsight.core.history import History
    from hindsight.core.registry import Registry
    from hindsight.core.types import Entry
    rng = np.random.default_rng(5)
    reg = Registry()
    a, b = History("0123456789abcdef", ["objA"]), History("fedcba9876543210", ["objB", "objC"])
    a.entries = [Entry(step=st) for st in _steps(rng, 5990, "objA")]
    b.entries = [Entry(step=st) for st in _steps(rng, 10, "objB", seq=5991)]
    a.cursor, a.morph_target, b.cursor = 5980, 17, 10
    reg.histories = {a.uid: a, b.uid: b}
    reg.by_object = {"objA": a.uid, "objB": b.uid, "objC": b.uid}
    reg.next_seq, reg.disabled, reg.everything, reg.save_id = 6001, {"objD"}, {"matE"}, "a1b2c3d4e5f6"
    index, parts = persist.registry_to_format5(reg, "zstd")
    return {"index": index, "index_copy": index, **{uid: {str(i): p for i, p in enumerate(ps)}
                                                     for uid, ps in parts.items()}}


_BIG = []       # big_format5, made once for wrong_types


def wrong_types(kind):
    """[(name, input)]: rule 2's wrong types for a reader of `kind` ("registry" also covers "text"): values that are
    not bytes; format 5 parts of the wrong type; a journal whose record headers hold JSON of the wrong type."""
    if kind == "registry":
        data = payload("beta2/folder/scene_hindsight/history.hsr", "registry")
        return [("none", None), ("str", data.decode("latin-1")), ("int", 5), ("float", 1.5), ("list", [data]),
                ("dict", {"HSR1": data}), ("bytearray", bytearray(data)), ("memoryview", memoryview(data))]
    if kind == "parts":
        _BIG[:] = _BIG or [make("big_format5")]
        uid = "fedcba9876543210"
        good = _BIG[0]
        part = bytes(good[uid]["0"])

        def edit(**kw):
            out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in good.items()}
            out.update(kw)
            return out
        return [("index_str", edit(index=bytes(good["index"]).decode("latin-1"), index_copy=None)),
                ("index_int", edit(index=5, index_copy=5)), ("index_none", edit(index=None, index_copy=None)),
                ("index_list", edit(index=[good["index"]], index_copy=None)),
                ("part_str", edit(**{uid: {"0": part.decode("latin-1")}})), ("part_int", edit(**{uid: {"0": 7}})),
                ("part_list", edit(**{uid: {"0": [part]}})), ("part_bytearray", edit(**{uid: {"0": bytearray(part)}}))]
    from hindsight.core.journal import Journal
    d = tempfile.mkdtemp(prefix="hs_corpus_")
    try:
        path = os.path.join(d, "scene.hsj")
        j = Journal(path)
        for rtype, header, pl in (("SAVE", [1, 2], b""), ("META", 5, b""), ("META", "x", b""),
                                  ("STEP", {"hist": 5, "at": "0"}, b"not a step"),
                                  ("CURSOR", {"hist": ["H"], "cursor": None}, b""),
                                  ("TRUNC", {"hist": "H", "keep": -1.5}, b""), ("DROP", {"hist": "H", "n": True}, b""),
                                  ("RESET", {"hist": {"H": 1}, "at": [2]}, b""),
                                  ("META_EVERYTHING", {"unnamed": "yes", "on": 1}, b"")):
            j.append(rtype, header, pl)
        j.close()
        with open(path, "rb") as fh:
            return [("headers", fh.read())]
    finally:
        shutil.rmtree(d, ignore_errors=True)
