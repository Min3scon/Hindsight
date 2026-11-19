import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""M5: the checksum of the registry stream and the journal is the engine's crc32.

The framing itself (registry stream, journal records) stays Python: measured against a Rust reader
(dev/reports/RUST.md, M5), reading is the kernel's copy and what Python spent its time on was
zlib.crc32, which the engine does ten times as fast. So hindsight/core/{persist,journal,types}.py
call `native.crc32`, and the pre-M5 files (zlib.crc32) are kept as tests/reference/{persist,journal}.py.

  crc32      equals zlib.crc32 for every buffer kind, length, start value and chaining
  journal    the bytes written, the records read back, the size found on open: equal to the reference,
             for whole files, every truncation (each byte), every flipped byte, huge claimed lengths
  registry   the bytes written, and what registry_from_bytes returns (entries, cursors, load_report,
             errors) equal to the reference, for formats 1 to 4, every truncation, every flipped byte,
             damaged steps with their checksums patched to match, huge claimed lengths"""
import array
import json
import struct
import tempfile
import traceback
import zipfile
import zlib

import numpy as np

from reference import journal as rjournal, persist as rpersist                 # noqa: E402
from hindsight.core import journal, native, persist, step as stepmod          # noqa: E402
from hindsight.core.history import History                                    # noqa: E402
from hindsight.core.registry import Registry                                  # noqa: E402
from hindsight.core.types import Entry                                        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "fixtures", "step_corpus_base.zip")
RNG = np.random.default_rng(5)
SCRATCH = tempfile.mkdtemp(prefix="hs_fcrc_")      # one folder for the whole run, removed at the end


def test_crc32_equals_zlib():
    crc = native.crc32
    sizes = [0, 1, 2, 3, 7, 8, 15, 16, 17, 63, 64, 65, 255, 1000, 4095, 4096, 9001, 65535, 65536, 65537, 200_001, 3_000_001]
    n = 0
    for sz in sizes:
        b = RNG.bytes(sz)
        for init in (0, 1, 0x12345678, 0xFFFFFFFF):
            assert crc(b, init) == zlib.crc32(b, init), (sz, init)
            n += 1
        assert crc(b) == zlib.crc32(b)
        # every buffer kind
        for kind in (bytearray(b), memoryview(b), memoryview(bytearray(b)), array.array("B", b), np.frombuffer(b, np.uint8)):
            assert crc(kind) == zlib.crc32(b), (sz, type(kind))
    # chaining, as the journal does it
    a, b, c = RNG.bytes(70_000), RNG.bytes(123), RNG.bytes(0)
    assert crc(c, crc(b, crc(a))) == zlib.crc32(a + b + c)
    # a slice of a bigger buffer, a multi-dimensional contiguous array
    big = RNG.bytes(300_000)
    assert crc(memoryview(big)[1001:250_001]) == zlib.crc32(big[1001:250_001])
    assert crc(np.arange(24, dtype=np.float32).reshape(2, 3, 4)) == zlib.crc32(np.arange(24, dtype=np.float32).tobytes())
    # what is not a contiguous buffer is refused (zlib raises too), and so is a str or a float
    for bad in (memoryview(big)[::2], "text", 1.5, None, [1, 2]):
        for f in (crc, zlib.crc32):
            try:
                f(bad)
            except (TypeError, BufferError, ValueError):
                pass
            else:
                raise AssertionError((f, bad))
    print(f"PASS test_crc32_equals_zlib ({n} start values and sizes, six buffer kinds)")


def test_crc32_with_other_threads():
    """A large bytes object is hashed without the GIL: other threads run meanwhile and the value
    is right; a bytearray is hashed with the GIL held, so a writer can't change it halfway."""
    import threading
    big = RNG.bytes(40_000_000)
    want = zlib.crc32(big)
    got, ticks, stop = [], [0], threading.Event()

    def spin():
        while not stop.is_set():
            ticks[0] += 1

    t = threading.Thread(target=spin); t.start()
    try:
        for _ in range(3):
            got.append(native.crc32(big))
    finally:
        stop.set(); t.join()
    assert got == [want] * 3
    assert ticks[0] > 100, ticks
    ba = bytearray(big[:5_000_000])
    assert native.crc32(ba) == zlib.crc32(bytes(ba))
    print(f"PASS test_crc32_with_other_threads (a second thread ran {ticks[0]} turns during 3 hashes of 40 MB)")


# ------------------------------------------------------------------------------ journal
def journal_records():
    recs = []
    for i, code in enumerate(sorted(journal.RTYPE_NAMES)):
        sz = (0, 1, 33, 200, 5000, 70_000)[i % 6]
        recs.append((journal.RTYPE_NAMES[code], {"i": i, "uid": "H%d" % (i % 3), "s": "é" * (i % 4)}, RNG.bytes(sz)))
    recs.append(("STEP", {}, b""))
    recs.append(("CURSOR", {"at": 3, "note": "x" * 3000}, b"\x00" * 10))
    return recs


def write_journal(mod, path, recs):
    j = mod.Journal(path)
    offs = [j.append(t, h, p) for t, h, p in recs]
    j.flush(False)
    j.close()
    return offs


def read_all(mod, path, upto=None):
    j = mod.Journal(path)
    try:
        return j.size(), j.read(upto)
    finally:
        j.close()


def scan_end(mod, data):
    """The last valid record end that `mod`'s scanner finds in `data`, and how many records."""
    p = os.path.join(SCRATCH, "scan.j")
    with open(p, "wb") as fh:
        fh.write(data)
    with open(p, "rb") as fh:
        got = [(s, e, c, h, pl) for s, e, c, h, pl in mod._scan(fh, len(data), True)]
    with open(p, "rb") as fh:
        got2 = [(s, e, c, h) for s, e, c, h, _ in mod._scan(fh, len(data), False)]
    assert [g[:4] for g in got] == got2
    return got


def test_journal_equals_the_reference():
    d = SCRATCH
    recs = journal_records()
    pa, pb = os.path.join(d, "live.j"), os.path.join(d, "ref.j")
    oa, ob = write_journal(journal, pa, recs), write_journal(rjournal, pb, recs)
    assert oa == ob
    da, db = open(pa, "rb").read(), open(pb, "rb").read()
    assert da == db, "journal bytes differ"
    assert read_all(journal, pa) == read_all(rjournal, pb)
    for upto in (0, 1, 16, 17, oa[1], oa[1] - 1, oa[2] + 5, len(da) - 1, len(da), len(da) + 50):
        assert read_all(journal, pa, upto) == read_all(rjournal, pb, upto), upto
    # a record over the 4 MB read chunk (the size scan reads it in pieces)
    big = [("STEP", {"big": 1}, RNG.bytes(9_500_000)), ("CURSOR", {"at": 1}, b"")]
    qa, qb = os.path.join(d, "big_live.j"), os.path.join(d, "big_ref.j")
    write_journal(journal, qa, big); write_journal(rjournal, qb, big)
    assert open(qa, "rb").read() == open(qb, "rb").read()
    assert read_all(journal, qa) == read_all(rjournal, qb)
    print(f"PASS test_journal_equals_the_reference ({len(recs)} records, {len(da)} bytes, a 9.5 MB record)")


def outcome_journal(mod, data):
    return [(s, e, c, h, p) for s, e, c, h, p in scan_end(mod, data)]


def test_journal_truncated_and_flipped_everywhere():
    d = SCRATCH
    recs = [("STEP", {"i": i}, RNG.bytes(int(RNG.integers(0, 90)))) for i in range(7)] + [("CURSOR", {"at": 2}, b"")]
    p = os.path.join(d, "j")
    write_journal(journal, p, recs)
    data = open(p, "rb").read()
    tests = 0
    # cut at every byte: both readers find the same records, and opening cuts the file back to the same size
    for n in range(len(data) + 1):
        cut = data[:n]
        a, b = outcome_journal(journal, cut), outcome_journal(rjournal, cut)
        assert a == b, n
        tests += 1
    for n in range(0, len(data), 5):
        for name in ("a.j", "b.j"):
            with open(os.path.join(d, name), "wb") as fh:
                fh.write(data[:n])
        assert read_all(journal, os.path.join(d, "a.j")) == read_all(rjournal, os.path.join(d, "b.j")), n
    # a flipped byte anywhere, and the same with the record's CRC patched to match the damage
    for i in range(len(data)):
        for xor in (0x01, 0x80, 0xFF):
            bad = bytearray(data)
            bad[i] ^= xor
            assert outcome_journal(journal, bytes(bad)) == outcome_journal(rjournal, bytes(bad)), (i, xor)
            tests += 1
    # a claimed payload or header length that runs past the file, or past the u64
    head = struct.Struct("<4sBIQ")
    for plen in (2 ** 64 - 1, 2 ** 63, 2 ** 40, len(data) + 1, 10):
        for hlen in (0, 2, 64 * 2 ** 20, 64 * 2 ** 20 + 1, 2 ** 32 - 1):
            bad = head.pack(b"HSJ1", 1, hlen, plen) + b"{}" * 8 + b"\x00" * 8
            assert outcome_journal(journal, bad) == outcome_journal(rjournal, bad), (plen, hlen)
            bad2 = data[:40] + bad
            assert outcome_journal(journal, bad2) == outcome_journal(rjournal, bad2), (plen, hlen)
    # records with the CRC recomputed after damage (the damage is then not detectable by the CRC): a header that
    # is not JSON stops read() the same way in both
    hb = b"{not json"
    prefix = head.pack(b"HSJ1", 1, len(hb), 3) + hb + b"abc"
    rec = prefix + struct.pack("<I", zlib.crc32(prefix))
    data3 = data + rec + data
    pa, pb = os.path.join(d, "a.j"), os.path.join(d, "b.j")
    open(pa, "wb").write(data3); open(pb, "wb").write(data3)
    assert read_all(journal, pa) == read_all(rjournal, pb)
    print(f"PASS test_journal_truncated_and_flipped_everywhere ({tests} damaged journals read the same)")


# ------------------------------------------------------------------------------ registry
def corpus_blobs(limit=2600):
    z = zipfile.ZipFile(CORPUS)
    rows = [r for r in json.loads(z.read("index.json")) if not r.get("legacy")]
    out = []
    for r in rows:
        b = z.read(r["name"])
        if 0 < len(b) <= limit:
            out.append(b)
    assert len(out) >= 8, len(out)
    return out


def build_registry(blobs):
    reg = Registry()
    seq = 1
    for hi, (uid, members, picks) in enumerate((("H0", ["a", "b"], blobs[:5]), ("H1", ["c"], blobs[3:8]), ("H2", ["d"], []))):
        h = History(uid, list(members))
        for b in picks:
            meta = dict(stepmod.read_header(b)["meta"], seq=seq)
            seq += 1
            h.entries.append(Entry(blob=b, meta=meta))
        h.cursor = max(0, len(h.entries) - 1 - hi)
        h.enabled = hi != 1
        h.morph_target = 2 if hi == 0 else None
        reg.histories[uid] = h
        for m in members:
            reg.by_object[m] = uid
    reg.next_seq = seq + 3
    reg.saved = {"a": [1, 2]}
    reg.save_id = "sid-1"
    return reg


def canon(reg):
    out = {"next_seq": reg.next_seq, "by_object": reg.by_object, "saved": reg.saved, "save_id": reg.save_id,
           "use_global": reg.use_global, "preserve_modes": reg.preserve_modes,
           "disabled": sorted(getattr(reg, "disabled", ())), "everything": sorted(getattr(reg, "everything", ())),
           "report": reg.load_report, "h": []}
    for uid, h in reg.histories.items():
        out["h"].append((uid, list(h.members), h.cursor, h.enabled, h.morph_target,
                         [(e.blob, e.meta, e.blob_crc()) for e in h.entries]))
    return out


def parse(mod, data):
    try:
        return ("ok", canon(mod.registry_from_bytes(data)))
    except Exception as exc:                  # the classes differ (each module has its own NewerFormatError)
        return ("err", type(exc).__name__, str(exc))


def parse_file(mod, data):
    p = os.path.join(SCRATCH, "f.hsr")
    with open(p, "wb") as fh:
        fh.write(data)
    try:
        with open(p, "rb") as fh:
            return ("ok", canon(mod.registry_from_bytes(mod._FileBytes(fh))))
    except Exception as exc:
        return ("err", type(exc).__name__, str(exc))


def layout(data):
    """(json_len, doc, [(len_pos, blob_pos, n)]) of a format 3 or 4 registry blob."""
    (jl,) = struct.unpack("<I", data[4:8])
    doc = json.loads(data[8:8 + jl])
    pos, spans = 8 + jl, []
    for hd in doc["histories"]:
        for _ in range(hd["n_entries"]):
            (n,) = struct.unpack("<Q", data[pos:pos + 8])
            spans.append((pos, pos + 8, n))
            pos += 8 + n
    return jl, doc, spans


def rebuild(doc, blobs, lens=None):
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    out = [b"HSR1", struct.pack("<I", len(js)), js]
    for i, b in enumerate(blobs):
        out += [struct.pack("<Q", len(b) if lens is None or lens.get(i) is None else lens[i]), b]
    out += [b"HSRT", struct.pack("<I", zlib.crc32(js))]
    return b"".join(out)


def same(data, what):
    a, b, c = parse(persist, data), parse(rpersist, data), parse_file(persist, data)
    assert a == b, (what, a if a[0] == "err" else "live ok", b if b[0] == "err" else "ref ok")
    assert c == parse_file(rpersist, data), what
    # the reference reads bytes and files alike; so must the live reader (same outcome)
    assert a == c or a[0] == "err" and c[0] == "err", what
    return a


def test_registry_written_and_read_like_the_reference():
    blobs = corpus_blobs()
    reg = build_registry(blobs)
    # a raw (unpacked) step in a history is packed on save: use the reference writer for the same registry
    live = persist.registry_to_bytes(reg)
    ref = rpersist.registry_to_bytes(reg)
    assert live == ref, "registry bytes differ"
    for codec in ("auto", "zstd", "lz4", "zlib", "none"):
        assert persist.registry_to_bytes(reg, codec) == rpersist.registry_to_bytes(reg, codec), codec
    ok = same(live, "whole")
    assert ok[0] == "ok" and not ok[1]["report"]["damaged"], ok
    assert sum(len(h[5]) for h in ok[1]["h"]) == 10
    print(f"PASS test_registry_written_and_read_like_the_reference ({len(live)} bytes, 3 histories, 5 codec settings)")
    return live


def test_registry_truncated_and_flipped_everywhere():
    live = persist.registry_to_bytes(build_registry(corpus_blobs()))
    jl, doc, spans = layout(live)
    n = 0
    for cut in range(len(live) + 1):
        same(live[:cut], ("cut", cut))
        n += 1
    for i in range(len(live)):
        for xor in (0x01, 0x40, 0xFF):
            bad = bytearray(live)
            bad[i] ^= xor
            same(bytes(bad), ("flip", i, xor))
            n += 1
    # damage inside a step with everything else intact: the step's saved CRC disagrees; and with the CRC
    # list patched to match, the step is accepted as it is (the step's own CRC decides when it is applied)
    blobs = [live[bp:bp + ln] for _lp, bp, ln in spans]
    for k in range(len(blobs)):
        for where in (0, len(blobs[k]) // 2, len(blobs[k]) - 1):
            if not blobs[k]:
                continue
            dmg = list(blobs)
            bb = bytearray(blobs[k]); bb[where] ^= 0x55
            dmg[k] = bytes(bb)
            r = same(rebuild(doc, dmg), ("damaged step", k, where))
            assert r[0] == "ok" and r[1]["report"]["damaged"], r
            d2 = json.loads(json.dumps(doc))
            flat = [(hi, i) for hi, hd in enumerate(d2["histories"]) for i in range(hd["n_entries"])]
            hi, i = flat[k]
            d2["histories"][hi]["crcs"][i] = zlib.crc32(dmg[k])
            r2 = same(rebuild(d2, dmg), ("patched", k, where))
            assert r2[0] == "ok" and not r2[1]["report"]["damaged"], r2
            n += 2
    # claimed step lengths: huge, past the end, zero, exactly the rest of the file
    for k in range(len(blobs)):
        for claim in (2 ** 64 - 1, 2 ** 63, 2 ** 32, len(live), 0, len(blobs[k]) + 1, max(0, len(blobs[k]) - 1)):
            same(rebuild(doc, blobs, {k: claim}), ("length claim", k, claim))
            n += 1
    # fewer or more steps than the header says, metas missing (the step must be read for its meta)
    d3 = json.loads(json.dumps(doc))
    d3["histories"][0]["n_entries"] += 2
    same(rebuild(d3, blobs), "n_entries too many")
    d3 = json.loads(json.dumps(doc))
    d3["histories"][0]["metas"] = [None] * len(d3["histories"][0]["metas"])
    r = same(rebuild(d3, blobs), "metas missing")
    assert r[0] == "ok"
    d3 = json.loads(json.dumps(doc))
    for hd in d3["histories"]:
        hd.pop("crcs", None)
    same(rebuild(d3, blobs), "no crcs")
    d3 = json.loads(json.dumps(doc)); d3["format"] = 5
    r = same(rebuild(d3, blobs), "newer format")
    assert r[0] == "err" and r[1] == "NewerFormatError", r
    print(f"PASS test_registry_truncated_and_flipped_everywhere ({n} damaged registries read the same, errors and load_report included)")


def test_registry_old_files():
    """Formats 1 and 2, as the betas wrote them (real files): equal results, and cut or flipped
    inside the header and at every step boundary."""
    n = 0
    for name in ("beta1_format1.hsr", "beta2wip_format2.hsr"):
        data = open(os.path.join(HERE, "fixtures", name), "rb").read()
        r = same(data, name)
        assert r[0] == "ok", r
        assert r[1]["report"]["format"] in (1, 2) and r[1]["report"]["migrated"]
        (jl,) = struct.unpack("<I", data[4:8])
        cuts = set(range(0, 8 + jl + 40)) | set(range(8 + jl, len(data), 97)) | {len(data) - 1, len(data) - 8, len(data) - 9}
        # every step boundary
        pos = 8 + jl
        while pos + 8 <= len(data):
            (ln,) = struct.unpack("<Q", data[pos:pos + 8])
            cuts |= {pos - 1, pos, pos + 7, pos + 8, pos + 8 + ln - 1, pos + 8 + ln}
            pos += 8 + ln
            if ln > len(data):
                break
        for cut in sorted(c for c in cuts if 0 <= c <= len(data)):
            same(data[:cut], (name, cut))
            n += 1
        for i in range(0, len(data), 53):
            bad = bytearray(data); bad[i] ^= 0x20
            same(bytes(bad), (name, "flip", i))
            n += 1
    print(f"PASS test_registry_old_files (formats 1 and 2: {n} cut or flipped files read the same)")


def test_loaded_entries_keep_their_checksum():
    """Entry(blob, meta, crc=...) from the loader carries the CRC of its bytes (engine crc32), and
    the stored CRC list of what is written next time is the same."""
    blobs = corpus_blobs()
    reg = persist.registry_from_bytes(persist.registry_to_bytes(build_registry(blobs)))
    for h in reg.histories.values():
        for e in h.entries:
            b, crc = e.blob_crc()
            assert crc == zlib.crc32(b) and e.blob == b
    again = persist.registry_to_bytes(reg)
    _jl, doc, spans = layout(again)
    flat = [c for hd in doc["histories"] for c in hd["crcs"]]
    assert flat == [zlib.crc32(again[bp:bp + ln]) for _lp, bp, ln in spans]
    print("PASS test_loaded_entries_keep_their_checksum")


def test_loaded_metas_share_their_strings():
    """label, mode, target and kind values that are equal are one object per load (json makes one per
    step); `extra` dicts are never shared, and nothing else about the metas changes."""
    blobs = corpus_blobs()
    reg = build_registry(blobs)
    for h in reg.histories.values():
        for i, e in enumerate(h.entries):
            e.meta = dict(e.meta, label="Sculpt " + "x" * 3, mode="SCULPT", target="Cube", kind="DELTA", extra={"n": i})
    data = persist.registry_to_bytes(reg)
    live, ref = persist.registry_from_bytes(data), rpersist.registry_from_bytes(data)
    assert canon(live) == canon(ref)
    metas = [e.meta for h in live.histories.values() for e in h.entries]
    assert len(metas) == 10
    for k in ("label", "mode", "target", "kind"):
        assert len({id(m[k]) for m in metas}) == 1, k
    assert len({id(m["extra"]) for m in metas}) == len(metas)
    metas[0]["extra"]["n"] = 99
    assert metas[1]["extra"]["n"] != 99
    print("PASS test_loaded_metas_share_their_strings")


def main():
    tests = [test_crc32_equals_zlib, test_crc32_with_other_threads, test_journal_equals_the_reference,
             test_journal_truncated_and_flipped_everywhere, test_registry_written_and_read_like_the_reference,
             test_registry_truncated_and_flipped_everywhere, test_registry_old_files, test_loaded_entries_keep_their_checksum,
             test_loaded_metas_share_their_strings]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    import shutil
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
