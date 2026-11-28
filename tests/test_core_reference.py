"""The Rust engine's step container and codecs against the Python reference (tests/reference), bit
for bit: bytes written, steps read, errors, hostile input. And: nothing under hindsight/ may import
the reference.

The reference is the pre-Rust step.py and codec.py (python zstandard and lz4 packages, zlib), run
with the engine's delta paths off (reference.pure()) so it is Python all the way down.

Corpus: tests/fixtures/step_corpus_base.zip, 46 steps written by the baseline checkout (commit
66fcdf8) with tests/reference/make_step_corpus.py. The same script run with the engine and with the
reference writes identical bytes (checked below), so one corpus stands for all three.
"""
import contextlib
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R
import ast
import importlib
import json
import struct
import zipfile
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from reference import codec as rcodec, pure, step as rstep, types as rtypes  # noqa: E402
from tests import equiv                                                     # noqa: E402
import test_core_native_steps as ns                                         # noqa: E402
from hindsight.core import codec, delta, native, step as stepmod, types     # noqa: E402
from hindsight.core.types import DELTA, Entry, Step                         # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "step_corpus_base.zip")
NAMES = ("zstd", "lz4", "zlib", "none")
RNG = np.random.default_rng(7)


def ref_write(st, *a):
    with pure():
        return rstep.step_to_bytes(equiv.like(st, rtypes), *a)       # the reference gets its own objects


def ref_read(b):
    with pure():
        return equiv.like(rstep.step_from_bytes(b), types)           # the engine's, for same_step


def ref_recompress(b):
    with pure():
        return rstep.recompress(b)


def captured_steps(seed, n_steps=60, n=200, mix=True):
    """Steps of every kind (the generator of test_core_native_steps), raw."""
    got = []
    real = ns.pack
    ns.pack = lambda rng, st, mix_: (got.append(st), Entry(st))[1]
    try:
        ns.build(seed, n_steps=n_steps, n=n, targets=("A", "B"), mix=mix)
    finally:
        ns.pack = real
    return got


# ------------------------------------------------------------------------------ guard
def test_nothing_in_hindsight_imports_the_reference():
    bad = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "hindsight")):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(base, f)
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "")] + [a.name for a in node.names]
                if any(n == "reference" or n.startswith(("reference.", "tests.reference", "tests")) or n == "tests"
                       for n in names):
                    bad.append((os.path.relpath(path, ROOT), names))
            text = open(path, encoding="utf-8").read()
            if "tests/reference" in text.replace("\\", "/") and "import" in text and "tests.reference" in text:
                bad.append((os.path.relpath(path, ROOT), "mentions tests.reference"))
    assert not bad, bad
    for name in ("step", "codec"):
        importlib.import_module(f"hindsight.core.{name}")
    loaded = [m for m in sys.modules if m.startswith("hindsight.") and getattr(sys.modules[m], "__file__", "")
              and "reference" in os.path.relpath(sys.modules[m].__file__, ROOT).split(os.sep)]
    assert not loaded, loaded
    assert stepmod.step_to_bytes.__module__ == "hindsight.core.step"
    assert stepmod._N is native.ENGINE and codec._N is native.ENGINE


# hindsight/core modules that are facades of the engine, their Python in tests/reference (dev/RUST_PLAN.md)
FACADES = ("codec", "crc", "delta", "history", "journal", "step", "store", "topopack", "types")


def test_no_reference_imports_a_facade():
    """Rule 1 (dev/RUST_PLAN.md): a reference imports the reference copy of a moved module, never its facade, so no
    test checks the engine against itself (#144's review). Not counted: pure(), whose job is to patch delta's facade,
    and make_step_corpus.py, which writes the corpus with both sides."""
    facades = {"hindsight.core." + f for f in FACADES}
    bad = []
    folder = os.path.join(ROOT, "tests", "reference")
    for f in sorted(os.listdir(folder)):
        if not f.endswith(".py") or f in ("__init__.py", "make_step_corpus.py"):
            continue
        for node in ast.walk(ast.parse(open(os.path.join(folder, f), encoding="utf-8").read())):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            bad += [f"{f}:{node.lineno} {m}" for m in mods if m in facades]
    assert not bad, bad


def test_engine_runs_the_container():
    """The facade holds no codec or container logic of its own: its functions are thin and the work
    is the engine's (a crude check that nothing re-grew: no zstandard, lz4 or zlib import)."""
    for name in ("step", "codec"):
        tree = ast.parse(open(os.path.join(ROOT, "hindsight", "core", name + ".py"), encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
        assert not imported & {"zstandard", "lz4", "zlib"}, (name, imported)
    assert "zstandard" not in sys.modules or "hindsight" not in (getattr(sys.modules["zstandard"], "__file__", "") or "")


# ------------------------------------------------------------------------------ codec
def corpus_data():
    return [b"", b"a", bytes(range(256)) * 100, RNG.bytes(10000), bytes(100000), b"hello world " * 3000,
            RNG.integers(0, 3, 200000, dtype=np.uint8).tobytes(), RNG.bytes(70000) + bytes(70000)]


# libzstd the engine is built with (native/Cargo.lock: zstd-sys 2.1.3+zstd.1.5.7). zstd's output bytes
# are only the same with the same libzstd: where python-zstandard has another one (CI pins 0.23.0 with
# 1.5.6, Blender 4.2 bundles an older one), zstd is compared by what decompresses, not by bytes.
ENGINE_ZSTD = (1, 5, 7)
try:
    import zstandard as _zs
    PY_ZSTD = tuple(_zs.ZSTD_VERSION)
except Exception:
    PY_ZSTD = (0, 0, 0)


def bytes_comparable(name) -> bool:
    return name != "zstd" or PY_ZSTD == ENGINE_ZSTD


def same_recompressed(ra, rc, src_a, src_b) -> bool:
    """recompress of the reference (ra, from src_a) and of the engine (rc, from src_b): the same bytes
    where both have the same libzstd. With another libzstd (Blender 4.2 to 5.2 bundle older
    python-zstandard builds, 4.2: 0.16.0 with libzstd 1.5.0) the strong setting writes other bytes, and
    whether that is smaller than what is stored, the only thing that makes recompress return None,
    can come out either way: then the step inside must be the same (None: the step as it was)."""
    if PY_ZSTD == ENGINE_ZSTD:
        return ra == rc
    a, b = outcome(stepmod.step_from_bytes, src_a if ra is None else ra), \
        outcome(stepmod.step_from_bytes, src_b if rc is None else rc)
    return a[0] == b[0] and (a[0] != "ok" or stepmod.same_step(a[1], b[1]))


def test_codec_bytes_equal_the_libraries():
    differ = []
    for name in NAMES:
        for strong in (False, True):
            for d in corpus_data():
                a, b = rcodec.compress(d, name, strong), codec.compress(d, name, strong)
                if a != b and bytes_comparable(name):
                    differ.append((name, strong, len(d)))
                assert codec.decompress(a) == d and rcodec.decompress(b) == d
                assert codec.decompress(a, len(d)) == d and codec.decompress(b, len(d)) == d
                assert codec.codec_of(b) == rcodec.codec_of(a) and codec.is_strong(b) == rcodec.is_strong(a)
    assert not differ, f"bytes differ from the Python libraries: {differ}"
    for name in ("auto", "ZSTD", "bogus", "", None, "Lz4"):
        assert codec.resolve(name) == rcodec.resolve(name) or name in ("bogus", "", None), name
    assert codec.resolve("bogus") == "zlib" and codec.resolve("auto") == "zstd"
    note = "all bytes equal" if PY_ZSTD == ENGINE_ZSTD else \
        f"zstd compared by content: python-zstandard has libzstd {PY_ZSTD}, the engine {ENGINE_ZSTD}"
    print(f"  test_codec_bytes_equal_the_libraries ({note})")


def outcome(fn, *a):
    try:
        return ("ok", fn(*a))
    except Exception as e:                          # noqa: BLE001
        return ("err", type(e).__name__, str(e))


def test_codec_reads_like_the_reference():
    base = bytes(range(256)) * 50
    good = {n: rcodec.compress(base, n) for n in NAMES}
    cases = []
    for n, b in good.items():
        cases += [b, b + b"junk", b + b, b[:-3], b[:-1], b[:2], b[:1], b"", b[:1] + b"x" * 40, b"\x09" + b[1:],
                  b[:1] + b[1:][::-1]]
    nosize = rcodec._zstd.ZstdCompressor(level=3, write_content_size=False).compress(base)
    cases += [bytes((3,)) + nosize]
    for blob in cases:
        for exp in (None, len(base), len(base) + 1, 0, 5, -1, 1 << 40):
            ra, rb_ = outcome(rcodec.decompress, blob, exp), outcome(codec.decompress, blob, exp)
            if ra[0] == "ok":
                assert rb_ == ra, (blob[:8], exp, rb_[:2])
            else:
                assert rb_[0] == "err", (blob[:8], exp, ra, rb_)
                # the same class of error; a plain ValueError (empty) is the same in both
                if ra[1] in ("CorruptBlob", "ValueError"):
                    assert rb_[1] == ra[1], (blob[:8], exp, ra, rb_)
    # exact messages the engine promises
    for blob, exp, msg in ((good["zstd"], len(base) + 1, "zstd size doesn't match"),
                           (good["none"], len(base) + 1, "wrong length"),
                           (good["zlib"], 5, "zlib stream damaged"),
                           (good["zlib"], 1 << 31, "implausible size"),
                           (b"\x09abc", None, "unknown codec id 9")):
        try:
            codec.decompress(blob, exp)
        except codec.CorruptBlob as e:
            assert msg in str(e), (msg, str(e))
        else:
            raise AssertionError(msg)
    try:
        codec.decompress(b"")
    except ValueError as e:
        assert str(e) == "empty blob" and not isinstance(e, codec.CorruptBlob)
    else:
        raise AssertionError("empty blob accepted")
    print(f"  test_codec_reads_like_the_reference ({len(cases)} blobs x 7 sizes)")


def test_codec_hostile_sizes():
    """A blob that claims a huge body is refused before anything is allocated."""
    big = rcodec._zstd.ZstdCompressor(level=1).compress(bytes(1 << 20))
    blob = bytes((3,)) + big
    for claim in (1 << 40, 1 << 31, (1 << 30) + 1):
        try:
            codec.decompress(blob, claim)
        except codec.CorruptBlob:
            continue
        raise AssertionError(claim)
    # a frame header that says 3 GB with a tiny payload
    hdr = bytes.fromhex("28b52ffd") + bytes((0xA0,)) + struct.pack("<I", 3 << 30)[:4] + b"\x01\x00\x00"
    for exp in (None, 3 << 30):
        try:
            codec.decompress(bytes((3,)) + hdr, exp)
        except (codec.CorruptBlob, ValueError):
            pass
        else:
            raise AssertionError("accepted a 3 GB claim")


# ------------------------------------------------------------------------------ steps
def check_pair(st, name, effort, strong):
    rb = ref_write(st, name, effort, strong)
    eb = stepmod.step_to_bytes(st, name, effort, strong)
    assert rb == eb or not bytes_comparable(name), (st.seq, st.kind, name, effort, strong, len(rb), len(eb))
    e_step, r_step = stepmod.step_from_bytes(rb), ref_read(eb)
    assert stepmod.same_step(st, e_step) and stepmod.same_step(st, r_step) and stepmod.same_step(e_step, r_step)
    assert stepmod.same_step(e_step, stepmod.step_from_bytes(eb))
    assert rstep.read_header(rb) == stepmod.read_header(eb) or True       # compared below
    ha, hb = rstep.read_header(rb), stepmod.read_header(eb)
    assert ha == hb, (ha, hb)
    assert stepmod.version_of(eb) == rstep.version_of(rb)
    ra, rc = ref_recompress(rb), stepmod.recompress(eb)
    if bytes_comparable(name):
        assert ra == rc, (st.seq, name, effort, strong, None if ra is None else len(ra), None if rc is None else len(rc))
    else:                                   # another libzstd: the same step inside (see same_recompressed)
        assert same_recompressed(ra, rc, rb, eb), (st.seq, name, effort, strong)
        assert rc is None or stepmod.same_step(st, stepmod.step_from_bytes(rc))
    return len(eb)


def test_steps_equal_the_reference():
    n = 0
    total = 0
    for seed in (1, 2, 3, 4, 5, 6):
        for st in captured_steps(seed, n_steps=40, n=150 + 40 * seed):
            for name in NAMES:
                for effort in (delta.QUICK, delta.NORMAL):
                    for strong in (False, True):
                        total += check_pair(st, name, effort, strong)
                        n += 1
    print(f"  test_steps_equal_the_reference ({n} steps written, read and recompressed both ways, {total} bytes)")


def test_property():
    from hypothesis import HealthCheck, given, settings, strategies as hs

    @settings(max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))
    @given(hs.integers(0, 2 ** 31), hs.integers(1, 12), hs.integers(1, 90), hs.sampled_from(NAMES),
           hs.booleans(), hs.booleans())
    def prop(seed, n_steps, n, name, quick, strong):
        for st in captured_steps(seed, n_steps=n_steps, n=n):
            check_pair(st, name, delta.QUICK if quick else delta.NORMAL, strong)

    prop()


def test_corpus():
    z = zipfile.ZipFile(CORPUS)
    index = json.loads(z.read("index.json"))
    kinds = set()
    rewritten = strong_rows = 0
    for row in index:
        b = z.read(row["name"])
        e_step, r_step = stepmod.step_from_bytes(b), ref_read(b)
        assert stepmod.same_step(e_step, r_step), row["name"]
        assert stepmod.read_header(b) == rstep.read_header(b), row["name"]
        kinds.add(e_step.kind)
        assert same_recompressed(ref_recompress(b), stepmod.recompress(b), b, b), row["name"]
        if row.get("legacy"):
            assert stepmod.version_of(b) == 1
            continue
        # the engine writes back exactly the bytes the baseline wrote. Strong blobs were written at zstd
        # level 19: since R072 data up to 1 MB gets level 15, so they are written again at 19 (the engine's
        # measurement switch) for the byte check, and with the rule they must hold the same step, strong
        def write():
            again = stepmod.step_to_bytes(e_step, row["codec"], row["effort"], row["strong"])
            if row["recompressed"]:
                again = stepmod.recompress(again) or again
            return again
        if row["strong"] or row["recompressed"]:
            native.ENGINE._strong_level(19)
            try:
                old = write()
            finally:
                native.ENGINE._strong_level(0)
            assert old == b, (row["name"], len(old), len(b))
            now = write()
            assert stepmod.same_step(stepmod.step_from_bytes(now), e_step), row["name"]
            assert stepmod.read_header(now) == stepmod.read_header(b), row["name"]
            strong_rows += 1
        else:
            again = write()
            assert again == b, (row["name"], len(again), len(b))
        rewritten += 1
    assert kinds == {"DELTA", "TOPO", "JOIN", "DATA"}, kinds
    assert strong_rows > 0
    print(f"  test_corpus ({len(index)} baseline steps read by both, {rewritten} written back identically, "
          f"{strong_rows} of them strong: at level 19 and, with the rule, the same step)")


def test_legacy_files():
    """Real registries of beta 1 (format 1) and a beta 2 work in progress (format 2)."""
    from hindsight.core import persist
    fx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
    n = 0
    for name in ("beta1_format1.hsr", "beta2wip_format2.hsr"):
        data = open(os.path.join(fx, name), "rb").read()
        reg = persist.registry_from_bytes(data)
        for h in reg.histories.values():
            for e in h.entries:
                if e.blob is None:
                    continue
                a, b = stepmod.step_from_bytes(e.blob), ref_read(e.blob)
                assert stepmod.same_step(a, b)
                assert stepmod.recompress(e.blob) == ref_recompress(e.blob) or stepmod.version_of(e.blob) == 1
                n += 1
    assert n > 0
    print(f"  test_legacy_files ({n} steps of old files, engine equals reference)")


# ------------------------------------------------------------------------------ hostile
def mutate(rng, b, patch):
    b = bytearray(b)
    how = int(rng.integers(8))
    if how == 0 and len(b) > 1:
        b[int(rng.integers(len(b)))] ^= 1 << int(rng.integers(8))
    elif how == 1 and len(b) > 1:
        del b[int(rng.integers(len(b))):]
    elif how == 2:
        b += rng.bytes(int(rng.integers(1, 20)))
    elif how == 3 and len(b) > 12:
        i = int(rng.integers(8, min(len(b), 120)))
        b[i:i + int(rng.integers(1, 8))] = rng.bytes(int(rng.integers(1, 8)))
    elif how == 4 and len(b) > 14:
        i = int(rng.integers(10, min(len(b), 60)))
        b[i] = int(rng.choice([0, 1, 255, 127, 128]))
    elif how == 5 and len(b) > 30:
        i = int(rng.integers(len(b) - 10))
        del b[i:i + int(rng.integers(1, 10))]
    elif how == 6 and len(b) > 8:
        b[int(rng.integers(8, len(b)))] = int(rng.integers(256))
    else:
        b = bytearray(rng.bytes(int(rng.integers(0, 40))))
    b = bytes(b)
    if patch and b[:4] == b"HSS3" and len(b) >= 8:
        b = b[:4] + struct.pack("<I", zlib.crc32(b[8:])) + b[8:]
    return b


def _time_bits(b):
    return bytes(b[22:30]) if bytes(b[:4]) == b"HSS3" and len(b) >= 30 else None


def _subnormal_time(b):
    t = _time_bits(b)
    if t is None:
        return False
    x = struct.unpack("<d", t)[0]
    return x != 0.0 and abs(x) < 2.2250738585072014e-308


def judge(b, stats):
    ra = outcome(ref_read, b)
    ea = outcome(stepmod.step_from_bytes, b)
    if ea[0] == "err":
        assert ea[1] in ("CorruptStep", "ValueError", "UnknownEncoding"), (ea, b[:40])      # never a panic or a leaked type
        # #66: UnknownEncoding only for an intact step prefix that names a later encoding (byte 10 above 4)
        assert ea[1] != "UnknownEncoding" or (stepmod.is_unknown(b) and b[10] > 4), (ea, b[:40])
        assert "internal error" not in ea[2], ea
    if ra[0] == "ok":
        assert ea[0] == "ok" and stepmod.same_step(ra[1], ea[1]), (str(ea)[:200] if ea[0] == "err" else "different data", b[:60])
        stats["both ok"] += 1
    elif ea[0] == "ok" and PY_ZSTD != ENGINE_ZSTD and "decompression error" in str(ra[2:]):
        # Another libzstd in python-zstandard (Blender 4.2: 1.5.0): the checksum was made again over
        # the damage, and the older library finds a corrupted block in a frame the engine's libzstd
        # decodes. What it decoded is an ordinary step: written again, it reads back the same.
        again = stepmod.step_from_bytes(stepmod.step_to_bytes(ea[1], "zstd"))
        assert stepmod.same_step(again, ea[1]), b[:60]
        stats["older libzstd refused"] = stats.get("older libzstd refused", 0) + 1
    else:
        assert ea[0] == "err", ("the engine accepted what the reference refused", ra, b[:60])
        stats["both refuse"] += 1
        if ra[1] in ("CorruptStep",):
            assert ea[1] in ("CorruptStep", "UnknownEncoding"), (ra, ea)     # #66: the latter checked above
    r1, e1 = outcome(ref_recompress, b), outcome(stepmod.recompress, b)
    if r1[0] == "ok" and e1 != r1 and _subnormal_time(b):
        # The reference writes `float(time or 0.0)`: where the process flushes subnormals to zero
        # (Blender does, with DAZ) a subnormal time is falsy and goes out as 0.0. The engine keeps
        # the bits as they are on every platform. Everything else must still be equal.
        assert r1[1] is not None and e1[0] == "ok" and e1[1] is not None, (e1[:2], r1[:2])
        assert len(e1[1]) == len(r1[1]) and _time_bits(e1[1]) == _time_bits(b)       # (subnormal: never zero)
        stats["subnormal time kept"] = stats.get("subnormal time kept", 0) + 1
    elif r1[0] == "ok" and PY_ZSTD == ENGINE_ZSTD:
        assert e1 == r1, (b[:40], e1[:2], r1[:2])
    elif r1[0] == "ok":
        # another libzstd in python-zstandard: both succeed, with the same step inside
        assert e1[0] == "ok" and same_recompressed(r1[1], e1[1], b, b), (b[:40], e1[:2], r1[:2])
    elif e1[0] == "ok" and e1[1] is not None:
        # The engine does not parse the JSON of extras and data records (they stay Python's): a
        # step whose JSON is damaged is stored again as it was, and refused when read, as before.
        # What recompress writes must read back exactly like what it was given.
        back = outcome(stepmod.step_from_bytes, e1[1])
        assert back[0] == ea[0], (back[:2], ea[:2])
        if back[0] == "ok":
            assert stepmod.same_step(back[1], ea[1])
        stats["recompress kept damaged json"] = stats.get("recompress kept damaged json", 0) + 1
    else:
        assert e1[0] == "err" or e1 == ("ok", None), (str(e1)[:200], str(r1)[:200])


def test_hostile_blobs():
    stats = {"both ok": 0, "both refuse": 0}
    rng = np.random.default_rng(99)
    z = zipfile.ZipFile(CORPUS)
    good = [z.read(r["name"]) for r in json.loads(z.read("index.json"))]
    for st in captured_steps(11, n_steps=30, n=120):
        for name in NAMES:
            good.append(stepmod.step_to_bytes(st, name, delta.NORMAL))
        good.append(stepmod.step_to_bytes(st, "none", delta.QUICK))
    n = 0
    for b in good:
        # small blobs and the first part of large ones: damage lands in headers as often as in bodies
        for _ in range(12 if len(b) > 20000 else 25):
            judge(mutate(rng, b, patch=bool(rng.integers(2))), stats)
            n += 1
    print(f"  test_hostile_blobs ({n} damaged blobs: {stats})")


def test_subnormal_time_is_kept():
    """CI case: a header byte of the time changed to 0xff (checksum patched) makes a subnormal or
    tiny time. The engine's recompress keeps the time bits exactly, on every platform (the Python
    reference does too unless the process flushes subnormals, as Blender's does)."""
    st = [s for s in captured_steps(11, n_steps=30, n=120) if s.deltas and s.kind == DELTA][0]
    base = stepmod.step_to_bytes(st, "zstd", delta.QUICK)
    n = 0
    for off in range(22, 30):
        for v in (0xFF, 0x01, 0x80, 0x7F):
            m = bytearray(base)
            m[off] = v
            m = bytes(m[:4]) + struct.pack("<I", zlib.crc32(bytes(m[8:]))) + bytes(m[8:])
            out = stepmod.recompress(m)
            want = _time_bits(m)
            if struct.unpack("<d", want)[0] == 0.0:
                want = bytes(8)                  # a zero time of either sign is written as +0.0, as the reference does
            assert out is not None and _time_bits(out) == want, (off, v)
            ref = ref_recompress(m)
            if ref is not None and not _subnormal_time(m):
                assert ref == out
            n += 1
    print(f"  test_subnormal_time_is_kept ({n} mutations)")


def test_hostile_headers():
    """Valid checksum and codec 'none', so every field of the header and body is read for itself."""
    st = [s for s in captured_steps(5, n_steps=30, n=100) if s.deltas and s.kind == DELTA][0]
    blob = stepmod.step_to_bytes(st, "none", delta.NORMAL)
    stats = {"both ok": 0, "both refuse": 0}
    rng = np.random.default_rng(5)
    hl = struct.unpack_from("<H", blob, 8)[0]
    for pos in range(8, 10 + hl + 40):
        for v in (0, 1, 2, 3, 4, 5, 7, 8, 16, 31, 32, 127, 128, 254, 255, int(rng.integers(256))):
            b = bytearray(blob)
            b[pos] = v
            b = bytes(b[:4]) + struct.pack("<I", zlib.crc32(bytes(b[8:]))) + bytes(b[8:])
            judge(b, stats)
    print(f"  test_hostile_headers ({stats})")


def _peak_mb() -> float:
    """Peak resident memory of this process in MB: ru_maxrss (KB on Linux, bytes on macOS), or the peak
    working set on Windows (no resource module there)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return pmc.PeakWorkingSetSize / 2 ** 20
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 2 ** 20 if sys.platform == "darwin" else r / 1024


def test_no_unbounded_allocation():
    """Headers that claim sizes of gigabytes fail at once, before anything is allocated."""
    st = [s for s in captured_steps(5, n_steps=30, n=100) if s.deltas][0]
    blob = stepmod.step_to_bytes(st, "none", delta.NORMAL)
    hl = struct.unpack_from("<H", blob, 8)[0]
    before = _peak_mb()
    for off, size, val in ((10 + 20, 4, 0xFFFFFFFF), (10 + 20, 4, 1 << 30), (10 + 20, 4, (1 << 30) + 1)):
        b = bytearray(blob)
        b[off:off + size] = struct.pack("<I", val)
        b = bytes(b[:4]) + struct.pack("<I", zlib.crc32(bytes(b[8:]))) + bytes(b[8:])
        try:
            stepmod.step_from_bytes(b)
        except stepmod.CorruptStep:
            pass
        else:
            raise AssertionError("accepted a huge claimed length")
    # a record that claims 4 billion elements
    body = bytearray(blob[10 + hl + 1:])
    body[8:12] = struct.pack("<I", 0xFFFFFFFF)
    rest = bytearray(blob[:10 + hl + 1]) + body
    rest[4:8] = struct.pack("<I", zlib.crc32(bytes(rest[8:])))
    with contextlib.suppress(stepmod.CorruptStep):
        stepmod.step_from_bytes(bytes(rest))
    grew = _peak_mb() - before
    assert grew < 200, f"peak memory grew {grew:.0f} MB on claims of gigabytes"
    print(f"  test_no_unbounded_allocation (peak grew {grew:.0f} MB)")


# ------------------------------------------------------------------------------ api
def test_errors_and_exceptions():
    assert issubclass(stepmod.CorruptStep, ValueError) and issubclass(codec.CorruptBlob, ValueError)
    assert issubclass(delta.CorruptDelta, ValueError)
    E = native.ENGINE
    assert stepmod.CorruptStep is E.CorruptStep and codec.CorruptBlob is E.CorruptBlob
    assert delta.CorruptDelta is E.CorruptDelta and issubclass(E.NewerFormatError, ValueError)
    for bad in (b"", b"HSS3", b"HSS3" + bytes(20), b"XXXX" + bytes(40), b"HSS1" + bytes(10)):
        try:
            stepmod.step_from_bytes(bad)
        except stepmod.CorruptStep:
            pass
        else:
            raise AssertionError(bad)
    captured_steps(1, n_steps=5)[0]
    for kw in ({"kind": "NOPE"},):
        try:
            stepmod.step_to_bytes(Step(1, "l", "m", "t", 0.0, kw["kind"], []))
        except ValueError as e:
            assert "unknown step kind" in str(e)
        else:
            raise AssertionError
    try:
        stepmod.step_to_bytes(Step(1, "l", "m" * 300, "t", 0.0, DELTA, []))
    except ValueError as e:
        assert "step field too long" in str(e)
    else:
        raise AssertionError("long mode accepted")


def test_input_types():
    """bytes, bytearray, memoryview (also a slice of a bigger buffer) and numpy arrays all work."""
    st = captured_steps(2, n_steps=8)[0]
    b = stepmod.step_to_bytes(st, "zstd")
    big = b"\x00" * 7 + b + b"\x01" * 5
    for v in (b, bytearray(b), memoryview(b), memoryview(big)[7:7 + len(b)], np.frombuffer(b, np.uint8)):
        assert stepmod.same_step(st, stepmod.step_from_bytes(v))
        assert stepmod.read_header(v)["meta"]["seq"] == st.seq
        assert stepmod.version_of(v) == 3
    for v in (bytearray(b"hello" * 100), memoryview(b"hello" * 100), np.frombuffer(b"hello" * 100, np.uint8)):
        assert codec.decompress(codec.compress(v, "zstd")) == bytes(v)


def test_decoded_arrays_are_owned_and_writable():
    st = [s for s in captured_steps(3, n_steps=30) if s.deltas][0]
    s2 = stepmod.step_from_bytes(stepmod.step_to_bytes(st, "zstd"))
    for d in s2.deltas:
        assert d.idx.flags.writeable and d.vals.flags.writeable
        assert d.idx.dtype == np.uint32 and d.vals.dtype.kind == "u"
        d.vals[...] = d.vals            # no error, no read-only buffer
    snap_step = next(s for s in captured_steps(3, n_steps=80) if s.kind == "TOPO")
    s3 = stepmod.step_from_bytes(stepmod.step_to_bytes(snap_step, "zstd"))
    for a in s3.before.arrays.values():
        assert a.flags.writeable and a.flags.c_contiguous


def test_gil_released():
    """Compression and decoding run without the GIL: another thread keeps going."""
    import threading
    import time
    data = np.random.default_rng(1).bytes(1 << 24)
    ticks = [0]
    stop = [False]

    def spin():
        while not stop[0]:
            ticks[0] += 1
            time.sleep(0)

    t = threading.Thread(target=spin)
    t.start()
    try:
        t0 = time.perf_counter()
        blob = codec.compress(data, "zstd", True)           # level 19 on 16 MB: seconds
        elapsed = time.perf_counter() - t0
    finally:
        stop[0] = True
        t.join()
    assert codec.decompress(blob, len(data)) == data
    assert ticks[0] > elapsed * 100, f"the other thread ran {ticks[0]} times in {elapsed:.2f} s"
    print(f"  test_gil_released ({ticks[0]} ticks of another thread during a {elapsed:.2f} s compress)")


def test_record_equals_diff_and_encode():
    """record(): the whole stroke in one engine call gives the bytes of diff per channel, a Step and
    the Python reference writing it (every channel type, runs and scatters, channels that appear or
    go, a changed empty channel, sizes that differ, nothing changed)."""
    from hindsight.core.types import CHANNEL_TYPES, make_key
    keys = [make_key(f"c_{t.lower()}", "POINT", t) for t in CHANNEL_TYPES]
    rng = np.random.default_rng(21)
    n = 0
    for trial in range(60):
        nv = int(rng.integers(1, 400))
        chans, deltas = [], []
        for key in keys:
            if rng.random() < 0.3:
                continue
            w = ns.key_width(key)
            old = ns.values(rng, key, nv)
            how = ["run", "scatter", "nudge", "all", "same"][int(rng.integers(5))]
            new = old if how == "same" else ns.stroke(rng, key, old, how)
            e = (True, True)
            r = rng.random()
            if r < 0.1:
                old_a, new_a, e = None, new, (False, True)
                d = delta.diff(key, np.zeros_like(new), new, e)
            elif r < 0.2:
                old_a, new_a, e = old, None, (True, False)
                d = delta.diff(key, old, np.zeros_like(old), e)
            elif r < 0.25:
                old_a, new_a = old, np.concatenate([new, new[:w]])          # sizes differ: left out
                d = None
            else:
                old_a, new_a = old, new
                d = delta.diff(key, old, new, e)
            chans.append((key, old_a, new_a, e[0], e[1]))
            if d is not None:
                deltas.append(d)
        for name in NAMES:
            for quick in (False, True):
                for strong in (False, True):
                    eff = delta.QUICK if quick else delta.NORMAL
                    got = stepmod.record_step("DELTA", 5, 1.5, "Stroke", "SCULPT", "obj", chans,
                                              {"op": 1} if trial % 3 == 0 else None, name, eff, strong)
                    if not deltas:
                        assert got is None
                        continue
                    st = Step(5, "Stroke", "SCULPT", "obj", 1.5, DELTA, deltas,
                              extra={"op": 1} if trial % 3 == 0 else {})
                    rb = ref_write(st, name, eff, strong)
                    if bytes_comparable(name):
                        assert got == rb, (trial, name, quick, strong)
                    else:                   # another libzstd in python-zstandard: the same step inside
                        assert stepmod.same_step(stepmod.step_from_bytes(got), ref_read(rb)), (trial, name)
                        assert stepmod.same_step(st, ref_read(got)), (trial, name)
                    assert got == stepmod.step_to_bytes(st, name, eff, strong)
                    n += 1
    how = "bytes equal the reference" if bytes_comparable("zstd") else \
        f"zstd compared by content: libzstd {PY_ZSTD} in python-zstandard, {ENGINE_ZSTD} in the engine"
    print(f"  test_record_equals_diff_and_encode ({n} strokes, {how})")


def main():
    tests = [test_record_equals_diff_and_encode, test_nothing_in_hindsight_imports_the_reference,
             test_no_reference_imports_a_facade, test_engine_runs_the_container,
             test_codec_bytes_equal_the_libraries, test_codec_reads_like_the_reference, test_codec_hostile_sizes,
             test_steps_equal_the_reference, test_property, test_corpus, test_legacy_files, test_hostile_blobs,
             test_subnormal_time_is_kept, test_hostile_headers, test_no_unbounded_allocation, test_errors_and_exceptions, test_input_types,
             test_decoded_arrays_are_owned_and_writable, test_gil_released]
    for t in tests:
        R.run(t)
    R.finish()


if __name__ == "__main__":
    main()
