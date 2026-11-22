"""tools/check_copy.py scans the public text and nothing else. No bpy.

The website is gone (#5): docs/ now holds the ADRs, which are internal, so nothing under docs/ is scanned.
"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import traceback
from pathlib import Path

import check_copy as cc


def test_scans_the_public_text_only():
    scanned = [p.relative_to(cc.ROOT).as_posix() for p in cc.public_files()]
    expected = ["README.md", "CHANGELOG.md"]
    expected += sorted(p.relative_to(cc.ROOT).as_posix() for p in (cc.ROOT / ".github" / "release-notes").glob("*.md"))
    expected += sorted(p.relative_to(cc.ROOT).as_posix() for p in (cc.ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
    assert scanned == expected, scanned
    assert not any(p.startswith("docs/") for p in scanned)
    print("PASS test_scans_the_public_text_only")


def test_no_html_scanner_left():
    assert not hasattr(cc, "check_html") and not hasattr(cc, "_Html")
    assert "html.parser" not in Path(cc.__file__).read_text(encoding="utf-8")
    print("PASS test_no_html_scanner_left")


def main():
    ok = True
    for t in (test_scans_the_public_text_only, test_no_html_scanner_left):
        try:
            t()
        except Exception:
            ok = False
            print("FAIL", t.__name__)
            traceback.print_exc()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
