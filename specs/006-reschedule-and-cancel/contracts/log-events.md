# Contract: structured log events — the 006 delta

**Feature**: `006-reschedule-and-cancel` | **Date**: 2026-08-29

Extends [005's event set](../../005-scheduling-and-booking/contracts/log-events.md), which remains
authoritative for everything not restated here. Every event still flows through its service's single
processor chain and inherits the bound correlation id — `turn_id` chat-side, propagated as
`x-turn-id` gRPC metadata and re-bound scheduler-side — so a change and the conversation that caused
it join on one key (FR-039).

**Unchanged**: every node lifecycle event, every `booking.*` event, `scheduling.call`,
`scheduling.unavailable`, and the whole chat/provisioning set. The node keeps its name and so do its
events (research #15).

---

## Where each record is emitted, and why

FR-036–FR-041 want one record per thing that happened. Each is emitted by the process that actually
observed it:

| Fact | Emitted by | Because |
|---|---|---|
| an appointment moved, or was cancelled | **scheduler** | it alone knows both sides of the write atomically (research #6) |
| a request transitioned nothing | **scheduler** | same statement, same knowledge |
| a change was refused, with its one reason | **scheduler** | the reason is decided there, by the fixed precedence |
| the outcome is unknown | **chat** | the scheduler never learns that its answer was lost |

**All of these are best-effort.** Recording follows the change; it does not gate it. A failure to
write a record must not fail, retry, or roll back a change that has already happened, and must not
alter what the patient is told (FR-041). SC-009's "100% recoverable from the logs" is measured over
runs in which the logging path is working — it is not a licence to build an outbox (research #14).

---

## Scheduler service — new events

| Event | Level | Fields | When |
|---|---|---|---|
| `appointment.rescheduled` | info | `appointment_id`, `old_starts_at`, `new_starts_at`, `old_practitioner_id`, `new_practitioner_id` | The update moved the row. Both practitioner fields are always present, equal when the practitioner did not change — FR-038: without them a same-time swap logs as a change from a time to the identical time, which reads as a change that did nothing. |
| `appointment.cancelled` | info | `appointment_id`, `old_starts_at`, `practitioner_id` | The status was set. **Carries no new-start field at all** — not an empty or placeholder one — which is what makes a cancellation distinguishable from a move at a glance (FR-037). |
| `appointment.unchanged` | info | `appointment_id`, `operation` (`reschedule`/`cancel`), `starts_at` | The request completed but transitioned nothing: the appointment was already in the state asked for (FR-040). Its own event kind, so one `appointment.rescheduled` still means one move — a re-sent change must not make the log show two moves where one happened (SC-009). |
| `change.refused` | info | `appointment_id`, `operation`, `reason` | An evaluated refusal — a normal outcome, not an error, exactly as `booking.refused` is. `reason` is one of the twelve, already resolved by precedence. |
| `change.key_released` | info | `appointment_id`, `idempotency_key` | Emitted with a cancellation. The key left the partial unique index in the same statement, so the freed slot rebooks as an ordinary new booking (FR-011). Logged because a released key is the one consequence of cancellation that is invisible in the row itself. |

`appointment.unchanged` and `appointment.rescheduled` are mutually exclusive for one request — that is
the point of having both.

## Chat service — new event

| Event | Level | Fields | When |
|---|---|---|---|
| `change.outcome_unknown` | error | `operation`, `appointment_id`, `attempts` | A change was sent and the 2s/2-attempt budget was exhausted without an answer (FR-023). Accompanied by the existing `scheduling.unavailable` and `critical.dependency_unreachable`, which describe the transport failure; **this** event records the domain consequence — that the outcome of a write is genuinely unknown. Error level because an unknown write outcome is an operator's problem even though the turn answered the patient correctly. |

This is the only new chat-side event **that records something about the domain**. A refusal and a
no-op are already visible chat-side through `booking.tool_result`, whose `status` field carries the
tool result's own value — now including `changed`, `unchanged` and `unknown`
(contracts/agent-tools.md).

The change path also emits three error-level **defect diagnostics** — `change.unknown_failure_reason`
and `change.response_without_result` from the client, and `change.response_unreadable` from the
handler. They are deliberately not enumerated as contract events, following 005's own convention for
`booking.unknown_failure_reason` and `scheduling.unknown_not_found_entity`: each records that this build could not read something the
contract promises, which is a deployment skew or a defect rather than a fact about an appointment.
Nothing consumes them, and none of them describes a change. A record that *does* describe a change
belongs in the table above.

---

## What does NOT get a record

| Situation | Why no change record |
|---|---|
| the patient declined the confirmation | nothing was sent; there is no change to record (FR-040, SC-010) |
| the assistant asked for confirmation and is waiting | same |
| a change was refused | recorded as `change.refused`, never as a completed change (FR-040) |
| the outcome is unknown | recorded as `change.outcome_unknown`, never as a completed change |

`node.completed`'s `result` payload for `handle_booking` is unchanged in shape; its `outcome` field
now also carries `rescheduled`, `cancelled`, `unchanged`, and `outcome_unknown`.

---

## Privacy

005's rule stands and extends cleanly: the new events carry ids, times and reasons — never the
patient's raw message, never a reply body, and never a practitioner's or patient's name. A change
record is joinable to the conversation by `turn_id` for anyone who is entitled to read both; it does
not restate the conversation.
