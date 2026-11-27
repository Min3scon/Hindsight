import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tomllib
import tests.harness as H
from hindsight.bl import notice

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


def test_manifest_tagline_has_no_beta():
    m = tomllib.load(open(os.path.join(ROOT, "hindsight", "blender_manifest.toml"), "rb"))
    assert "beta" not in m["tagline"].lower(), m["tagline"]


def test_no_beta_label_and_report_button():
    log = []
    notice.draw_note(_Rec(log))
    assert all("beta" not in e[1].lower() for e in log if e[0] == "label"), log
    ops = [e for e in log if e[0] == "op"]
    assert ops and ops[0][1] == "wm.url_open" and ops[0][2] == "Report a bug"
    assert ops[0][3].url == notice.ISSUES_URL and "issues" in notice.ISSUES_URL


def test_no_beta_text_in_the_ui_sources():
    for name in ("header.py", "ui_panel.py", "notice.py"):
        src = open(os.path.join(ROOT, "hindsight", "bl", name), encoding="utf-8").read()
        assert "beta" not in src.lower(), name


H.run(test_manifest_tagline_has_no_beta)
H.run(test_no_beta_label_and_report_button)
H.run(test_no_beta_text_in_the_ui_sources)
H.finish()
