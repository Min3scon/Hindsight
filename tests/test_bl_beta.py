import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tomllib
import tests.harness as H
from hindsight.bl import beta

ROOT = os.path.join(os.path.dirname(__file__), "..")


class _Rec:
    """Minimal stand-in for a Blender UILayout that records labels and operators."""
    def __init__(self, log):
        self.log = log

    def row(self, **kw):
        return _Rec(self.log)

    def column(self, **kw):
        return _Rec(self.log)

    def label(self, text="", **kw):
        self.log.append(("label", text))

    def operator(self, idname, text="", **kw):
        class Op:
            pass
        op = Op()
        self.log.append(("op", idname, text, op))
        return op


def test_manifest_is_beta():
    m = tomllib.load(open(os.path.join(ROOT, "hindsight", "blender_manifest.toml"), "rb"))
    assert "-beta" in m["version"], m["version"]
    assert "(beta)" in m["tagline"]


def test_beta_tag_and_report_button():
    log = []
    beta.draw_header_tag(_Rec(log))
    assert ("label", "BETA") in log
    log.clear()
    beta.draw_note(_Rec(log))
    assert any(e[0] == "label" and "report bugs" in e[1] for e in log)
    ops = [e for e in log if e[0] == "op"]
    assert ops and ops[0][1] == "wm.url_open" and ops[0][2] == "Report a bug"
    assert ops[0][3].url == beta.ISSUES_URL and "issues" in beta.ISSUES_URL


H.run(test_manifest_is_beta)
H.run(test_beta_tag_and_report_button)
H.finish()
