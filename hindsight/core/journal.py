"""Append-only crash journal + atomic file helpers (pure Python, no bpy).

Record layout (little endian):

    MAGIC(4s) | type(u8) | header_len(u32) | payload_len(u64)
    | header JSON (utf-8) | payload | crc32(u32 over everything before it)

Reading stops silently at the first truncated or corrupt record, so a crash
in the middle of an append never loses the records written before it. On
open, a trailing partial/corrupt record is cut off so new appends follow the
last valid record.
"""
from __future__ import annotations

import errno
import json
import os
import struct
import tempfile
from .crc import crc32

MAGIC = b"HSJ1"

RTYPES = {"STEP": 1, "CURSOR": 2, "TRUNC": 3, "DROP": 4, "RESET": 5, "META": 6, "SAVE": 7, "META_EVERYTHING": 8}
RTYPE_NAMES = {v: k for k, v in RTYPES.items()}

_HEAD = struct.Struct("<4sBIQ")
_CRC = struct.Struct("<I")
_MAX_HEADER = 64 * 2 ** 20          # sanity bound for header JSON length
_CHUNK = 4 * 2 ** 20
# R065: reading a journal goes through a 1 MB buffer: a record's parts (head, header, payload, checksum)
# are read from memory instead of one read of the file each (a payload of a few KB bypassed the 8 KB one)
_READ_BUF = 2 ** 20
# fsync errors that mean "this file system has nothing to sync" (some network and FUSE file
# systems), not "the data didn't reach the disk"
_NO_SYNC = {errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL), getattr(errno, "EOPNOTSUPP", errno.EINVAL)}


def _scan(fh, limit: int, want_payload: bool):
    """Yield (start, end, type_code, header_bytes, payload_or_None) for each
    valid record in fh (positioned at 0), never reading past `limit` bytes."""
    pos = 0
    at = None                     # where fh is (records are read in order: no seek between them)
    while True:
        if pos + _HEAD.size > limit:
            return
        if pos != at:
            fh.seek(pos)
        raw = fh.read(_HEAD.size)
        if len(raw) < _HEAD.size:
            return
        magic, code, hlen, plen = _HEAD.unpack(raw)
        if magic != MAGIC or code not in RTYPE_NAMES or hlen > _MAX_HEADER:
            return
        end = pos + _HEAD.size + hlen + plen + _CRC.size
        if end > limit:
            return
        hbytes = fh.read(hlen)
        if len(hbytes) < hlen:
            return
        crc = crc32(hbytes, crc32(raw))
        payload = None
        if want_payload:
            payload = fh.read(plen)
            if len(payload) < plen:
                return
            crc = crc32(payload, crc)
        else:
            left = plen
            while left:
                chunk = fh.read(min(left, _CHUNK))
                if not chunk:
                    return
                crc = crc32(chunk, crc)
                left -= len(chunk)
        tail = fh.read(_CRC.size)
        if len(tail) < _CRC.size or _CRC.unpack(tail)[0] != (crc & 0xFFFFFFFF):
            return
        yield pos, end, code, hbytes, payload
        pos = at = end


def _records(scan, out: list):
    """Pass _scan's records through, putting (type, header, payload) into out up to the first header
    that isn't JSON (none after it: what read() has always returned)."""
    ok = True
    for rec in scan:
        if ok:
            _s, _e, code, hbytes, payload = rec
            try:
                header = json.loads(hbytes.decode("utf-8")) if hbytes else {}
            except (ValueError, UnicodeDecodeError):
                ok = False
            else:
                out.append((RTYPE_NAMES[code], header, payload))
        yield rec


class Journal:
    """Append-only record file. See module docstring for the record format."""

    def __init__(self, path: str, keep: bool = False):
        """keep (R065): the caller reads the journal right away (crash recovery). The scan that finds
        where the good records end reads them too, and the first read() gets them from it instead of
        reading the file a second time (if nothing was written in between)."""
        self.path = os.path.abspath(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._size = 0
        self._kept = None                # (size, records) from that scan, until read() takes them
        if os.path.exists(self.path):
            file_size = os.path.getsize(self.path)
            recs = [] if keep else None
            with open(self.path, "rb", buffering=_READ_BUF) as fh:
                scan = _scan(fh, file_size, keep)
                for _start, end, _c, _h, _p in (_records(scan, recs) if keep else scan):
                    self._size = end
            if file_size > self._size:
                os.truncate(self.path, self._size)
            if keep:
                self._kept = (self._size, recs)
        self._good = self._size          # size up to which every record was written out whole
        self._fh = open(self.path, "ab")

    def _cut_back(self, size: int) -> None:
        """After a failed write (disk full, I/O error): drop whatever part of a record reached the
        file, so the records after it are not hidden behind a broken one. `size` is where the
        failed record started; if the records buffered before it can't be written out either, the
        file goes back to the last good flush."""
        try:
            self._fh.close()             # writes out what is still buffered, or fails again
        except OSError:
            size = self._good
        try:
            os.truncate(self.path, size)
        except OSError:
            pass
        self._size = size
        self._good = min(self._good, size)
        self._fh = open(self.path, "ab")

    def append(self, rtype: str, header: dict, payload: bytes = b"") -> int:
        if self._fh is None:
            raise ValueError("journal is closed")
        self._kept = None
        code = RTYPES[rtype]
        hbytes = json.dumps(header if header is not None else {},
                            separators=(",", ":")).encode("utf-8")
        if not isinstance(payload, (bytes, bytearray)):
            payload = bytes(payload)
        prefix = _HEAD.pack(MAGIC, code, len(hbytes), len(payload)) + hbytes
        crc = crc32(payload, crc32(prefix)) & 0xFFFFFFFF
        start = self._size
        try:
            self._fh.write(prefix)
            if len(payload):
                self._fh.write(payload)
            self._fh.write(_CRC.pack(crc))
        except OSError:
            self._cut_back(start)
            raise
        self._size = start + len(prefix) + len(payload) + _CRC.size
        return start

    def flush(self, fsync: bool = True) -> None:
        """Write the appended records out. Raises OSError if they could not be written (the records
        since the last good flush are dropped from the file again) or, with fsync, if the disk
        did not confirm them: a save point is only durable when this returns."""
        if self._fh is None:
            return
        try:
            self._fh.flush()
        except OSError:
            self._cut_back(self._good)
            raise
        self._good = self._size
        if fsync:
            try:
                os.fsync(self._fh.fileno())
            except OSError as e:
                if e.errno not in _NO_SYNC:
                    raise

    def size(self) -> int:
        return self._size

    # -- reading ---------------------------------------------------------
    def read(self, upto: int | None = None) -> list:
        kept, self._kept = self._kept, None
        if kept is not None and upto is None and kept[0] == self._size:
            return kept[1]
        if self._fh is not None:
            self._fh.flush()
        limit = self._size if upto is None else max(0, min(int(upto), self._size))
        out = []
        try:
            with open(self.path, "rb", buffering=_READ_BUF) as fh:
                for _rec in _records(_scan(fh, limit, True), out):
                    pass
        except OSError:
            pass
        return out

    # -- maintenance -----------------------------------------------------
    def truncate(self, size: int) -> None:
        self._kept = None
        size = max(0, min(int(size), self._size))
        if self._fh is not None:
            self._fh.close()
        os.truncate(self.path, size)
        self._size = size
        self._good = size
        self._fh = open(self.path, "ab")

    def close(self) -> None:
        if self._fh is None:
            return
        try:
            self.flush(True)
        finally:
            self._fh.close()
            self._fh = None

    @property
    def closed(self) -> bool:
        return self._fh is None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            if self._fh is not None:
                self._fh.close()
        except Exception:
            pass


def atomic_write(path: str, data) -> None:
    """Write `data` (bytes, or a list of buffers written one after another) to `path` atomically:
    tmp file in the same directory, flush + fsync, then os.replace. The tmp file is removed on
    failure."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb", buffering=1 << 20) as fh:   # a list of parts: few large writes
            if isinstance(data, list):
                fh.writelines(data)
            else:
                fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if hasattr(os, "O_DIRECTORY"):
        try:
            dfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass


def atomic_write_json(path: str, obj) -> None:
    atomic_write(path, json.dumps(obj, indent=1, sort_keys=True).encode("utf-8"))
