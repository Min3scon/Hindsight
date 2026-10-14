#!/usr/bin/env python3
"""Build the website from site/ into docs/ (what GitHub Pages publishes).

    python site/build.py            # after scripts/build_zip.py has put docs/download/hindsight.zip in place

Pages live in site/pages/*.html with a small front matter block and {{placeholders}}; facts that
change (version, zip size, test results, benchmarks, known issues) come from the built zip and
site/data.json, never typed into a page. CSS is inlined; scripts are deferred. Standard library only.
"""
import html
import json
import re
import shutil
import sys
import tomllib
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = ROOT / "docs"
BASE = "https://min3scon.github.io/Hindsight/"
BRANCH = "claude/lucid-brahmagupta-9gsdsn"
REPO = "https://github.com/Min3scon/Hindsight"
ISSUES = REPO + "/issues/new/choose"

ICONS = {
    "download": '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 3v10m0 0 4-4m-4 4-4-4M4 16h12" '
                'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "back": '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M10 3 5 8l5 5" stroke="currentColor" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "fwd": '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m6 3 5 5-5 5" stroke="currentColor" '
           'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "play": '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9l7.5-4.5z" fill="currentColor"/></svg>',
    "close": '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" '
             'stroke-width="1.8" stroke-linecap="round"/></svg>',
    "reset": '<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3.5 8a4.5 4.5 0 1 0 1.3-3.2M3.5 3v2.5H6" '
             'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
}
LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="8" fill="#1d1d22"/>'
        '<circle cx="8.5" cy="16" r="3.3" fill="none" stroke="#ff9b42" stroke-width="1.8"/>'
        '<rect x="14" y="14.6" width="13" height="2.8" rx="1.4" fill="#3b3a42"/>'
        '<rect x="14" y="14.6" width="7.5" height="2.8" rx="1.4" fill="#ff9b42"/>'
        '<circle cx="21.5" cy="16" r="3" fill="#ffd2a6"/></svg>')


def esc(s):
    return html.escape(str(s), quote=True)


# --------------------------------------------------------------------------- facts
def zip_facts():
    z = OUT / "download" / "hindsight.zip"
    manifest = tomllib.loads((ROOT / "hindsight" / "blender_manifest.toml").read_text(encoding="utf-8"))
    version = manifest["version"]
    size = "?"
    if z.is_file():
        with zipfile.ZipFile(z) as zf:
            packed = tomllib.loads(zf.read("blender_manifest.toml").decode("utf-8"))
        if packed["version"] != version:
            sys.exit(f"docs/download/hindsight.zip is {packed['version']}, source is {version}: rebuild the zip first")
        kb = z.stat().st_size / 1024
        size = f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"
    return version, size


# --------------------------------------------------------------------------- partials
def nav(current):
    items = [("Demo", "{{root}}#demo-section", "demo"), ("Docs", "{{root}}docs/", "docs"),
             ("Changelog", "{{root}}changelog/", "changelog")]
    lis = "".join(f'<li><a href="{href}"{" aria-current=page" if key == current else ""}>{name}</a></li>'
                  for name, href, key in items)
    return (f'<a class="skip" href="#main">Skip to content</a>\n<header class="nav"><div class="wrap">'
            f'<a class="brand" href="{{{{root}}}}" aria-label="Hindsight home">{LOGO}<span>Hindsight</span></a>'
            f'<ul>{lis}<li class="keep"><a class="btn btn-primary btn-small" href="{{{{root}}}}download/hindsight.zip" '
            f'download>Download beta</a></li></ul></div></header>')


def footer():
    return (f'<footer><div class="wrap"><p>Hindsight is free software under the GPL 3.0 or later. Made by Min3scon. '
            f'Blender is a trademark of the Blender Foundation.</p>'
            f'<nav aria-label="Footer"><a href="{{{{root}}}}docs/">Docs</a><a href="{{{{root}}}}changelog/">Changelog</a>'
            f'<a href="{REPO}">GitHub</a><a href="{ISSUES}">Report a bug</a></nav></div></footer>')


def video(spec):
    name, alt, w, h, mode = (spec.split("|") + ["", "", "1600", "900", "lazy"])[:5]
    media = SITE / "assets" / "media"
    poster = f"{{{{root}}}}assets/media/{name}.webp"
    has_video = (media / f"{name}.webm").is_file() or (media / f"{name}.mp4").is_file()
    if not has_video:
        if (media / f"{name}.webp").is_file():
            return f'<img src="{poster}" alt="{esc(alt)}" width="{w}" height="{h}" loading="{"eager" if mode == "eager" else "lazy"}">'
        return f'<div role="img" aria-label="{esc(alt)}" style="aspect-ratio:{w}/{h}"></div>'
    srcs = ""
    for ext, typ in (("webm", "video/webm"), ("mp4", "video/mp4")):
        if (media / f"{name}.{ext}").is_file():
            srcs += f'<source data-src="{{{{root}}}}assets/media/{name}.{ext}" type="{typ}">'
    return (f'<video muted loop playsinline preload="none" data-lazy width="{w}" height="{h}" poster="{poster}" '
            f'aria-label="{esc(alt)}">{srcs}</video>')


def demo():
    i = ICONS
    return f'''<div class="demo reveal" id="demo" data-script="{{{{root}}}}assets/demo.js">
  <div class="demo-stage" role="application" aria-label="Sculpt demo. Drag on the shape to push or pull it. Drag around it to turn it.">
    <canvas aria-hidden="true"></canvas>
    <div class="ring"></div>
    <p class="hint"></p>
    <div class="tools" role="group" aria-label="Brush">
      <button class="tool" type="button" data-tool="pull" aria-pressed="true">Pull</button>
      <button class="tool" type="button" data-tool="push" aria-pressed="false">Push</button>
      <button class="tool" type="button" data-tool="morph" aria-pressed="false" disabled>Morph</button>
    </div>
    <button class="icon-btn demo-reset" type="button" data-act="reset" aria-label="Start over">{i["reset"]}</button>
  </div>
  <div class="dock">
    <div class="ctl">
      <button class="icon-btn dot" type="button" data-act="menu" aria-label="History Menu" aria-haspopup="menu" aria-expanded="false"></button>
      <button class="icon-btn" type="button" data-act="back" aria-label="Step back" disabled>{i["back"]}</button>
      <button class="icon-btn" type="button" data-act="fwd" aria-label="Step forward" disabled>{i["fwd"]}</button>
      <button class="icon-btn" type="button" data-act="play" aria-label="Play the history (Movie Mode)" aria-pressed="false" disabled>{i["play"]}</button>
    </div>
    <div class="track" role="slider" tabindex="0" aria-label="History" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0" aria-valuetext="Step 0 of 0">
      <span class="rail"></span><span class="fill"></span><span class="tick"></span><span class="knob"></span>
    </div>
    <div class="count" aria-hidden="true">0 / 0</div>
  </div>
  <p class="readout"></p>
  <div class="menu" role="menu" aria-label="History Menu">
    <button type="button" role="menuitem" data-m="target">Set morph target here</button>
    <button type="button" role="menuitem" data-m="morph">Use the Morph brush</button>
    <hr>
    <button type="button" role="menuitem" data-m="movie">Play as a timelapse</button>
    <button type="button" role="menuitem" data-m="reset">Start over</button>
  </div>
  <p class="visually-hidden" aria-live="polite"></p>
  <noscript><p class="nogl">The demo needs JavaScript.</p></noscript>
</div>'''


def shots(data):
    out = []
    for s in data.get("shots", []):
        p = SITE / "assets" / "shots" / f"{s['file']}.webp"
        if not p.is_file():
            continue
        w, h = s.get("w", 1200), s.get("h", 900)
        out.append(f'<li class="shot"><figure><button type="button" data-zoom="{{{{root}}}}assets/shots/{s["file"]}.webp" '
                   f'data-w="{w}" data-h="{h}" data-caption="{esc(s["caption"])}" aria-label="Enlarge: {esc(s["title"])}">'
                   f'<img src="{{{{root}}}}assets/shots/{s["file"]}-thumb.webp" alt="{esc(s["alt"])}" width="600" height="450" '
                   f'loading="lazy" decoding="async"></button><figcaption><b>{esc(s["title"])}</b>{esc(s["caption"])}</figcaption>'
                   f'</figure></li>')
    return "\n".join(out)


def annotated(data):
    a = data.get("annotated")
    if not a or not (SITE / "assets" / "shots" / f"{a['file']}.webp").is_file():
        return ""
    w, h = a["w"], a["h"]
    labels = []
    for lab in a["labels"]:
        x, y, tx, ty, text = lab["x"], lab["y"], lab["tx"], lab["ty"], lab["text"]
        anchor = lab.get("anchor", "start")
        labels.append(
            f'<circle cx="{x}" cy="{y}" r="9" fill="none" stroke="#ff9b42" stroke-width="3"/>'
            f'<line x1="{x}" y1="{y}" x2="{tx}" y2="{ty}" stroke="#ff9b42" stroke-width="2.5"/>'
            f'<rect x="{tx - (0 if anchor == "start" else lab["tw"])}" y="{ty - 26}" width="{lab["tw"]}" height="40" rx="8" '
            f'fill="#0c0c0e" fill-opacity="0.9" stroke="#ff9b42" stroke-opacity="0.6"/>'
            f'<text x="{tx + (14 if anchor == "start" else -14)}" y="{ty + 1}" fill="#edebe6" font-family="Inter, sans-serif" '
            f'font-size="22" font-weight="600" text-anchor="{anchor}">{esc(text)}</text>')
    return (f'<figure class="annot" style="margin:0"><img src="{{{{root}}}}assets/shots/{a["file"]}.webp" '
            f'alt="{esc(a["alt"])}" width="{w}" height="{h}" loading="lazy" decoding="async">'
            f'<svg class="labels" viewBox="0 0 {w} {h}" aria-hidden="true">{"".join(labels)}</svg></figure>')


def lightbox():
    return (f'<div class="lightbox" role="dialog" aria-modal="true" aria-label="Screenshot" aria-hidden="true" inert>'
            f'<button class="icon-btn close" type="button" aria-label="Close">{ICONS["close"]}</button>'
            f'<figure style="margin:0"><img alt="" src="data:," width="1200" height="900"><p></p></figure></div>')


def figure(spec, data):
    name, caption = spec.split("|", 1)
    p = SITE / "assets" / "shots" / f"{name}.webp"
    if not p.is_file():
        return ""
    a = data.get("annotated") or {}
    if a.get("file") == name:
        inner = annotated(data).replace('<figure class="annot" style="margin:0">', '<div class="annot">')
        inner = inner.replace("</figure>", "</div>")
        return f"<figure>{inner}<figcaption>{esc(caption)}</figcaption></figure>"
    return (f'<figure><img src="{{{{root}}}}assets/shots/{name}.webp" alt="{esc(caption)}" loading="lazy">'
            f"<figcaption>{esc(caption)}</figcaption></figure>")


# --------------------------------------------------------------------------- changelog
def inline_md(s):
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def changelog():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    body, toc = [], []
    for part in parts:
        lines = part.strip().splitlines()
        title = lines[0].strip()
        m = re.match(r"([\w.\-]+)\s*\(([^)]*)\)", title)
        ver, when = (m.group(1), m.group(2)) if m else (title, "")
        rid = "v" + re.sub(r"[^\w-]", "-", ver)
        toc.append(f'<a href="#{rid}">{esc(ver)}</a>')
        html_lines, in_list, para = [], False, []

        def flush_para():
            if para:
                html_lines.append("<p>" + inline_md(" ".join(para)) + "</p>")
                para.clear()
        for line in lines[1:]:
            if line.startswith("- "):
                flush_para()
                if not in_list:
                    html_lines.append("<ul>")
                    in_list = True
                html_lines.append("<li>" + inline_md(line[2:].strip()) + "</li>")
            elif not line.strip():
                flush_para()
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
            else:
                if in_list:
                    html_lines.append("</ul>")
                    in_list = False
                para.append(line.strip())
        flush_para()
        if in_list:
            html_lines.append("</ul>")
        body.append(f'<section class="release" id="{rid}"><h2>{esc(ver)}</h2><p class="date">{esc(when)}</p>'
                    + "\n".join(html_lines) + "</section>")
    return "\n".join(body), "\n".join(toc)


# --------------------------------------------------------------------------- head
def head(meta, css):
    title, desc, path = meta["title"], meta["description"], meta.get("path", "")
    url = BASE + path
    img = BASE + "assets/img/og.png"
    robots = '<meta name="robots" content="noindex">' if meta.get("noindex") else ""
    return f'''<!doctype html>
<html lang="en" class="no-js">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">
{robots}
<meta name="theme-color" content="#0c0c0e">
<meta name="color-scheme" content="dark">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Hindsight">
<meta property="og:locale" content="en_GB">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{img}">
<meta property="og:image:secure_url" content="{img}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Hindsight, a Blender add-on: a sculpt with a history slider under it">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{img}">
<meta name="twitter:image:alt" content="Hindsight, a Blender add-on: a sculpt with a history slider under it">
<link rel="icon" href="{{{{root}}}}favicon.svg" type="image/svg+xml">
<link rel="icon" href="{{{{root}}}}assets/img/favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="{{{{root}}}}assets/img/apple-touch-icon.png">
<link rel="manifest" href="{{{{root}}}}site.webmanifest">
<link rel="preload" href="{{{{root}}}}assets/fonts/inter-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{{{{root}}}}assets/fonts/fraunces-var.woff2" as="font" type="font/woff2" crossorigin>
<style>{css}</style>
</head>
<body>
'''


# --------------------------------------------------------------------------- build
def render(page_file, data, facts):
    raw = page_file.read_text(encoding="utf-8")
    m = re.match(r"<!--page\n(.*?)\n-->\n", raw, re.S)
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    body = raw[m.end():]
    version, size = facts
    clog, ctoc = changelog()
    simple = {
        "version": esc(version), "size": esc(size), "tested": esc(data["tested"]), "branch": BRANCH,
        "bytes_per_step": esc(data.get("bytes_per_step", "a few KB")),
        "known_issues": "\n".join(f"<li>{esc(k)}</li>" for k in data.get("known_issues", [])),
        "benchmarks": data.get("benchmarks_html", "<p>Numbers are being measured.</p>"),
        "changelog": clog, "changelog_toc": ctoc, "footer": footer(), "demo": demo(), "shots": shots(data),
        "annotated": annotated(data), "lightbox": lightbox(),
    }

    def sub(mo):
        key = mo.group(1)
        if key.startswith("nav:"):
            return nav(key[4:])
        if key.startswith("icon:"):
            return ICONS[key[5:]]
        if key.startswith("video:"):
            return video(key[6:])
        if key.startswith("figure:"):
            return figure(key[7:], data)
        if key in simple:
            return simple[key]
        if key == "root":
            return "{{root}}"
        sys.exit(f"{page_file.name}: unknown placeholder {key}")
    for _ in range(3):   # partials may contain placeholders
        body = re.sub(r"\{\{([\w:|.,' \-()/]+?)\}\}", sub, body)
    css = (SITE / "assets" / "site.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s*\n\s*", "\n", css).strip()
    page = head(meta, css) + body + '<script src="{{root}}assets/site.js" defer></script>\n</body>\n</html>\n'
    page = page.replace("{{root}}", meta.get("root", "./"))
    return meta, page


def main():
    data = json.loads((SITE / "data.json").read_text(encoding="utf-8"))
    facts = zip_facts()
    keep = OUT / "download"
    if OUT.exists():
        for child in OUT.iterdir():
            if child == keep:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    OUT.mkdir(exist_ok=True)
    dst = OUT / "assets"
    for sub in ("fonts", "img", "media", "shots"):
        src = SITE / "assets" / sub
        if src.is_dir():
            shutil.copytree(src, dst / sub, dirs_exist_ok=True)
    for f in ("site.js", "demo.js"):
        shutil.copyfile(SITE / "assets" / f, dst / f)
    pages = []
    for page_file in sorted((SITE / "pages").glob("*.html")):
        meta, text = render(page_file, data, facts)
        path = meta.get("path", "")
        target = OUT / (path if path.endswith(".html") else path + "index.html")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        if not meta.get("noindex"):
            pages.append(path)
    shutil.copyfile(SITE / "assets" / "img" / "favicon.svg", OUT / "favicon.svg") \
        if (SITE / "assets" / "img" / "favicon.svg").is_file() else None
    today = date.today().isoformat()
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{BASE}{p}</loc><lastmod>{today}</lastmod></url>\n" for p in pages)
        + "</urlset>\n", encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {BASE}sitemap.xml\n", encoding="utf-8")
    (OUT / "site.webmanifest").write_text(json.dumps({
        "name": "Hindsight", "short_name": "Hindsight",
        "description": "Scrub back through your sculpt in Blender.",
        "start_url": "./", "display": "browser", "background_color": "#0c0c0e", "theme_color": "#0c0c0e",
        "icons": [{"src": "assets/img/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "assets/img/icon-512.png", "sizes": "512x512", "type": "image/png"}]}, indent=1),
        encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"site built: {len(pages) + 1} pages, version {facts[0]}, zip {facts[1]}")


if __name__ == "__main__":
    main()
