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
| `escalation.ended` | info | `chat_id`, `ended_by` (`staff_message` \| `switch`), `escalated_for` (the reason it had), `waited_seconds` | The silencing state was cleared. Exactly two things can do it and the field names which (FR-009a). `waited_seconds` is how long the conversation was escalated, which is the one number this phase records about response time. **Emitted only when the clearing write actually landed** — a conversation deleted while the gesture ran had its escalation ended by nothing, and a phantom entry here is worse than a phantom elsewhere, since its duration inflates every count and response time taken over the event. |
| `assistant.paused` | info | `chat_id`, `until`, `paused_by` (`staff_message` \| `switch`), `restarted` (bool) | The 2-minute pause started or restarted (FR-013, FR-014, FR-017b). `restarted` is true when a pause was already running, which is what makes a sequence of staff messages legible as one lead rather than several. **`paused_by` is the only place the two triggers differ at all** — the deadline they write is identical, so this field exists to make a silence traceable, not because anything behaves differently (research #24). **Emitted only when the write actually landed** — a conversation deleted between the gesture resolving it and the write reaching it was never silenced, and an entry there would be a silence to count that never happened. |
| `assistant.resumed` | info | `chat_id`, `resumed_by` (`switch`) | The assistant may speak again. A **staff message is not a resume**: it ends an escalation and starts a pause, so the assistant stays silent across it (FR-009a, FR-013). **Emitted only when the write actually landed** — a conversation deleted between the switch resolving it and the clears reaching it was never resumed, and an entry there would be a resumption to count that never happened. |
| `message.unanswered` | info | `chat_id`, `message_id`, `silenced_by` (`escalation` \| `pause`), `turn_id` | A patient message arrived while the assistant was silent, was kept, and was marked (FR-019). `silenced_by` says which of the two states was in force, which the mark itself does not record. |
| `staff.message_posted` | info | `chat_id`, `message_id`, `marks_cleared` (int), `ended_escalation` (bool), `cancelled_generation` (bool) | A staff member posted. The three booleans/counts are the three side effects of one act (FR-009a, FR-013a, FR-027c) — a reply that cleared four marks and cancelled a generation is a different event from one that cleared none. **Emitted only when the conversation was still there when the post finished** — a chat deleted mid-post takes the message with it by cascade, and an entry there would name a message id nothing can be found under, counting a reply the patient never saw as one they were sent. |

**`resumed_by` has one value in this build, `switch`, and the switch is its only emitter.** A pause
elapsing was to have been a second value, `expiry`, emitted by the first turn that found the
deadline passed — nothing runs when a deadline passes, so that turn is the moment the resumption
becomes observable. It is not built: no site emits it, and the value is left out of the field's
domain rather than documented, so an operator filtering for it reads zero rows as "not a thing this
build records" rather than as "no pause ever expired".

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

### `turn.error` — unchanged in name, one new `pipeline_step` value

| Value | When |
|---|---|
| `persistence` | **NEW** — the turn's own writes failed: the reply's insert, the takeover read, the escalation writes, or the commit that makes them durable. (Not the lock release — that one is recorded as `chat.lock_release_failed` and never raises, because nothing it reports can undo a write that has committed.) The existing values — `embedding`, `retrieval`, `groundedness`, `generation` — each name a step of the pipeline that produces an answer; this one names the section that stores it, which runs after the pipeline is done. |

**It exists because `unknown` was standing for two different failures.** The writes run under the
same catch-all as the graph, so a store that dropped its connection during them was recorded as
`pipeline_step="unknown"` — indistinguishable from a graph node blowing up, and pointing whoever
reads it at the pipeline instead of the store. `unknown` remains, and now means what it says: a
failure this build could not attribute to any step.

**It raises no `critical.dependency_unreachable`.** That event is scoped to the dependencies an
answer is produced from (FR-015), and a write the store refused is not the store being unreachable —
`persistence` is deliberately absent from the step-to-dependency mapping alongside `embedding` and
`groundedness`.

**`generation` covers every model call a turn can fail on**, not just the FAQ specialist's: the
merge's composing call and each iteration of the booking loop's tool-use call are tagged with it
too. The dependency is the same one and the outage is the same outage, so which specialist the
classifier happened to pick must not decide whether FR-015's alert fires. The booking loop's
*tools* are outside that tag — a tool that fails is answered to the model and the loop continues,
and a scheduler outage is already reported as `dependency="scheduler"` by the client that made the
call.

**The classification call is the one model call `generation` does not cover, and it raises the
alert itself.** It cannot be tagged, because a failed classification does not fail the turn: it is
recorded as `classification_failed` and the turn falls back to the FAQ path, so no
`TurnPipelineError` is raised and `turn.error` never fires for it. That left one outage silent —
with the model API unreachable and a corpus that cannot answer the question, the classification
call is the *only* model call the turn makes, since the FAQ path abstains before generating, and
the turn ended with no `turn.error` and no alert. So `intent.classification_failed` raises
`critical.dependency_unreachable` (`dependency="anthropic_api"`) beside itself, leaving what the
turn does for the patient exactly as it was. A turn whose corpus *does* answer raises it twice —
once here and once from the generation step — which is two failed calls to one dependency honestly
reported, not one event counted twice.

**`intent.classification_failed` gains `failure` (`unreachable` | `timed_out` | `answered`) and
`dependency_unreachable` (bool, true exactly for `unreachable`), and only `true` raises the
alert.** Not every classification failure is a dependency failure: a response that came back and
would not validate against the structured-output schema, or one carrying `classification_failed`
itself, is the API answering — what failed is the response, not reaching it, and an alert that
fires for those is one an operator learns to ignore. `unreachable` is set only for the shapes that
mean the request was never served: a connection error, or a 5xx (the API answering that it is not
serving). A 4xx and an exhausted rate limit are answers, and are `answered`.

**A timeout is its own value, and raises no alert.** A deadline is the caller's, not the callee's:
it expiring means the answer did not arrive, not that the request went unserved — the project's
"a timeout never proves the server did nothing" rule. The SDK compounds this by making
`APITimeoutError` a subclass of `APIConnectionError`, and httpx maps a *pool* timeout onto it too,
so an alert that fired on it would page an operator for this service's own connection limit. It is
still recorded, under `failure="timed_out"`, so a run of them is visible without any of them
claiming an outage — the same suppression the scheduling client already applies to its own
`DEADLINE_EXCEEDED`, where the outcome is unknown rather than known to be nothing.

**One decider serves both sites.** `chat.clients.anthropic_failure.classify_failure` is what
answers "was the model API unreachable" for the classifier's failure handler *and* for the
`generation` pipeline step, so the two cannot drift into calling the same status an outage in one
place and a defect in the other. It has to be applied at the `generation` site as well as here,
because the step name says only which call failed: all three generation sites wrap a bare
`except Exception`, so a rotated key's 401, an exhausted rate limit and a schema 400 arrive tagged
`generation` alongside a real outage. `retrieval`/`qdrant` carries no such test — every failure of
it is still reported as an outage, which is what it has always done.

### `turn.chat_vanished` — new

| Event | Level | Fields | When |
|---|---|---|---|
| `turn.chat_vanished` | info | `chat_id`, `vanished_before` (`patient_message` \| `reply`), `message_id` (nullable), `turn_id` (bound) | The conversation a running turn was answering stopped existing under it: `DELETE /chats/{chat_id}` landing in one of the two windows no cancellation covers — before the turn's message was written, so the registry did not hold it yet, or after it deregistered and before its reply was written — or the admin session sweep, which deletes the row and cancels nothing at all, so it reaches a turn in any state. Nothing was stored either way, and the turn ends in `cancelled`. |

**`info`, not `error`, and its own event.** It is a race a turn is built to lose safely — nothing is
written and nothing is inconsistent — so it must not read like `turn.error`. Its own kind, rather
than folded into the takeover no-op, because no person did anything: recording it as a takeover
would put a staff member in a conversation nobody ever touched.

**`vanished_before` names the window, and `message_id` names a row or names nothing.** The two
windows are different events for whoever reads this: one lost the turn of a message that is
committed, the other never wrote the message at all. Nothing else in the entry tells them apart — a
turn's message reuses its `turn_id` as its id, so both windows had the same string to offer and the
two entries came out identical in every field. `message_id` is therefore **null** in the
`patient_message` window, where no row with that id exists or ever will, and carries the committed
patient message — the one the reply would have answered — in the `reply` window. An operator
correlating on it finds a row or finds a declared absence, never an id nothing was stored under.
The `reply` window covers a turn that had no reply to store as much as one whose reply's insert
found the chat gone: both committed the message and stored no answer to it, which is the whole of
what separates this window from the other.

**It is also the whole account of such a turn: no escalation record accompanies it.** A turn whose
chat vanished applies none of the calls to staff it collected — there is no conversation left to
silence and no message left to mark — and records none of them either. An `escalation.unchanged`
carrying a deleted `chat_id` and a null `existing_reason` is byte-identical to the entry a takeover
writes, so emitting one would reintroduce exactly the conflation this event was split out to
remove. That holds however the turn ended: a turn that settled no reply has no insert of its own to
report the chat gone, so it reads the takeover instead — and that read answers `chat_gone` in its
own right rather than folding a missing conversation into "nobody took this over".

### `chat.delete_rejected` - new

| Event | Level | Fields | When |
|---|---|---|---|
| `chat.delete_rejected` | error | `chat_id`, `error_type`, `error_detail`, `outcome_known` (bool) | `DELETE /chats/{chat_id}` reached the scheduler and the scheduler answered with a failure rather than an outage. |

*(This entry was written as a pair with `patient.rename_rejected`. That route is withdrawn along
with patient renaming - see 005 FR-048 as amended - so only the deletion half remains, unchanged in
shape and meaning.)*

**`outcome_known` is the field that matters**, and it is why one event covers both answers instead
of two. It is true only for the subclasses that are decided *before* the write - a rejection, or an
id the scheduler does not know - and those are the only ones the caller is told "nothing was
deleted" about, with a 502. Anything else this build cannot place answers 504 and claims
nothing, because a scheduling failure it has never seen is not evidence that nothing happened.

**`error`, not `warning`:** an outage is ordinary and is not logged here at all; reaching this event
means the scheduler answered something the route did not expect, which is either a contract drift or
a defect. The name reads as "rejected" because that was the only case when it was written; a
`false` `outcome_known` is the entry saying it was something else.

**It carries no correlation id.** `bind_turn_id` is entered by `POST /chat` and `bind_operation_id`
by the FAQ routes; a delete is neither, so this entry is correlated by `chat_id` alone.

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
Postgres, the embedding service and the model API when an operation actually failed against them —
with the model API's own qualification above: the failure has to be one that means the request went
unserved, not merely one tagged with a step that calls the model.

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
