"""Facts about a code root and the interpreter, as one JSON object (used by bench/suite.py).

    <bpy python> bench/scenarios/facts.py CODE_ROOT
"""
import json
import os
import platform
import re
import sys

root = os.path.abspath(sys.argv[1])
sys.path.insert(0, root)
os.chdir(root)
out = {"python": platform.python_version(), "executable": sys.executable}
try:
    import bpy
    out["blender"] = bpy.app.version_string
except Exception as e:
    out["blender"] = f"unavailable: {e}"
try:
    with open(os.path.join(root, "hindsight", "blender_manifest.toml")) as fh:
        out["engine_version"] = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.M).group(1)
except Exception as e:
    out["engine_version"] = f"unknown: {e}"
try:
    from hindsight.core import native
    out["rust_engine_loaded"] = native.lib is not None
    out["native_api_expected"] = getattr(native, "API", None)
    if native.lib is not None:
        out["native_module"] = getattr(native.lib, "__file__", None)
        out["native_api"] = getattr(native.lib, "API", None)
        out["native_version"] = getattr(native.lib, "__version__", None)
    else:
        out["native_error"] = native.ERROR
    out["hindsight_native_env"] = os.environ.get("HINDSIGHT_NATIVE", "")
except Exception as e:
    out["rust_engine_loaded"] = False
    out["native_error"] = f"{type(e).__name__}: {e}"
sys.stdout.write("\n" + json.dumps(out) + "\n")
sys.stdout.flush()
os._exit(0)
