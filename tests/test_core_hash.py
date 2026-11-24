import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tests.runner as R
"""R060: the engine's Hasher128 (xxh3-128, seed 0), which makes the mesh fingerprints.

  known values   equal to an independent implementation for every length class of xxh3
  streaming      any split of the input into update() calls gives the one-shot value
  buffer kinds   bytes, bytearray, memoryview, array.array, numpy arrays (also 2-D and other dtypes)
                 give the value of their bytes; what is not one contiguous buffer is refused
  threads        a large bytes object is hashed with the GIL released and the value is right"""
import array
import threading
import time

import numpy as np

from hindsight.core.native import ENGINE

RNG = np.random.default_rng(60)


def x3(*parts):
    h = ENGINE.Hasher128()
    for p in parts:
        h.update(p)
    return h.hexdigest()


def pattern(n):
    return bytes((i * 7 + 3) & 255 for i in range(n))


# Expected values computed with the `xxhash` package from PyPI (python-xxhash 4.1.0 on libxxhash 0.8.4,
# installed into a throwaway virtual environment, not the engine's Rust crate):
#     xxhash.xxh3_128_hexdigest(data)
# xxh3 has its own code path for lengths 0, 1-3, 4-8, 9-16, 17-128, 129-240 and over 240 (stripes and
# blocks of 1,024 bytes), so the pattern lengths cover each of them and their edges.
KNOWN = {
    b"": "99aa06d3014798d86001c324468d497f",
    b"a": "a96faf705af16834e6c632b61e964e1f",
    b"abc": "06b05ab6733a618578af5f94892f3950",
    b"Hindsight": "346fc89cdb97f201f14e4e46633c55b8",
}
KNOWN_PATTERN = {
    1: "22bbb76b211a39ba13e608bc156defed",
    3: "ce31763cbf8245a5a9088dda485b481c",
    4: "47197970590746b1788a609154b0fe20",
    8: "e3bc8a5f461715553cd024e3d63a1588",
    9: "c72c88247a9a56d7eafab1c7f123109f",
    16: "ce0b9647ab24f88460d75c5e47d40a24",
    17: "bfd327edcc2fbd12eeed7654312a26d7",
    128: "1b1962a096bac78bc580008b6c92ac53",
    129: "293e4968c4619023bd91ce7ace4d385b",
    240: "ad46c1021b076bc704e0b5f034bee80b",
    241: "ac6c3492c3d6b45d8beadd3a8874fe17",
    1024: "18bc0eaca9a336369b81661c641c72b1",
    65536: "6a2504ac37f3b3b35d03b43b14c75500",
    100_003: "45791f178bfff9e8d3c5426780ed98b3",
    1_000_000: "0e8e6fe1a1638b080a8b9cfcd629bb97",
}


def test_known_values():
    assert ENGINE.Hasher128().hexdigest() == KNOWN[b""], "nothing fed"
    for data, want in KNOWN.items():
        assert x3(data) == want, data
    for n, want in KNOWN_PATTERN.items():
        assert x3(pattern(n)) == want, n
    h = ENGINE.Hasher128()
    h.update(b"abc")
    assert h.hexdigest() == h.hexdigest() == KNOWN[b"abc"], "hexdigest doesn't change the state"
    h.update(b"")
    assert h.hexdigest() == KNOWN[b"abc"], "an empty piece changes nothing"
    print(f"  test_known_values ({len(KNOWN) + len(KNOWN_PATTERN)} inputs)")


def test_streaming_equals_one_shot():
    data = RNG.bytes(700_000)
    want = x3(data)
    n = 0
    for cuts in ((1,), (240,), (241, 1000), (1024, 2048), (65535,), (65536, 65537), (100_000, 300_000, 400_000),
                 tuple(range(0, 700_000, 77_777)), (0, 0, 5, 5)):
        pos, parts = 0, []
        for c in cuts:
            parts.append(data[pos:c])
            pos = c
        parts.append(data[pos:])
        assert x3(*parts) == want, cuts
        n += 1
    # byte by byte over every length class, against one shot and against the known values
    for size in (0, 1, 3, 4, 8, 9, 16, 17, 128, 129, 240, 241, 1024, 3000):
        b = pattern(size)
        assert x3(*[b[i:i + 1] for i in range(size)]) == x3(b), size
        if size in KNOWN_PATTERN:
            assert x3(*[b[i:i + 1] for i in range(size)]) == KNOWN_PATTERN[size], size
        n += 1
    # random cuts
    for _ in range(40):
        size = int(RNG.integers(0, 20_000))
        b = RNG.bytes(size)
        cuts = sorted(int(c) for c in RNG.integers(0, size + 1, int(RNG.integers(0, 12))))
        pos, parts = 0, []
        for c in cuts:
            parts.append(b[pos:c])
            pos = c
        parts.append(b[pos:])
        assert x3(*parts) == x3(b), (size, cuts)
        n += 1
    print(f"  test_streaming_equals_one_shot ({n} splits)")


def test_buffer_kinds():
    data = RNG.bytes(300_000)
    want = x3(data)
    for kind in (bytearray(data), memoryview(data), memoryview(bytearray(data)), array.array("B", data),
                 np.frombuffer(data, np.uint8), np.frombuffer(data, np.uint8).reshape(3000, 100),
                 np.frombuffer(data, np.float32), np.frombuffer(data, np.int32).reshape(-1, 3, 5)):
        assert x3(kind) == want, (type(kind), getattr(kind, "shape", None))
    # a contiguous slice, a float array as the fingerprint feeds it (its uint8 view)
    assert x3(memoryview(data)[1001:250_001]) == x3(data[1001:250_001])
    co = np.arange(30_000, dtype=np.float32)
    assert x3(co.view(np.uint8)) == x3(co) == x3(co.tobytes())
    # what isn't one contiguous buffer of bytes is refused
    for bad in (memoryview(data)[::2], np.frombuffer(data, np.uint8)[::3], "text", 3, 1.5, None, [1, 2]):
        try:
            ENGINE.Hasher128().update(bad)
        except (TypeError, BufferError, ValueError):
            continue
        raise AssertionError(type(bad))
    # a refused piece leaves the hasher as it was
    h = ENGINE.Hasher128()
    h.update(b"abc")
    try:
        h.update("text")
    except (TypeError, BufferError, ValueError):
        pass
    assert h.hexdigest() == KNOWN[b"abc"]


def test_hasher_with_other_threads():
    """A large bytes object is hashed without the GIL: another thread runs meanwhile and the value is
    right; a bytearray and a numpy array are hashed with the GIL held and give the same value."""
    big = RNG.bytes(40_000_000)
    want = x3(big)
    got, ticks, stop = [], [0], threading.Event()

    def spin():                     # gives the GIL back at every turn: it runs only while the engine lets it go
        while not stop.is_set():
            ticks[0] += 1
            time.sleep(0)

    old = sys.getswitchinterval()
    sys.setswitchinterval(5.0)      # no forced hand-over of the GIL during the test
    t = threading.Thread(target=spin)
    t.start()
    try:
        for _ in range(3):
            got.append(x3(big))
        ran = ticks[0]
    finally:
        stop.set()
        sys.setswitchinterval(old)
        t.join()
    assert got == [want] * 3
    assert ran > 20, ran
    assert x3(bytearray(big)) == want and x3(np.frombuffer(big, np.uint8)) == want
    print(f"  test_hasher_with_other_threads (a second thread ran {ran} turns during 3 hashes of 40 MB)")


def main():
    tests = [test_known_values, test_streaming_equals_one_shot, test_buffer_kinds, test_hasher_with_other_threads]
    for t in tests:
        R.run(t)
    R.finish()


if __name__ == "__main__":
    main()
