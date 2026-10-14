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

import json
import os
import struct
import tempfile
import zlib

MAGIC = b"HSJ1"

RTYPES = {"STEP": 1, "CURSOR": 2, "TRUNC": 3, "DROP": 4, "RESET": 5, "META": 6, "SAVE": 7}
RTYPE_NAMES = {v: k for k, v in RTYPES.items()}

_HEAD = struct.Struct("<4sBIQ")
_CRC = struct.Struct("<I")
_MAX_HEADER = 64 * 2 ** 20          # sanity bound for header JSON length
_CHUNK = 4 * 2 ** 20


def _scan(fh, limit: int, want_payload: bool):
    """Yield (start, end, type_code, header_bytes, payload_or_None) for each
    valid record in fh (positioned at 0), never reading past `limit` bytes."""
    pos = 0
    while True:
        if pos + _HEAD.size > limit:
            return
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
        crc = zlib.crc32(hbytes, zlib.crc32(raw))
        payload = None
        if want_payload:
            payload = fh.read(plen)
            if len(payload) < plen:
                return
            crc = zlib.crc32(payload, crc)
        else:
            left = plen
            while left:
                chunk = fh.read(min(left, _CHUNK))
                if not chunk:
                    return
                crc = zlib.crc32(chunk, crc)
                left -= len(chunk)
        tail = fh.read(_CRC.size)
        if len(tail) < _CRC.size or _CRC.unpack(tail)[0] != (crc & 0xFFFFFFFF):
            return
        yield pos, end, code, hbytes, payload
        pos = end


class Journal:
    """Append-only record file. See module docstring for the record format."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._size = 0
        if os.path.exists(self.path):
            file_size = os.path.getsize(self.path)
            with open(self.path, "rb") as fh:
                for _start, end, _c, _h, _p in _scan(fh, file_size, False):
                    self._size = end
            if file_size > self._size:
                os.truncate(self.path, self._size)
        self._fh = open(self.path, "ab")

    # -- writing ---------------------------------------------------------
    def append(self, rtype: str, header: dict, payload: bytes = b"") -> int:
        if self._fh is None:
            raise ValueError("journal is closed")
        code = RTYPES[rtype]
        hbytes = json.dumps(header if header is not None else {},
                            separators=(",", ":")).encode("utf-8")
        if not isinstance(payload, (bytes, bytearray)):
            payload = bytes(payload)
        prefix = _HEAD.pack(MAGIC, code, len(hbytes), len(payload)) + hbytes
        crc = zlib.crc32(payload, zlib.crc32(prefix)) & 0xFFFFFFFF
        start = self._size
        self._fh.write(prefix)
        if len(payload):
            self._fh.write(payload)
        self._fh.write(_CRC.pack(crc))
        self._size = start + len(prefix) + len(payload) + _CRC.size
        return start

    def flush(self, fsync: bool = True) -> None:
        if self._fh is None:
            return
        self._fh.flush()
        if fsync:
            try:
                os.fsync(self._fh.fileno())
            except OSError:
                pass

    def size(self) -> int:
        return self._size

    # -- reading ---------------------------------------------------------
    def read(self, upto: int | None = None) -> list:
        if self._fh is not None:
            self._fh.flush()
        limit = self._size if upto is None else max(0, min(int(upto), self._size))
        out = []
        try:
            with open(self.path, "rb") as fh:
                for _s, _e, code, hbytes, payload in _scan(fh, limit, True):
                    try:
                        header = json.loads(hbytes.decode("utf-8")) if hbytes else {}
                    except (ValueError, UnicodeDecodeError):
                        break
                    out.append((RTYPE_NAMES[code], header, payload))
        except OSError:
            pass
        return out

    # -- maintenance -----------------------------------------------------
    def truncate(self, size: int) -> None:
        size = max(0, min(int(size), self._size))
        if self._fh is not None:
            self._fh.close()
        os.truncate(self.path, size)
        self._size = size
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


def atomic_write(path: str, data: bytes) -> None:
    """Write `data` to `path` atomically: tmp file in the same directory,
    flush + fsync, then os.replace. The tmp file is removed on failure."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
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
