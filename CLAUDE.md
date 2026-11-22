## Agent skills

### Issue tracker

Issues live in GitHub Issues on Min3scon/Hindsight; outside pull requests are not triaged. See `dev/agents/issue-tracker.md`.

### Triage labels

The five default labels: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `dev/agents/triage-labels.md`.

### Domain docs

Single context: `GLOSSARY.md` at the root, decisions as ADRs in `dev/adr/` (and `DECISIONS.md` before that). See `dev/agents/domain.md`.

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

## Handing off at 150,000 tokens

Owner's rule (2026-11-22): a session never works past about 150,000 tokens of context; past that, the work moves to a
fresh session automatically. `.claude/settings.json` sets `autoCompactWindow` to 200000: cloud sessions compact at
about 92% of that window less a reserve for the reply, so the compaction runs at about 150,000 (measured: 150000
compacted at 104,000 to 107,000, and with about 90,000 of tools and instructions loaded before any work, a session
compacted every minute). The same file defers MCP tool schemas (`ENABLE_TOOL_SEARCH`) and denies the connectors
this repo never uses (Gmail, Coros, Claude Docs), so less of the 150,000 goes to tools. Its PreCompact hook (`.claude/hooks/precompact.sh`) makes the summary the handoff, and the SessionStart hook after it
(`.claude/hooks/after_compact.sh`) says to hand off. Then, before any other work:

1. **Commit and push** the ticket's branch (a work-in-progress commit with `[skip ci]` if it isn't finished), so
   nothing lives only in this container: the handoff points at commits, and a new session gets a fresh container.
2. **Write the handoff** the way Matt Pocock's `handoff` skill does: a document summarising the conversation so a
   fresh agent can continue, saved to the OS temp directory (not the repo), with a "suggested skills" section naming
   the skills the next agent should call; it references what other artifacts already hold (the map and its tickets,
   DECISIONS.md, commits, PRs) by path or URL instead of repeating it, and leaves out anything sensitive. Say what is
   half done, the next concrete step, anything that was running and its last result (a run in this container is lost:
   the new session runs it again), and questions waiting on the owner.
3. **Start the new session** the way his `claude-handoff` skill does, with the handoff as its prompt: in a cloud
   session that is `create_session` (claude-code-remote tools) with `source_url` `https://github.com/Min3scon/Hindsight`,
   `source_revision` the pushed branch, a descriptive `title` ("Hindsight V2: <what it continues>") and
   `tags: ["remote-agents-project:hindsight-v2"]`, so every Hindsight V2 session stays in one group; check with
   `get_session` that the new session has the tag.
4. **Tell the owner** the new session's title, then **stop**: no more work in this one.

A new session's container is set up by the "Hindsight" environment's setup script (`tools/dev_env.sh --all --blender
4.2,5.3`: all five bpy test modules, Blender 4.2 and 5.3, the Rust tools). It starts by reading this file, the map and
the handoff.
