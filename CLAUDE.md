## Agent skills

### Issue tracker

Issues live in GitHub Issues on Min3scon/Hindsight; outside pull requests are not triaged. See `dev/agents/issue-tracker.md`.

### Triage labels

The five default labels: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `dev/agents/triage-labels.md`.

### Domain docs

Single context: `GLOSSARY.md` at the root, decisions as ADRs in `docs/adr/` (and `DECISIONS.md` before that). See `dev/agents/domain.md`.

## Working a ticket

Owner's rule (2026-11-22): the work of every ticket goes up as a pull request the owner can review quickly, never as a
plain commit to the main branch. The plan is the GitHub map "Map: Hindsight V2"; take the first open, unblocked,
unassigned ticket (blocked tickets start with a `Blocked by:` line).

1. **Claim** the ticket (assign it), then branch from `claude/lucid-brahmagupta-9gsdsn` as `v2/<ticket number>-<short-name>`
   (from the previous ticket's branch if that one isn't merged yet, and say so in the PR).
2. **Build** it the way Matt Pocock's `implement` skill does: `/tdd` for new behaviour (a failing test first, at agreed
   seams), `/diagnosing-bugs` for bugs, then `/code-review` of the diff before committing. Changes aimed at speed, memory
   or code size are measured rounds in `OPTIMISATION_LOG.md`.
3. **Test** on Blender 4.2, 5.0, 5.1, 5.2 and 5.3 (full suites) before pushing. Commit subjects end with `[skip ci]`.
4. **Open the PR** into `claude/lucid-brahmagupta-9gsdsn` (a draft), its body in the shape of Matt Pocock's `pr` skill,
   using the words in `GLOSSARY.md`, no preamble, prose brief:

   ```markdown
   Closes #<ticket>

   ## Summary

   <the smallest diagram, call tree, file tree or diff sketch that makes the change clear>

   ## Evidence

   - **Before:** <failing test, output or measurement>
     **After:** <passing test, output or measurement>

   ## Merge Danger

   **Door:** <one-way or two-way>

   <optional: why>

   **Blast Radius:** <one word>

   <optional: what could break>
   ```

   Also say in it anything the owner must check or decide. Then watch the PR: fix failing checks and answer review
   comments.
5. **After the owner merges**, add the ticket's one-line result to the map's "Decisions so far".

## One session, helper agents, 150,000 tokens

Owner's rules (2026-11-22): no context past about 150,000 tokens, and no time lost to new sessions. So the work runs
in **one long session** that leads, and **helper agents** (the Agent tool) do the long pieces.

- **The lead session** picks the next ticket on the map, briefs helpers, checks what they bring back (re-runs their
  tests), opens the pull requests and talks to the owner. It keeps its own context small: no file dumps, no whole
  logs, only helpers' short reports.
- **Helpers** each get one narrow job (build one ticket's change test-first, research a question, find why a test
  fails) and a brief that names the files and the rules that apply. Each starts with an empty context and returns a
  short report: what changed (commits), the test results, what is left. At most 3 helpers at once (usage limits);
  never two on the same file.
- **150,000 tokens**: `.claude/settings.json` sets `autoCompactWindow` to 207500. Cloud sessions compact at 80% of
  that window less 20,000 (measured on 2026-11-22: 150000 gave a compaction point of 104,000), so the compaction
  runs at 150,000, for the lead session and for helpers alike. The PreCompact hook (`.claude/hooks/precompact.sh`)
  makes the summary a handoff note, and the session **continues in place** from it: same machine, running tests
  keep running, nothing to set up again. The hook after it (`.claude/hooks/after_compact.sh`) says so.
- About 55,000 to 90,000 tokens are instructions and tools loaded before any work. The same settings file defers
  MCP tool schemas (`ENABLE_TOOL_SEARCH`) and denies the connectors this repo never uses (Gmail, Coros, Claude Docs).
- **Talking to the owner**: a few short lines on what is being done and what is next; no tool output, no ids or
  codes. Tool output stays short too (`head`, `tail`, `grep`; never a whole log).

**Only if this session's machine is gone** (the container was reclaimed and the session can't resume) does the work
move to a new session: commit and push the branch, write a handoff the way Matt Pocock's `handoff` skill does (the
OS temp directory, a "suggested skills" section, references to the map, DECISIONS.md, commits and PRs instead of
copies, what is half done, the next step, questions for the owner), then `create_session` (claude-code-remote) with
`source_url` `https://github.com/Min3scon/Hindsight`, `source_revision` the pushed branch, a `title` "Hindsight V2:
<what it continues>" and `tags: ["remote-agents-project:hindsight-v2"]`, and tell the owner its title.

A new session's machine is set up by the "Hindsight" environment's setup script (`tools/dev_env.sh --all --blender
4.2,5.3`: all five bpy test modules, Blender 4.2 and 5.3, the Rust tools, installed side by side). The platform saves
that setup and reuses it, so a new session usually starts in under a minute.
