"""#66 (11.3): a step in an encoding no build knows (dev/FORMAT5_ENCODINGS.md: 255 is never given out), made from a
real step, and a saved format 5 history with one of its steps swapped for it, as a later build would have saved it."""
import json
import zlib

from hindsight.core.native import ENGINE

UNKNOWN = 255


def as_encoding(b, n=UNKNOWN, fix_crc=True) -> bytes:
    """Step bytes `b` (format 3) with byte 10, the encoding number, set to `n`; the step prefix CRC made right
    again unless `fix_crc` is False (then it is damage)."""
    out = bytearray(b)
    out[10] = n
    if fix_crc:
        out[4:8] = (zlib.crc32(bytes(out[8:])) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(out)


def steps(parts) -> list:
    """The bytes of every step in a history's step parts, in order."""
    out = []
    for p in parts:
        _first, offs, lens, _crcs, _metas = ENGINE.part_read(p)
        out += [bytes(p[o:o + n]) for o, n in zip(offs, lens)]
    return out


def swap_step(index, parts, uid, at, blob):
    """(index part, parts) with step `at` of history `uid` replaced by `blob`: its step part built again around it
    and the index part listing that step part's new length and CRC-32."""
    ps = list(parts[uid])
    for i, p in enumerate(ps):
        first, offs, lens, crcs, metas = ENGINE.part_read(p)
        if at >= len(offs):
            at -= len(offs)
            continue
        blobs = [bytes(p[o:o + n]) for o, n in zip(offs, lens)]
        crcs = list(crcs)
        blobs[at], crcs[at] = blob, zlib.crc32(blob) & 0xFFFFFFFF
        part, begin, crc = ENGINE.part_build(first, blobs, crcs, bytes(metas))
        ps[i] = part
        doc = json.loads(bytes(ENGINE.index_read(index)).decode("utf-8"))
        hd = next(h for h in doc["histories"] if h["uid"] == uid)
        hd["parts"][i].update(len=len(part), crc=crc, begin=begin)
        return ENGINE.index_build(json.dumps(doc).encode("utf-8")), dict(parts, **{uid: ps})
    raise IndexError(at)
