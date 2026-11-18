"""CPU the packer's idle recompression takes, with and without strokes coming in (no Blender).

Run: <python with numpy + zstandard> bench/packer_bench.py ROOT STEPFILE [SECONDS]
  ROOT      checkout whose `hindsight` package is measured (run old and new alternately)
  STEPFILE  steps cache written by bench/saveload_bench.py (u64 length + blob, repeated)
Phases of SECONDS each (default 20) on a history of those steps, as just loaded from a file:
  fresh      a stroke every 2 s right after opening the file (the raw tier isn't full yet, so
             nothing is submitted for packing; the packer is told when the build can be told)
  sculpting  the same with a raw step submitted for packing at each stroke (raw tier full)
  idle       nothing happens
  strong     the same steps already recompressed (a file saved after recompression), idle
Prints "RESULT {json}": per phase the packer thread's CPU seconds (from /proc, 10 ms ticks), steps
recompressed and how often the worker woke up to look at a step.
"""
import os, sys
ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, ROOT)
import json
import struct
import threading
import time

import numpy as np
from hindsight.core import step as stepmod, store
from hindsight.core.types import DELTA, POSITION_KEY, ChannelDelta, Entry, Step

assert store.__file__.startswith(ROOT), store.__file__
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
TICK = os.sysconf("SC_CLK_TCK")


def blobs(path):
    out = []
    with open(path, "rb") as fh:
        while True:
            h = fh.read(8)
            if not h:
                return out
            out.append(fh.read(struct.unpack("<Q", h)[0]))


def thread_cpu(tid):
    with open(f"/proc/self/task/{tid}/stat") as fh:
        f = fh.read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / TICK


def raw_entry(i):
    rng = np.random.default_rng(i)
    idx = np.sort(rng.choice(100_000, 2000, replace=False)).astype(np.uint32)
    return Entry(Step(i, "Draw", "SCULPT", "obj", float(i), DELTA,
                      [ChannelDelta(POSITION_KEY, idx, rng.normal(0, 1e-3, (2000, 3)).astype(np.float32))]))


def phase(p, entries, strokes, submit=True):
    calls = [0]
    orig = stepmod.recompress
    store.recompress = lambda b: (calls.__setitem__(0, calls[0] + 1), orig(b))[1]
    p.queue_recompress(entries)
    p.submit(raw_entry(10 ** 6), "auto")          # wakes a worker that only waits for new work
    tid = p._thread.native_id
    c0, r0 = thread_cpu(tid), p.recompressed
    end = time.monotonic() + SECONDS
    k = 0
    while time.monotonic() < end:
        if strokes:
            k += 1
            note = getattr(p, "note_activity", None)
            if note is not None:
                note()
            if submit:
                p.submit(raw_entry(k), "auto")
            time.sleep(2.0)
        else:
            time.sleep(0.1)
    out = {"cpu_s": round(thread_cpu(tid) - c0, 2), "recompressed": p.recompressed - r0, "looked_at": calls[0]}
    store.recompress = orig
    p._strong.clear()
    return out


def main():
    bs = blobs(sys.argv[2])
    entries = [Entry(blob=b, meta={}) for b in bs]
    strong = [Entry(blob=stepmod.recompress(b) or b, meta={}) for b in bs[:300]]
    p = store.Packer()
    try:
        res = {"steps": len(bs), "seconds": SECONDS,
               "fresh": phase(p, entries[:len(entries) // 3], True, False),
               "sculpting": phase(p, entries[len(entries) // 3:2 * len(entries) // 3], True),
               "idle": phase(p, entries[2 * len(entries) // 3:], False),
               "strong": phase(p, strong, False)}
    finally:
        p.stop()
    print("RESULT " + json.dumps(res))


main()
