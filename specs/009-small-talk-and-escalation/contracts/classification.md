# Contract: Classification and Routing

The classifier's output is the only decision this phase makes; everything downstream is a table
lookup. This file is the table.

## Label rules (the prompt's contract)

| Label | Applies when | Does **not** apply when |
|---|---|---|
| `small_talk` | The message asks for nothing that can be acted on: a greeting, an acknowledgement, a thank-you, a farewell, a reaction, a statement of thinking it over, or a fragment with no recoverable meaning | Anything else applies. A need implied but not stated ("my tooth is killing me") is a request, not small talk |
| `urgent_condition` | The message describes a condition needing immediate attention | The condition is described in the past, or as history |
| `distress` | The message expresses fear, panic, or acute upset | Ordinary frustration inside an otherwise routine request |
| `booking_for_another` | The message **explicitly** says the appointment is for someone other than the person in this chat | Another person is merely mentioned ("my wife recommended you"), or who it is for is unclear — both are `booking` |
| `call_staff` | The patient **explicitly asks for a human** | A staff-handled topic without that request — that is `unknown` |
| `unknown` | The message is a request the assistant is not authorized to serve (a sick note, a prescription, a billing correction) | The message asks nothing — that is `small_talk` |
| `faq_question`, `booking` | unchanged from today | — |

**Two standing biases, both stated in the spec:**
1. Between small talk and a request, choose the request (FR-007).
2. Between a stopping cause and an ordinary one, prefer the stopping cause **only** on an explicit
   signal — for `booking_for_another` this is a hard rule (FR-045a), because a false positive costs
   the patient the assistant until a person intervenes.

## Routing table

Read top to bottom; the first row that matches decides the turn.

| Condition on the label set | Specialists run | Escalation recorded | Reply |
|---|---|---|---|
| contains `urgent_condition` | none | `urgent_condition` (silences) | that cause's constant |
| contains `distress` | none | `distress` (silences) | that cause's constant |
| contains `call_staff` | none | `patient_asked_for_person` (silences) | today's hand-off constant |
| contains `booking_for_another` | none | `booking_for_another_person` (silences) | that cause's constant |
| contains `unknown` **and** a servable label (`faq_question`, `booking`) | those servable ones | `not_authorized` (does not silence) | merged reply, which must convey the notice (contracts/replies.md) |
| contains `unknown` alone | none | `not_authorized` (does not silence) | that cause's constant |
| contains `faq_question` and/or `booking` | those | none from routing | as today |
| `small_talk` alone | small-talk node | none | generated, bounded |
| `classification_failed` | FAQ path | none from routing | as today |
| anything else / empty of known labels | FAQ path | none from routing | as today |

**Consequences that must hold**
- `small_talk` never routes when any other label is present (FR-008); it is dropped, not merged.
- The first four rows suppress *every* other label, including each other — precedence decides which
  cause is recorded, and the discarded ones are logged (FR-046, FR-047).
- An **unclear** beneficiary is `booking`, not `booking_for_another`, and the booking path
  establishes who the appointment is for before writing anything (FR-045b). The stopping cause is
  reserved for a message that says so explicitly.
- No row causes a scheduling call for `booking_for_another` (FR-045). The refusal precedes the
  boundary; the scheduler never hears about it.
- `classification_failed` reaches none of the new routes (FR-009).

## Invocation

Unchanged: one call, `CLASSIFICATION_MODEL`, JSON Outputs against a schema whose enum excludes
`classification_failed`, bounded to `CONTEXT_TURNS` of history. The added labels extend the enum in
the same generated schema; nothing else about the call changes (FR-002).
