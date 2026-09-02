# Contract: agent tool registry — the 007 delta

**Feature**: `007-escalation-and-staff-console` | **Date**: 2026-09-01

Extends [005's registry contract](../../005-scheduling-and-booking/contracts/agent-tools.md) and
[006's delta](../../006-reschedule-and-cancel/contracts/agent-tools.md), both of which remain
authoritative for everything not restated here — the `(name, description, input_schema, handler)`
record shape, the closed schemas, the ambient arguments, and the rule that handlers own every
provider detail so the node never sees gRPC.

**One tool is added. No existing tool changes shape.** `search_faq` is still not in the registry —
the FAQ path is a graph node, not a tool, and 1e does not change that.

---

## `escalate_to_staff` — NEW

> Hands this conversation to the clinic's staff, who will reply in this same conversation. Call this
> when the visitor asks to speak to a person, a human, staff, or the clinic itself. Do NOT call it
> because you are unsure of an answer, because a booking was refused, or because a tool failed —
> those are handled elsewhere. After calling it, tell the visitor that a staff member has been
> notified and will reply here, and do not promise a response time.

```json
{
  "type": "object",
  "properties": {},
  "required": [],
  "additionalProperties": false
}
```

| Property | Value | Why |
|---|---|---|
| `requires_patient` | `false` | FR-002: available in every conversation regardless of whether the chat has a patient record. |
| `writes` | `false` | It creates nothing the patient cannot undo, and a failure to record it is not an unknown outcome — the conversation is escalated or it is not. |

### The schema is empty, and that is the contract

The tool takes no arguments at all — not a reason, not a summary, not the patient's words.

**No reason parameter.** A reason exists (FR-007a) and it is a closed set of three, but the model can
only ever raise **one** of them: the other two are decided by a gate and by a failure, neither of
which runs inside a model turn (FR-001a). A `reason` parameter would therefore be a field with one
legal value that a model could nonetheless get wrong, and getting it wrong would mis-set the
conversation's silencing state. The caller identity *is* the reason, so it is bound by the handler,
never supplied.

**No summary parameter.** The spec's Assumptions rule it out: the thread is what says what the
patient wanted, and a generated summary that can be wrong would be a second, less reliable account
of it sitting beside the real one.

**Result shape:**

```json
{ "status": "ok", "explanation": "Staff have been notified and will reply in this conversation." }
```

Always `ok`. There is no failure this handler can report, because it performs no I/O: it records a
request into the turn's collector, and the transition is applied once the turn completes (below).

---

## The handler records; it does not transition

`escalate_to_staff`'s handler writes **nothing**. It appends a request —
`(reason=patient_asked_for_person, message_id=<the turn's causing patient message>)` — to the turn's
`EscalationRequests` collector, and returns. `turn.py` applies the collected result once, after the
graph has completed.

Two requirements force this shape, and only this shape satisfies both:

- **FR-006**: a turn that escalates must run to completion first, and the escalated state takes
  effect at the end of it. So no caller may write the transition when it decides.
- **FR-001a**: escalation is one capability with one implementation, reachable by several callers,
  every one of which must produce the same state, the same record, and the same reason handling. So
  the three callers cannot each write their own.

The consequence a reader should expect: within the turn that escalates, the assistant still speaks —
that is FR-005's message telling the patient staff have it. Silence begins with the **next** message
(spec Edge Cases). A mixed-intent turn delivers both halves and escalates at the end of it —
*unless* the classifier labelled the message `call_staff`, which takes the whole turn on its own
(below).

---

## The other three callers of the same capability

None is a tool, and none runs inside a model turn — which is exactly why they call the handler
directly rather than being given a tool to choose (FR-001a). They record into the same collector and
are applied by the same `apply_escalation()`.

| Caller | Where | Reason recorded | Silences? |
|---|---|---|---|
| the **router** | `classify_intent_node`, on `call_staff` among the classified intents | `patient_asked_for_person` | yes |
| the abstention gate | `answer_faq`, on `is_grounded(...) == False`, **before** any generation call | `corpus_could_not_answer` | yes |
| the failure path | `handle_booking`, on an unreachable dependency, an unknown write outcome, or a tool error | `assistant_failed` | **no** (FR-003d) |

**The router records rather than routing to a specialist that can call the tool.** `call_staff` is
already a decision: the classifier has read the message and concluded the visitor asked for a
person, and asking a second model to reach that conclusion again is a call that can disagree with
the first. It also removes the dependence on retrieval that routing to the FAQ path creates — that
path escalates only by *abstaining*, so a corpus that happened to ground the sentence the visitor
used to ask for a human would answer them from the clinic's documents and fetch nobody.

**And `call_staff` takes the whole turn.** It selects no specialist: nothing is retrieved, nothing
is generated, nothing is booked, and the turn's entire reply is one fixed sentence saying a staff
member has it and will reply in this conversation (FR-005). The label suppresses every other one on
the same message — "book me Friday and have someone call me" books nothing.

Two reasons, and the second is the one that decides it. A visitor who has asked for a person is
going to get one, and the conversation falls silent from their *next* message: answering half of
what they said and then going quiet without explanation is worse than handing over cleanly. And
writing an appointment for a patient who has just asked to stop talking to a machine is the harder
of the two things to undo.

This does not weaken FR-006. That requires a turn that escalates to run to completion, with every
specialist it *selected* finishing — and this selects none, so nothing is interrupted.

**The abstention gate raises no model call to decide.** The signal that produces the abstention is
the signal that escalates — the same one, at the same moment — so the two can never disagree
(FR-003b), and no model call is spent re-deciding something a gate already decided. Phase 1e
replaces that gate; it does not replace this wiring.

**The failure path is narrow, and the line is FR-003a.** A *refusal* is an answer — the slot is
taken, the time is outside the practitioner's hours, the appointment has already started — and 006
already requires an alternative to be offered with each. It escalates nothing. A *failure* is the
absence of an answer, and it is these three, all of which already exist as distinct values in 006's
result vocabulary:

| 006 result | Escalates? |
|---|---|
| `status: "refused"` with a `ChangeFailureReason` / `BookingFailureReason` | **no** — the assistant explains it and offers alternatives |
| `status: "unavailable"` (scheduling unreachable, nothing happened) | yes, `assistant_failed` |
| `status: "unknown"` (a write whose outcome never came back) | yes, `assistant_failed` |
| a handler raising, or an unregistered tool name | yes, `assistant_failed` |

`ToolArgumentError` is deliberately **not** in that list: it is raised while reading the arguments,
before the handler calls anything, and the model gets another attempt inside the same turn. An
escalation there would call a person for a model's typo that the model then corrected.

---

## Resolution when one turn raises more than one

A mixed-intent turn can raise two — an abstaining FAQ half and a failing booking half — against the
same patient message. The collector resolves them without either branch knowing about the other:

- **the conversation's escalation**: the highest-precedence *silencing* reason, if any, and it is
  set once and never overwritten by a later request (FR-007);
- **the message's mark**: the highest of `patient_asked_for_person` > `corpus_could_not_answer` >
  `assistant_failed` (research #6);
- **`attention_since`**: set if unset, by any of the three (FR-003d);
- **the log**: one record per request, so nothing is lost to the precedence (FR-033).

Because the resolution is a precedence over a set, the order in which two concurrent specialists
record is irrelevant — which is what makes a shared mutable collector safe where a LangGraph state
key would not be.

---

## Unchanged, and worth stating

- **`classify_intent` is untouched, and its `call_staff` label finally does something.** The
  classifier's schema, its model and its output set are unchanged; what changes is that the router
  reading its result now records a call to staff when the label is present, instead of dropping the
  label on the floor and falling through to the FAQ path. No new intent, no new model, no new
  schema, and no second model call.
- **Ambient context is unchanged.** `session_id`, `patient_id` and `local_now` stay bound from the
  turn, never model-supplied. The escalation collector joins them as ambient state for the same
  reason: a model must not be able to escalate a conversation other than the one it is in.
- **No tool is added for the staff side.** Staff post through HTTP, not through the agent (FR-044);
  the model has no capability to write as staff, to end an escalation, or to pause itself.
