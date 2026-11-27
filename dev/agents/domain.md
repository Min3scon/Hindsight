# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Where things live in this repo

ADRs live in `docs/adr/`, the usual place. Decisions made before `docs/adr/` existed are in `DECISIONS.md` at the root,
newest first. Read the parts of it that touch the area you're working in, as you would ADRs.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root (this repo has a single context; there is no `CONTEXT-MAP.md`). Older versions of
  these skills call this file `CONTEXT.md`; in this repo it is `GLOSSARY.md`, as in Matt Pocock's current skills.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in.
- **`DECISIONS.md`** — the decision log from before the ADR folder.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

```
/
├── GLOSSARY.md
├── DECISIONS.md          ← decisions before docs/adr/ existed
├── docs/adr/
│   ├── 0001-....md
│   └── 0002-....md
└── hindsight/            ← the add-on
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR (or a decision in `DECISIONS.md`), surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_
