"""#129: the corpus index (tests/corpus.py) names every history file in tests/fixtures with its format, today's code
opens each one and the ones corpus.py makes (a journal with every record type, a big format 5 history), each damaged
copy is either read or refused with a ValueError, as the readers promise, and each wrong-type input is read or refused
the same way every time. The equivalence check catches a changed result on every new case (#144 review, point 2).
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
from tests import equiv  # noqa: E402
from hindsight.core import journal, persist  # noqa: E402

PAYLOADS = {path: C.payload(path, kind) for path, kind, _f in C.FILES}
PAYLOADS.update({name: C.make(name) for name, _k, _f in C.MADE})
ALL = C.FILES + C.MADE


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
    for path, kind, fmt in ALL:
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


def test_the_made_files_are_the_same_every_time():
    """The journal has every record type (written by today's Journal); the format 5 history has 6,000 steps of
    several MB in more than one step part, with every kind of step."""
    for name, _k, _f in C.MADE:
        assert C.make(name) == PAYLOADS[name], name
    recs = today("journal", PAYLOADS["every_record.hsj"])
    assert {r[0] for r in recs} == set(journal.RTYPES), sorted({r[0] for r in recs})
    big = PAYLOADS["big_format5"]
    size = sum(len(p) for k, v in big.items() for p in ((v,) if k.startswith("index") else v.values()))
    assert size > 4 << 20, size
    reg = today("parts", big)
    assert sum(len(h.entries) for h in reg.histories.values()) >= 6000
    assert max(len(v) for k, v in big.items() if not k.startswith("index")) >= 2
    assert {e.meta["kind"] for h in reg.histories.values() for e in h.entries} == {"DELTA", "TOPO", "DATA"}


def test_wrong_types_are_read_or_refused_the_same_every_time():
    """Rule 2's wrong types: today's readers either read each one or refuse it with an Exception, and do the same on
    a second call (equiv.check of today's code against itself)."""
    for kind in ("registry", "parts", "journal"):
        cases = C.wrong_types(kind)
        assert cases and len({n for n, _v in cases}) == len(cases), kind
        for name, bad in cases:
            try:
                today(kind, bad)
            except Exception:                       # noqa: BLE001 - any refusal, but a BaseException fails
                pass
            equiv.check(lambda b: today(kind, b), lambda b: today(kind, b), bad,
                        key=equiv.registry if kind != "journal" else None)


def _changed(kind, data):
    """today's reader with one deliberate difference: another exception class (same message), one int in a record
    header read as a float, or two neighbouring steps of the longest history swapped."""
    try:
        got = today(kind, data)
    except Exception as e:                          # noqa: BLE001
        raise (TypeError if type(e) is not TypeError else ValueError)(str(e)) from None
    if kind == "journal":
        for _t, header, _p in reversed(got):
            ints = [k for k, v in header.items() if type(v) is int] if type(header) is dict else []
            if ints:
                header[ints[0]] = float(header[ints[0]])
                return got
        return got + [("SAVE", {}, b"")]
    h = max(got.histories.values(), key=lambda h: len(h.entries))
    if len(h.entries) < 2:
        got.load_report = dict(got.load_report, format=-1)
    else:
        i = len(h.entries) // 2
        h.entries[i - 1], h.entries[i] = h.entries[i], h.entries[i - 1]
    return got


def test_the_equivalence_check_catches_a_changed_result_on_each_new_case():
    new = [(kind, PAYLOADS[name]) for name, kind, _f in C.MADE]
    new += [(kind, bad) for kind in ("registry", "parts", "journal") for _n, bad in C.wrong_types(kind)]
    for kind, data in new:
        key = equiv.registry if kind != "journal" else None
        try:
            equiv.check(lambda b: today(kind, b), lambda b: _changed(kind, b), data, key=key)
        except AssertionError:
            continue
        raise AssertionError(f"equiv.check passed a changed {kind} result")


def test_damaged_copies_are_read_or_refused():
    for path, kind, _f in ALL:
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
          test_the_made_files_are_the_same_every_time, test_wrong_types_are_read_or_refused_the_same_every_time,
          test_the_equivalence_check_catches_a_changed_result_on_each_new_case, test_damaged_copies_are_read_or_refused):
    H.run(t)
H.finish()
