"""The tier gate (HANDOFF.md, "Test guarantee") as data: the items every COVERAGE row needs, with a
passing run on every Blender in BLENDERS, before it may be `partial` or `supported`."""

BLENDERS = ("5.0.1", "5.1.1", "5.1.2")

# id -> what a test tagged with it must show
ITEMS = {
    "OPS": "the change is made with real operators (bpy.ops with context, or real events in a window)",
    "EXACT": "every recorded step restored in both directions; mesh bit-exact, other data value-exact",
    "ORACLE": "compared with a snapshot read by code independent of the adapter (tests/oracle.py)",
    "REPLAY": "compared with the same operations replayed in a fresh scene without Hindsight",
    "STEPS": "exactly one step per user operation, counted (operator end and settle paths)",
    "UNDO": "Ctrl+Z and Ctrl+Shift+Z right after an operation, mixed with scrubbing; cursor and data in sync",
    "UNDO.GLOBAL_OFF": "the same with Global Undo off",
    "UNDO.LIMITS": "the same with the undo step limit at its default, minimum and maximum",
    "UNDO.MODES": "the same across mode switches",
    "RELOAD.SIDECAR": "saved with history in a sidecar folder, reopened, reattached, scrubbed exactly",
    "RELOAD.EMBED": "the same with history embedded in the .blend",
    "RELOAD.BOTH": "the same with both",
    "RELOAD.RENAME": "the object renamed, history still attached",
    "RELOAD.PASTE": "copied and pasted into another file",
    "RELOAD.APPEND": "appended and linked from another file",
    "RELOAD.DUPLICATE": "duplicated with Shift+D and Alt+D",
    "RELOAD.MIGRATE": "a file saved by the previous format opens and records",
    "PROP": "Hypothesis: 2,000 or more random operation sequences, any step restored exactly",
    "MUT": "mutation testing of the adapter's diff and apply code: every mutant killed or explained",
    "FAULT": "the adapter raising in capture, diff or apply disables only itself, with a message",
    "FAULT.CORRUPT": "damaged stored data gives a clear message, never a crash or a silent wrong state",
    "PERF": "record and restore cost and memory per step on small, medium and heavy scenes within budget",
    "SOAK": "30 minutes of recording without growth in memory, threads, timers or file handles",
    "WARN": "everything unsupported or partial in the row shows a message in the add-on (tested)",
    "VISUAL": "screenshots of the row's UI in real Blender, looked at, nothing cut off or overlapping",
}
