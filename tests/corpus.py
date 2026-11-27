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
"""
import binascii
import os
import struct

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
    flipped byte (see damaged)."""
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
    return out
