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
Prints "RESULT {json}": per phase the packer thread's CPU seconds (the process's CPU less the main thread's: the
packer's is the only other thread), steps recompressed and how often the worker woke up to look at a step (the
engine's count, #155, or store.recompress's calls for the Python packer).
"""
import os, sys
ROOT = os.path.abspath(sys.argv[1])
sys.path.insert(0, ROOT)
import json
import struct
import time

import numpy as np
from hindsight.core import step as stepmod, store
from hindsight.core.types import DELTA, POSITION_KEY, ChannelDelta, Entry, Step

assert store.__file__.startswith(ROOT), store.__file__
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0


def blobs(path):
    out = []
    with open(path, "rb") as fh:
        while True:
            h = fh.read(8)
            if not h:
                return out
            out.append(fh.read(struct.unpack("<Q", h)[0]))


def thread_cpu():
    return time.process_time() - time.thread_time()


def raw_entry(i):
    rng = np.random.default_rng(i)
    idx = np.sort(rng.choice(100_000, 2000, replace=False)).astype(np.uint32)
    return Entry(Step(i, "Draw", "SCULPT", "obj", float(i), DELTA,
                      [ChannelDelta(POSITION_KEY, idx, rng.normal(0, 1e-3, (2000, 3)).astype(np.float32))]))


def phase(entries, strokes, submit=True):
    p = store.Packer()          # one per phase: what a phase leaves to recompress doesn't count in the next
    calls = [0]
    orig = getattr(store, "recompress", None)
    tried = lambda: p._tried if hasattr(p, "_tried") else calls[0]
    if orig is not None:
        store.recompress = lambda b: (calls.__setitem__(0, calls[0] + 1), orig(b))[1]
    p.queue_recompress(entries)
    p.submit(raw_entry(10 ** 6), "auto")          # wakes a worker that only waits for new work
    c0, r0, t0 = thread_cpu(), p.recompressed, tried()
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
        getattr(p, "take_in", lambda: None)()     # Blender's tick takes the engine packer's results in
    out = {"cpu_s": round(thread_cpu() - c0, 2), "recompressed": p.recompressed - r0, "looked_at": tried() - t0}
    if orig is not None:
        store.recompress = orig
    p.stop()
    return out


def main():
    bs = blobs(sys.argv[2])
    entries = [Entry(blob=b, meta={}) for b in bs]
    strong = [Entry(blob=stepmod.recompress(b) or b, meta={}) for b in bs[:300]]
    res = {"steps": len(bs), "seconds": SECONDS,
           "fresh": phase(entries[:len(entries) // 3], True, False),
           "sculpting": phase(entries[len(entries) // 3:2 * len(entries) // 3], True),
           "idle": phase(entries[2 * len(entries) // 3:], False),
           "strong": phase(strong, False)}
    print("RESULT " + json.dumps(res))


main()
