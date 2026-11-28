"""#81: the sculpt refresh toggle is read by the one preference reader (prefs.value), so a test's FORCE entry reaches it.
Run: <bpy python> tests/test_bl_refresh_toggle.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.harness as H  # noqa: E402
from hindsight.bl import prefs, runtime  # noqa: E402


def test_refresh_toggle_uses_shared_reader():
    try:
        prefs.FORCE["sculpt_refresh_toggle"] = True
        assert runtime._refresh_toggle() is True, "FORCE reaches _refresh_toggle"
        prefs.FORCE["sculpt_refresh_toggle"] = False
        assert runtime._refresh_toggle() is False
    finally:
        prefs.FORCE.pop("sculpt_refresh_toggle", None)


H.run(test_refresh_toggle_uses_shared_reader)
H.finish()
