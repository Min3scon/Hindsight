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


def enforce_tiers(histories: list, settings, packer: Packer | None = None) -> int:
    """Pack every entry older than the newest `settings.raw_steps` of each history. Returns by how
    many bytes (Entry.nbytes) the entries packed here got smaller (with a packer: 0, it packs later)."""
    raw = max(0, int(settings.raw_steps))
    codec = settings.compression
    saved = 0
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
                b = e.nbytes()
                e.pack(codec)
                saved += b - e.nbytes()
    return saved


def total_bytes(histories: list) -> int:
    return int(sum(e.nbytes() for h in histories for e in h.entries))


def _drop(h, n: int) -> int:
    if n <= 0:
        return 0
    r = h.drop_oldest(n)
    return n if r is None else int(r)


EVICT_TO = 0.95     # past the memory limit, drop down to this share of it: the next full pass then
                    # comes 5% of the limit later instead of on every stroke


def enforce_caps(histories: list, settings, stats: dict | None = None) -> int:
    """Apply max_steps per history, then the global memory limit (oldest seq first).

    A step at or past a history's cursor is never dropped: the mesh shows the state at the cursor,
    so only steps before it can go without the mesh and the history coming apart.
    Returns the number of entries dropped. If `stats` is given it receives "memory_dropped"
    (steps dropped because of the memory limit), "total" (bytes after dropping) and "stuck" (still
    over the limit with nothing left that may be dropped: every history's cursor is 0).
    """
    dropped = 0
    max_steps = max(1, int(settings.max_steps))
    for h in histories:
        over = min(len(h.entries) - max_steps, int(h.cursor))
        if over > 0:
            dropped += _drop(h, over)

    limit = int(settings.memory_limit_mb) * (1 << 20)
    total = total_bytes(histories)
    if stats is not None:
        stats["memory_dropped"] = 0
        stats["total"] = total
        stats["stuck"] = False
    if total <= limit:
        return dropped
    # plan: merge histories by seq of their oldest remaining entry; count drops per history
    plan = [0] * len(histories)
    heap = [(h.entries[0].meta.get("seq", 0), i) for i, h in enumerate(histories) if h.entries and h.cursor > 0]
    heapq.heapify(heap)
    target = int(limit * EVICT_TO)
    while total > target and heap:
        _seq, i = heapq.heappop(heap)
        entries = histories[i].entries
        j = plan[i]
        total -= entries[j].nbytes()
        plan[i] = j + 1
        if j + 1 < min(len(entries), int(histories[i].cursor)):
            heapq.heappush(heap, (entries[j + 1].meta.get("seq", 0), i))
    for i, n in enumerate(plan):
        dropped += _drop(histories[i], n)
    if stats is not None:
        stats["memory_dropped"] = sum(plan)
        stats["total"] = total
        stats["stuck"] = total > target         # the loop ran out of steps that may go
    return dropped


# ----------------------------------------------------------------------------- the whole pass
def _sig(h) -> tuple:
    """Changes when a history's steps or cursor change (not when its steps are packed)."""
    e = h.entries
    return (id(h), id(e), len(e), int(h.cursor), id(e[0]) if e else 0, id(e[-1]) if e else 0)


def _packer_key(packer):
    """Changes whenever the packer has changed the size of a step."""
    return None if packer is None else (packer.packed, packer.recompressed, packer.errors)


def _settings_key(settings) -> tuple:
    return (int(settings.max_steps), int(settings.memory_limit_mb), int(settings.raw_steps),
            settings.compression)


class CapsMemo:
    """What a pass of `enforce` that couldn't get under the memory limit found (S4-3).

    When everything that may be dropped is gone (every cursor at 0, D64) and the history is still
    over the limit, every new step brings the next pass, and that pass used to read the size of
    every step of every history. While nothing but one history has changed since (same steps,
    same cursors, the packer hasn't changed any step's size, same settings), the others' size is
    known: the next pass only needs to look at that one history, and does exactly what the whole
    pass would do. Anything else changed: the whole pass runs again."""
    __slots__ = ("state",)

    def __init__(self):
        self.state = None

    def clear(self) -> None:
        self.state = None

    def remember(self, histories, total: int, pkey, skey) -> None:
        self.state = {"sigs": {h.uid: _sig(h) for h in histories},
                      "bytes": {h.uid: sum(e.nbytes() for e in h.entries) for h in histories},
                      "total": int(total), "packer": pkey, "settings": skey}

    def usable(self, histories, changed, pkey, skey) -> bool:
        st = self.state
        if st is None or changed is None or st["packer"] != pkey or st["settings"] != skey:
            return False
        sigs = st["sigs"]
        if len(sigs) != len(histories) or changed.uid not in sigs:
            return False
        for h in histories:
            if h is not changed and sigs.get(h.uid) != _sig(h):
                return False
        return True


def enforce(histories: list, settings, packer: Packer | None = None, stats: dict | None = None,
            memo: CapsMemo | None = None, changed=None, on_caps=None) -> list:
    """enforce_caps, then enforce_tiers. Returns [(history, steps dropped)] for the histories that
    lost steps, in the order of `histories`; on_caps(that list, stats) is called with it before
    the packing starts (so the caller hears of every drop even if packing fails). `stats` receives
    "memory_dropped" and "total" (bytes after the pass). With `memo` (kept by the caller between
    passes) and `changed` (the one history that may have changed since the last pass), a pass
    that can only drop steps of `changed` looks at nothing else (CapsMemo)."""
    stats = {} if stats is None else stats
    pkey, skey = _packer_key(packer), _settings_key(settings)
    if memo is not None and memo.usable(histories, changed, pkey, skey):
        return _enforce_one(histories, changed, settings, packer, stats, memo, pkey, skey, on_caps)
    if memo is not None:
        memo.clear()
    before = [len(h.entries) for h in histories]
    enforce_caps(histories, settings, stats)
    drops = [(h, n - len(h.entries)) for h, n in zip(histories, before) if n != len(h.entries)]
    if on_caps is not None:
        on_caps(drops, stats)
    saved = enforce_tiers(histories, settings, packer)
    stats["total"] = stats["total"] - saved
    if memo is not None:
        if stats["stuck"]:
            memo.remember(histories, stats["total"], pkey, skey)
            stats["total"] = memo.state["total"]
    return drops


def _enforce_one(histories, h, settings, packer, stats, memo, pkey, skey, on_caps=None) -> list:
    """enforce for the case CapsMemo describes: the other histories have nothing that may go."""
    st = memo.state
    others = st["total"] - st["bytes"][h.uid]
    n0 = len(h.entries)
    max_steps = max(1, int(settings.max_steps))
    over = min(len(h.entries) - max_steps, int(h.cursor))
    if over > 0:
        _drop(h, over)
    hb = sum(e.nbytes() for e in h.entries)
    total = others + hb
    limit = int(settings.memory_limit_mb) * (1 << 20)
    stats["memory_dropped"] = 0
    stuck = False
    if total > limit:
        target = int(limit * EVICT_TO)
        entries = h.entries
        cap = min(len(entries), int(h.cursor))
        j = 0
        while total > target and j < cap:
            total -= entries[j].nbytes()
            j += 1
        _drop(h, j)
        stats["memory_dropped"] = j
        stuck = total > target
    n = n0 - len(h.entries)
    drops = [(h, n)] if n else []
    if on_caps is not None:
        on_caps(drops, stats)
    total -= enforce_tiers([h], settings, packer)
    stats["total"] = total
    if stuck:
        st["sigs"][h.uid] = _sig(h)
        st["bytes"][h.uid] = total - others
        st["total"] = total
    else:
        memo.clear()
    return drops
