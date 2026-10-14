# Writing style

How everything public about Hindsight is written: the site, README, changelog, release notes, issue
templates, the add-on's UI strings and error messages, and social previews. tools/check_copy.py
enforces the mechanical parts. The rest is judgement, so read this before writing.

## What gives AI-written text away

Short version of the research (sources at the end). No single sign proves anything; people use all
of these sometimes. It's the cluster that reads as machine-made, and it reads as a brochure even when
a person wrote it.

- Vocabulary. A small set of words shows up far more often in model output than in human writing:
  delve, tapestry, testament, pivotal, crucial, intricate, vibrant, showcase, underscore, foster,
  enhance, landscape and realm used as metaphors, seamless, robust, leverage, elevate, unlock. Newer
  models moved on to words like multifaceted, quietly, steady and dependable.
- Puffed-up framing. "Serves as" and "stands as" instead of "is". "Boasts" and "features" instead of
  "has". Claims about significance and legacy. Trailing -ing clauses that add commentary
  ("..., highlighting its importance").
- Negative parallelism. "Not just X, but Y." "It's not X, it's Y." "No X, no Y, just Z." Setting up
  a misconception nobody had so it can be corrected.
- Rule of three. Lists of three adjectives or three benefits by reflex (measured at about 4x the
  human rate).
- Punctuation. Em dashes at roughly 10x the human rate. Unusual characters: arrows, check marks,
  stars, box drawing.
- Formatting. Bold scattered through paragraphs (about 40x), Title Case Headings, bullet dumps where
  a sentence would do, emoji as decoration (rockets, check marks).
- Structure. A filler intro, a summary at the end ("In short", "Overall"), "It's important to note",
  rhetorical questions as headlines, "Imagine..." openers, "Whether you're X or Y".
- Rhythm. Every sentence the same middling length. Every paragraph the same shape.

## Hard rules

1. No em dashes or en dashes as punctuation, anywhere. Use a full stop, comma, colon or brackets.
   Hyphens only inside compound words and in ranges such as 4.2-5.x.
2. Nothing from the banned list below.
3. No rhetorical-question headlines, no "Imagine..." openers, no exclamation marks, no emoji, no
   arrows or check-mark characters in copy.
4. No filler intro, no summary outro. Start with the point and stop when it's made.
5. No bold inside paragraphs. Headings and button labels only.
6. Sentence case for every heading and label. Proper names keep their capitals: Hindsight, Blender,
   Sculpt Mode, Vertex Paint, Edit Mode, Undo History (the tab), History Menu, Morph History,
   Movie Mode, Collection Filtering, GitHub.
7. No padding lists of three. If there are two things, write two. If there are four, write four.
8. Paragraphs stay short: 55 words at most on the site and in the README, 40 in UI strings.

## Banned words and phrases

Matched case-insensitively as whole words or phrases by tools/check_copy.py.

seamless, seamlessly, elevate, unlock, empower, leverage, harness, streamline, revolutionise,
revolutionize, revolutionary, cutting-edge, cutting edge, state-of-the-art, robust, powerful,
game-changer, game changer, next-level, supercharge, delve, tapestry, landscape (as a metaphor),
realm, journey, crafted, meticulously, meticulous, effortlessly, effortless, comprehensive,
holistic, in today's fast-paced world, whether you're, say goodbye to, look no further,
it's worth noting, worth noting, not just, more than just, at its core, testament, pivotal,
crucial, intricate, vibrant, showcase, showcasing, underscore, underscores, foster, fostering,
enhance, enhancing, multifaceted, boasts, unleash, unparalleled, best-in-class, world-class,
next-gen, ever-evolving, dive into, deep dive, in summary, in conclusion, moreover, furthermore,
additionally, it's important to note, serves as, stands as, nestled, breathtaking, stunning,
elevated, transformative, empowering, game changing, take your, to the next level, magic, magical,
blazing, lightning-fast, butter-smooth, buttery, hassle-free, out of the box, plug and play,
here's the thing, the best part, simply put, quietly, delightful, intuitive, unrivaled,
unrivalled, effortless, synergy, paradigm, ecosystem.

("landscape" is banned outright in copy because the literal sense never comes up here.)

## How a solo developer writes about their own tool

- Say what it does, then what it doesn't. "It records every stroke on each object. It doesn't record
  Multires or Dyntopo yet."
- Real numbers instead of adjectives. "10,000 steps per object." "A stroke on a 100k mesh stores about
  12 KB." "Jumping 300 steps on a 500k mesh takes about 0.16 s."
- First person is fine. "I built this because I kept losing shapes to Ctrl+Z."
- Plain verbs. "Saves", "keeps", "drops", "jumps". Not "enables you to effortlessly".
- Be honest about the beta. "Beta. It works in my tests on Blender 5.0 and 5.1. Back up your files."
- Vary sentence length. A short one lands better after a long one. Don't stack three short ones.
- Say each thing once. If the hero says it, the feature section doesn't repeat it.
- A bit dry beats a bit excited.

### Before and after

- Before: "Hindsight seamlessly empowers artists to unlock their full creative potential."
  After: "Scrub back through your sculpt with a slider."
- Before: "Not just an undo tool, it's a whole new way to sculpt."
  After: "Ctrl+Z goes back one stroke. This goes back to any of them."
- Before: "Robust, lightweight, and intuitive."
  After: "Each stroke stores only the vertices it moved."

## UI strings

- Labels: two to four words, sentence case, verbs for buttons ("Run self-check").
- Tooltips: one sentence that says what the control does. No "This button allows you to".
- Errors: what happened, then what to do. "History folder missing. Keep 'scene_hindsight' next to
  the .blend to see this file's history."
- Never blame the user. Never say "oops".

## Sources

- Wikipedia: Signs of AI writing (WikiProject AI Cleanup), read 2026-10-14.
  https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing
- Pangram Labs: 9 signs of AI writing, backed by data (rates per 10,000 words).
  https://www.pangram.com/signs-of-ai-writing
- TechCrunch, 20 Nov 2025: The best guide to spotting AI writing comes from Wikipedia.
  https://techcrunch.com/2025/11/20/the-best-guide-to-spotting-ai-writing-comes-from-wikipedia/
