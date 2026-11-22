#!/usr/bin/env bash
# SessionStart hook, matcher "compact" (CLAUDE.md, "Handing off at 150,000 tokens"): after the compaction at
# the 150,000-token limit, tell the session to hand its work to a fresh session before doing anything else.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || true
br=$(git branch --show-current 2>/dev/null)
python3 - "${br:-unknown}" <<'PY'
import json, sys
branch = sys.argv[1]
text = ("The context reached the 150,000-token limit and was just compacted. Do not continue the work in this "
        "session. Hand off now, following CLAUDE.md, \"Handing off at 150,000 tokens\": commit and push branch "
        f"{branch}, write the handoff as Matt Pocock's handoff skill does, start the new session with it "
        "(create_session), tell the owner its title, then stop.")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
PY
exit 0
