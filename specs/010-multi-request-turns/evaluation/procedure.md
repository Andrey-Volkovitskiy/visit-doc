# Segmentation measurement: how it is run, and what it produced

Committed data plus a written procedure, following spec 008's calibration sweep and spec 009's
labelled sets. **No runner, no assertions, and no place in any gate** — the behaviour this phase
adds is unit-tested offline against a stubbed classifier (FR-082); what cannot be tested that way is
how well the real classifier *splits*, and that is measured here, by hand, once, with the result
recorded so a later run can be compared rather than started from scratch.

## What is measured, and against which criterion

| Set | Messages | Criterion | What a row asserts |
|---|---|---|---|
| `compound.json` | 12 | SC-001 | A compound question whose halves are each individually answerable splits into that many requests |
| `mixed.json` | 22 | SC-002 | A corpus question paired with a scheduling request splits into one of each |
| `multi.json` | 26 | SC-003 (≥90%) | The expected number of requests, including ellipsis, pronouns and the cap |
| `single.json` | 22 | SC-004 (≥95%) | One request stays one segment — long, comma-heavy and repetitive messages included |
| `overriding.json` | 16 | SC-012 | A multi-request message carrying an overriding intent still produces that label |

SC-005 (nothing invented) and SC-006 (never above the cap) are read off the same outputs.

## Running it

The driver calls `classify_intent()` in-process. It deliberately does **not** drive whole turns
through the API: what these sets measure is the segmentation, and a turn would spend retrieval and
generation on every message to say nothing more about it. It needs `ANTHROPIC_API_KEY` in the repo
root's `.env`, and nothing else running — no database, no Qdrant, no services.

```bash
cd <repo root>
for f in compound mixed multi single overriding; do
  uv run python specs/010-multi-request-turns/evaluation/inputs/segment.py \
      specs/010-multi-request-turns/evaluation/inputs/$f.json \
      specs/010-multi-request-turns/evaluation/inputs/$f.out.json
done
```

Each run prints one JSON row per message and a summary line, and writes every row to `<set>.out.json`
— the committed record of what the shipped implementation produced. A message the classifier's
result fails validation for is counted as a **miss**, not left out of the denominator: the rows that
never came back are exactly where it failed hardest.

Cost is one cheap-model classification call per message — 98 calls for the whole sweep.

## Results

### Run 3 — 2026-09-09, the shipped prompt

| Set | Result |
|---|---|
| `compound` | **12/12 (100%)** |
| `mixed` | **22/22 (100%)** |
| `multi` | **25/26 (96%)** — SC-003's bar is 90% |
| `single` | **22/22 (100%)** — SC-004's bar is 95% |
| `overriding` | **16/16 (100%)** |
| **Total** | **97/98 (99%)** |

- **SC-005 — nothing invented: 0 occurrences across 179 segments.** Checked by scanning every
  segment for a capitalised word or a number that the message it came from does not contain.
- **SC-006 — no turn above the cap: 0 occurrences.** One *response* carried four segments (C26,
  below); the turn rejected it and fell back, so no turn ever ran more than three requests.
- **`cap_bound` was never set** in any run. The one message that exceeds the cap is rejected rather
  than combined, so nothing has yet had occasion to set it.

### The one standing miss

**C26** — *"What are your hours, where are you, do you take Aetna, and can I book Friday?"* — four
independently answerable requests. The classifier returns four segments; the result fails validation
and the turn falls back to the whole message on the FAQ path, which is FR-008's rule and is exactly
today's behaviour for that message. Nothing the patient asked is dropped, and no turn runs more than
three requests — but FR-006a's *combine the least separable* is not what the model does here, at
this prompt. Three prompt attempts did not move it (below).

Recorded rather than worked around, because it is the datum the cap of 3 is meant to be revisited
against in Phase 2: either the model learns to combine, or the cap moves, or messages of this shape
stay on the fallback. Raising the cap now, on one message, would be tuning to a sample of one.

### Run 1 — 2026-09-09, first measurement

The sweep found two defects, one of them in the *code* rather than the prompt:

1. **The request schema was rejected outright.** Every call returned
   `400 ... For 'array' type, property 'maxItems' is not supported`. The cap had been written into
   the JSON Outputs schema as `minItems`/`maxItems` (which Pydantic emits from the field's own
   bounds), and the API does not accept array bounds in a constrained-output schema. FR-006 and
   `contracts/segmentation.md` were corrected in the same change: the cap is stated in the prompt and
   enforced on arrival by validation, never by trimming. **This is the finding that justifies the
   whole procedure** — no offline test could have caught it, since every unit test stubs the call.
2. **C19 and C26 missed** (24/26 on `multi`): an anaphoric follow-up clause was dropped rather than
   split ("do you take Medicare? *what if you do not?*" → one segment), and the four-request message
   returned four segments.

### Run 2 — after stating the cap up front and adding a "nothing may be left out" rule

`multi` fell to **23/26**: C10 joined C19 as a dropped follow-up clause, and C26 was unchanged. The
general rule did not teach the specific skill.

### Run 3 — after naming the follow-up clause explicitly

Rule 2a was added, with the two failing messages as worked examples, and the cap's own wording
tightened. `multi` reached **25/26**, and the other four sets were re-run on the same prompt to
confirm the change cost nothing elsewhere — all four at 100%.

## Repeating it

Run the same command. Compare against the table above rather than against a memory of it: a set that
drops below its criterion's bar is a regression in the classifier prompt, and the rows in
`<set>.out.json` say which messages moved.
