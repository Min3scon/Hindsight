#!/usr/bin/env bash
# PreCompact hook (CLAUDE.md, "One session, helper agents, 150,000 tokens"). A compaction means the context reached
# the 150,000-token limit (autoCompactWindow in .claude/settings.json); the session then continues in place from
# the summary. What this prints becomes the instructions for the summary, so the summary is a handoff note.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || true
br=$(git branch --show-current 2>/dev/null)
cat <<TEXT
This compaction means the context reached the 150,000-token limit. The session continues from this summary alone,
so write it as a handoff note (Matt Pocock's handoff skill): short, and pointing at the map, tickets, commits and
pull requests instead of copying them. Keep, exactly:
- the ticket being worked (its number and name on the map "Map: Hindsight V2"), its branch (now: ${br:-unknown})
  and its pull request, if any;
- what is done, what is half done (files, uncommitted changes) and the next concrete step;
- helper agents still working, and what each was asked to do;
- anything running (test suites, benchmarks), where its output goes and its last known result;
- questions waiting on the owner, and owner decisions not yet in DECISIONS.md;
- measurements not yet logged in OPTIMISATION_LOG.md, with their numbers.
TEXT
git status --short 2>/dev/null | head -20 | sed 's/^/uncommitted: /'
git log --oneline -5 2>/dev/null | sed 's/^/recent commit: /'
exit 0
