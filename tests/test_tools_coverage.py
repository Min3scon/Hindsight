"""tools/check_copy.py coverage rule: the site may only name data types COVERAGE.md supports, except
in sentences that say they aren't recorded or are planned."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import traceback

import check_copy as cc


def findings_for(text):
    cc.findings.clear()
    cc._COVERAGE_SEEN.clear()
    cc.check_coverage("test.html <p>", text)
    return list(cc.findings)


def test_terms_come_from_coverage():
    assert "materials" in cc.COVERAGE_TERMS and "shape keys" in cc.COVERAGE_TERMS
    assert "masks" not in cc.COVERAGE_TERMS          # a supported row
    assert "object transforms" not in cc.COVERAGE_TERMS   # an opt-in row
    print("PASS test_terms_come_from_coverage")


def test_claims_and_negations():
    assert findings_for("Hindsight records materials and shape keys.")
    assert findings_for("Scrub back through your keyframes.")
    assert not findings_for("Materials and shape keys aren't recorded yet.")
    assert not findings_for("Not recorded: modifiers, vertex groups and materials.")
    assert not findings_for("Texture paint is planned.")
    assert not findings_for("Sculpt strokes record masks and face sets.")
    assert not findings_for("Object transforms are recorded when you switch on Record everything.")
    # one bad sentence among good ones is still found
    assert findings_for("Masks are recorded. Materials too. Shape keys aren't recorded.")
    print("PASS test_claims_and_negations")


def main():
    ok = True
    for t in (test_terms_come_from_coverage, test_claims_and_negations):
        try:
            t()
        except Exception:
            ok = False
            print("FAIL", t.__name__)
            traceback.print_exc()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
