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

## One chat, one helper agent at a time

Owner's rules (2026-11-22): the work runs in the owner's one main chat, with no new sessions, and no helper's
context goes past 150,000 tokens.

- **The chat leads**: it picks the next ticket on the map, briefs a helper agent (the Agent tool), checks what comes
  back (re-runs its tests), opens the pull requests and talks to the owner. It keeps its own context small: no file
  dumps, no whole logs, only the helpers' short reports.
- **One helper at a time**, each with one narrow job (build one ticket's change test-first, research a question,
  find why a test fails) and a brief naming the files and the rules that apply. A helper starts with an empty
  context; its job is sized to stay well under 150,000 tokens (it reads only what it needs and keeps tool output
  short), and a job that would grow past that is split. It reports in a few lines: commits, test results, what is
  left.
- **Long test runs** go in the background (`run_in_background`), one test file at a time into a results file that
  skips finished files, so a container restart loses only the file that was running.
- **Talking to the owner**: a few short lines on what is being done and what is next; no tool output, no ids or
  codes. Tool output stays short too (`head`, `tail`, `grep`; never a whole log).

A fresh machine is set up by `tools/dev_env.sh --all --blender 4.2,5.3` (all five bpy test modules, Blender 4.2 and
5.3, the Rust tools, installed side by side).
