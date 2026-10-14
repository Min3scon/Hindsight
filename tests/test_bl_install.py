import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Build the extension zip with Blender's own tool, validate it, and install + enable it in bpy.

Does NOT use the repo's `hindsight` package: the installed copy is what gets registered. Blender's user
folders are redirected to a temp dir so nothing is written to the real user config.
"""
import shutil
import subprocess
import tempfile
import traceback
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="hs_install_")
for var, sub in (("BLENDER_USER_RESOURCES", "res"), ("BLENDER_USER_CONFIG", "config"),
                 ("BLENDER_USER_SCRIPTS", "scripts"), ("BLENDER_USER_DATAFILES", "datafiles"),
                 ("BLENDER_USER_EXTENSIONS", "extensions")):
    os.environ[var] = os.path.join(TMP, sub)
    os.makedirs(os.environ[var], exist_ok=True)

import bpy  # noqa: E402

SCRIPTS = bpy.utils.system_resource("SCRIPTS")       # .../site-packages/bpy/4.2/scripts
BLENDER_EXT = os.path.join(SCRIPTS, "addons_core", "bl_pkg", "cli", "blender_ext.py")
TAGS = os.path.join(SCRIPTS, "modules", "_bpy_internal", "extensions", "tags.py")
PY = "/usr/bin/python3.11" if os.path.exists("/usr/bin/python3.11") else sys.executable
ZIP = os.path.join(TMP, "hindsight.zip")
OPS = ("step_back", "step_forward", "scrub", "goto", "toggle_global", "reset_history", "turn_off", "turn_on",
       "delete_past", "reset_all")
_failures = []


def run(fn):
    try:
        fn()
        print("PASS", fn.__name__)
    except Exception:
        traceback.print_exc()
        print("FAIL", fn.__name__)
        _failures.append(fn.__name__)


def _cli(*args):
    p = subprocess.run([PY, BLENDER_EXT, *args], capture_output=True, text=True, cwd=ROOT)
    print(f"$ blender_ext.py {' '.join(args)} -> exit {p.returncode}")
    if p.returncode != 0:
        print(p.stdout[-3000:], p.stderr[-3000:])
    return p


def test_build_and_validate():
    args = ["build", "--source-dir", os.path.join(ROOT, "hindsight"), "--output-filepath", ZIP]
    if os.path.exists(TAGS):
        args += ["--valid-tags", TAGS]
    assert _cli(*args).returncode == 0, "build failed"
    assert os.path.isfile(ZIP)
    vargs = ["validate", ZIP] + (["--valid-tags", TAGS] if os.path.exists(TAGS) else [])
    assert _cli(*vargs).returncode == 0, "validate failed"
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
    assert "blender_manifest.toml" in names, names[:20]
    assert "__init__.py" in names, names[:20]
    assert "bl/runtime.py" in names and "core/history.py" in names, "package modules missing"
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names), "build must exclude caches"
    assert not any(n.startswith("tests/") for n in names)
    print(f"zip: {len(names)} files, {os.path.getsize(ZIP):,} bytes")


def _ops_present():
    return [o for o in OPS if hasattr(bpy.ops.hindsight, o) and _op_exists(o)]


def _op_exists(name):
    try:
        getattr(bpy.ops.hindsight, name).get_rna_type()
        return True
    except Exception:
        return False


def _install_operator():
    """Real path: Blender's extension installer. Returns the module name or raises."""
    import addon_utils
    try:
        bpy.ops.preferences.addon_enable(module="bl_pkg")
    except Exception:
        pass
    repos = [r.module for r in bpy.context.preferences.extensions.repos]
    assert "user_default" in repos, f"no user_default repo: {repos}"
    if not hasattr(bpy.ops.extensions, "package_install_files"):
        raise RuntimeError("bpy.ops.extensions.package_install_files unavailable")
    res = bpy.ops.extensions.package_install_files(filepath=ZIP, repo="user_default", enable_on_install=True)
    assert res == {"FINISHED"}, res
    mod = "bl_ext.user_default.hindsight"
    inst = os.path.join(bpy.utils.user_resource("EXTENSIONS"), "user_default", "hindsight")
    assert os.path.isfile(os.path.join(inst, "blender_manifest.toml")), f"not installed in {inst}"
    assert mod in bpy.context.preferences.addons, "installed but not enabled"
    assert addon_utils.check(mod)[1], "addon_utils says not loaded"
    return mod


def _install_fallback():
    """Fallback: extract the zip and import it as a package, then register()."""
    import importlib
    dest = os.path.join(TMP, "extracted")
    with zipfile.ZipFile(ZIP) as z:
        z.extractall(os.path.join(dest, "hindsight_zip"))
    sys.path.insert(0, dest)
    mod = importlib.import_module("hindsight_zip")
    mod.register()
    return mod


def test_install_and_enable():
    path = "operator"
    try:
        mod = _install_operator()
    except Exception as e:
        print("package_install_files path failed:", repr(e))
        path = "fallback"
        mod = _install_fallback()
    print("INSTALL PATH:", "bpy.ops.extensions.package_install_files(repo='user_default', enable_on_install=True)"
          if path == "operator" else "fallback: extracted zip imported as package 'hindsight_zip', register()")
    present = _ops_present()
    assert set(present) == set(OPS), f"missing operators: {sorted(set(OPS) - set(present))}"
    assert hasattr(bpy.types, "VIEW3D_PT_hindsight"), "sidebar panel not registered"
    # prefs are registered under the extension's package name
    if path == "operator":
        assert bpy.context.preferences.addons[mod].preferences is not None, "no add-on preferences"
        bpy.ops.preferences.addon_disable(module=mod)
        assert mod not in bpy.context.preferences.addons
    else:
        mod.unregister()
    assert not _op_exists("step_back"), "operators still registered after disable/unregister"
    assert not hasattr(bpy.types, "VIEW3D_PT_hindsight"), "panel still registered after disable"


if __name__ == "__main__":
    run(test_build_and_validate)
    if not _failures:
        run(test_install_and_enable)
    if _failures:
        print("FAILED:", ", ".join(_failures))
    shutil.rmtree(TMP, ignore_errors=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if _failures else 0)
