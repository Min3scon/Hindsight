import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Fault injection for the crash journal and its replay (pure Python, no bpy).

A model session writes the journal records the add-on writes (the same calls in the same order as
bl/runtime.py, bl/joinsep.py and bl/persistence.py), with steps whose "mesh" is a name: every step says
which state of each object it starts from and which it leads to (extra "from" / "to"). That makes the
oracle exact:

  consistent(history, file)  for every object a history touches,
    - the steps that touch it follow on from each other (step i ends where the next one starts),
      because a delta played back onto any other state gives a shape that never existed;
    - the state at the cursor is the object's state in the file (absent for an object a join removed);
    - step numbers increase (the global timeline orders every history by them).

The journal is then cut at every byte, damaged, crashed during a save, written by two sessions at
once, and replayed onto what the file holds. Replay must never raise and never give an inconsistent
history. Seeded random sessions do the same with strokes, scrubbing, branches, the step cap, memory
drops, Delete earlier steps, Clear, recording off and on, morph targets, joins, operations on several
objects at once and two interruptions in a row. Seeds that once failed are kept in REGRESSION_SEEDS.
"""
import random
import shutil
import tempfile
import time
import traceback

import numpy as np

from hindsight.core import persist
from hindsight.core.journal import Journal
from hindsight.core.registry import Registry
from hindsight.core.types import ChannelDelta, Step, Entry, DELTA, POSITION_KEY

ABSENT = "-"            # an object a join removed


def _step(seq, target, frm, to, op=None):
    d = ChannelDelta(POSITION_KEY, np.array([seq % 97], dtype=np.uint32),
                     np.array([[seq, 1, 2]], dtype=np.uint32), (True, True))
    extra = {"from": dict(frm), "to": dict(to)}
    if op is not None:
        extra["op"] = op
    return Step(seq=seq, label="Stroke", mode="SCULPT", target=target, time=1000.0 + seq, kind=DELTA,
                deltas=[d], extra=extra)


def _ft(e):
    ex = e.meta.get("extra") or {}
    return ex.get("from") or {}, ex.get("to") or {}


def state_at(h, p, m):
    """The state of object m at position p of history h (None: no step touches m)."""
    for e in reversed(h.entries[:p]):
        _f, t = _ft(e)
        if m in t:
            return t[m]
    for e in h.entries[p:]:
        f, _t = _ft(e)
        if m in f:
            return f[m]
    return None


def consistency_errors(reg, file_meshes, where=""):
    errs = []
    for uid, h in reg.histories.items():
        if not h.entries:
            continue
        seqs = [e.seq for e in h.entries]
        if seqs != sorted(set(seqs)):
            errs.append(f"{where}{uid}: step numbers not increasing {seqs}")
        if not 0 <= h.cursor <= len(h.entries):
            errs.append(f"{where}{uid}: cursor {h.cursor} outside 0..{len(h.entries)}")
            continue
        touched = []
        for e in h.entries:
            f, t = _ft(e)
            for m in list(f) + list(t):
                if m not in touched:
                    touched.append(m)
        for m in touched:
            last = None
            for i, e in enumerate(h.entries):
                f, t = _ft(e)
                if m in f:
                    if last is not None and f[m] != last:
                        errs.append(f"{where}{uid}: step {e.seq} starts {m} at {f[m]}, the step before ends at {last}")
                    last = t.get(m, last)
            if m not in file_meshes:
                continue                 # not in the file (made after the save, or joined into another)
            want = file_meshes[m]
            got = state_at(h, h.cursor, m)
            if got is not None and got != want:
                errs.append(f"{where}{uid}: {m} at cursor {h.cursor} is {got}, the file holds {want} ({seqs})")
    return errs


class Session:
    """What the add-on does to its registry and journal, without Blender."""

    def __init__(self, folder, cap=1000, sid="s1"):
        self.folder = folder
        self.jpath = os.path.join(folder, "j.hsj")
        self.reg = Registry()
        self.mesh = {}           # object -> state as it is
        self.st = {}             # object -> state as recorded (runtime's MeshState)
        self.n = 0
        self.cap = cap
        self.journal = Journal(self.jpath)
        self.file = None         # (registry bytes, meshes) of the last save
        self.sid = sid
        self.save_no = 0
        self.log = []

    # -- journal (persistence._on_journal / _mark_saved) --------------------------------------
    def rec(self, rtype, header, payload=b""):
        self.journal.append(rtype, dict(header, s=self.sid), payload)
        self.journal.flush(fsync=False)

    def new_state(self, m):
        self.n += 1
        return f"{m}{self.n}"

    def add(self, m):
        s = self.new_state(m)
        self.mesh[m] = self.st[m] = s

    def hist(self, m):
        return self.reg.history_for(m)

    # -- runtime.commit ------------------------------------------------------------------------
    def _commit(self, m, op=None, group=None):
        h = self.hist(m)
        if h is None:
            h = self.reg.create(m)
            self.rec("META", {"hist": h.uid, "members": list(h.members), "enabled": True, "morph_target": None})
        seq = self.reg.new_seq()
        if group is not None:
            if group[0] is None:
                group[0] = seq
            else:
                op = group[0]
        step = _step(seq, m, {m: self.st[m]}, {m: self.mesh[m]}, op)
        self.st[m] = self.mesh[m]
        at = h.cursor
        removed = h.push(Entry(step))
        if removed:
            self.rec("TRUNC", {"hist": h.uid, "keep": at})
        e = h.entries[-1]
        self.rec("STEP", {"hist": h.uid, "at": at}, e.to_bytes("auto", 0))
        excess = len(h.entries) - self.cap
        if excess > 0:
            n = h.drop_oldest(excess)
            if n:
                self.rec("DROP", {"hist": h.uid, "n": int(n)})

    def stroke(self, m):
        self.mesh[m] = self.new_state(m)
        h = self.hist(m)
        if h is not None and not h.enabled:
            return                                   # not recorded while off
        self._commit(m)

    def operation(self, ms):
        """One operator changing several objects: one step in each history, one global position."""
        group = [None]
        for m in ms:
            h = self.hist(m)
            if h is not None and not h.enabled:
                continue
            self.mesh[m] = self.new_state(m)
            self._commit(m, group=group)

    # -- runtime.goto / native undo -------------------------------------------------------------
    def scrub(self, m, pos, journaled=True):
        h = self.hist(m)
        if h is None:
            return
        pos = max(0, min(pos, len(h.entries)))
        h.cursor = pos
        for x in h.members:
            s = state_at(h, pos, x)
            if s == ABSENT:
                self.mesh.pop(x, None)
                self.st.pop(x, None)
            elif s is not None:
                self.mesh[x] = self.st[x] = s
        if journaled:
            self.rec("CURSOR", {"hist": h.uid, "cursor": h.cursor})

    # -- menu actions ---------------------------------------------------------------------------
    def delete_past(self, m):
        h = self.hist(m)
        if h is None:
            return
        n = h.delete_past()
        if n:
            self.rec("DROP", {"hist": h.uid, "n": n})

    def memory_drop(self, m, n):
        """store.enforce_caps: never a step at or past the cursor."""
        h = self.hist(m)
        if h is None:
            return
        n = h.drop_oldest(min(n, h.cursor))
        if n:
            self.rec("DROP", {"hist": h.uid, "n": int(n)})

    def clear(self, m):
        h = self.hist(m)
        if h is None:
            return
        at = h.cursor
        h.reset()
        self.rec("RESET", {"hist": h.uid, "at": at})

    def turn_off(self, m):
        h = self.hist(m)
        if h is None:
            return
        h.enabled = False
        self.rec("META", {"hist": h.uid, "members": list(h.members), "enabled": False,
                          "morph_target": h.morph_target})

    def turn_on(self, m):
        h = self.hist(m)
        if h is None or h.enabled:
            return
        h.enabled = True
        self.rec("META", {"hist": h.uid, "members": list(h.members), "enabled": True,
                          "morph_target": h.morph_target})
        if self.mesh[m] != self.st[m]:
            self._commit(m)                          # "Changes While Off"

    def morph(self, m):
        h = self.hist(m)
        if h is None:
            return
        h.morph_target = h.cursor
        self.rec("META", {"hist": h.uid, "members": list(h.members), "enabled": h.enabled,
                          "morph_target": h.morph_target})

    def join(self, a, b):
        """joinsep.join_with_history: b goes into a."""
        R = self.reg
        if a not in self.mesh or b not in self.mesh or a == b:
            return
        if not all(h.enabled for h in R.histories.values()):
            return                       # (joining with recording off is not modelled)
        for m in (a, b):
            h = R.history_for(m)
            if h is not None and h.future_count():
                at = h.cursor
                h.truncate_future()
                self.rec("TRUNC", {"hist": h.uid, "keep": at})
            if h is not None and self.mesh[m] != self.st[m]:
                self._commit(m)
        hist = R.history_for(a)
        if hist is None:
            hist = R.create(a)
        hb = R.history_for(b)
        absorbed = [hb] if hb is not None and hb is not hist else []
        members = list(hist.members)
        entries = list(hist.entries)
        for x in absorbed:
            entries.extend(x.entries)
            members += [m for m in x.members if m not in members]
        if b not in members:
            members.append(b)
        rewrite = bool(absorbed and any(x.entries for x in absorbed))
        entries.sort(key=lambda e: e.seq)
        hist.entries = entries
        hist.cursor = len(entries)
        hist.members = members
        for x in absorbed:
            R.histories.pop(x.uid, None)
            x.entries = []
            x.cursor = 0
            self.rec("RESET", {"hist": x.uid})
            self.rec("META", {"hist": x.uid, "members": []})
            x.members = []
        for m in members:
            R.attach(m, hist)
        self.rec("META", {"hist": hist.uid, "members": list(hist.members), "enabled": hist.enabled,
                          "morph_target": hist.morph_target})
        if rewrite:
            self.rec("RESET", {"hist": hist.uid})
            for i, e in enumerate(hist.entries):
                self.rec("STEP", {"hist": hist.uid, "at": i}, e.to_bytes("auto", 0))
        new = self.new_state(a)
        frm = {a: self.mesh[a], b: self.mesh[b]}
        step = Step(seq=R.new_seq(), label="Join", mode="OBJECT", target=a, time=time.time(), kind=DELTA,
                    deltas=[], extra={"from": frm, "to": {a: new, b: ABSENT}})
        at = hist.cursor
        hist.push(Entry(step))
        self.rec("STEP", {"hist": hist.uid, "at": at}, hist.entries[-1].to_bytes("auto", 0))
        self.mesh[a] = self.st[a] = new
        del self.mesh[b]
        del self.st[b]

    # -- persistence ----------------------------------------------------------------------------
    def save(self, journal_reset=True):
        self.save_no += 1
        self.reg.save_id = f"{self.sid}-save{self.save_no}"
        self.file = (persist.registry_to_bytes(self.reg), dict(self.mesh))
        if journal_reset:
            self.journal.truncate(0)
            self.journal.append("SAVE", {"blend": "/x.blend", "save_id": self.reg.save_id, "s": self.sid})
            self.journal.flush(fsync=True)

    def snapshot(self):
        """Live history of every object, for the liveness check."""
        out = {}
        for uid, h in self.reg.histories.items():
            out[uid] = [e.seq for e in h.entries]
        return out


def recover(file_bytes, records):
    reg = persist.registry_from_bytes(file_bytes)
    persist.replay(reg, records)
    return reg


def reopen(s, new_sid):
    """on_load_post with Interrupted session: what the next session starts from."""
    file_bytes, meshes = s.file
    j = Journal(s.jpath)
    recs = j.read()
    reg = persist.registry_from_bytes(file_bytes)
    if persist.journal_belongs(reg, recs) and any(r[0] == "SAVE" for r in recs):
        persist.replay(reg, recs)
        j.append("META", {"resync": True, "s": new_sid})      # this session owns the journal now
        j.flush(fsync=False)
    else:
        j.truncate(0)
        j.append("SAVE", {"blend": "/x.blend", "save_id": getattr(reg, "save_id", None), "s": new_sid})
        j.flush(fsync=True)
    j.close()
    s.journal.close()
    s.journal = Journal(s.jpath)
    s.reg = reg
    s.sid = new_sid
    s.mesh = dict(meshes)
    s.st = dict(meshes)
    s.n += 1000
    return reg


def check_replay(file_bytes, meshes, records, where):
    reg = persist.registry_from_bytes(file_bytes)
    try:
        if persist.journal_belongs(reg, records):
            persist.replay(reg, records)
    except Exception:
        return [f"{where}: replay raised\n{traceback.format_exc()}"], reg
    return consistency_errors(reg, meshes, where + ": "), reg


# ---------------------------------------------------------------------------------------- sessions
def realistic_session(folder):
    """A session after a save that does a bit of everything once, with two objects."""
    s = Session(folder, cap=6)
    for m in ("A", "B", "C"):
        s.add(m)
    for _ in range(4):
        s.stroke("A")
    s.stroke("B")
    s.stroke("C")
    s.scrub("A", 3)
    s.save()
    s.stroke("A")                       # branch at 3 (after the saved state of A: it is 3)
    s.scrub("A", 4)
    for _ in range(4):
        s.stroke("A")                    # past the cap: drops
    s.memory_drop("B", 1)
    s.operation(["A", "B"])
    s.morph("A")
    s.turn_off("C")
    s.stroke("C")
    s.turn_on("C")
    s.delete_past("A")
    s.scrub("B", 0, journaled=False)     # Blender's undo moving a data history
    s.scrub("B", 1)
    s.stroke("B")
    s.join("A", "B")
    s.stroke("A")
    s.scrub("A", 1)
    s.stroke("A")                        # below the saved state: left out from here on
    s.clear("C")
    s.stroke("C")
    s.journal.flush()
    with open(s.jpath, "rb") as fh:
        data = fh.read()
    return s, data


def test_journal_cut_at_every_byte():
    """The journal cut at every byte offset (a crash during any write): the reader keeps the whole
    records before the cut, and replay of them onto the saved file is consistent."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        s, data = realistic_session(d)
        file_bytes, meshes = s.file
        cut = os.path.join(d, "cut.hsj")
        full = Journal(s.jpath).read()
        seen = set()
        errors = []
        for k in range(len(data) + 1):
            with open(cut, "wb") as fh:
                fh.write(data[:k])
            j = Journal(cut)
            recs = j.read()
            j.close()
            assert os.path.getsize(cut) <= k
            assert recs == full[:len(recs)], f"cut at {k}: records read are not a prefix"
            if len(recs) in seen:
                continue
            seen.add(len(recs))
            errs, _reg = check_replay(file_bytes, meshes, recs, f"cut at {k} ({len(recs)} records)")
            errors += errs
        assert len(seen) == len(full) + 1, (len(seen), len(full))
        assert not errors, "\n".join(errors[:10])
        types = {r[0] for r in full}
        assert types >= {"STEP", "CURSOR", "TRUNC", "DROP", "RESET", "META", "SAVE"}, types
        print(f"INFO {len(data)} byte offsets, {len(full)} records")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_damaged_journal_reads_a_prefix():
    """Bit flips and zero-filled stretches: reading stops at the first damaged record, never reads a
    damaged record as a good one, and replay of what is read is consistent."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        s, data = realistic_session(d)
        file_bytes, meshes = s.file
        full = Journal(s.jpath).read()
        rng = random.Random(7)
        path = os.path.join(d, "bad.hsj")
        errors = []
        for trial in range(400):
            b = bytearray(data)
            if trial % 4 == 3:
                at = rng.randrange(len(b))
                b[at:] = bytes(len(b) - at)          # zero-filled tail
            else:
                for _ in range(1 + trial % 3):
                    i = rng.randrange(len(b))
                    b[i] ^= 1 << rng.randrange(8)
            with open(path, "wb") as fh:
                fh.write(bytes(b))
            recs = Journal(path).read()
            assert recs == full[:len(recs)], f"trial {trial}: a damaged record was read"
            errors += check_replay(file_bytes, meshes, recs, f"trial {trial}")[0]
        assert not errors, "\n".join(errors[:10])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_every_record_type_last():
    """Replay of every prefix that ends on each record type, for each kind of action."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        s, _data = realistic_session(d)
        file_bytes, meshes = s.file
        full = Journal(s.jpath).read()
        last = {}
        errors = []
        for n in range(len(full) + 1):
            if n:
                last.setdefault(full[n - 1][0], n)
            errors += check_replay(file_bytes, meshes, full[:n], f"{n} records")[0]
        assert set(last) >= {"STEP", "CURSOR", "TRUNC", "DROP", "RESET", "META", "SAVE"}, last
        assert not errors, "\n".join(errors[:10])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_crash_between_file_written_and_journal_reset():
    """A crash (or a failed journal write) after the file was saved and before its journal was reset:
    the journal still holds the records of the save before, which are already in the file. Replaying
    them onto the new file put steps in twice (a step after itself: shapes that never existed)."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        s = Session(d, cap=5)
        s.add("A")
        for _ in range(5):
            s.stroke("A")
        s.save()
        for _ in range(2):
            s.stroke("A")                # the cap drops the two oldest
        s.save(journal_reset=False)      # file written, then the crash
        recs = Journal(s.jpath).read()
        errs, reg = check_replay(*s.file, recs, "old journal on the new file")
        assert not errs, "\n".join(errs)
        h = reg.history_for("A")
        assert [e.seq for e in h.entries] == [3, 4, 5, 6, 7] and h.cursor == 5, ([e.seq for e in h.entries], h.cursor)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_journal_of_another_save_is_not_replayed():
    """The .blend replaced by another save of the same path (a .blend1 renamed back, a copy from
    another machine, an older version from a backup) while a journal for the path is left over."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        s = Session(d)
        s.add("A")
        for _ in range(5):
            s.stroke("A")
        s.save()
        older = s.file                    # the .blend1
        for _ in range(3):
            s.stroke("A")
        s.scrub("A", 7)
        s.save()
        s.stroke("A")
        s.stroke("A")
        recs = Journal(s.jpath).read()
        errs, reg = check_replay(*older, recs, "journal of the newer save on the older file")
        assert not errs, "\n".join(errs)
        assert [e.seq for e in reg.history_for("A").entries] == [1, 2, 3, 4, 5]
        assert not persist.journal_belongs(reg, recs)
        # a file saved by a build without save ids, with a journal that has one: not the same save
        legacy = persist.registry_from_bytes(older[0])
        legacy.save_id = None
        assert not persist.journal_belongs(legacy, recs)
        # both without (files and journals of older builds): replayed as before
        old_recs = [(t, {k: v for k, v in h.items() if k not in ("save_id", "s")}, p) for t, h, p in recs]
        assert persist.journal_belongs(legacy, old_recs)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_two_sessions_on_one_journal():
    """The same file open in two Blender windows: both write to the journal of its path. The one that
    opened (or saved) it last owns the journal; the other's records are not replayed onto it."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        one = Session(d, sid="one")
        one.add("A")
        for _ in range(3):
            one.stroke("A")
        one.save()
        file1 = one.file
        two = Session(d, sid="two")      # opens the same file: Last saved, a fresh journal
        two.reg = persist.registry_from_bytes(file1[0])
        two.mesh = dict(file1[1])
        two.st = dict(file1[1])
        two.n = 500
        two.journal.truncate(0)
        two.journal.append("SAVE", {"blend": "/x.blend", "save_id": two.reg.save_id, "s": "two"})
        two.journal.flush()
        two.file = file1
        one.journal.close()
        one.journal = Journal(one.jpath)
        for i in range(6):
            (one if i % 2 else two).stroke("A")
        one.scrub("A", 1)
        one.stroke("A")
        two.scrub("A", 4)
        two.stroke("A")
        recs = Journal(one.jpath).read()
        assert {r[1].get("s") for r in recs} == {"one", "two"}
        errs, reg = check_replay(*file1, recs, "two sessions")
        assert not errs, "\n".join(errs)
        assert [e.seq for e in reg.history_for("A").entries] == [e.seq for e in two.reg.history_for("A").entries]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_operation_on_several_objects_is_kept_whole():
    """One operation's steps in several histories are one global position (D16, extra "op"). When
    replay leaves one history's step out, the others' steps of that operation can't stay: the global
    timeline would apply half an operation. They go too, with what follows them, and are counted."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        s = Session(d)
        for m in ("A", "B"):
            s.add(m)
            s.stroke(m)
            s.stroke(m)
        s.save()
        s.scrub("A", 1)                  # below A's saved state
        s.operation(["A", "B"])          # A's half is left out
        s.stroke("B")
        recs = Journal(s.jpath).read()
        errs, reg = check_replay(*s.file, recs, "operation")
        assert not errs, "\n".join(errs)
        hb = reg.history_for("B")
        assert [e.seq for e in hb.entries] == [3, 4] and hb.cursor == 2, [e.seq for e in hb.entries]
        rep = reg.replay_report["stopped"]
        assert rep == {reg.by_object["A"]: 1, reg.by_object["B"]: 2}, rep
        # nothing of the operation was left out: all of it stays
        s2 = Session(os.path.join(d, "2"))
        for m in ("A", "B"):
            s2.add(m)
            s2.stroke(m)
        s2.save()
        s2.operation(["A", "B"])
        recs = Journal(s2.jpath).read()
        reg = recover(s2.file[0], recs)
        assert [len(h.entries) for h in reg.histories.values()] == [2, 2] and not reg.replay_report["stopped"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _join_session(d, b_steps=1, before=None):
    """A [1, 2, 3] and B saved; `before(s)` runs after the save; then B is joined into A."""
    s = Session(d)
    s.add("A")
    s.add("B")
    for _ in range(3):
        s.stroke("A")
    for _ in range(b_steps):
        s.stroke("B")
    s.save()
    if before is not None:
        before(s)
    start = len(Journal(s.jpath).read())
    s.join("A", "B")
    s.stroke("A")
    return s, Journal(s.jpath).read(), start


def test_crash_in_the_middle_of_a_join():
    """A join writes RESET and then every step of the merged history again. Cut after some of them,
    the steps of the file's state that are back looked like the whole run, and the cursor went after
    them: the mesh in the file is after B's step, which hadn't been written again yet."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        s, recs, start = _join_session(d)
        errors = []
        for n in range(start, len(recs) + 1):
            errors += check_replay(*s.file, recs[:n], f"{n} records")[0]
        assert not errors, "\n".join(errors)
        # cut between the cleared history and the merged one: B keeps pointing at its own history,
        # which lists it again (jumps move the meshes of the objects a history lists)
        n = next(i for i, r in enumerate(recs) if r[0] == "META" and r[1].get("members") == []) + 1
        reg = recover(s.file[0], recs[:n])
        h = reg.history_for("B")
        assert h is not None and "B" in h.members, (h, h and h.members)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_join_after_steps_the_session_dropped():
    """The session dropped steps of A that recovery keeps (Delete earlier steps after the save), then
    joined B into A. The merged history has no step from A's state in the file to A's state at the
    join: it can't be placed, and is left out."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        def before(s):
            s.stroke("A")
            s.delete_past("A")
        s, recs, _start = _join_session(d, before=before)
        errs, reg = check_replay(*s.file, recs, "join")
        assert not errs, "\n".join(errs)
        assert reg.history_for("A").entries == [] and reg.replay_report["stopped"], reg.replay_report
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_join_of_a_history_that_was_left_out():
    """B's history was stopped (scrubbed back past its saved state and recorded), then the session
    dropped B's remaining steps and joined it into A. With no steps of B to write again the join is
    one step, which starts from B as the session had it, not as the file has it."""
    d = tempfile.mkdtemp(prefix="hs_faults_")
    try:
        def before(s):
            s.scrub("B", 0)
            s.stroke("B")
            s.memory_drop("B", 1)
            s.clear("B")
        s, recs, _start = _join_session(d, b_steps=2, before=before)
        assert not [r for r in recs if r[0] == "RESET" and r[1].get("hist") == s.hist("A").uid]
        errs, reg = check_replay(*s.file, recs, "join")
        assert not errs, "\n".join(errs)
        h = reg.history_for("A")
        assert [e.seq for e in h.entries] == [1, 2, 3] and h.cursor == 3, ([e.seq for e in h.entries], h.cursor)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------------------------ random sessions
ACTIONS = ("stroke", "stroke", "stroke", "stroke", "scrub", "scrub", "silent", "operation", "delete_past",
           "memory_drop", "clear", "off", "on", "morph", "join", "save", "add")


def run_seed(seed, n_actions=60, d=None):
    """One random session with a crash at a random byte, recovery, more work, a second crash."""
    rng = random.Random(seed)
    own = d is None
    d = d or tempfile.mkdtemp(prefix="hs_faults_")
    try:
        s = Session(d, cap=rng.choice((3, 5, 8, 1000)))
        names = ["A", "B", "C"]
        for m in names[:rng.randint(1, 3)]:
            s.add(m)
        s.save()
        for round_ in range(2):
            lives = []                                       # (journal size, live histories)
            for _ in range(n_actions):
                objs = sorted(s.mesh)
                if not objs:
                    s.add(f"N{s.n}")
                    continue
                m = rng.choice(objs)
                h = s.hist(m)
                a = rng.choice(ACTIONS)
                if a == "stroke":
                    s.stroke(m)
                elif a == "scrub" and h is not None:
                    s.scrub(m, rng.randint(0, len(h.entries)))
                elif a == "silent" and h is not None:
                    s.scrub(m, rng.randint(0, len(h.entries)), journaled=False)
                elif a == "operation":
                    s.operation(rng.sample(objs, min(len(objs), rng.randint(1, 3))))
                elif a == "delete_past":
                    s.delete_past(m)
                elif a == "memory_drop":
                    s.memory_drop(m, rng.randint(1, 4))
                elif a == "clear" and rng.random() < 0.3:
                    s.clear(m)
                elif a == "off" and all(x.enabled for x in s.reg.histories.values()):
                    s.turn_off(m)
                elif a == "on":
                    for x in sorted(s.mesh):
                        s.turn_on(x)
                elif a == "morph":
                    s.morph(m)
                elif a == "join" and len(objs) > 1 and rng.random() < 0.3:
                    s.join(m, rng.choice([o for o in objs if o != m]))
                elif a == "save" and rng.random() < 0.3:
                    for x in sorted(s.mesh):                 # nothing unrecorded goes into a save
                        s.turn_on(x)                         # (tested on its own in Blender)
                    for x in sorted(s.mesh):
                        if s.mesh[x] != s.st[x]:
                            s._commit(x)
                    s.save(journal_reset=rng.random() > 0.1)
                    lives = []                                   # sizes before it are another journal
                elif a == "add" and len(objs) < 4:
                    s.add(f"N{s.n}")
                lives.append((s.journal.size(), s.snapshot()))
            s.journal.flush()
            with open(s.jpath, "rb") as fh:
                data = fh.read()
            k = rng.randint(0, len(data))
            with open(s.jpath, "wb") as fh:
                fh.write(data[:k])
            s.journal.close()
            s.journal = Journal(s.jpath)
            recs = s.journal.read()
            errs, reg = check_replay(*s.file, recs, f"seed {seed} round {round_} cut {k}/{len(data)}")
            if errs:
                return errs
            # liveness at an action boundary: a history not reported as left out has every step
            # the session had when it stopped
            live = None
            for size, snap in lives:
                if size == s.journal.size():
                    live = snap
            if live is not None and persist.journal_belongs(reg, recs):
                stopped = reg.replay_report["stopped"]
                for uid, seqs in live.items():
                    h = reg.histories.get(uid)
                    got = [e.seq for e in h.entries] if h is not None else []
                    if uid not in stopped and seqs and got[-len(seqs):] != seqs:
                        return [f"seed {seed} round {round_}: {uid} lost steps silently: live {seqs}, recovered {got}"]
            reopen(s, f"s{round_ + 2}")
        return []
    finally:
        if own:
            shutil.rmtree(d, ignore_errors=True)


REGRESSION_SEEDS = [34, 350, 1633, 1868, 2157]    # failed before the replay fixes of this round


def test_random_sessions():
    n = int(os.environ.get("HS_FAULT_SEEDS", "300"))
    failed = []
    for seed in REGRESSION_SEEDS + list(range(n)):
        errs = run_seed(seed)
        if errs:
            failed.append((seed, errs))
    assert not failed, "\n".join(f"seed {s}: {e[0]}" for s, e in failed[:8]) + f"\n{len(failed)} seeds failed: {[s for s, _ in failed]}"


TESTS = [
    test_journal_cut_at_every_byte,
    test_damaged_journal_reads_a_prefix,
    test_every_record_type_last,
    test_crash_between_file_written_and_journal_reset,
    test_journal_of_another_save_is_not_replayed,
    test_two_sessions_on_one_journal,
    test_operation_on_several_objects_is_kept_whole,
    test_crash_in_the_middle_of_a_join,
    test_join_after_steps_the_session_dropped,
    test_join_of_a_history_that_was_left_out,
    test_random_sessions,
]


def main():
    failed = 0
    for t in TESTS:
        t0 = time.time()
        try:
            t()
            print("PASS %s (%.1f s)" % (t.__name__, time.time() - t0))
        except Exception:
            failed += 1
            print("FAIL %s" % t.__name__)
            traceback.print_exc()
    sys.stdout.flush()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
