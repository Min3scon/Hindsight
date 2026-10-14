#!/usr/bin/env python3
"""Turn the frames and stills from the capture scenario into the site's media.

    python tools/encode_media.py [CAPTURE_DIR]      default .tmp/ui/capture/5.1.2 (frames in ./frames)

Writes site/assets/media/{hero,feature-scrub,feature-morph,feature-movie}.{webm,mp4,webp} and
site/assets/shots/*.webp (+ -thumb.webp), then updates the "shots" and "annotated" entries of
site/data.json. Crops are in source-image pixels; CROPS below is the only place to tune them.
Uses ffmpeg and Pillow. Every output is checked against its size budget.
"""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CAP = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".tmp" / "ui" / "capture" / "5.1.2"
FRAMES = CAP / "frames"
MEDIA = ROOT / "site" / "assets" / "media"
SHOTS = ROOT / "site" / "assets" / "shots"
NOTES = json.loads((CAP / "result.json").read_text())["notes"]
BUDGET_VIDEO = 900 * 1024
BUDGET_STILL = 200 * 1024


def ff(*args):
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *map(str, args)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(r.stderr)


def encode(name, src_dir, crop=None, width=1280, fps=24, crf_webm=38, crf_mp4=27):
    """crop = (x, y, w, h) in source pixels, or None."""
    vf = []
    if crop:
        vf.append("crop=%d:%d:%d:%d" % (crop[2], crop[3], crop[0], crop[1]))
    vf.append(f"scale={width}:-2:flags=lanczos")
    vf = ",".join(vf)
    pattern = src_dir / "f%04d.png"
    out = MEDIA / name
    ff("-framerate", fps, "-i", pattern, "-vf", vf + ",format=yuv420p", "-c:v", "libvpx-vp9", "-b:v", "0",
       "-crf", crf_webm, "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-an", out.with_suffix(".webm"))
    ff("-framerate", fps, "-i", pattern, "-vf", vf + ",format=yuv420p", "-c:v", "libx264", "-preset", "slow",
       "-crf", crf_mp4, "-movflags", "+faststart", "-an", out.with_suffix(".mp4"))
    first = sorted(src_dir.glob("f*.png"))[0]
    ff("-i", first, "-vf", vf, "-quality", "78", out.with_suffix(".webp"))
    for ext in (".webm", ".mp4"):
        size = out.with_suffix(ext).stat().st_size
        print(f"{name}{ext}: {size // 1024} KB")
        if size > BUDGET_VIDEO:
            print(f"  over budget ({BUDGET_VIDEO // 1024} KB)")


def still(name, src, crop=None, width=None, quality=82):
    im = Image.open(src).convert("RGB")
    if crop:
        x, y, w, h = crop
        im = im.crop((x, y, x + w, y + h))
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    dst = SHOTS / f"{name}.webp"
    q = quality
    while True:
        im.save(dst, "WEBP", quality=q, method=6)
        if dst.stat().st_size <= BUDGET_STILL or q <= 50:
            break
        q -= 6
    th = im.copy()
    tw, tht = 600, 450
    scale = max(tw / th.width, tht / th.height)
    th = th.resize((round(th.width * scale), round(th.height * scale)), Image.LANCZOS).crop((0, 0, tw, tht))
    th.save(SHOTS / f"{name}-thumb.webp", "WEBP", quality=76, method=6)
    print(f"{name}.webp: {im.width}x{im.height} {dst.stat().st_size // 1024} KB (q{q})")
    return im.width, im.height


# --------------------------------------------------------------------------- crops (tuned by eye)
CROPS = json.loads((ROOT / "tools" / "media_crops.json").read_text()) if (ROOT / "tools" / "media_crops.json").is_file() else {}


def main():
    MEDIA.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    c = CROPS
    encode("hero", FRAMES / "hero", c.get("hero"), width=1600)
    encode("feature-scrub", FRAMES / "scrub", c.get("scrub"), width=960)
    encode("feature-morph", FRAMES / "morph", c.get("morph"), width=960)
    encode("feature-movie", FRAMES / "movie", c.get("movie"), width=960, fps=20)
    st = FRAMES / "stills"
    shots = []
    for key, title, caption, alt, src in (
        ("tab", "The Undo History tab", "Press N in the 3D Viewport. The slider, the arrows and the step count work in every mode.",
         "The Undo History tab in Blender's sidebar, with a slider at step 18 of 30", "tab_and_slider.png"),
        ("slider", "The on-screen slider", "Drag it to scrub. The ring on the left opens the History Menu.",
         "A history slider along the bottom of the 3D Viewport, under a sculpted Suzanne", "viewport_slider.png"),
        ("menu", "History Menu", "Global history, the morph target, and clearing or pausing the history.",
         "The History Menu open above the on-screen slider", "history_menu_window.png"),
        ("morph", "Morph History brush", "Paints the shape of an earlier step back onto the area under the brush.",
         "The Morph History brush selected in the Sculpt Mode toolbar", "morph_tool.png"),
        ("panels", "Movie Mode and Collection Filtering", "Movie Mode keyframes the history as a timelapse. Filtering picks which collections are recorded.",
         "The Movie Mode and Collection Filtering panels in the Undo History tab", "panels_window.png"),
        ("prefs", "Preferences", "Where the history is saved, how much memory it may use, and the slider's look.",
         "Hindsight's add-on preferences with the beta note at the top", "preferences.png"),
    ):
        w, h = still(key, st / src, c.get("still_" + key), width=1400)
        shots.append({"file": key, "title": title, "caption": caption, "alt": alt, "w": w, "h": h})
    w, h = still("where", st / "where_window.png", c.get("still_where"), width=1600)
    data_path = ROOT / "site" / "data.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    data["shots"] = shots
    ann = c.get("annotated_labels", [])
    data["annotated"] = {"file": "where", "w": w, "h": h, "labels": ann,
                         "alt": "Blender in Sculpt Mode with the Undo History tab open in the sidebar and the history slider along the bottom of the viewport"}
    data_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print("site/data.json updated")


if __name__ == "__main__":
    main()
