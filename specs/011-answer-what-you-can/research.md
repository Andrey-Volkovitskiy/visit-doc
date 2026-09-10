# Phase 0 Research: Answer What You Can (Phase 1h)

Twelve decisions, each with what it rejected. Nothing here is a NEEDS CLARIFICATION left over from
the spec: the five that were genuinely open were settled in `spec.md`'s Clarifications session on
2026-09-10, and this file records the design consequences of those answers plus the choices the
implementation forces that a spec should not contain.

---

## #1 — The record is one JSONB list on the message, not a table

**Decision.** `messages.request_outcomes`, a nullable JSONB column holding an ordered list of
`{position, question, answer, verdict, citations}`. It replaces `messages.faq_verdict` (String(32))
and `messages.citations` (JSONB) outright.

**Rationale.** The outcomes are always read *with* the message that carries them, never queried on
their own: the console renders them under a reply, and every aggregate question — how often a floor
stops a request, how often a turn served half — is answered from the log, which is where Phase 2
reads. That is exactly the profile `citations` and `reply_to_message_ids` already have on this
table, and both are JSONB for the same stated reason. A list also enforces order structurally, which
is a requirement (FR-040) rather than a convenience.

**Alternatives rejected.**
- *A `message_request_outcomes` table.* Buys per-element referential integrity nothing needs (the
  chunk ids are not foreign keys today either) and a join on every history read, in exchange for
  queries this phase does not make. It would be right the moment something aggregates outcomes in
  SQL; that is Phase 2's harness, and Phase 2 reads logs.
- *Keeping `citations` and adding a parallel verdict list.* Two lists aligned by position is two
  values that can disagree, in a phase whose subject is exactly that failure.
- *Keeping `faq_verdict` as a summary beside the list.* Rejected by the spec (FR-002), not here.

---

## #2 — `RequestOutcome` is a Pydantic model in `domain/schemas.py`; `FaqSegmentAnswer` stays internal

**Decision.** A new `RequestOutcome` model sits beside `Citation`, and is what `ChatDoneEvent` and
`MessageOut` carry and what is dumped into the column. `compose_answer.FaqSegmentAnswer` — the
frozen dataclass the FAQ half already produces per request — stays internal and gains a projection
to it.

**Rationale.** `FaqSegmentAnswer` carries `scored_chunks`, which exist for the record's *log* side
and which the console deliberately never renders (`Citation` "carries no number at all", as
`FaqResult` already says). Putting it on the wire would either leak the scores or require stripping
them at every call site. One projection, in the type that owns the data, is the smaller surface.

**Alternatives rejected.** Reusing the dataclass on the wire (leaks scores; a dataclass is not a
validated wire type); a second dataclass mirroring it (a copy that drifts).

---

## #3 — The FAQ half stops collapsing: `from_segments` keeps what was answered

**Decision.** `FaqResult.from_segments` no longer checks "did any request abstain" to decide the
whole half. It carries every answered request's answer and citations, and the abstained ones remain
in `segment_answers` with no answer text. `summarize_verdict` is **deleted**, not repurposed.

**Rationale.** The reduction existed only to produce the single field FR-002 removes. Keeping it as
"the summary the log reports" would put back the value with two meanings one layer down.

**Alternatives rejected.** Keeping `summarize_verdict` for the log (see above); computing a summary
at render time in the console (same value, same two meanings, further from where it could be
checked).

---

## #4 — `part_count` becomes *answered + one gap*, and that keeps the routing guard true

**Decision.** The FAQ half contributes one part per answered request, plus exactly one part for the
gap if any request abstained. `_expected_parts` (routing time) is unchanged.

**Rationale.** The streaming decision is made before retrieval runs and must bound what the turn
actually produces, or a specialist streams a reply the composer then writes over. Under the new
count that bound still holds: with *k* FAQ requests of which *m* abstained, actual parts are
`k − m + (1 if m else 0)`, which is `k` when `m = 0` and strictly less than `k` when `m ≥ 1`. So a
turn routed to collect never finds it has *more* parts than expected, and the existing
`compose.unexpected_parts` guard keeps its meaning.

**Alternatives rejected.** One gap part *per* abstained request (FR-012 forbids it, and it would
break the bound above when a two-request turn abstained on both); recomputing the streaming decision
after retrieval (the specialist has already started streaming by then — that is the whole reason the
decision is made early).

---

## #5 — The all-abstained turn keeps the constant reply, and reaches it through the existing collapse

**Decision.** When every FAQ request abstained and nothing else contributed a part, the turn takes
1g's collapse path: `_ABSTENTION_MESSAGE` verbatim, no composing call.

**Rationale.** Falls out of #4 without new code — such a turn has exactly one part — and it keeps a
model out of the one reply that makes no claim about the clinic. It also means the constant survives
as the *only* abstention wording a patient ever sees unaccompanied, which is what makes the composed
gap's three obligations (FR-024) a restatement of something already fixed rather than a new promise.

**Alternatives rejected.** Composing every turn for uniformity (puts a paraphrase in front of a
constant, which 1g's FR-051a rejected for the same reason).

---

## #6 — The gap is one labelled prompt block listing every unanswered question

**Decision.** The composing prompt gains one block naming each unanswered request, and instructing
that the gap be stated in the composer's own words rather than by quoting the block back. The
existing "no confident answer" instruction is replaced by it.

**Rationale.** The composer needs the questions to name the gap at all (FR-012a), and needs them
*labelled as unanswered* so the "preserve every claim exactly" rule extends to the abstention. One
block rather than one per question is FR-012; naming in the composer's own words is the
clarification's answer, and the block says so explicitly because the input and the instruction fail
independently — the same reasoning 1g applied to the booking prompt.

**Alternatives rejected.** Passing the abstained questions unlabelled (they read as answered
questions with missing answers); interleaving gap and answer blocks in message order (the composer
already receives answers labelled by question, and ordering the reply is a copy decision the
constraint does not depend on).

---

## #7 — The three constraints live in the system prompt, and are made checkable from the record

**Decision.** FR-021–FR-023 become three explicit clauses of the composing system prompt, and
FR-040a/FR-040b make the parts and the reply separately readable so SC-004a can be checked against
real turns.

**Rationale.** This is the phase's one model-obeyed contract. A prompt clause is necessary and not
sufficient; what makes it a *contract* is that a violation is detectable after the fact from stored
data rather than only in a test with a stubbed model.

**Alternatives rejected.** A post-generation verification call over the composed reply — that is
per-turn groundedness checking, which `docs/ROADMAP.md` assigns to Phase 2's offline harness and
which this project has deliberately not done per turn since 1e.

---

## #8 — The escalation records on *any* abstained request, and carries nothing new

**Decision.** `answer_faq` records `CORPUS_COULD_NOT_ANSWER` when **any** request abstained (today:
when the half's summary verdict was an abstention). Nothing is added to `EscalationRequests`, to the
mark, or to any stored escalation field.

**Rationale.** The trigger changes only because the summary it read is gone; the cause, the
precedence, the one-per-turn shape and the non-silencing behaviour are all unchanged (FR-033). What
staff receive as "the unserved requests" is derived (#9), so the escalation itself needs no payload.

**Alternatives rejected.** Recording one escalation per abstained request (FR-030 forbids it, and
`EscalationRequests` already de-duplicates by precedence, so it would change nothing but the log).

---

## #9 — The unserved list is derived at render time; `answer_source` leaves the completion event

**Decision (a).** The console filters the assistant message's outcomes to those whose verdict is an
abstention, in position order. Nothing is written twice.

**Decision (b).** `turn.completed` keeps `outcome` and **drops** `answer_source`.

**Rationale.** (a) is the clarification's answer. (b) is its consequence: once `outcome` names the
turn's shape rather than a verdict (FR-050a), it carries exactly what `answer_source` carries, and
two fields holding one fact in one event is the defect this phase exists to remove — applied to the
event that reports it. `outcome` is kept rather than `answer_source` because it is the field
existing log queries group by, and because "what this turn did" is the question a completion line
answers. The wire keeps `answer_source`: a client reading one event asks a different question than a
log reader scanning a million.

**Alternatives rejected.** Keeping both and documenting the duplication (a contract that says "these
two are always equal" is a contract nobody re-checks); dropping `outcome` instead (the user
considered and rejected it in clarification; it is also the field with existing readers).

---

## #10 — One migration, no backfill, and the downgrade loses data

**Decision.** A single Alembic revision drops `faq_verdict` and `citations` and adds
`request_outcomes`. The downgrade reverses the schema and restores no data. No code may read the old
shape (FR-044a).

**Rationale.** FR-044 deleted every session first, and all three stores were verified empty, so
there is nothing to carry across. A backfill would have to invent the `question` text that was never
stored, leaving a field that is sometimes absent for every future reader to branch on.

**Alternatives rejected.** A data migration writing `{position: 0, question: null, …}` per row
(invents the shape's one nullable field for rows that no longer exist); keeping the old columns
alongside the new one for a release (dual-read, forbidden by FR-044a, and the thing that makes a
"which shape is this?" branch permanent).

---

## #11 — Citations deduplicate within a request, and `deduplicate_chunks` survives unchanged

**Decision.** `deduplicate_chunks` stays exactly as it is and is applied per request rather than
across the turn's pooled survivors.

**Rationale.** Two chunks of one entry are still two chunks; one chunk that a single request's
shortlist listed twice is still one. What changes is only the collection it runs over. A chunk that
supported two *different* requests is now cited under each, which is the point (FR-003).

**Alternatives rejected.** Dropping the function and relying on the pipeline's own uniqueness (the
gates do not promise it, and the function is three lines with a test).

---

## #12 — The frontend renders one block per outcome, and the patient pane still renders none

**Decision.** `MessageView` takes `requestOutcomes` in place of `citations` + `faqVerdict`, and the
staff pane draws one block per outcome: the question, the degraded marker when *that* outcome is
`answered_unreranked`, and either its citations or its "no answer — forwarded to staff" line. The
patient pane passes nothing, exactly as today.

**Rationale.** The degraded marker and the citation list were message-level because the verdict was;
both are per request now, and a marker on the message would be a claim about requests it does not
describe. The `data-faq-verdict` attribute on the message wrapper goes away, and the tests that read
it re-point at the per-outcome blocks — that is the frontend half of FR-050b's "the record and the
questions asked of it must not disagree for a release".

**Alternatives rejected.** Keeping a message-level marker derived from the outcomes (a summary, on
the surface where the summary is most misleading — a staff member auditing one answer); rendering
the outcomes in the patient pane (FR-042 keeps that boundary where it is).
