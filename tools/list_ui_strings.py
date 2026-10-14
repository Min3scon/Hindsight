#!/usr/bin/env python3
"""Print every user-facing string in the add-on with its location, for an editorial read.

    python tools/list_ui_strings.py
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for path in sorted((ROOT / "hindsight").rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                if not (isinstance(node, ast.ClassDef) and re.search(r"_(OT|MT|PT|UL)_", node.name)):
                    docs.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            s = node.value
            if re.search(r"[A-Za-z]{2,}[ ,.][^|]*[A-Za-z]", s) and not re.fullmatch(r"[\w.|:/%{}()\[\]-]+", s):
                print(f"{path.relative_to(ROOT)}:{node.lineno}: {s}")
