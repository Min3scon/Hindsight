"""Without its Rust engine Hindsight must say so clearly and record nothing (owner decision: the
engine is required). Run in a separate Blender process with hindsight_native blocked: registering
doesn't raise, the sidebar shows why, and no recording handler is installed."""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import subprocess
import tempfile
import bpy
import tests.harness as H

PROBE = r'''
import sys
sys.path.insert(0, ROOT)
sys.modules["hindsight_native"] = None
import bpy
import hindsight
hindsight.register()
from types import SimpleNamespace
from hindsight.bl import ui_panel


class FL:
    def __init__(s, log=None):
        s.__dict__["log"] = log if log is not None else []

    def __setattr__(s, k, v):
        s.__dict__[k] = v

    def __getattr__(s, n):
        def f(*a, **k):
            s.log.append((n, a, k))
            return FL(s.log)
        return f


lay = FL()
ui_panel.VIEW3D_PT_hindsight.draw(SimpleNamespace(layout=lay), bpy.context)
text = " ".join(k.get("text", "") for n, a, k in lay.log)
print("ENGINE_TEXT_SHOWN", " ".join(ui_panel.ENGINE_TEXT.split()[:6]) in " ".join(text.split()))
print("REASON_SHOWN", "could not load" in text)
names = [getattr(f, "__module__", "") for f in bpy.app.handlers.depsgraph_update_post]
print("RECORDING_HANDLER", any(n.startswith("hindsight") for n in names))
'''


def test_missing_engine_is_said_and_nothing_records():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    path = os.path.join(tempfile.mkdtemp(prefix="hs_engine_"), "probe.py")
    with open(path, "w") as f:
        f.write(f"ROOT = {root!r}\n" + PROBE)
    exe = bpy.app.binary_path
    cmd = [exe, "-b", "--factory-startup", "--python", path] if exe else [sys.executable, path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    out = r.stdout
    assert "ENGINE_TEXT_SHOWN True" in out, (out[-2000:], r.stderr[-2000:])
    assert "REASON_SHOWN True" in out, out[-2000:]
    assert "RECORDING_HANDLER False" in out, out[-2000:]


if __name__ == "__main__":
    H.run(test_missing_engine_is_said_and_nothing_records)
    H.finish()
