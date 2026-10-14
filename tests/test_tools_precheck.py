import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
"""Tests for tools/precommit_check.py (the pre-commit large-file / secret / personal-path gate). No bpy.

Sample bad strings are assembled at runtime so this file itself passes the check it tests.
"""
import re
import traceback

from tools import precommit_check as pc

SEP = "\\"


def j(*parts):
    return "".join(parts)


def problems(text, path="README.md", private=()):
    return pc.scan_text(path, text, list(private))


def test_windows_user_folder_flagged():
    assert problems(j("see C:", SEP, "Users", SEP, "someone", SEP, "file.txt"))
    assert problems(j("cd /c/", "Users/someone/x"))
    print("PASS test_windows_user_folder_flagged")


def test_home_and_appdata_flagged():
    assert problems(j("run /", "home/user/venvs/bpy42/bin/python"))
    assert problems(j("`/", "Users/someone/project`"))
    assert problems(j("AppData", SEP, "Local", SEP, "x"))
    assert problems(j("/tmp/", "claude/abc"))
    print("PASS test_home_and_appdata_flagged")


def test_drive_letters():
    ws = j("D:", SEP, "Hindsight", SEP, "tests")
    assert problems(ws, path="README.md"), "public files may not name the workspace"
    assert not problems(ws, path="HANDOFF.md"), "internal docs may name the workspace"
    assert not problems(j("D:", SEP, "Hindsight-backup"), path="ROLLBACK.md")
    assert problems(j("E:", SEP, "stuff"), path="HANDOFF.md"), "other drive paths are never allowed"
    assert problems(ws, path="docs/HANDOFF.md"), "only the root internal docs are exempt"
    print("PASS test_drive_letters")


def test_urls_not_flagged():
    for s in ["https://min3scon.github.io/Hindsight/", "http://127.0.0.1:8765/x", "file:///x",
              "a ratio of 3:4 and time 12:30", "re.compile(r'\\d+')"]:
        assert not problems(s), s
    print("PASS test_urls_not_flagged")


def test_secrets_flagged():
    assert problems(j("token = ", "ghp_", "A" * 36))
    assert problems(j("github_", "pat_", "11ABCDEFGHIJKLMNOPQRSTUV_abc"))
    assert problems(j("AKIA", "ABCDEFGHIJKLMNOP"))
    assert problems(j("-----BEGIN ", "RSA PRIVATE KEY-----"))
    assert problems(j('password = "', "hunter2hunter2", '"'))
    assert not problems("id-token: write")
    print("PASS test_secrets_flagged")


def test_private_patterns_and_allow_marker():
    priv = [("private pattern", re.compile("zz" + "secretname", re.IGNORECASE))]
    assert problems(j("by ZZ", "SecretName"), private=priv)
    assert not problems(j("by zz", "secretname  # precheck: ", "allow"), private=priv)
    print("PASS test_private_patterns_and_allow_marker")


def test_forbidden_dirs():
    for p in [".venv/lib/x.py", "node_modules/a/b.js", "test_models/a.blend", "my_models_copies/a.blend",
              "test_screens_raw/a.png", ".blender_profiles/x", "build/x", "dist/hindsight.zip",
              "tests/site/node_modules/x.js", ".tools/blender/blender.exe", "logs/run.log"]:
        assert pc.in_forbidden_dir(p), p
    for p in ["scripts/build_zip.py", "docs/download/hindsight.zip", "tests/test_bl_install.py",
              "tools/precommit_check.py"]:
        assert not pc.in_forbidden_dir(p), p
    print("PASS test_forbidden_dirs")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.stdout.flush()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
