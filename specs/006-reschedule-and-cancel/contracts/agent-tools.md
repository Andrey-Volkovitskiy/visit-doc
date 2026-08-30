# Contract: agent tool registry — the 006 delta

**Feature**: `006-reschedule-and-cancel` | **Date**: 2026-08-29

Extends [005's registry contract](../../005-scheduling-and-booking/contracts/agent-tools.md), which
remains authoritative for everything not restated here — the `(name, description, input_schema,
handler)` record shape, the closed schemas, and the rule that handlers own every provider detail so
the node never sees gRPC.

Two tools are added and one is modified. `search_faq` is still not in the registry, and
`escalate_to_staff` is still Phase 1d part 2 (FR-042).

---

## Ambient arguments — unchanged, and now load-bearing for a second reason

`session_id`, `patient_id` and `local_now` remain bound from graph state, never model-supplied. With
changes in play, `local_now` decides two more things a model must not be able to influence: whether
an appointment has already started (FR-005) and which leg of a listing it falls in (FR-016).

**The guard fields are the exception, and it is a deliberate one.** `expected_starts_at` and
`expected_practitioner_id` *are* model parameters, because only the model knows what it stated to the
patient. The handler cannot derive them — re-reading the appointment would return its current state,
which matches itself by definition and disables the guard entirely — and the registry cannot cache
them, because a confirmation spans turns while per-turn ambient state does not. Stated plainly: this
guard protects a confirmation from the appointment changing underneath it, not from a model that
fabricates what it showed. That is the threat FR-021 names (research #12).

---

## `reschedule_appointment`

> Moves a REAL appointment to a different time, and optionally to a different practitioner. The
> appointment keeps its identity — this is not a cancellation plus a new booking. Only call this
> after the patient has explicitly confirmed, in this turn, the appointment being moved and the exact
> new time. `expected_starts_at` and `expected_practitioner_id` must be the values you stated to the
> patient when you asked them to confirm — not values you have just re-read.

```json
{
  "type": "object",
  "properties": {
    "appointment_id": { "type": "string" },
    "new_starts_at": { "type": "string", "description": "local date-time, YYYY-MM-DDTHH:MM:SS" },
    "new_practitioner_id": { "type": "string", "description": "omit to keep the current practitioner" },
    "expected_starts_at": { "type": "string", "description": "the start you read out to the patient" },
    "expected_practitioner_id": { "type": "string", "description": "the practitioner you read out" }
  },
  "required": ["appointment_id", "new_starts_at", "expected_starts_at", "expected_practitioner_id"],
  "additionalProperties": false
}
```

**Result — changed**

```json
{ "status": "changed", "change": "rescheduled",
  "appointment": { "id": "01J...", "practitioner_full_name": "William Osler",
                   "specialty": "General Practice", "starts_at": "2026-09-02T10:00:00",
                   "ends_at": "2026-09-02T11:00:00", "status": "standing" },
  "previous_starts_at": "2026-09-02T09:00:00",
  "previous_practitioner_full_name": "William Osler" }
```

`ends_at` is recomputed from the practitioner who will now hold the appointment, read at the moment
of the change (FR-004) — so a practitioner swap can return an appointment that is longer or shorter
than it went in. **When the new length differs from the old one, the model must say so** in its
confirmation and in its report (FR-025): a 15-minute appointment becoming an hour is not something to
discover on arrival.

**Result — unchanged**

```json
{ "status": "unchanged",
  "appointment": { "id": "01J...", "starts_at": "2026-09-02T10:00:00", "...": "..." },
  "explanation": "That appointment is already at that time with that practitioner." }
```

The appointment was already in the state asked for (FR-019) — a re-send of a change that landed, or a
patient asking for the time they already have. The model reports it as done, never as a failure and
never as a second move.

**Result — refused**

```json
{ "status": "refused", "reason": "stale_confirmation",
  "explanation": "That appointment has changed since I read it out to you." }
```

`reason` is one of the twelve `ChangeFailureReason` values, chosen by the fixed precedence in
[scheduling.proto](./scheduling.proto). `explanation` is a fixed, handler-authored sentence per
reason — the mapping is total because the set is closed — which the model rephrases but cannot
contradict (FR-032).

**What the model must do per reason** (FR-032, SC-018):

| Reason group | Required response |
|---|---|
| the six placement reasons — `practitioner_busy`, `patient_busy`, `outside_schedule`, `off_grid`, `in_past`, `beyond_horizon` | call `check_availability` and offer other times |
| `stale_confirmation` | describe the appointment as it now stands and ask again — **never** re-issue the change, and never treat the earlier yes as covering the new state (FR-022) |
| `appointment_not_found`, `already_cancelled`, `already_started` | say plainly what is so; invent no alternative. Offer to book afresh only where the spec provides for it — a patient asking to undo a cancellation |

**Result — unknown**

```json
{ "status": "unknown",
  "explanation": "I could not confirm whether that change was applied. Please check with the clinic." }
```

Returned when the 2s/2-attempt budget was exhausted **after the request was sent**. This is a
distinct status from `unavailable`, not a variant of it (research #13): `unavailable` states that
nothing happened, and FR-023 forbids saying that here. The model must not claim the appointment moved,
must not claim it did not, and must not retry.

---

## `cancel_appointment`

> Cancels a REAL appointment. Cancellation is final — there is no way to un-cancel, and the freed time
> may be taken by someone else immediately. Only call this after the patient has explicitly confirmed,
> in this turn, which appointment is being cancelled. `expected_starts_at` and
> `expected_practitioner_id` must be the values you stated to the patient when you asked them to
> confirm.

```json
{
  "type": "object",
  "properties": {
    "appointment_id": { "type": "string" },
    "expected_starts_at": { "type": "string" },
    "expected_practitioner_id": { "type": "string" }
  },
  "required": ["appointment_id", "expected_starts_at", "expected_practitioner_id"],
  "additionalProperties": false
}
```

Results take the same four shapes, with `"change": "cancelled"` on success. Two differences:

- **Cancelling an already-cancelled appointment is `unchanged`, never `refused`** (FR-017, FR-019).
  It is distinguishable from `appointment_not_found`, which is what an appointment that never existed
  — or belongs to another session — returns (FR-018).
- Only the first four reasons are reachable. A cancellation places nothing, so booking's eight cannot
  refuse it.

---

## `list_my_appointments` — MODIFIED

> Lists this patient's appointments. By default: still to come, and not cancelled. Widen either axis
> only when the patient asks. Every appointment carries an `id` you need in order to change or cancel
> it — never say an id to the patient.

```json
{
  "type": "object",
  "properties": {
    "time_filter":   { "type": "string", "enum": ["future", "past", "both"] },
    "status_filter": { "type": "string", "enum": ["standing", "cancelled", "both"] }
  },
  "required": [],
  "additionalProperties": false
}
```

Both parameters are optional and both default to the **narrowest** value — `future` and `standing` —
so the unqualified question answers FR-014 even when the model sends no arguments at all.

**Result**

```json
{ "future": [ { "id": "01J...", "practitioner_full_name": "William Osler",
                "specialty": "General Practice", "starts_at": "2026-09-02T09:00:00",
                "ends_at": "2026-09-02T10:00:00", "status": "standing" } ],
  "past": [],
  "past_truncated": false }
```

Two legs, never merged (FR-016). The future leg is complete; the past leg holds at most the 20 most
recent, most recent first. `past_truncated: true` means the model must say **that part** of the list
is not complete — not that the whole answer is partial.

`status` appears on every entry, and a cancelled one must be identified as cancelled wherever it is
mentioned (FR-015). An entirely empty result to the default question is "you have nothing booked" —
not "you have cancelled appointments", which surface only when asked for.

---

## `check_availability` — MODIFIED

The schema gains one optional property:

```json
{ "excluded_appointment_id": { "type": "string",
    "description": "the appointment being moved, so it does not block its own new time" } }
```

The model **must** set it when offering times for a change (FR-007). Without it, the appointment's
current slot is missing from its own options and the patient cannot move an appointment to a time
overlapping the one it holds — including keeping the time and changing only the practitioner.

---

## Loop and prompt rules — additions to 005's table

| Rule | Requirement |
|---|---|
| Never call `reschedule_appointment` or `cancel_appointment` without an explicit confirmation **given in the current turn** | FR-024, FR-029, SC-002 |
| A confirmation binds only for the turn it was asked in. After any intervening turn, answer what the patient actually said, then re-state the confirmation in full before accepting a "yes" | FR-029 |
| A reply that neither confirms nor declines is **not** a decline — answer it, keep the offer, and re-ask. Never make the patient restate the appointment, practitioner or time they already gave | FR-029, FR-034 |
| State, before every change: the start date-time, the practitioner's full name, and their specialty. For a move, both the current and the proposed start. For a practitioner swap, both practitioners with both specialties | FR-025–FR-027, SC-003 |
| State the new length or end time whenever the change alters it; say nothing about length when it does not | FR-025, SC-003 |
| When the request could mean more than one upcoming appointment, list the candidates and ask which. Never choose; never act on more than one appointment per confirmation | FR-030 |
| Never state or imply an appointment was moved or cancelled unless a `status: "changed"` or `"unchanged"` result was received **this turn** | FR-028, SC-007 |
| On `status: "unknown"`, say the outcome is not known, do not claim either way, and do not retry | FR-023, SC-007 |
| Never mention an appointment id, a practitioner id, or a tool name to the patient | FR-034 |
| Cancellation is final: offer to book again rather than to "restore", and only after checking the slot is still free | spec Edge Cases |

The turn's `BookingOutcome` gains `rescheduled`, `cancelled`, `unchanged`, and `outcome_unknown`
(data-model.md). As in 005 it is derived from the tool results actually observed, never from the reply
text, and it is what the composing step is constrained by and what the tests assert against.
