# Contract: The scheduling fixture label

The extension `evals/golden/schema.json` takes in this phase, and the rule that decides whether a
case's post-state matched. Field names here are part of the label, not an implementation detail —
the same status `specs/008-.../contracts/log-events.md` gives its event fields.

## Shape

```json
{
  "id": "G102",
  "family": "segmentation-edges",
  "message": "Can you cancel Friday and book Wednesday with William Osler instead?",
  "requests": [
    {"intent": "booking", "gist": "cancel Friday", "tools": ["cancel_appointment"]},
    {"intent": "booking", "gist": "book Wednesday", "tools": ["book_appointment"]}
  ],
  "scheduling": {
    "given":  [{"practitioner": "William Osler", "day": "+4d", "time": "10:00"}],
    "expect": [
      {"practitioner": "William Osler", "day": "+4d", "time": "10:00", "status": "cancelled"},
      {"practitioner": "William Osler", "day": "+2d", "status": "standing"}
    ],
    "reply": "Yes, please cancel Friday. The earliest Wednesday time is fine."
  },
  "source": "010/multi#C9"
}
```

`scheduling` is present **exactly** on the 18 cases carrying a booking request. `given` is the
appointments planted before the turn; `expect` is the **complete** set of the patient's appointments
after the case's last turn; `reply`, when present, is the patient's answer to what the first turn
asked, posted as a second turn in the same chat (FR-037b).

| Field | Required | Meaning |
|---|---|---|
| `practitioner` | yes | A seeded pool name. A fresh session has exactly two — `William Osler` (default specialty, Mon–Fri 09:00–17:00) and `Andreas Vesalius` (dentistry, Mon–Sat 09:00–14:00). |
| `day` | yes | `+Nd` / `-Nd` from the run clock's **date**. On the default Monday-08:00 clock, `+3d` is Thursday and `+7d` is the following Monday. |
| `time` | no | `HH:MM`. Omitted means *any time that day* — which is how a message that names no time is labelled. |
| `reply` | no | A string, on the fixture rather than on an appointment. The patient's scripted answer, posted verbatim once the first turn has replied. |
| `status` | `expect` only | `standing` or `cancelled`. A precondition is always a standing booking, so `given` carries none; an expectation must say which, because a cancelled appointment is still a row the scheduler returns. |

## The matching rule

A case's post-state matches when there is a **perfect one-to-one matching** between `expect` and the
patient's appointments after the turn:

1. An `expect` entry matches an appointment when the practitioner's full name is equal, the
   appointment's date equals `clock.date + day`, the status is equal, and — if `time` is present —
   the appointment's start time is equal.
2. Every `expect` entry must match exactly one appointment, and **no appointment may be left
   unmatched** (FR-041).
3. The matching is computed exhaustively. A case holds at most a handful of appointments, so there
   is no need for a greedy rule whose result would depend on the order the labels happen to be in.

**A case with a `reply` is read twice** (FR-037b). After the first turn, the appointments must match
`given` restated with `status: standing` under the same rule — the booking loop confirms before it
writes, so a first turn that booked or cancelled anything fails the case. After the reply, they must
match `expect`. A failure says which read failed.

A failure names both halves: which `expect` entries went unmatched, and which appointments no entry
accounts for (FR-041a). Too little and too much are opposite defects in the booking loop and a
single share cannot tell them apart.

## What "unchanged" is

It is `expect` restating `given`. There is no second label kind and no "unchanged" flag, because a
flag would be a second way of saying what the set already says — and the first turn that booked
something nobody asked for would still be reported as unchanged.

`given` may be empty. A case with `"given": [], "expect": []` is a patient with no appointments whom
the turn must leave with none — which is what the read-only tool cases are.

## Worked examples from the set

| Case | Message | `given` | `expect` |
|---|---|---|---|
| G049 | *which cardiologists do you have?* | `[]` | `[]` — nothing to plant, nothing may be written. A fresh session **has** no cardiologist; the honest answer is none, and that is not a fixture's problem. |
| G042 | *Hi, I need to cancel tomorrow* — reply *Yes, please go ahead.* | one at `+1d` | the same at `+1d`, `cancelled`; still standing after the first turn |
| G088 | *what should I bring, and can you book me Wednesday at 9 with William Osler?* — reply *Yes, please book it.* | `[]` | one at `+2d 09:00`, `standing`; nothing after the first turn |
| G102 | *cancel Friday and book Wednesday with William Osler instead* — reply above | one at `+4d 10:00` | that one `cancelled`, plus one at `+2d`, `standing`; untouched after the first turn |

## Two things a labeller has to decide, and one the first run decides

**A fixture describes what the scripted exchange should leave behind**, not what an open-ended
conversation would eventually achieve. The booking loop confirms before it writes, so a case whose
message asks for a write carries a `reply` that answers what the first turn will ask — the
confirmation, and any choice the loop is specified to leave to the patient, such as which
practitioner. Where a message leaves too much open for one scripted answer — "can I move my Thursday
appointment?" — the case carries no reply, its expected post-state is unchanged, and it still
scores tool selection on whatever the loop called to answer.

**A message whose tool needs a practitioner names one.** `check_availability` and
`book_appointment` take a practitioner id and the loop is told never to choose one for the patient,
so a message that leaves the practitioner open is answered with *which practitioner?* — correct, and
not what the case is for. Name the practitioner, unless a specialty in the message resolves to one
practitioner or to none.

**No booking message uses the run clock's own weekday.** On the Monday-08:00 clock, "Monday" is
either today or a week out, and a label can only mean one of them. Use another weekday.

**A `given` entry must actually plant.** It has to fall inside its practitioner's weekly range, on
the 60-minute grid, strictly after the clock and inside the 90-day horizon, or `BookAppointment`
refuses it. The loader checks all four before the run starts, so a bad label fails as a label rather
than as a mysterious exclusion halfway through a paid run.

**The `tools` labels are 2a's and are not re-opened here.** For a case with a reply they are scored
across both turns, which is where a confirmed write lands. Some others — G048's
`reschedule_appointment`, for instance — presume a turn completes an action that case's single turn
may only start. Whether that is the right label is exactly what the first run measures. If it proves wrong it
is a finding for a person to adjudicate, never a label re-taken from what the model did (spec, Out
of Scope).
