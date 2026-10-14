#!/usr/bin/env python3
"""Subset the two variable fonts the site uses into site/assets/fonts/ (WOFF2).

    .tools/venv/Scripts/python tools/subset_fonts.py     (needs fonttools and brotli)

Sources come from the npm devDependencies @fontsource-variable/fraunces and @fontsource-variable/inter
(both SIL Open Font License 1.1). Only Basic Latin and a few typographic marks are kept.
"""
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "assets" / "fonts"
UNICODES = "U+0020-007E,U+00A0,U+00A9,U+00B7,U+00D7,U+2019,U+201C,U+201D"
FONTS = [
    (ROOT / "node_modules/@fontsource-variable/fraunces/files/fraunces-latin-opsz-normal.woff2",
     OUT / "fraunces-var.woff2", "kern,liga,calt,lnum,pnum"),
    (ROOT / "node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2",
     OUT / "inter-var.woff2", "kern,liga,calt,tnum,case,cv11,ss01"),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for src, dst, features in FONTS:
        args = [str(src), f"--unicodes={UNICODES}", f"--layout-features={features}", "--flavor=woff2",
                f"--output-file={dst}", "--no-hinting", "--desubroutinize"]
        subset.main(args)
        print(f"{dst.name}: {src.stat().st_size // 1024} KB -> {dst.stat().st_size // 1024} KB")
    for lic in ("fraunces", "inter"):
        p = ROOT / f"node_modules/@fontsource-variable/{lic}/LICENSE"
        if p.is_file():
            (OUT / f"{lic}-OFL.txt").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    main()
