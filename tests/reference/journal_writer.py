"""The crash journal's writer thread as hindsight/bl/persistence.py had it before 24.2b (#153): R079's helper that
encodes and writes the records of big steps, in order. The engine's (native/src/journal.rs) is checked against it.
Moved unchanged but for `put` and `pending` (the two lines of _on_journal that hand a record over and ask whether
one is waiting) and the drain raising the error the Blender side logs (log.error_once(("journal_writer", str(e)),
f"crash-recovery journal error: {e}", e)).

R079: the records of big steps (a topology change at 1M vertices is about 120 MB to encode and write) are encoded
and written on a helper thread, in order: once one is waiting, every later record waits behind it. Whatever reads,
empties, switches or closes the journal waits for the helper first. A crash can lose only the newest records still
waiting, so the journal recovery reads is always a prefix of what was written."""
import queue
import threading

_JW = {"q": None, "thread": None, "error": None}
_JLOCK = threading.Lock()        # one writer at a time into the journal file


def _writer_loop(q) -> None:
    while True:
        item = q.get()
        try:
            if item is None:
                return
            j, rtype, header, fn = item
            payload = fn() if fn is not None else b""
            with _JLOCK:
                if not j.closed:
                    j.append(rtype, header, payload)
                    j.flush(fsync=False)
        except Exception as e:
            _JW["error"] = e
        finally:
            q.task_done()


def _writer_queue():
    q = _JW["q"]
    t = _JW["thread"]
    if q is None or t is None or not t.is_alive():
        q = _JW["q"] = queue.Queue()
        t = _JW["thread"] = threading.Thread(target=_writer_loop, args=(q,), name="hindsight-journal", daemon=True)
        t.start()
    return q


def put(j, rtype, header, payload_fn=None) -> None:
    """R079: the record is encoded and written by the helper."""
    _writer_queue().put((j, rtype, header, payload_fn))


def pending() -> int:
    """How many records handed over are not in the journal yet (_on_journal's q.unfinished_tasks)."""
    q = _JW["q"]
    return q.unfinished_tasks if q is not None else 0


def drain_journal_writer() -> None:
    """Wait until every record handed to the helper is in the journal (main thread)."""
    q = _JW["q"]
    if q is not None:
        q.join()
    e, _JW["error"] = _JW["error"], None
    if e is not None:
        raise e


def _stop_journal_writer() -> None:
    q, t = _JW["q"], _JW["thread"]
    if q is not None and t is not None and t.is_alive():
        drain_journal_writer()
        q.put(None)
        t.join(timeout=10.0)
    _JW["q"] = _JW["thread"] = None
