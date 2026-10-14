"""Read COVERAGE.md: one dict per row, keyed by the column headers (lower case)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUSES = ("supported", "partial", "not covered")
CLAIMABLE = ("supported", "partial")          # statuses the site may name as recorded


def parse(text: str) -> list:
    rows, header = [], None
    for line in text.splitlines():
        if not line.startswith("|"):
            header = None if not line.strip() else header
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cols]
            continue
        if line.startswith("|---"):
            continue
        if len(cols) != len(header):
            raise ValueError(f"COVERAGE.md row has {len(cols)} columns, header has {len(header)}: {line}")
        rows.append(dict(zip(header, cols)))
    return rows


def rows(path=None) -> list:
    p = Path(path) if path else ROOT / "COVERAGE.md"
    return parse(p.read_text(encoding="utf-8")) if p.is_file() else []


def terms(row) -> list:
    return [t.strip().lower() for t in row.get("site terms", "").split(";") if t.strip()]
