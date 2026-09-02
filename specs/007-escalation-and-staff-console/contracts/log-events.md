# Contract: structured log events — the 007 delta

**Feature**: `007-escalation-and-staff-console` | **Date**: 2026-09-01

Extends [005's event set](../../005-scheduling-and-booking/contracts/log-events.md) and
[006's delta](../../006-reschedule-and-cancel/contracts/log-events.md), which remain authoritative
for everything not restated here. Every event flows through its service's single processor chain and
inherits the bound correlation id — `turn_id` chat-side, `operation_id` for a console/FAQ operation,
propagated as `x-turn-id` gRPC metadata and re-bound scheduler-side.

**All of these are best-effort.** Recording follows a transition; it never gates one. A log entry
that fails to be written cannot un-happen a handoff that already occurred (FR-034), which is 006's
rule for change records unchanged. SC-010's "100% recoverable from the logs" is measured over runs
in which the logging path is working.

---

## Escalation and silence — chat service

| Event | Level | Fields | When |
|---|---|---|---|
| `escalation.raised` | info | `chat_id`, `reason`, `message_id`, `silenced` (bool), `turn_id` (bound) | A call to staff **transitioned** something: the conversation became escalated, or (for `assistant_failed`) it became emphasized without being silenced. `silenced` is what distinguishes those, and is `false` exactly when `reason` is `assistant_failed` (FR-003d). |
| `escalation.unchanged` | info | `chat_id`, `requested_reason`, `existing_reason`, `message_id`, `turn_id` | A call that **transitioned nothing**: the conversation was already escalated, or already emphasized, or a person had taken it over while the turn ran. **Its own event kind**, so one `escalation.raised` still means one handoff (FR-007, SC-010). Both reasons are carried, because the point of the record is that the second did **not** overwrite the first — `existing_reason` is null where there is no escalation to name, which is every no-op that is not one. |
| `escalation.ended` | info | `chat_id`, `ended_by` (`staff_message` \| `switch`), `escalated_for` (the reason it had), `waited_seconds` | The silencing state was cleared. Exactly two things can do it and the field names which (FR-009a). `waited_seconds` is how long the conversation was escalated, which is the one number this phase records about response time. |
| `assistant.paused` | info | `chat_id`, `until`, `paused_by` (`staff_message` \| `switch`), `restarted` (bool) | The 2-minute pause started or restarted (FR-013, FR-014, FR-017b). `restarted` is true when a pause was already running, which is what makes a sequence of staff messages legible as one lead rather than several. **`paused_by` is the only place the two triggers differ at all** — the deadline they write is identical, so this field exists to make a silence traceable, not because anything behaves differently (research #24). |
| `assistant.resumed` | info | `chat_id`, `resumed_by` (`expiry` \| `switch`) | The assistant may speak again. Exactly two things can do it. Note `expiry` is **not** emitted by a timer — nothing runs when a deadline passes; it is emitted by the first turn that finds the pause elapsed, which is the moment the resumption becomes observable. A **staff message is not a resume**: it ends an escalation and starts a pause, so the assistant stays silent across it (FR-009a, FR-013). |
| `message.unanswered` | info | `chat_id`, `message_id`, `silenced_by` (`escalation` \| `pause`), `turn_id` | A patient message arrived while the assistant was silent, was kept, and was marked (FR-019). `silenced_by` says which of the two states was in force, which the mark itself does not record. |
| `staff.message_posted` | info | `chat_id`, `message_id`, `marks_cleared` (int), `ended_escalation` (bool), `cancelled_generation` (bool) | A staff member posted. The three booleans/counts are the three side effects of one act (FR-009a, FR-013a, FR-027c) — a reply that cleared four marks and cancelled a generation is a different event from one that cleared none. |

`escalation.raised` and `escalation.unchanged` are mutually exclusive for one request. That is the
whole point of having both: SC-010 counts escalation records against conversations actually
silenced, and a no-op that logged as a raise would over-count every one of them.

**No event is emitted when a mark is cleared on its own** — clearing is never on its own; it is one
of `staff.message_posted`'s effects, and a second event would double-count one act.

### `turn.completed` — unchanged in name, one new `outcome` value

| Value | When |
|---|---|
| `handed_off` | **NEW** — the classifier labelled the message `call_staff`, so the turn fetched a person and produced no answer: no retrieval, no generation, no tool call. Carries `answer_source: "hand_off"`, `grounded: null`, and no citations. |

The existing values — `grounded`, `abstained`, `booking` on a single-specialist turn,
`merged` on a mixed one — are unchanged.

**It is derived from the answer source, not from the absent groundedness verdict.** A handoff and a
booking reply both carry `grounded: null`, so reading the outcome off that alone would file every
handed-off turn in the log as a booking — and the one number SC-010 and the escalation records exist
to make countable is how often a person was actually fetched.

---

## The FAQ write path — chat service

`faq.entry_created`, `faq.entry_updated`, `faq.entry_deleted`, `faq.operation_failed`,
`faq.content_chunked` and `faq.chunks_embedded` are **unchanged in name**. Three gain a field and
one event is added:

| Event | Level | Change | Notes |
|---|---|---|---|
| `faq.entry_created` | info | `+ session_id`, `+ revision` | The revision published by this create. |
| `faq.entry_updated` | info | `+ session_id`, `+ revision`, `+ superseded_revision` | Both revisions, because "which text is the assistant answering from now" is the question this record exists to answer. |
| `faq.entry_deleted` | info | `+ session_id` | |
| `faq.publish_conflict` | info | **NEW** — `entry_id`, `session_id`, `expected_revision` | The publishing `UPDATE` matched no row: another save had already superseded the revision this one read (FR-042c). An ordinary outcome, not an error — the loser is reported as a failed, retryable save. |
| `faq.create_refused` | info | **NEW** — `session_id`, `entry_count`, `cap` | A create refused because the corpus is at its cap (FR-039f). Logged so a session hitting the ceiling is visible without inferring it from a 409. |

### The sweep logs nothing, and that is a requirement

FR-042h forbids it in terms: a failed sweep raises **no event of any kind**, and in particular must
not raise `critical.dependency_unreachable`. That event means *an operation could not be completed*,
and a sweep is not an operation — chunks that are not live are already unreachable. An event raised
for housekeeping would sit alongside events raised for operations that failed, which is the
confusion this path spent its design avoiding.

The same applies to the chunk removal that follows a delete (FR-042f) and to the session-wide sweep
that follows an admin deletion.

`critical.dependency_unreachable` is otherwise unchanged and still fires for the retrieval store,
Postgres, the embedding service and the model API when an operation actually failed against them.

---

## The admin surface — chat service

| Event | Level | Fields | When |
|---|---|---|---|
| `admin.refused` | warning | `route` | The secret was absent, wrong, or not configured. **Carries nothing about the attempt**: not the supplied value, not its length, not which of the three it was (FR-048, SC-019). |
| `session.deleted` | info | `session_id`, `chats_deleted`, `faq_entries_deleted`, `patients_deleted`, `practitioners_deleted`, `appointments_deleted` | One session removed from both stores. |
| `session.delete_incomplete` | warning | `session_id`, `failed_at` (`scheduling` \| `chat_store`) | One store was updated and the other was not. Paired with FR-051's per-session `incomplete` in the response, so the log and the admin's screen agree. |

The secret itself never appears in any of these. It is added to the chat service's
`_SECRET_SETTINGS_FIELDS`, so the existing redaction processor matches its live value anywhere in an
event dict and `_SECRET_KEY_PATTERN` catches any key containing "secret" — no new redaction path is
introduced (FR-050).

---

## The scheduler service

| Event | Level | Fields | When |
|---|---|---|---|
| `session.purged` | info | `session_id`, `patients_deleted`, `practitioners_deleted`, `appointments_deleted` | `DeleteSession` completed. Emitted scheduler-side because it alone knows the counts atomically — the same reasoning 006 applied to `appointment.rescheduled`. |

No other scheduler event changes. Practitioner administration through the console emits exactly what
the practitioner API already emits, because it *is* that API — the proxy adds no event of its own
beyond the transport failures below.

---

## Where each fact is recorded, and why there

| Fact | Emitted by | Because |
|---|---|---|
| a conversation was silenced, or emphasized without silencing | chat | the state is chat-side, and only the turn knows which trigger fired |
| a call to staff changed nothing | chat | same statement, same knowledge |
| a patient message went unanswered | chat | it is the gate that declined to reply that knows why |
| a revision was published, or lost a race | chat | the publishing commit is the only moment it becomes true |
| a session's scheduler-side rows are gone, with counts | scheduler | it alone knows them atomically |
| a deletion was incomplete | chat | it is the caller that spans both stores, so it alone can observe the partial outcome |
