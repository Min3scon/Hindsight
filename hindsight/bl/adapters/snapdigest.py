"""D154, D156: the digest of a file-level datablock's .blend snapshot: its own blocks, with addresses
replaced by block numbers and what Blender gives again on reading left out (pure byte work, no bpy)."""
from __future__ import annotations

import hashlib
import re

import numpy as np

_DINFO: dict = {}          # DNA id -> _dna_info of that DNA block
# D156: written, but given again when the datablock is read (a file opened, a snapshot loaded); and every
# padding field (`_pad...`, D158: 5.0 writes an action channel bag's as it was in memory)
_ID_RUNTIME = (b"recalc", b"recalc_after_undo_push", b"session_uid")
# a char array of at least this many is a name or path: Blender reads it up to its end (5.0 writes a brush
# asset's catalog name with what is after its end as it was in memory); shorter ones can be small numbers
_TEXT_MIN = 32
_NOT_OWN = (b"REND", b"TEST", b"GLOB", b"DNA1", b"ENDB", b"USER")    # a .blend's file blocks, not the datablock's


def _heads(raw: bytes):
    """(block head size, offset of the SDNA number in a head) of a .blend written by 4.2 to 5.x."""
    return (32, 4) if raw[7:9] == b"17" else (24, 16)


def _dna_info(raw: bytes, blocks: list) -> tuple:
    """From the snapshot's DNA block: (the SDNA number of IDProperty, -1 when it isn't found; {SDNA number:
    (struct size, byte offsets of its pointers, byte offsets of its IDs' _ID_RUNTIME fields and padding)});
    (-1, {}) when it isn't read."""
    try:
        hs = _heads(raw)[0]
        pos, n = next((p, n) for p, c, n in blocks if c == b"DNA1")
        d = raw[pos + hs:pos + n]

        def strings(tag, i):
            i = d.index(tag, i) + 8
            out = []
            for _ in range(int.from_bytes(d[i - 4:i], "little")):
                j = d.index(b"\0", i)
                out.append(d[i:j])
                i = j + 1
            return out, i
        names, i = strings(b"NAME", 0)
        types, i = strings(b"TYPE", i)
        i = d.index(b"TLEN", i) + 4
        tlen = np.frombuffer(d, np.uint16, len(types), i).tolist()
        i = d.index(b"STRC", i + 2 * len(types)) + 8
        strc = {}
        for k in range(int.from_bytes(d[i - 4:i], "little")):
            m = int.from_bytes(d[i + 2:i + 4], "little")
            fields = np.frombuffer(d, np.uint16, 2 * m, i + 4).reshape(-1, 2).tolist()
            strc[int.from_bytes(d[i:i + 2], "little")] = (k, fields)
            i += 4 + 4 * m
    except (StopIteration, ValueError):
        return -1, {}
    offs: dict = {}

    def ptr_offsets(t):             # byte offsets of the pointers in struct type t, its embedded structs' too
        if t not in offs:
            out, run, txt, at = [], [], [], 0
            for ft, fn in strc[t][1]:
                nm = names[fn]
                dims = 1
                for x in re.findall(rb"\[(\d+)\]", nm):
                    dims *= int(x)
                if nm[:1] == b"*" or nm[:2] == b"(*":
                    out += range(at, at + 8 * dims, 8)
                    at += 8 * dims
                    continue
                if (types[t] == b"ID" and nm in _ID_RUNTIME) or nm.startswith(b"_pad"):
                    run += range(at, at + tlen[ft] * dims)
                elif types[ft] == b"char" and nm.count(b"[") == 1 and dims >= _TEXT_MIN:
                    txt.append((at, dims))
                if ft in strc:
                    sub = ptr_offsets(ft)
                    out += [at + e * tlen[ft] + o for e in range(dims) for o in sub[0]]
                    run += [at + e * tlen[ft] + o for e in range(dims) for o in sub[1]]
                    txt += [(at + e * tlen[ft] + o, m) for e in range(dims) for o, m in sub[2]]
                at += tlen[ft] * dims
            offs[t] = out, run, txt
        return offs[t]
    ptrs = {k: (tlen[t], *(np.array(x, dtype=np.int64) for x in ptr_offsets(t)))
            for t, (k, _) in strc.items()}
    idp = types.index(b"IDProperty") if b"IDProperty" in types else -1
    return (strc[idp][0] if idp in strc else -1), ptrs


def _ptr_words(n: int, info: tuple, sdna: int):
    """The numbers of the 8-byte words that are pointers in n bytes of struct sdna (info: _dna_info)."""
    size, offs = info[1].get(sdna, (0, ()))[:2]
    if not len(offs) or not size:
        return np.zeros(0, dtype=np.int64)
    b = (offs[None, :] + size * np.arange(n // size)[:, None]).ravel()
    return b[(b % 8 == 0) & (b < n - n % 8)] // 8


def _digest(raw: bytes, blocks: list, info: tuple = (-1, {})) -> bytes:
    """The hash of a snapshot's own blocks (not the file's: GLOB holds the .blend's path), with every
    block address (memory addresses, which change when Blender allocates the data again) and every
    pointer to one replaced by the block's number, and every pointer to nothing written by 0 (Blender reads
    it as null; 5.0 writes an image's runtime pointer as an address from the blocks' counter). info
    (_dna_info): the custom property blocks and the data they point to are left out (an empty group where
    there was none is the same; the D151 writer puts them back; 4.2 leaves a text's byte after its end)."""
    if not blocks:
        return hashlib.blake2b(raw, digest_size=16).digest()
    hs, so = _heads(raw)
    idprop = info[0]
    addr = np.array([int.from_bytes(raw[p + 8:p + 16], "little") for p, _, _ in blocks], dtype=np.uint64)
    sdna = [int.from_bytes(raw[p + so:p + so + 4], "little", signed=True) for p, _, _ in blocks]
    held = set()
    for (p, _, n), s in zip(blocks, sdna):
        if s == idprop >= 0:
            held.update(np.frombuffer(raw, np.uint64, (n - hs) // 8, p + hs)[_ptr_words(n - hs, info, s)].tolist())
    left = [c in _NOT_OWN or s == idprop or (c == b"DATA" and int(a) in held)
            for (p, c, _), s, a in zip(blocks, sdna, addr)]
    num = np.cumsum([not x for x in left], dtype=np.uint64) * np.array([not x for x in left], dtype=np.uint64)
    order = np.argsort(addr)
    keys = addr[order]
    h = hashlib.blake2b(digest_size=16)
    for (pos, code, n), s, skip in zip(blocks, sdna, left):
        if skip:
            continue
        h.update(raw[pos:pos + 8] + raw[pos + 16:pos + hs])
        k = (n - hs) // 8
        words = np.frombuffer(raw, dtype=np.uint64, count=k, offset=pos + hs).copy()
        at = np.minimum(np.searchsorted(keys, words), len(keys) - 1)
        hit = (keys[at] == words) & (words != 0)
        b = _ptr_words(n - hs, info, s)
        words[b[~hit[b]]] = 0
        words[hit] = num[order[at[hit]]]
        size, _p, run, txt = info[1].get(s, (0, (), (), ()))
        if len(run) and size:
            r = (run[None, :] + size * np.arange((n - hs) // size)[:, None]).ravel()
            words.view(np.uint8)[r[r < 8 * k]] = 0
        for a, m in txt if size else ():     # a name or path: what is after its end is left out
            r = (a + size * np.arange((n - hs) // size))[:, None] + np.arange(m)
            r = r[r[:, -1] < 8 * k]
            u8 = words.view(np.uint8)
            u8[r[np.cumsum(u8[r] == 0, axis=1) > 0]] = 0
        h.update(words.tobytes() + raw[pos + hs + 8 * k:pos + n])
    return h.digest()


def _dna_digest(raw: bytes, blocks: list) -> tuple:
    """(DNA block, its id, _digest) of a snapshot written with its DNA."""
    mv = memoryview(raw)
    dna = b"".join(mv[p:p + n] for p, c, n in blocks if c == b"DNA1")
    did = hashlib.blake2b(dna, digest_size=8).hexdigest() if blocks else ""
    if did and did not in _DINFO:
        _DINFO[did] = _dna_info(raw, blocks)
    return dna, did, _digest(raw, blocks, _DINFO.get(did, (-1, {})))
