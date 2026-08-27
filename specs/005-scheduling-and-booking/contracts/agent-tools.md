# Contract: agent tool registry

**Feature**: `005-scheduling-and-booking` | **Date**: 2026-08-12

The capability seam between the booking specialist and the scheduling service (research.md #1). Each
entry is a `(name, description, input_schema, handler)` record in
`services/chat/src/chat/agent/tools/`; the registry renders the first three into the Anthropic
Messages API's `tools=` parameter and dispatches `tool_use` blocks to the fourth.

**The node never sees gRPC.** Handlers own every provider detail — the stub, the deadline, the
retry, and the translation of a `BookingFailure` into a result the model can read — matching this
codebase's dependency-inversion rule (`.claude/CLAUDE.md`: translating domain data into a provider's
wire format is that provider-calling function's own responsibility).

`search_faq` is **not** in the registry: FAQ answering keeps its own graph node and its own
retrieval → groundedness → generate pipeline, unchanged (FR-034, research.md #2).

---

## Ambient arguments

`session_id`, `patient_id`, and `local_now` are **never** tool parameters. They are bound into each
handler from graph state when the registry is built for a turn. Two reasons: a model cannot invent
or leak a session id it was never shown (FR-002/SC-004), and a model cannot silently substitute a
different "now" than the client supplied (FR-058). Every schema below is closed
(`additionalProperties: false`).

---

## `list_practitioners`

> Lists the clinic's practitioners with their specialties. Call this when the patient asks who is
> available, or before offering appointment times when they named a specialty rather than a person.

```json
{ "type": "object", "properties": {}, "additionalProperties": false }
```

**Result**

```json
{ "practitioners": [
    { "id": "01J...", "full_name": "William Osler", "specialty": "General Practice",
      "appointment_duration_minutes": 60, "bookable": true }
] }
```

`bookable` is false for a practitioner whose grid holds no slot on any weekday — an empty schedule,
or an appointment duration longer than every one of their working ranges, which yields no whole slot
either (FR-019, spec Edge Cases). In both cases the model must say so rather than present a blank
list of times. Answers FR-030; supports FR-052/FR-053 (list the
matches and ask, or name the specialties that do exist).

`specialty` is one of the ten FR-005 values, returned verbatim — Cardiology, Dentistry, Dermatology,
General Practice, Gynecology, Neurology, Ophthalmology, Orthopedics, Pediatrics, Psychiatry. A
closed set does **not** make matching a string comparison: a patient asking for "a dentist", "my
teeth", or "a filling" never types "Dentistry", so mapping their words onto a specialty stays the
model's judgement (research.md #25). The tool takes no specialty filter for the same reason — the
model reads the list and decides, rather than guessing a filter value the patient never supplied.

---

## `check_availability`

> Returns bookable start times for one practitioner over a date range. Only these times can be
> booked. Never offer a time this tool did not return.

```json
{
  "type": "object",
  "properties": {
    "practitioner_id": { "type": "string" },
    "from_date": { "type": "string", "description": "local date, YYYY-MM-DD, inclusive" },
    "to_date":   { "type": "string", "description": "local date, YYYY-MM-DD, inclusive" }
  },
  "required": ["practitioner_id", "from_date", "to_date"],
  "additionalProperties": false
}
```

**Result**

```json
{ "available_starts": ["2026-08-18T09:00:00", "2026-08-18T10:00:00"],
  "appointment_duration_minutes": 60, "truncated": false }
```

Every returned start is bookable **by this patient** at the moment it is returned (FR-024/FR-025,
SC-009) — the handler passes the ambient `patient_id`, so slots colliding with the patient's own
appointments with *other* practitioners are already gone from the list, and the scheduler computes
availability and validates bookings with one code path (research.md #21). An empty list with
`truncated: false` means fully booked or no working time in that window; the model widens the window
rather than reporting nothing (spec Edge Cases).

`truncated: true` means an FR-067 cap bit — the window was clamped to 14 days, or more than 50 starts
existed. The model must then offer to look further ahead instead of presenting the list as the
practitioner's whole availability. Asking for a range wider than 14 days is never an error; it comes
back clamped and marked.

The guarantee is scoped to the moment of the call: another patient can take an offered slot before
this one confirms, in which case `book_appointment` returns `practitioner_busy` and the model
explains it like any other conflict. That race is the only acceptable reason an offered time is
later refused (FR-025).

---

## `book_appointment`

> Creates a REAL appointment. Only call this after the patient has explicitly confirmed both the
> practitioner and the exact start time. There is no way to cancel or change an appointment in this
> version, so never call it to "check" whether something is possible — use check_availability for
> that.

```json
{
  "type": "object",
  "properties": {
    "practitioner_id": { "type": "string" },
    "starts_at": { "type": "string", "description": "local date-time, YYYY-MM-DDTHH:MM:SS" }
  },
  "required": ["practitioner_id", "starts_at"],
  "additionalProperties": false
}
```

The handler derives the idempotency key from `(patient_id, practitioner_id, starts_at)` (research.md
#8) — it is not a parameter, so the model cannot weaken it, and the derivation is what satisfies
FR-062 (same booking ⇒ same key). Because the key is a function of exactly the fields the scheduler
re-checks it against, an `INVALID_ARGUMENT` key mismatch (FR-063) can only mean this derivation
broke; the handler treats it as non-retryable, returns the `unavailable` result below — which states
plainly that nothing was created — and logs it as a defect rather than surfacing it as a booking
conflict the patient could act on.

**Result — success**

```json
{ "status": "booked", "appointment": {
    "id": "01J...", "practitioner_full_name": "William Osler",
    "starts_at": "2026-08-18T09:00:00", "ends_at": "2026-08-18T10:00:00" } }
```

Returned identically whether the appointment was created now or the key matched an earlier attempt —
a lost confirmation is invisible to the patient, and their own booking is never reported as a
conflict with itself (FR-051, US1-7).

**Result — refused**

```json
{ "status": "refused", "reason": "practitioner_busy",
  "explanation": "That time was taken while we were talking." }
```

`reason` is one of the eight `BookingFailureReason` values — the closed set FR-065 fixes, one reason
per attempt even when several rules were broken, chosen by that requirement's precedence. Because the
set is closed and the choice deterministic, the handler's sentence-per-reason mapping is total and
each refusal is reproducible from the request alone. `explanation` is that fixed, handler-authored
sentence — the model rephrases it for the patient (FR-029) but has no freedom to invent a different
cause.

**Result — unavailable**

```json
{ "status": "unavailable",
  "explanation": "Booking is temporarily unavailable. Nothing was booked." }
```

Returned after the 2s/2-attempt budget is exhausted (FR-047), and also when the chat has no patient
record yet (FR-044/US2-3). The wording states plainly that nothing was created, so the model cannot
read ambiguity into it (FR-028/FR-046).

---

## `list_my_appointments`

> Lists this patient's upcoming appointments, earliest first. Past appointments are not available.

```json
{ "type": "object", "properties": {}, "additionalProperties": false }
```

**Result**

```json
{ "appointments": [
    { "practitioner_full_name": "William Osler", "specialty": "General Practice",
      "starts_at": "2026-08-18T09:00:00", "ends_at": "2026-08-18T10:00:00" } ] }
```

Filtered to starts strictly after the client's `local_now` (FR-031/FR-058); an appointment that has
already started disappears from this list while still blocking overlapping bookings (FR-023, spec
Edge Cases). An empty list is an explicit "you have nothing upcoming", never an error (US3-3).

---

## Loop and prompt rules

| Rule | Requirement |
|---|---|
| At most 6 tool iterations per turn; the 7th ends the loop with a plain failure reply | prevents an unbounded loop |
| Confirm practitioner **and** exact start time before `book_appointment` | FR-027 |
| Never state or imply an appointment exists unless a `status: "booked"` result was received this turn | FR-028, SC-008 |
| When several practitioners match, list them and ask — never choose | FR-052 |
| When none matches, say so and name the specialties this session actually has — not the full ten | FR-053, #25 |
| Express every time in plain local time; never mention a timezone, an id, or a tool name | FR-026, FR-033 |
| The system prompt states the patient's name and the client's `local_now` | FR-032 |
| Conversation context is bounded to the last 5 turns, as every other model call in the graph is | research.md #23 |

The 5-turn bound applies to the *conversation history* the loop starts from. The `tool_use` and
`tool_result` blocks accumulated **within** the current turn are not history and are never dropped —
truncating them mid-loop would hide from the model what it has already booked or been refused.

The turn's `BookingOutcome` (`booked` / `refused` / `unavailable` / `awaiting_confirmation` /
`informational`) is derived from the tool results actually observed — never from the reply text —
and is what `compose_answer` is constrained by and what the tests assert against.
