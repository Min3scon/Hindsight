#!/usr/bin/env python3
"""Compression benchmark for Hindsight per-stroke sculpt deltas.

Run:  $BENCH_VENV/bin/python research/compress_bench.py
Deps: numpy, lz4, zstandard (zlib is stdlib). Prints markdown tables to stdout.

Payloads (1,000,000-vertex UV-sphere-like mesh, index order is row-major so a
brush region is spatially coherent and its sorted indices form runs):
  a  raw sorted uint32 indices + uint32 XOR bit-deltas
  b  delta-encoded indices (np.diff) + uint32 XOR bit-deltas
  c  raw indices + byte-shuffled XOR deltas (arr.view(u1).reshape(-1,4).T)
  bc delta-encoded indices + byte-shuffled XOR deltas (combined, extra row)
  d  full float32 position array, 1,000,000 x 3 (topology snapshot)
  e  dense uint8 mask delta, length 1,000,000 (falloff 0..255 on touched verts)
"""
import platform
import time
import zlib

import lz4.frame
import numpy as np
import zstandard

N_VERTS = 1_000_000
N_TOUCHED = 5_000
GRID_ROWS = GRID_COLS = 1000
SEED = 1234
STROKE_DEG = 60.0   # arc length of the simulated brush stroke
PATH_SAMPLES = 128


def make_sphere(rng):
    """Jittered lat/long grid on the unit sphere, row-major index order."""
    lat = (np.arange(GRID_ROWS) + 0.5) / GRID_ROWS * np.pi - np.pi / 2
    lon = np.arange(GRID_COLS) / GRID_COLS * 2 * np.pi
    lat = lat[:, None] + rng.uniform(-0.3, 0.3, (GRID_ROWS, GRID_COLS)) * (np.pi / GRID_ROWS)
    lon = lon[None, :] + rng.uniform(-0.3, 0.3, (GRID_ROWS, GRID_COLS)) * (2 * np.pi / GRID_COLS)
    xyz = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1)
    return xyz.reshape(-1, 3).astype(np.float32)


def stroke(P, rng):
    """Indices of the N_TOUCHED vertices nearest to a great-circle brush path."""
    a = rng.standard_normal(3)
    a /= np.linalg.norm(a)
    b = rng.standard_normal(3)
    b -= a * np.dot(a, b)
    b /= np.linalg.norm(b)
    theta = np.linspace(0.0, np.deg2rad(STROKE_DEG), PATH_SAMPLES)
    path = np.cos(theta)[:, None] * a + np.sin(theta)[:, None] * b
    best = np.full(P.shape[0], np.inf, dtype=np.float32)
    for q in path.astype(np.float32):
        np.minimum(best, ((P - q) ** 2).sum(axis=1), out=best)
    dist = np.sqrt(best)
    idx = np.argpartition(dist, N_TOUCHED)[:N_TOUCHED]
    idx.sort()
    return idx.astype(np.uint32), dist[idx]


def build_payloads(rng):
    P = make_sphere(rng)
    idx, dist = stroke(P, rng)
    old = P[idx]                                    # (5000, 3) float32
    falloff = (1.0 - (dist / dist.max()) ** 2).astype(np.float32)
    offset = old * (1e-3 * falloff)[:, None]        # radial ~1e-3 on radius-1 sphere
    tangent = rng.normal(0.0, 1e-4, old.shape).astype(np.float32) * falloff[:, None]
    new = (old + offset + tangent).astype(np.float32)
    xor = (old.view(np.uint32) ^ new.view(np.uint32)).reshape(-1)   # uint32 bit deltas

    idx_raw = idx.astype("<u4").tobytes()
    idx_delta = np.concatenate([idx[:1], np.diff(idx)]).astype("<u4").tobytes()
    xor_raw = xor.astype("<u4").tobytes()
    xor_shuf = xor.astype("<u4").view(np.uint8).reshape(-1, 4).T.copy().tobytes()
    mask = np.zeros(N_VERTS, dtype=np.uint8)
    mask[idx] = np.round(255 * falloff).astype(np.uint8)

    return {
        "a raw idx + XOR": idx_raw + xor_raw,
        "b delta idx + XOR": idx_delta + xor_raw,
        "c raw idx + shuffled XOR": idx_raw + xor_shuf,
        "bc delta idx + shuffled XOR": idx_delta + xor_shuf,
        "d full f32 positions": P.astype("<f4").tobytes(),
        "e uint8 mask delta": mask.tobytes(),
    }


def zstd_codec(level):
    cctx = zstandard.ZstdCompressor(level=level)
    dctx = zstandard.ZstdDecompressor()
    return cctx.compress, dctx.decompress


CODECS = [
    ("zlib L1", lambda b: zlib.compress(b, 1), zlib.decompress),
    ("zlib L6", lambda b: zlib.compress(b, 6), zlib.decompress),
    ("lz4.frame default", lambda b: lz4.frame.compress(b), lz4.frame.decompress),
    ("lz4.frame L9 (HC)", lambda b: lz4.frame.compress(b, compression_level=9), lz4.frame.decompress),
    ("zstd L1", *zstd_codec(1)),
    ("zstd L3", *zstd_codec(3)),
    ("zstd L9", *zstd_codec(9)),
]


def best_time(fn, arg, min_time=0.1, repeats=3):
    """Seconds per call, best of several batches sized to run for at least min_time."""
    n = 1
    while True:
        t0 = time.perf_counter()
        for _ in range(n):
            fn(arg)
        dt = time.perf_counter() - t0
        if dt >= min_time or n >= 1 << 14:
            break
        n *= 2
    best = dt / n
    for _ in range(repeats - 1):
        t0 = time.perf_counter()
        for _ in range(n):
            fn(arg)
        best = min(best, (time.perf_counter() - t0) / n)
    return best


def main():
    import numpy
    print(f"env: python {platform.python_version()}, numpy {numpy.__version__}, "
          f"lz4 {lz4.__version__}, zstandard {zstandard.__version__}, "
          f"machine {platform.machine()}, seed {SEED}, "
          f"touched {N_TOUCHED}/{N_VERTS}")
    payloads = build_payloads(np.random.default_rng(SEED))
    results = {}
    for pname, raw in payloads.items():
        for cname, enc, dec in CODECS:
            blob = enc(raw)
            assert dec(blob) == raw, (pname, cname)
            t_enc = best_time(enc, raw)
            t_dec = best_time(dec, blob)
            results[(pname, cname)] = dict(
                raw=len(raw), out=len(blob), ratio=len(raw) / len(blob),
                enc_mbs=len(raw) / t_enc / 1e6, dec_mbs=len(raw) / t_dec / 1e6,
            )

    print("\n### Compression ratio (raw / compressed)\n")
    print("| codec | " + " | ".join(payloads) + " |")
    print("|---" * (len(payloads) + 1) + "|")
    for cname, _, _ in CODECS:
        cells = [f"{results[(p, cname)]['ratio']:.2f}" for p in payloads]
        print(f"| {cname} | " + " | ".join(cells) + " |")

    print("\n### Raw sizes and compressed sizes (bytes)\n")
    print("| payload | raw | " + " | ".join(c for c, _, _ in CODECS) + " |")
    print("|---" * (len(CODECS) + 2) + "|")
    for p in payloads:
        raw = results[(p, CODECS[0][0])]["raw"]
        cells = [f"{results[(p, c)]['out']}" for c, _, _ in CODECS]
        print(f"| {p} | {raw} | " + " | ".join(cells) + " |")

    print("\n### Throughput, MB/s (encode / decode, best of 3; 1 MB = 1e6 bytes)\n")
    print("| payload | " + " | ".join(c for c, _, _ in CODECS) + " |")
    print("|---" * (len(CODECS) + 1) + "|")
    for p in payloads:
        cells = [f"{results[(p, c)]['enc_mbs']:.0f} / {results[(p, c)]['dec_mbs']:.0f}"
                 for c, _, _ in CODECS]
        print(f"| {p} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
