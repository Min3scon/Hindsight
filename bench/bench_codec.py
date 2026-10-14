"""Compression lab: measure lossless step encodings on real recorded strokes.

Run with Blender's own Python (it has numpy and zstandard), for example
    <blender python> bench/bench_codec.py

Input: .tmp/corpus/<tag>.npz + .json, written by tests_ui/scenarios/s_corpus.py (real mouse strokes
in real Blender on the generated test models). Every candidate encoding is decoded again and checked
bit for bit before its size counts.

Output: a table per corpus and stroke-size bucket, and bench/results_codec.json.
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import json
import lzma
import struct
import time
import zlib

import numpy as np

from hindsight.core import step as stepmod
from hindsight.core.types import DELTA, ChannelDelta, Step, bits_dtype, key_dtype, key_width

try:
    import zstandard as zstd
except Exception:  # pragma: no cover
    zstd = None

ROOT = os.path.join(os.path.dirname(__file__), "..")
CORPUS = os.path.join(ROOT, ".tmp", "corpus")
BUCKETS = (("2%", 0.01, 0.035), ("10%", 0.05, 0.16), ("30%", 0.16, 0.45), ("all", 0.0, 2.0))


# ------------------------------------------------------------------------------------- corpus
class Rec:
    """One channel change: indices, old and new bit patterns (k, w)."""
    __slots__ = ("key", "idx", "old", "new", "exists", "w")

    def __init__(self, key, idx, old, new, exists):
        self.key, self.idx, self.old, self.new, self.exists = key, idx, old, new, exists
        self.w = old.shape[1]


def load_corpus(tag):
    meta = json.load(open(os.path.join(CORPUS, tag + ".json")))
    z = np.load(os.path.join(CORPUS, tag + ".npz"))
    counts = meta["counts"]
    state = {}
    for name in z.files:
        if name.startswith("base|"):
            key = name[5:]
            a = z[name]
            state[key] = a.view(bits_dtype(a.dtype)).reshape(-1, key_width(key)).copy()
    steps = []
    for i, sm in enumerate(meta["steps"]):
        recs = []
        for j, dm in enumerate(sm["deltas"]):
            key = dm["key"]
            idx = z[f"s{i}_{j}_idx"].astype(np.int64)
            xor = z[f"s{i}_{j}_xor"]
            w = key_width(key)
            bd = bits_dtype(key_dtype(key))
            xor = xor.view(bd).reshape(-1, w)
            if key not in state:
                from hindsight.core.types import split_key
                n = counts[split_key(key)[1]]
                state[key] = np.zeros((n, w), dtype=bd)
            arr = state[key]
            old = arr[idx].copy()
            new = (old + xor) if dm.get("op", 0) == 1 else (old ^ xor)
            arr[idx] = new
            recs.append(Rec(key, idx, old, new, tuple(dm["exists"])))
        steps.append({"label": sm.get("extra", ""), "recs": recs, "n": counts["POINT"]})
    return steps


def frac(step):
    pos = [r for r in step["recs"] if r.key.startswith("position|")]
    r = pos[0] if pos else step["recs"][0]
    return r.idx.size / step["n"]


# ------------------------------------------------------------------------------ helpers
def zigzag(v, bits):
    s = v.view(np.dtype(f"i{bits // 8}"))
    return ((s << 1) ^ (s >> (bits - 1))).view(v.dtype)


def unzigzag(z, bits):
    u = z.view(z.dtype)
    return ((u >> 1) ^ (-(u & 1)).astype(u.dtype)).view(z.dtype)


def varint(a):
    """LEB128 of a non-negative int64 array, vectorised."""
    a = np.asarray(a, dtype=np.uint64)
    if a.size == 0:
        return b""
    nb = np.ones(a.size, dtype=np.int64)
    t = a >> np.uint64(7)
    while t.any():
        nb += (t > 0)
        t >>= np.uint64(7)
    out = np.zeros(int(nb.sum()), dtype=np.uint8)
    pos = np.concatenate(([0], np.cumsum(nb)[:-1]))
    v = a.copy()
    for b in range(int(nb.max())):
        live = nb > b
        p = pos[live] + b
        cont = (nb[live] > b + 1).astype(np.uint8) << 7
        out[p] = (v[live] & np.uint64(0x7F)).astype(np.uint8) | cont
        v[live] >>= np.uint64(7)
    return out.tobytes()


def planes(v):
    """(k, w) uint array -> byte planes, component-major (the current layout)."""
    k, w = v.shape
    isz = v.dtype.itemsize
    return np.ascontiguousarray(v).view(np.uint8).reshape(k, w * isz).T.tobytes()


def bitplanes(v):
    k, w = v.shape
    isz = v.dtype.itemsize
    out = []
    for c in range(w):
        col = np.ascontiguousarray(v[:, c])
        bits = np.unpackbits(col.view(np.uint8).reshape(k, isz), axis=1, bitorder="little")  # (k, 8*isz)
        out.append(np.packbits(bits.T, axis=1, bitorder="little").tobytes())
    return b"".join(out)


# ------------------------------------------------------------------------- index coders
def idx_gapfixed(idx):
    if idx.size == 0:
        return b""
    g = np.diff(idx)
    m = int(g.max()) if g.size else 0
    dt = np.uint8 if m < 256 else np.uint16 if m < 65536 else np.uint32
    return struct.pack("<I", int(idx[0])) + g.astype(dt).tobytes()


def idx_gapfixed_planes(idx):
    if idx.size == 0:
        return b""
    g = np.diff(idx)
    m = int(g.max()) if g.size else 0
    dt = np.uint8 if m < 256 else np.uint16 if m < 65536 else np.uint32
    return struct.pack("<I", int(idx[0])) + planes(g.astype(dt).reshape(-1, 1))


def idx_varint(idx):
    if idx.size == 0:
        return b""
    return varint(np.concatenate(([idx[0]], np.diff(idx) - 1)))


def runs_of(idx):
    if idx.size == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    br = np.flatnonzero(np.diff(idx) != 1) + 1
    starts = np.concatenate(([0], br))
    ends = np.concatenate((br, [idx.size]))
    return idx[starts], ends - starts


def idx_runs(idx):
    s, l = runs_of(idx)
    if s.size == 0:
        return b""
    prev_end = np.concatenate(([0], s[:-1] + l[:-1]))
    gaps = s - prev_end
    return varint(np.stack([gaps, l - 1], 1).reshape(-1))


def idx_runs_split(idx):
    s, l = runs_of(idx)
    if s.size == 0:
        return b""
    prev_end = np.concatenate(([0], s[:-1] + l[:-1]))
    return varint(s - prev_end) + varint(l - 1)


def idx_bitmap(idx):
    if idx.size == 0:
        return b""
    lo, hi = int(idx[0]), int(idx[-1])
    m = np.zeros(hi - lo + 1, dtype=np.uint8)
    m[idx - lo] = 1
    return struct.pack("<II", lo, hi) + np.packbits(m, bitorder="little").tobytes()


IDX_CODERS = {"gapfixed": idx_gapfixed, "gapfixed_planes": idx_gapfixed_planes, "varint": idx_varint,
              "runs": idx_runs, "runs_split": idx_runs_split, "bitmap": idx_bitmap}


def idx_best(idx):
    cands = [IDX_CODERS[n](idx) for n in ("gapfixed", "varint", "runs_split", "bitmap")]
    return min(cands, key=len)


# ------------------------------------------------------------------------- value coders
def values(rec, mode, pred, layout):
    old, new = rec.old, rec.new
    bits = old.dtype.itemsize * 8
    if mode == "xor":
        v = old ^ new
    else:
        v = (new - old).astype(old.dtype)            # modular, exact for any bit pattern
    if pred == "prev" and v.shape[0] > 1:
        p = v.copy()
        p[1:] = (v[1:] - v[:-1]) if mode != "xor" else (v[1:] ^ v[:-1])
        v = p
    elif pred == "prev_run" and v.shape[0] > 1:
        same = np.concatenate(([False], np.diff(rec.idx) == 1))
        p = v.copy()
        d = (v[1:] - v[:-1]) if mode != "xor" else (v[1:] ^ v[:-1])
        p[1:][same[1:]] = d[same[1:]]
        v = p
    elif pred == "o3" and v.shape[0] > 3:
        p = v.copy()
        p[1:] = v[1:] - v[:-1]
        p[2:] = v[2:] - 2 * v[1:-1] + v[:-2]
        p[3:] = v[3:] - 3 * v[2:-1] + 3 * v[1:-2] - v[:-3]
        v = p.astype(old.dtype)
    elif pred == "lin" and v.shape[0] > 2:
        p = v.copy()
        p[1:] = v[1:] - v[:-1]
        p[2:] = v[2:] - 2 * v[1:-1] + v[:-2]
        v = p.astype(old.dtype)
    if mode == "diff_zz" or (mode == "diff" and pred != "none") or (mode == "diff" and pred == "none" and ZZ_ALWAYS):
        v = zigzag(np.ascontiguousarray(v), bits)
    if layout == "planes":
        return planes(v)
    if layout == "bitplanes":
        return bitplanes(v)
    return np.ascontiguousarray(v).tobytes()


# ------------------------------------------------------------------------------ codecs
def make_codec(name, level=None, dict_data=None):
    if name == "zstd":
        params = {"level": level or 3}
        if dict_data is not None:
            params["dict_data"] = dict_data
        c = zstd.ZstdCompressor(**params)
        dparams = {"dict_data": dict_data} if dict_data is not None else {}
        d = zstd.ZstdDecompressor(**dparams)
        return (lambda b: c.compress(b)), (lambda b: d.decompress(b))
    if name == "zlib":
        return (lambda b: zlib.compress(b, level or 6)), zlib.decompress
    if name == "lzma":
        filt = [{"id": lzma.FILTER_LZMA2, "preset": level or 6}]
        return (lambda b: lzma.compress(b, format=lzma.FORMAT_RAW, filters=filt)), \
               (lambda b: lzma.decompress(b, format=lzma.FORMAT_RAW, filters=filt))
    if name == "none":
        return (lambda b: bytes(b)), (lambda b: bytes(b))
    raise ValueError(name)


# --------------------------------------------------------------------------- baseline
def baseline_blob(step):
    deltas = [ChannelDelta(r.key, r.idx.astype(np.uint32), r.old ^ r.new, r.exists) for r in step["recs"]]
    s = Step(seq=1234, label="Stroke", mode="SCULPT", target="0123456789abcdef", time=1.76e9, kind=DELTA,
             deltas=deltas)
    return stepmod.step_to_bytes(s, "zstd")


def baseline_header_len(blob):
    (hl,) = struct.unpack_from("<I", blob, 4)
    return 8 + hl + 1


# --------------------------------------------------------------------------- variants
def body(step, mode="xor", pred="none", layout="planes", idx="gapfixed"):
    parts = []
    for r in step["recs"]:
        ib = idx_best(r.idx) if idx == "best" else IDX_CODERS[idx](r.idx)
        vb = values(r, mode, pred, layout)
        parts.append(struct.pack("<II", len(ib), len(vb)) + ib + vb)
    return b"".join(parts)


def body_adaptive(step, idx="best"):
    """Per channel: try the transforms and keep the smallest after compression (1-byte tag)."""
    comp, _ = CODEC_CACHE["zstd3"]
    parts = []
    for r in step["recs"]:
        ib = idx_best(r.idx) if idx == "best" else IDX_CODERS[idx](r.idx)
        best = None
        for t, (m, p) in enumerate((("xor", "none"), ("diff", "none"), ("diff", "prev"), ("diff", "prev_run"))):
            vb = values(r, m, p, "planes")
            n = len(comp(vb))
            if best is None or n < best[0]:
                best = (n, t, vb)
        parts.append(struct.pack("<BII", best[1], len(ib), len(best[2])) + ib + best[2])
    return b"".join(parts)


CODEC_CACHE = {}
ZZ_ALWAYS = False
PER_COMP_CHOICES = (("xor", "none"), ("diff", "none"), ("diff", "prev"), ("diff", "lin"), ("diff", "o3"))


class _One:
    """A single-component view of a Rec (for per-component predictor choice)."""
    __slots__ = ("key", "idx", "old", "new", "exists", "w")

    def __init__(self, r, c):
        self.key, self.idx, self.exists, self.w = r.key, r.idx, r.exists, 1
        self.old = np.ascontiguousarray(r.old[:, c:c + 1])
        self.new = np.ascontiguousarray(r.new[:, c:c + 1])


def body_percomp(step, idx="best", codec_name="zstd3"):
    """Per channel component: try each predictor, keep the smallest after compression (1-byte tag each)."""
    comp, _ = CODEC_CACHE.get(codec_name) or codec(codec_name)
    parts = []
    for r in step["recs"]:
        ib = idx_best(r.idx) if idx == "best" else IDX_CODERS[idx](r.idx)
        comps = []
        for c in range(r.w):
            one = _One(r, c)
            best = None
            for t, (m, p) in enumerate(PER_COMP_CHOICES):
                vb = values(one, m, p, "planes")
                n = len(comp(vb))
                if best is None or n < best[0]:
                    best = (n, t, vb)
            comps.append(bytes((best[1],)) + best[2])
        vb = b"".join(comps)
        parts.append(struct.pack("<II", len(ib), len(vb)) + ib + vb)
    return b"".join(parts)


def codec(name):
    if name not in CODEC_CACHE:
        base, _, lvl = name.partition(":")
        if base.startswith("zstd") and base != "zstd":
            lvl = base[4:]
            base = "zstd"
        CODEC_CACHE[name] = make_codec(base, int(lvl) if lvl else None)
    return CODEC_CACHE[name]


def measure(steps, make_body, codec_name="zstd3", header=None, group=1):
    comp, decomp = codec(codec_name)
    sizes, enc_t, dec_t = [], 0.0, 0.0
    bodies = [make_body(s) for s in steps]
    if group == 1:
        for s, b in zip(steps, bodies):
            t = time.perf_counter()
            c = comp(b)
            enc_t += time.perf_counter() - t
            t = time.perf_counter()
            assert decomp(c) == b
            dec_t += time.perf_counter() - t
            sizes.append(len(c) + (header if header is not None else s["hdr"]))
    else:
        for g in range(0, len(bodies), group):
            chunk = bodies[g:g + group]
            c = comp(b"".join(chunk))
            per = len(c) / len(chunk)
            for s in steps[g:g + group]:
                sizes.append(per + (header if header is not None else s["hdr"]))
    return sizes, enc_t, dec_t


def summarize(steps, sizes):
    out = {}
    for name, lo, hi in BUCKETS:
        sel = [sz for s, sz in zip(steps, sizes) if lo <= s["frac"] < hi]
        out[name] = (float(np.mean(sel)) if sel else None, len(sel))
    return out


def main():
    tags = [t[:-4] for t in sorted(os.listdir(CORPUS)) if t.endswith(".npz")]
    if len(sys.argv) > 1:
        tags = [t for t in tags if t in sys.argv[1:]]
    results = {}
    for tag in tags:
        steps = load_corpus(tag)
        for s in steps:
            s["frac"] = frac(s)
            blob = baseline_blob(s)
            s["base"] = len(blob)
            s["hdr"] = baseline_header_len(blob)
        print(f"\n=== {tag}: {len(steps)} steps, {steps[0]['n']} verts")
        rows = []

        def row(name, sizes):
            sm = summarize(steps, sizes)
            rows.append((name, sm))
            print(f"{name:52s} " + "  ".join(
                f"{b}: {v[0]:9.0f} (n={v[1]})" if v[0] is not None else f"{b}: -" for b, v in sm.items()))

        row("baseline (current format 2, zstd 3)", [s["base"] for s in steps])
        sz, _, _ = measure(steps, lambda s: body(s))
        row("same body, my framing", sz)
        for mode, pred in (("xor", "none"), ("diff", "none"), ("diff_zz", "none"), ("diff", "prev"),
                           ("diff", "prev_run"), ("diff", "lin"), ("xor", "prev")):
            sz, _, _ = measure(steps, lambda s: body(s, mode, pred, "planes", "gapfixed"))
            row(f"values {mode}/{pred} planes, idx gapfixed", sz)
        for layout in ("raw", "bitplanes"):
            sz, _, _ = measure(steps, lambda s: body(s, "diff", "prev", layout, "gapfixed"))
            row(f"values diff/prev {layout}", sz)
        for ic in IDX_CODERS:
            sz, _, _ = measure(steps, lambda s: body(s, "diff", "prev", "planes", ic))
            row(f"diff/prev planes, idx {ic}", sz)
        sz, _, _ = measure(steps, lambda s: body(s, "diff", "prev", "planes", "best"))
        row("diff/prev planes, idx best", sz)
        sz, _, _ = measure(steps, lambda s: body_adaptive(s))
        row("adaptive values, idx best", sz)
        sz, _, _ = measure(steps, lambda s: body(s, "diff", "o3", "planes", "gapfixed"))
        row("values diff/o3 planes, idx gapfixed", sz)
        sz, et, dt = measure(steps, lambda s: body_percomp(s))
        row("per-component predictor, idx best", sz)
        sz, et, dt = measure(steps, lambda s: body_percomp(s), header=40)
        row("per-component predictor, idx best, 40-byte header", sz)
        sz, et, dt = measure(steps, lambda s: body_percomp(s), "zstd19", header=40)
        row("per-comp, best idx, 40 B header, zstd19", sz)
        for cn in ("zstd1", "zstd6", "zstd9", "zstd12", "zstd19", "zlib:9", "lzma:6"):
            sz, et, dt = measure(steps, lambda s: body_adaptive(s), cn)
            row(f"adaptive, codec {cn} (enc {et * 1000 / len(steps):.2f} ms, dec {dt * 1000 / len(steps):.2f} ms)", sz)
        for g in (8, 16, 32):
            sz, _, _ = measure(steps, lambda s: body_adaptive(s), "zstd3", group=g)
            row(f"adaptive, zstd3, groups of {g}", sz)
        results[tag] = rows
    with open(os.path.join(os.path.dirname(__file__), "results_codec.json"), "w") as f:
        json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
