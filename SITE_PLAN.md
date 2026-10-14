# Site plan

Internal planning doc for https://min3scon.github.io/Hindsight/. Not published.

## Who visits

1. Blender artists who sculpt. They know the pain: Ctrl+Z walks back one stroke at a time, it
   forgets everything when Blender closes, and it can't bring back one part of an older shape.
   They decide in seconds whether this is worth a download.
2. People who got a link from a friend or a forum post. They may not sculpt much. They need to see
   what it does before they read anything.

## What they must get in 10 seconds

It gives every sculpted object its own history that you can scrub back and forth with a slider,
and it keeps that history with the file.

The hero visual does most of this: a real Blender viewport, a sculpt, the slider moving it back and
forward. The headline and one sentence back it up.

## What they should do

1. Try the demo on the page (no install, works on a phone).
2. Download the beta.

Everything else (docs, changelog, GitHub) is one click away in the nav, not on the landing page.

## Landing page: six sections, one job each

| # | Section | Its one job | Visual |
|---|---------|-------------|--------|
| 1 | Hero | Say what it is and offer the two actions | Short looping capture of the slider scrubbing a sculpt in real Blender |
| 2 | Demo | Let people feel it: sculpt, scrub, morph back | The in-browser sculpt toy |
| 3 | Three features | Show the three things a sculptor will use | One short loop each, real Blender |
| 4 | Screenshots | Prove it's a real, finished-looking add-on and show where things are | Real screenshots, captions, click to zoom, one annotated "where to find it" |
| 5 | Install and beta | Three steps to get it running; be straight about beta status | Plain steps, tested versions |
| 6 | Footer | Links and the licence | none |

The three features (each a short line and a short loop):
1. Scrub any object's history. Each object keeps up to 10,000 steps. Scrub back, look, scrub forward;
   nothing is lost until you sculpt from an earlier step.
2. Paint an earlier shape back. Set a step as the morph target, then paint it back onto just the area
   you want with the Morph History brush.
3. Turn the history into a timelapse. Movie Mode plays every step on the timeline so you can render it.

Things that are real but do not earn landing-page space (they go to /docs/): global history,
collection filtering, join and unmerge, save modes, crash recovery, memory settings, shortcuts,
troubleshooting, FAQ, known issues, benchmarks.

Copy budget for the landing page: under about 400 words in total.

## Other pages

- /docs/ : one page with a simple sidebar. Install, quick start, every feature in detail, shortcuts,
  troubleshooting, FAQ, known issues, benchmarks (real numbers, each marked as real Blender or
  headless, with the machine they ran on).
- /changelog/ : generated from CHANGELOG.md.
- 404.html : same style, one line and a way home.

## Rules for the build

- Everything self-hosted. No CDNs, no trackers, no external fonts at runtime.
- Real screenshots and captures from the generated test models only, never the owner's files.
- All copy follows STYLE.md and passes tools/check_copy.py.
- Version and file size in the hero come from the build, never typed by hand.
