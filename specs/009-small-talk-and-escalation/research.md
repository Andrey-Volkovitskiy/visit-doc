# Phase 0 Research: Small Talk and What Escalation Is For (Phase 1f)

Every decision below was reachable from the spec plus the existing code; none needed an external
dependency, a benchmark, or a new library. What follows is why each shape was chosen and what it
rejected.

## 1. One flat label set, one call

**Decision**: Add `small_talk`, `urgent_condition`, `distress`, `booking_for_another` to
`IntentLabel`, narrow `call_staff` to an explicit request for a human, and re-point `unknown` from
"fits none of the above" to "a request the assistant is not authorized to serve". One classification
call, unchanged in model, shape and bounding.

**Rationale**: Everything this phase adds is downstream of one question — *what is this message?* —
which the turn already asks once, with structured output, on the cheap model. A second call to ask
"is this urgent?" would double the router's cost and introduce two answers that can disagree.
`IntentClassificationResult.intents` is already a list, so multi-label messages need no schema
change beyond the enum members.

**Alternatives considered**: A dedicated safety classifier running in parallel (rejected: two model
calls, two sources of truth, and a merge rule nobody asked for); a keyword pre-filter for emergency
terms ahead of the model (rejected: a phrase list is exactly the brittle free-text matching
principle IV exists to forbid, and it would fire on "I had chest pain last year").

## 2. `small_talk` also carries the unintelligible message

**Decision**: One label meaning "asks for nothing that can be acted on", covering both a pleasantry
and a fragment with no recoverable meaning (FR-022e). The node's prompt decides whether to
acknowledge or to invite a rephrase; nothing branches on the difference.

**Rationale**: The two share every consequence — a courteous reply, no retrieval, no staff, no
silence. Splitting them would create a second label whose only effect is a different sentence from
the same model on the same input, which is a prompt concern, not a routing one. The value still
means one thing at the routing layer, which is what "one value, one meaning" governs.

**Alternatives considered**: A separate `unintelligible` label (rejected as above); routing gibberish
to `unknown` and escalating it (rejected by the spec — it refills the queue the phase empties).

## 3. One hand-off node, keyed by cause

**Decision**: Generalize the existing `hand_off` node. Today it writes one constant and the router
records one reason beside it; it becomes a node that reads the turn's stopping cause from graph
state and emits that cause's constant. One table maps cause → (fixed text, silencing, mark).

**Rationale**: Four causes now end a turn the same way — a constant sentence, a call to staff, no
model call, no tool call. Four nodes would be four copies of six lines, and the failure mode is
silent drift: one of them forgetting to silence, or getting a mark whose value no longer matches its
reason. One table makes "every cause has exactly one text, one mark, and one silencing answer" a
property of the data rather than a convention four functions each have to honour.

**Alternatives considered**: Four sibling nodes (rejected: duplication and drift); a single node
that re-asks a model which sentence to write (rejected: the classification already decided, and
paying a call to re-decide it is what the fixed text exists to avoid).

## 4. The not-authorized notice: constant alone, composed when accompanied

**Decision**: Solo, `unknown` routes to the hand-off node and emits its constant with no model call.
Alongside a servable intent, no hand-off node runs; instead the turn's merge step is told a notice
is owed, and its prompt requires the three things FR-022c names.

**Rationale**: The spec's clarification is explicit that the sentence is fixed only when it is the
whole reply. A merged turn is already paying for the composing call, and stapling a verbatim
sentence onto a composed answer produces a reply in two voices. Passing a flag into
`compose_answer()` is smaller than modelling the notice as a fourth specialist result, and it keeps
the composer's inputs as "what the turn has to say", which is what it already is.

**Alternatives considered**: Appending the constant verbatim after the composed answer (rejected by
the clarification); making `unknown` suppress the servable intents (rejected: it drops an answer the
assistant could have given, for no gain, since this cause does not silence).

## 5. Silencing is a set, not a special case

**Decision**: `_SILENCING` grows from one member to four — patient-asked-for-person, urgent
condition, distress, booking-for-another. `corpus_could_not_answer`, `assistant_failed` and the new
`not_authorized` remain non-silencing.

**Rationale**: The existing code already treats "which reasons silence" as data separate from the
reason set, precisely so this kind of change is a membership edit rather than a new mechanism
(`escalation.py:_SILENCING`). The three added ones silence for the reason the first one did: more
assistant is the thing the patient should not get next. `not_authorized` does not, because its own
sentence invites the next question.

**Alternatives considered**: A `silences: bool` attribute on the reason enum (rejected: it puts a
policy decision inside a value's identity, and the existing separation is deliberate and documented).

## 6. Marks: four added, four clearable

**Decision**: `AttentionMark` gains the same four values; `CLEARABLE_MARKS` gains all four, joining
`patient_asked_for_person` and `unanswered`. `corpus_could_not_answer` and `assistant_failed` stay
permanent.

**Rationale**: The existing rule is that a mark clears when a staff reply *is* the whole of what the
mark asked for. A person answering an urgent message, a distressed patient, a third-party booking
request, or a request the assistant may not serve is exactly that. A corpus gap survives its answer
because the missing document is still missing, and a failure survives because it still happened.

## 7. Precedence

**Decision**: `_PRECEDENCE` becomes urgent condition → distress → asked for a person → booking for
another → not authorized → corpus gap → failure (FR-047).

**Rationale**: The tuple already means "strongest claim on a person first" and is already the single
place the one-mark-per-message rule is decided. Safety outranks everything; a patient who asked for
a human outranks a request refused on authority grounds; the two existing non-silencing causes keep
their relative order at the bottom.

## 8. No migration

**Decision**: No Alembic revision.

**Rationale**: Verified in `models.py` — `chats.escalation_reason` and `messages.attention_mark` are
`String(32)`, deliberately not database enums, with the comment saying a further value should need
no migration. The longest new value is `booking_for_another_person` at 26 characters. Nothing else
this phase writes is new state.

**Consequence**: The change is reversible by deploying the previous code; no data written under this
phase becomes unreadable to it, except that older code will not know the four new strings —
which is why the frontend's label map must ship in the same change (FR-041).

## 9. The test seam

**Decision**: Every behavioral test drives a stubbed classification result through the existing
`fake_classify_intent_client` helper in `conftest.py`. Accuracy of the real classifier is measured by hand against
committed data (FR-035, FR-036), not asserted in any test.

**Rationale**: Everything downstream of the label is deterministic — a routing table, a constant,
a set membership, a database write. Making the label an input turns the whole phase into offline
unit tests, which is what lets a per-push gate cover it. The classifier's judgement is the one part
a test cannot pin without a live call, and 1e already established where that belongs.

## 10. What is deliberately not built

- **No triage in the reply.** The assistant assesses nothing, scores nothing and advises nothing:
  `urgent_condition`'s only effect is to fetch a person and point at emergency services, in fixed
  text (FR-044). The *routing rule* is a different matter, and the first live run made it one: the
  classifier prompt now names red flags explicitly — chest pain, difficulty breathing, heavy
  bleeding, a suspected overdose, fainting, a child who cannot be roused, stroke signs, sudden
  severe pain — because tightening it in the abstract cost recall on "my chest hurts, can I see
  someone today?" (`evaluation/procedure.md`, 2026-09-08). Naming which words route where is still
  routing, and nothing downstream reads them as a clinical finding. But the honest claim is "the
  reply judges nothing", not "the system contains no clinical vocabulary".
- **No authority model.** A third-party booking is refused, never evaluated. There is no consent,
  relationship, or guardianship concept anywhere in this design.
- **No queue re-ordering, no out-of-band notification, no new console screen.** The mark beside the
  message is the whole of what a staff member gains.
- **No classification of messages arriving while the assistant is silent** (FR-025a) — which is
  achieved by changing nothing in `turn.py`'s gate.
