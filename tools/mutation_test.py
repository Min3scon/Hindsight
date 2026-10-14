"""Mutation testing for the storage engine: break the code on purpose, one change at a time, and
check that the core tests notice.

    <blender python> tools/mutation_test.py

Each mutant is a copy of hindsight/ and tests/ under .tmp/mutants/<n>/ with one edit applied; the
core test files run against it in a subprocess. A mutant is "killed" when a test fails. Every
mutant here changes behaviour that matters (data, safety checks, encoder choices), so all of them
must be killed. Prints a table and exits 1 if any survived.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".tmp", "mutants")
TESTS = ("test_core_codec.py", "test_core_store.py", "test_core_migration.py", "test_core_format.py",
         "test_core_history.py", "test_core_journal.py", "test_core_records.py")

# (file under hindsight/core, exact text, replacement, what it breaks)
MUTANTS = [
    ("delta.py", "        dst += v\n", "        dst -= v\n", "forward apply subtracts"),
    ("delta.py", "        _combine(b[lo:lo + k], v, d.op, forward)", "        _combine(b[lo + 1:lo + 1 + k], v, d.op, forward)",
     "contiguous apply off by one"),
    ("delta.py", "        a ^= sign\n", "        pass\n", "zigzag sign not undone (small arrays)"),
    ("delta.py", "        q ^= sign\n", "        pass\n", "zigzag sign not undone (large arrays)"),
    ("delta.py", "    for _ in range(low):\n        np.add.accumulate(a, axis=0, out=a)",
     "    for _ in range(max(0, low - 1)):\n        np.add.accumulate(a, axis=0, out=a)", "one prediction order too few"),
    ("delta.py", "    a <<= 1\n    a ^= sign.view(a.dtype)", "    a <<= 2\n    a ^= sign.view(a.dtype)", "zigzag encodes wrongly"),
    ("delta.py", "    g = (np.diff(idx.astype(np.int64)) if gaps is None else gaps) - 1",
     "    g = (np.diff(idx.astype(np.int64)) if gaps is None else gaps) - 0", "gaps stored without the minus one"),
    ("delta.py", "            tail += _ramp(k)[1:]\n", "", "gap decoding drops the +1 per element"),
    ("delta.py", "            return _ramp(k) + _U32(first)", "            return _ramp(k) + _U32(first + 1)", "single run starts one late"),
    ("delta.py", "    return ChannelDelta(key, idx, nb[idx] - ob[idx], exists, ADD)",
     "    return ChannelDelta(key, idx, ob[idx] - nb[idx], exists, ADD)", "diff points the wrong way"),
    ("delta.py", "                    if est[order][c] < est[best][c] * ORDER_GAIN:",
     "                    if est[order][c] > est[best][c] * ORDER_GAIN:", "order choice inverted (size only)"),
    ("delta.py", "    if gaps.size and int(gaps.min()) < 1:", "    if False:", "repeated indices accepted"),
    ("delta.py", "        want = (np.zeros_like(rec.vals) - rec.vals) if inverse else rec.vals",
     "        want = rec.vals", "undo matching ignores direction"),
    ("delta.py", "    if int(d.idx[-1]) >= b.shape[0]:", "    if False:", "apply skips the range check"),
    ("step.py", "    if zlib.crc32(mv[8:]) != crc:", "    if False:", "step checksum not checked"),
    ("step.py", "_KINDS[step.kind], flags, 0, int(step.seq),",
     "_KINDS[step.kind], flags, 0, int(step.seq) + 1,", "sequence number written wrongly"),
    ("step.py", "    if len(new) >= len(b) or not same_step(step, step_from_bytes(new)):",
     "    if len(new) >= len(b):", "recompression not verified"),
    ("step.py", "    if off != len(body):", "    if False:", "trailing data after the last record accepted"),
    ("codec.py", "    if expected is not None and len(out) != expected:", "    if False:", "decompressed length not checked"),
    ("persist.py", "        if zlib.crc32(mv[8:pos]) & 0xFFFFFFFF != want:", "        if False:", "file header checksum not checked"),
    ("persist.py", "            if crcs is not None and i < len(crcs) and (zlib.crc32(blob) & 0xFFFFFFFF) != int(crcs[i]):",
     "            if False:", "per-step file checksum not checked"),
    ("history.py", "            _delta.apply(arr, d, forward)", "            _delta.apply(arr, d, True)", "backward jumps apply forward"),
    ("history.py", "        for rec in (step.records if forward else reversed(step.records)):",
     "        for rec in step.records:", "records undone in recording order"),
    ("history.py", "            v = new if forward else old", "            v = new", "records always set the new value"),
    ("history.py", "            if v is MISSING:\n                self.values.pop(k, None)",
     "            if v is MISSING:\n                self.values[k] = None", "a missing value kept as None"),
    ("step.py", "    if v is MISSING:\n        return _MISSING_JSON", "    if v is MISSING:\n        return None",
     "MISSING stored as null"),
    ("step.py", "        if flags & RECORDS_FLAG:\n            (info[\"records\"],) = _U32.unpack_from(h, off)",
     "        if False:\n            (info[\"records\"],) = _U32.unpack_from(h, off)", "record section ignored"),
    ("persist.py", "            if bad_at < saved_cursor:", "            if False:",
     "a damaged step before the saved one keeps unreachable steps (D27)"),
    ("history.py", "    for e in entries:\n        yield e.load() if e.target in states else None",
     "    for e, w in zip(entries, [x.target in states for x in entries]):\n        yield e.load() if w else None",
     "objects brought back mid-jump skipped"),
]


def make(n, fname, old, new):
    d = os.path.join(OUT, str(n))
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "hindsight"), os.path.join(d, "hindsight"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(d, "tests"), ignore=shutil.ignore_patterns("__pycache__", "site"))
    path = os.path.join(d, "hindsight", "core", fname)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    if src.count(old) != 1:
        raise SystemExit(f"mutant {n}: text not found exactly once in {fname}: {old!r}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(src.replace(old, new))
    return d


def run(d):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for t in TESTS:
        try:
            r = subprocess.run([sys.executable, os.path.join(d, "tests", t)], capture_output=True, text=True,
                               timeout=600, env=env, cwd=d)
        except subprocess.TimeoutExpired:
            return t + " (timeout)"
        if r.returncode != 0 or "FAIL" in r.stdout:
            return t
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    survived = []
    for n, (fname, old, new, what) in enumerate(MUTANTS, 1):
        d = make(n, fname, old, new)
        killer = run(d)
        print(f"{n:2d} {'killed ' if killer else 'SURVIVED'} {fname:10s} {what}" + (f"  (by {killer})" if killer else ""),
              flush=True)
        if not killer:
            survived.append(what)
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{len(MUTANTS) - len(survived)}/{len(MUTANTS)} mutants killed")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
