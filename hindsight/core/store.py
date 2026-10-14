"""Storage tiering and caps. Pure Python + numpy, no bpy.

Histories are duck-typed: h.entries (list[Entry]), h.cursor (int), h.drop_oldest(n) -> int.
Recent entries stay raw (fast undo); older ones are packed (compressed) by a background
worker thread; caps drop the oldest entries. When the worker has nothing else to do it recompresses
packed steps with a stronger setting, checked byte for byte, a few at a time.
"""
from __future__ import annotations

import collections
import heapq
import queue
import threading
import time
import weakref

from .step import recompress, step_to_bytes

_STOP = object()


QUIET_S = 0.3         # pack only once no new step has arrived for this long (the main thread is
                      # busy right after a commit; packing then would compete with it for Python)
IDLE_S = 1.0          # recompress only after the packer has been idle this long
DUTY = 0.25           # and then use at most this share of one core (sleep in between)
STRONG_MAX_BYTES = 8 << 20   # larger steps are left at the normal setting (too slow to redo)


class Packer:
    """Background thread that packs entries off the main thread.

    The worker only touches numpy + codec. The swap is atomic for readers:
    entry.blob is set first, then entry.step = None, so Entry.load() always finds one.
    Recompression replaces entry.blob with a smaller blob that decodes to the same bytes.
    """

    def __init__(self, recompress_idle: bool = True):
        self._q = queue.SimpleQueue()
        self._cond = threading.Condition()
        self._pending = 0
        self._inflight = set()           # id(entry) queued and not yet processed
        self._stopped = False
        self.errors = 0
        self.last_error = None
        self.packed = 0
        self.recompressed = 0
        self.saved_bytes = 0
        self._strong = collections.deque()   # weak refs to packed entries not yet recompressed
        self._recompress = recompress_idle
        self._wake = threading.Event()        # set on stop, ends a pause early
        self._last_submit = 0.0
        self._hurry = 0                       # drains waiting: pack right away
        self._thread = threading.Thread(target=self._run, name="HindsightPacker", daemon=True)
        self._thread.start()

    def queue_recompress(self, entries) -> None:
        """Offer packed entries (for example after loading a file) for idle-time recompression."""
        if not self._recompress:
            return
        for e in entries:
            if e.blob is not None:
                self._strong.append(weakref.ref(e))

    # main thread -----------------------------------------------------------
    def submit(self, entry, codec: str) -> None:
        if entry.step is None:
            return
        if self._stopped or not self._thread.is_alive():
            entry.pack(codec)
            return
        with self._cond:
            if id(entry) in self._inflight:
                return
            self._inflight.add(id(entry))
            self._pending += 1
        self._last_submit = time.monotonic()
        self._q.put((entry, codec))

    def pending(self) -> int:
        with self._cond:
            return self._pending

    def drain(self, timeout: float | None = None) -> None:
        with self._cond:
            self._hurry += 1
            try:
                self._cond.wait_for(lambda: self._pending == 0, timeout)
            finally:
                self._hurry -= 1

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._wake.set()
        self._q.put(_STOP)
        self._thread.join(timeout=10.0)

    # worker thread ---------------------------------------------------------
    def _run(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=IDLE_S if self._strong else None)
            except queue.Empty:
                self._recompress_one()
                continue
            if item is _STOP:
                return
            entry, codec = item
            while not self._stopped and not self._hurry and time.monotonic() - self._last_submit < QUIET_S:
                time.sleep(0.05)
            try:
                step = entry.step
                if step is not None:
                    blob = step_to_bytes(step, codec)
                    if entry.step is step:      # not replaced/packed meanwhile
                        entry.blob = blob
                        entry.step = None
                        self.packed += 1
                        if self._recompress:
                            self._strong.append(weakref.ref(entry))
            except Exception as e:            # keep the worker alive; entry stays raw
                self.errors += 1
                self.last_error = repr(e)
            finally:
                with self._cond:
                    self._inflight.discard(id(entry))
                    self._pending -= 1
                    if self._pending == 0:
                        self._cond.notify_all()

    def _recompress_one(self) -> None:
        """Idle work: recompress one packed step (oldest first), then rest in proportion."""
        while self._strong and not self._stopped:
            entry = self._strong.popleft()()
            blob = getattr(entry, "blob", None)
            if blob is None or len(blob) > STRONG_MAX_BYTES:
                continue
            t = time.perf_counter()
            try:
                new = recompress(blob)
            except Exception as e:            # damaged or unexpected: leave the step as it is
                self.errors += 1
                self.last_error = repr(e)
                return
            if new is not None and entry.blob is blob:
                entry.blob = new
                self.recompressed += 1
                self.saved_bytes += len(blob) - len(new)
            dt = time.perf_counter() - t
            if dt > 0:
                self._wake.wait(min(1.0, dt * (1.0 - DUTY) / DUTY))
            return


def enforce_tiers(histories: list, settings, packer: Packer | None = None) -> None:
    """Pack every entry older than the newest `settings.raw_steps` of each history."""
    raw = max(0, int(settings.raw_steps))
    codec = settings.compression
    for h in histories:
        entries = h.entries
        n = len(entries) - raw
        for i in range(n):
            e = entries[i]
            if e.step is None:
                continue
            if packer is not None:
                packer.submit(e, codec)
            else:
                e.pack(codec)


def total_bytes(histories: list) -> int:
    return int(sum(e.nbytes() for h in histories for e in h.entries))


def _drop(h, n: int) -> int:
    if n <= 0:
        return 0
    r = h.drop_oldest(n)
    return n if r is None else int(r)


def enforce_caps(histories: list, settings, stats: dict | None = None) -> int:
    """Apply max_steps per history, then the global memory limit (oldest seq first).

    Returns the number of entries dropped. If `stats` is given it receives "memory_dropped"
    (steps dropped because of the memory limit) and "total" (bytes after dropping).
    """
    dropped = 0
    max_steps = max(1, int(settings.max_steps))
    for h in histories:
        over = len(h.entries) - max_steps
        if over > 0:
            dropped += _drop(h, over)

    limit = int(settings.memory_limit_mb) * (1 << 20)
    total = total_bytes(histories)
    if stats is not None:
        stats["memory_dropped"] = 0
        stats["total"] = total
    if total <= limit:
        return dropped
    # plan: merge histories by seq of their oldest remaining entry; count drops per history
    plan = [0] * len(histories)
    heap = [(h.entries[0].meta.get("seq", 0), i) for i, h in enumerate(histories) if h.entries]
    heapq.heapify(heap)
    while total > limit and heap:
        _seq, i = heapq.heappop(heap)
        entries = histories[i].entries
        j = plan[i]
        total -= entries[j].nbytes()
        plan[i] = j + 1
        if j + 1 < len(entries):
            heapq.heappush(heap, (entries[j + 1].meta.get("seq", 0), i))
    for i, n in enumerate(plan):
        dropped += _drop(histories[i], n)
    if stats is not None:
        stats["memory_dropped"] = sum(plan)
        stats["total"] = total
    return dropped
