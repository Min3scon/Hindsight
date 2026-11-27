"""REFERENCE COPY (tests only; nothing under hindsight/ may import it). The Python that hindsight/core/step.py still ran
until #131 (24.3) moved it into the engine: the JSON of a step's extra and data records (`_json_default`, `_dumps`,
`_enc_value`, `_dec_value`, `records_to_bytes`, `records_from_bytes`, `_extra`) and the header dict of `read_header`.
The JSON functions were unchanged since 5a909c5, so they are the ones in that copy (tests/reference/step.py); `_extra`
and `read_header` are step.py's at 2606c17, unchanged but for their imports.
"""
from __future__ import annotations

import json

from hindsight.core import native as _native
from .step import _dec_value, _dumps, _enc_value, _json_default, records_from_bytes, records_to_bytes

__all__ = ["_json_default", "_dumps", "_enc_value", "_dec_value", "records_to_bytes", "records_from_bytes", "_extra",
           "read_header"]

_N = _native.ENGINE
CorruptStep = _N.CorruptStep


def _extra(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        e = json.loads(raw.decode("utf-8"))
        return dict(e)
    except (ValueError, TypeError) as e:
        raise CorruptStep(f"step blob: bad header ({e})") from e


def read_header(b) -> dict:
    """Step metadata without decompressing the body."""
    r = _N.step_info(b)
    if r[0] == 1:
        return json.loads(bytes(r[1]).decode("utf-8"))
    _v, kind, seq, t, label, mode, target, extra, nd, before, after, snaps, raw, quick, records = r
    meta = {"seq": seq, "label": label, "mode": mode, "target": target, "time": t, "kind": kind,
            "extra": _extra(extra)}
    return {"v": 3, "meta": meta, "deltas": nd, "before": before, "after": after,
            "snaps": None if snaps is None else [[u, n] for u, n in snaps], "raw": raw, "quick": quick,
            "records": records}
