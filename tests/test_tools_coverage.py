"""tools/check_copy.py coverage rule: the site may only name data types COVERAGE.md lists as
supported or partial, except in sentences that say they aren't recorded or are planned."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import traceback

import check_copy as cc
import coverage_md as cov

# A fixed table, so the rule is tested independently of the current statuses in COVERAGE.md.
FIXTURE = """
| ID | Data | Tier | Recording | Status | Since | Limits | Notes | Cost | Site terms |
|---|---|---|---|---|---|---|---|---|---|
| a | Sculpt masks | 0 | default | supported | 1.0.0 | | | small | masks; face sets |
| b | Transforms | 1 | opt-in | partial | | only location | | small | object transforms |
| c | Materials | 2 | - | not covered | | | | - | materials; material |
| d | Shape keys | 4 | - | not covered | | | | - | shape keys |
| e | Keyframes | 4 | - | not covered | | | | - | keyframes |
"""


def findings_for(text):
    cc.findings.clear()
    cc._COVERAGE_SEEN.clear()
    cc.check_coverage("test.html <p>", text)
    return list(cc.findings)


def test_terms_come_from_coverage():
    terms = cc._coverage_terms(FIXTURE)
    assert "materials" in terms and "shape keys" in terms and "keyframes" in terms
    assert "masks" not in terms                    # a supported row
    assert "object transforms" not in terms        # a partial row
    live = cc._coverage_terms()                    # the real file parses, and planned rows are in it
    assert "shape keys" in live and "texture paint" in live
    print("PASS test_terms_come_from_coverage")


def test_parser_refuses_a_broken_row():
    try:
        cov.parse(FIXTURE + "| x | too few |\n")
    except ValueError:
        pass
    else:
        raise AssertionError("a row with the wrong number of columns was accepted")
    rows = cov.parse(FIXTURE)
    assert [r["id"] for r in rows] == ["a", "b", "c", "d", "e"] and rows[1]["status"] == "partial"
    print("PASS test_parser_refuses_a_broken_row")


def test_claims_and_negations():
    saved = cc.COVERAGE_TERMS
    cc.COVERAGE_TERMS = cc._coverage_terms(FIXTURE)
    try:
        assert findings_for("Hindsight records materials and shape keys.")
        assert findings_for("Scrub back through your keyframes.")
        assert not findings_for("Materials and shape keys aren't recorded yet.")
        assert not findings_for("Not recorded: modifiers, vertex groups and materials.")
        assert not findings_for("Texture paint is planned.")
        assert not findings_for("Sculpt strokes record masks and face sets.")
        assert not findings_for("Object transforms are recorded when you switch on Record everything.")
        # one bad sentence among good ones is still found
        assert findings_for("Masks are recorded. Materials too. Shape keys aren't recorded.")
    finally:
        cc.COVERAGE_TERMS = saved
    print("PASS test_claims_and_negations")


def main():
    ok = True
    for t in (test_terms_come_from_coverage, test_parser_refuses_a_broken_row, test_claims_and_negations):
        try:
            t()
        except Exception:
            ok = False
            print("FAIL", t.__name__)
            traceback.print_exc()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
