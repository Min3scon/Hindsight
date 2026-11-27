"""#129: the corpus index (tests/corpus.py) names every history file in tests/fixtures with its format, today's code
opens each one, and each damaged copy is either read or refused with a ValueError, as the readers promise.
Run: <bpy python> tests/test_core_corpus.py"""
import glob
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.argv = [sys.argv[0]]
import tests.harness as H  # noqa: E402
from tests import corpus as C  # noqa: E402
from hindsight.core import journal, persist  # noqa: E402

PAYLOADS = {path: C.payload(path, kind) for path, kind, _f in C.FILES}


def today(kind, data):
    """Open `data` with today's code: a registry, or the journal's records."""
    if kind in ("registry", "text"):
        return persist.registry_from_bytes(data)
    if kind == "parts":
        return persist.registry_from_format5(data["index"], data.get("index_copy"),
                                             lambda uid, i: data.get(uid, {}).get(str(i)))
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "scene.hsj")
        with open(path, "wb") as fh:
            fh.write(data)
        j = journal.Journal(path, keep=True)
        try:
            return j.read()
        finally:
            j.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_index_names_every_history_file():
    listed = {p for p, _k, _f in C.FILES}
    on_disk = {os.path.relpath(f, C.FIX).replace(os.sep, "/")
               for f in glob.glob(os.path.join(C.FIX, "**", "*.hs[rj]"), recursive=True)}
    assert on_disk <= listed, sorted(on_disk - listed)
    assert all(os.path.isfile(os.path.join(C.FIX, p)) for p in listed)
    assert {f for _p, _k, f in C.FILES if _k != "journal"} == {1, 2, 3, 4, 5}


def test_todays_code_opens_every_file():
    for path, kind, fmt in C.FILES:
        data = PAYLOADS[path]
        got = today(kind, data)
        if kind == "journal":
            assert data[:4] == journal.MAGIC and fmt == 1, path
            assert got and {r[0] for r in got} <= set(journal.RTYPES), (path, got)
            continue
        if kind != "parts":
            assert C.header_format(data) == fmt, (path, C.header_format(data))
        assert got.load_report["format"] == fmt and got.load_report["damaged"] == [], (path, got.load_report)
        assert got.histories and all(h.entries for h in got.histories.values()), path


def test_damaged_copies_are_read_or_refused():
    for path, kind, _f in C.FILES:
        data = PAYLOADS[path]
        cases = C.damaged_parts(data) if kind == "parts" else C.damaged(data, kind)
        again = C.damaged_parts(data) if kind == "parts" else C.damaged(data, kind)
        assert [n for n, _b in cases] == [n for n, _b in again] and len({n for n, _b in cases}) == len(cases), path
        for (name, bad), (_n, bad2) in zip(cases, again):
            assert bad == bad2 and bad != data, (path, name)
            try:
                today(kind, bad)
            except ValueError:
                pass


for t in (test_the_index_names_every_history_file, test_todays_code_opens_every_file,
          test_damaged_copies_are_read_or_refused):
    H.run(t)
H.finish()
