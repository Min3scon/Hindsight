"""Does a trained zstd dictionary or chunk dedup help format 3 steps? Measured on real strokes.

    <blender python> bench/bench_dict.py

The dictionary is trained on the step bodies of some corpora and tested on the others, so the
numbers show what a dictionary shipped with the add-on would do on meshes it has never seen.
Dedup counts 64-byte chunks of each body that already appeared earlier in the same history, and
grouped frames compress 8, 16 or 32 consecutive steps together (cross-step context).
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import statistics

import zstandard as zstd

from bench_codec import BUCKETS, load_corpus, frac
from bench_format import make_step
from hindsight.core import delta
from hindsight.core.types import ADD

TRAIN = ("100k", "500k")
TEST = ("100k_remesh", "100k_subsurf", "1m")


def bodies(tag):
    out = []
    for s in load_corpus(tag):
        st = make_step(s, ADD)
        out.append((frac(s), b"".join(delta.pack_delta(d) for d in st.deltas)))
    return out


def main():
    train = [b for t in TRAIN for _, b in bodies(t)]
    # samples: split big bodies so the trainer sees many small pieces
    samples = []
    for b in train:
        for i in range(0, len(b), 16384):
            samples.append(b[i:i + 16384])
    plain = zstd.ZstdCompressor(level=3)
    for size in (16384, 65536, 112640):
        d = zstd.train_dictionary(size, samples)
        cd = zstd.ZstdCompressor(level=3, dict_data=d)
        print(f"\ndictionary {size // 1024} KB")
        for tag in TEST:
            rows = bodies(tag)
            for name, lo, hi in BUCKETS:
                sel = [b for f, b in rows if lo <= f < hi]
                if not sel:
                    continue
                a = statistics.mean(len(plain.compress(b)) for b in sel)
                c = statistics.mean(len(cd.compress(b)) for b in sel)
                print(f"  {tag:13s} {name:4s} n={len(sel):2d} plain {a:9.0f} B  dict {c:9.0f} B  ({100 * (c / a - 1):+.1f}%)")
    print("\nchunk dedup (64-byte chunks already seen earlier in the same history) and grouped frames")
    for tag in TRAIN + TEST:
        seen, dup, tot = set(), 0, 0
        rows = bodies(tag)
        for _, b in rows:
            for i in range(0, len(b) - 63, 64):
                c = b[i:i + 64]
                if c.count(c[:1]) == 64:
                    continue                  # one repeated byte: compression removes these anyway
                tot += 1
                if c in seen:
                    dup += 1
                else:
                    seen.add(c)
        alone = sum(len(plain.compress(b)) for _, b in rows)
        grouped = {}
        for g in (8, 16, 32):
            grouped[g] = sum(len(plain.compress(b"".join(b for _, b in rows[i:i + g]))) for i in range(0, len(rows), g))
        print(f"  {tag:13s} {dup}/{tot} non-trivial chunks repeated ({100 * dup / max(1, tot):.2f}%); "
              f"compressed in groups of 8/16/32 steps: " +
              ", ".join(f"{100 * (grouped[g] / alone - 1):+.1f}%" for g in (8, 16, 32)))


if __name__ == "__main__":
    main()
