#!/usr/bin/env bash
# SessionStart hook, matcher "compact" (CLAUDE.md, "One session, helper agents, 150,000 tokens"): after the
# compaction at the 150,000-token limit, the session carries on in place from the summary.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || true
br=$(git branch --show-current 2>/dev/null)
python3 - "${br:-unknown}" <<'PY'
import json, sys
branch = sys.argv[1]
text = ("The context reached the 150,000-token limit and was compacted; the summary above is your handoff note. "
        "Carry on in this session (do not start a new one): check the state of branch "
        f"{branch} with git, re-read the ticket you are on if you need its details, and continue with the next "
        "step, following CLAUDE.md, \"One session, helper agents, 150,000 tokens\".")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
PY
exit 0
