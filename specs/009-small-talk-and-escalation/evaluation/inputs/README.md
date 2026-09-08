# The exact inputs used for the manual runs

[messages.md](../messages.md) is the labelled set as prose — what a person reads. These are the
same messages as machine-readable input, plus the harness that drove them, so a later run measures
the same thing rather than 90 newly invented sentences.

Nothing here runs in CI, and nothing here is a test. Driving one of these files spends real Claude
and Voyage calls against a running stack (see [../procedure.md](../procedure.md)).

## The files

| File | Messages | What it measures |
|---|---|---|
| `setA.json`, `setA2.json` | 32 | SC-001, SC-005 — messages that ask for nothing |
| `setB.json`, `setB2.json` | 20 | SC-003 — a pleasantry wrapped around a real request |
| `setC.json` | 16 | SC-011 — requests the assistant is not authorized to serve |
| `setD1.json`, `setD2.json` | 22 | SC-015 — the three situations that stop the conversation |
| `setE.json` | 9 | SC-016a — near misses that must **not** stop |
| `distress.json` | 12 | the distress boundary, cut from both sides in one run |
| `retest.json` | 17 | the 2026-09-08 failures plus the controls that must not regress |

Each entry is `{"id", "text", "expected"}`, and `"prior"` where the label depends on what the
assistant said first ("ok" means one thing after arrival instructions and another after a slot
offer).

## Running them

```bash
make services-up
uv run python specs/009-small-talk-and-escalation/evaluation/inputs/drive.py <input.json> <out.json>
uv run python specs/009-small-talk-and-escalation/evaluation/inputs/sc004.py   # the SC-004 pair, 3 trials each
```

`drive.py` mints one session, gives each message its own chat, and records for every turn: the
classifier's labels, the router's stopping cause, every escalation raised, the answer source, the
FAQ verdict, the reply text, and the conversation's resulting state (escalated, reason, marks). It
reads the labels out of `.run/chat.log`, so it must run against a stack started by `make
services-up`.

`sc004.py` is separate because SC-004 is the one criterion that needs the *same* word driven down
two different conversations, several times each — a single-message file cannot express it.

## A caution about re-running

These are live model calls, so a run is a sample, not a verdict. The 2026-09-08 session recorded a
message ("Oh no") that classified `small_talk` on one run and `distress` on the next, before the
prompt was tightened. Record what a run produced, including the disagreements, rather than
re-rolling until it looks clean.
