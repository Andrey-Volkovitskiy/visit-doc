# Feature Specification: Answer What You Can (Phase 1h)

**Feature Branch**: `011-answer-what-you-can`

**Created**: 2026-09-10

**Status**: Draft

**Input**: User description: "Create a spec for Phase 1h @docs/ROADMAP.md" — Phase 1h, "Answer what
you can": once a turn carries several requests, one verdict for the turn is a value with two
meanings. The verdict moves onto the request, the turn serves the requests it can and names the ones
it cannot, and staff receive the specific question the corpus could not answer.

## Why this exists

Phase 1g made the turn *see* several requests. It deliberately stopped one step short: the turn
still carries exactly one verdict, and its FAQ half abstains **as a whole** if any request could not
be answered (010 FR-042). So a patient who asks "What's your address, and do you take my insurance?"
— where the address is answered perfectly well and the insurance question has nothing behind it —
receives neither answer. The turn throws away work it already did, and hands the patient a sentence
that is false about the half it could have served.

Three things are wrong, and they are the same thing at three levels.

1. **One field, two meanings.** `answered` on a turn whose second question abstained says the turn
   was answered. `abstained_rerank_floor` on a turn whose first question was answered says nothing
   was. 1g named this itself: `summarize_verdict` picks the first abstaining request and documents
   that the log carries the rest, because "one field cannot say that two different fixes are
   needed". A summary is the right shape for a log line and the wrong shape for the record.
2. **A reply that is worse than the sum of its parts.** The FAQ half already generated an answer for
   the request that cleared both gates. 1g discards it, because delivering it beside a gap needs a
   constraint the composer did not have: an answered sentence sitting next to an abstention is an
   invitation to soften the abstention, and a softened abstention is exactly the confabulation the
   whole abstention path exists to prevent.
3. **An escalation that names nothing.** A person is called because "the corpus could not answer",
   and what reaches them is the patient's whole message — which they must re-read to work out which
   part of it failed. The system knows: it has the request, restated to stand on its own, and the
   gate that stopped it. It simply does not pass it on.

This phase closes all three. The verdict, the citations and the answer become properties of the
**request** rather than of the turn; the reply carries every answer the turn produced plus a plain
statement of what it could not answer; and the escalation carries the unserved requests verbatim.

## Clarifications

### Session 2026-09-10

- Q: What does the *stored* message carry once a turn can answer one request and abstain on another?
  → A: **Per-request records only.** The single `faq_verdict` and the single `citations` field are
  removed from the stored message and from every wire shape that repeats them, replaced by one
  ordered list of per-request outcomes. Every reader — the console, the patient pane, the log, the
  eval harness — re-points at the list in this phase. Keeping a turn-level summary beside the list
  would preserve exactly the two-meanings value this phase exists to remove, and a field that is
  only sometimes right is worse than one that is absent: a reader who does not know it is a summary
  reads it as the answer.
- Q: A turn answers request A and cannot answer request B. How is the reply that carries both
  produced? → A: **The existing composer writes both, under a new constraint.** An abstention may
  not be softened, hedged or covered by the answer beside it, and an answer may not be extended to
  reach the gap. One voice: the alternative — answered text verbatim plus a fixed sentence appended
  after it — is the two-voice stitch spec 009 rejected for the not-authorized notice (009
  FR-022c1), and a turn already paying for a composing call gains nothing by dodging it. The
  constraint is model-obeyed, so it is what the phase's tests are aimed at.
- Q: When two of a turn's requests both fail, what does the single escalation carry to staff? →
  A: **Every unanswered request, in message order.** One escalation per turn, as today, carrying
  the full list verbatim. A second gap behind the first is a second FAQ entry someone has to write;
  naming only the first would make the fix for the second invisible, which is the same loss the
  per-request verdict exists to stop.
- Q: Does each request's stored record carry that request's own answer text, alongside its
  question, verdict and citations? → A: **Yes — the answer the FAQ half generated for that request,
  before anything merged it.** An abstained request carries none, because none was generated. It is
  what makes FR-021 through FR-023 auditable on real traffic rather than in tests alone: the part
  the composer was given and the reply it produced are both on the record, so a softened abstention
  or an answer that spilled across leaves evidence. The merged reply stays where it is, as the
  message's content — the parts are what went *into* it, not a second copy of it.
- Q: Where do the unserved requests staff see come from — derived from the assistant message's
  outcomes, or written onto the patient message with the escalation? → A: **Derived.** The console
  shows the outcomes whose verdict is an abstention; nothing new is stored. The alternative writes
  the same text twice, in two rows updated by two different writes, and the first time they disagree
  there is nothing to say which is right. The consequence is accepted deliberately: a turn that
  raised an escalation but stored no reply shows staff no unserved request, because there is no
  record of what it retrieved for (see Edge Cases).
- Q: In the patient-facing reply, how is the unanswered request named? → A: **In the composer's own
  words, naming the subject.** The verbatim restatement is written for retrieval, not for a person:
  quoting "Is parking free?" back at someone who typed "and is it free?" reads like a transcription
  fault, and the patient already knows what they asked. Verbatim is what staff get (FR-031), because
  they are the ones acting on the exact question. What the reply owes the patient is that they can
  tell *which* of their requests went unanswered — stated as a testable rule in FR-012a, since
  "names the subject" is otherwise the kind of adjective that passes every review and no test.
- Q: The composing call fails on a turn that had already generated real answers. What happens? →
  A: **The turn fails whole, unchanged.** No reply is stored, one failure escalation is raised, and
  nothing partial reaches the patient. A composer failure means nothing wrote the reply: the parts
  were never checked against each other, so emitting them raw is precisely the bleed FR-021 through
  FR-023 exist to stop — on the one path where a gap sits beside an answer. Partial serving is
  authorized for a request whose outcome is *known*, never for a turn whose reply is unwritten; the
  generation already paid for is a cost, not a reason.
- Q: What does the completion log event's `outcome` field say once no turn-level verdict exists? →
  A: **The shape of the turn** — which specialist wrote the reply, or that it was merged, handed off
  or answered as small talk — never a verdict. It is a routing fact, true however many requests the
  turn carried, and it stops the field meaning "which gate stopped it" on a one-request turn and
  "how the reply was written" on every other. Every verdict is read from the per-request list, which
  is the one place a turn's outcomes live.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The turn answers what it can and names what it cannot (Priority: P1)

A patient asks two questions in one message. The corpus answers one of them and has nothing for the
other. The reply carries the answer, with the citations it earned, and says plainly that the other
question has no answer in the clinic's knowledge base and has been forwarded to staff.

**Why this priority**: It is the phase's headline behaviour and the only one a patient feels. Today
that patient gets nothing but an abstention, over a question the clinic can answer.

**Independent Test**: Take a corpus that answers question A and not question B, confirm each alone
behaves as expected, then ask both in one message and confirm the reply contains A's answer, A's
citations, and a statement of B's gap.

**Acceptance Scenarios**:

1. **Given** a corpus that answers A and not B, **When** the patient asks both in one message,
   **Then** the reply contains the answer to A and states that B could not be answered.
2. **Given** that turn, **When** its citations are read, **Then** the chunks that answered A are
   present and attributed to A, and nothing is cited for B.
3. **Given** that turn, **When** its record is read, **Then** A carries an answered verdict and B
   carries the verdict of the gate that stopped it.
4. **Given** a turn whose every FAQ request abstained, **When** the reply is read, **Then** it is
   the existing abstention message, unchanged, with no composing call made.
5. **Given** a turn whose every FAQ request was answered, **When** the reply is read, **Then** it is
   what 1g produces today for the same input.

---

### User Story 2 - The answered half never covers for the gap (Priority: P1)

The reply is written by one model call over both outcomes. It may not soften "there is no answer for
this" into "I'll check on that", may not let an answer spill across to cover the unanswered
question, and may not invent a bridge between them.

**Why this priority**: It is the reason 1g held this phase back. Delivering an answer beside an
abstention is only safe if the abstention survives the delivery — and the step that could break it
is the one step of the turn with a model in it.

**Independent Test**: Run a fixed set of answered/abstained pairs through the composing step alone,
with stubbed halves, and check every reply against the three prohibitions.

**Acceptance Scenarios**:

1. **Given** an answered request and an abstained one, **When** the reply is read, **Then** it
   states the gap as a gap: no promise of a callback time, no claim the assistant will look into it,
   no answer drawn from outside the retrieved context.
2. **Given** the same pair, **When** the answered part is compared to what the FAQ half generated,
   **Then** every factual claim is preserved exactly and none is extended to the unanswered
   question's subject.
3. **Given** a pair whose two questions are about the same subject, **When** the reply is read,
   **Then** the answered one is still answered and the unanswered one is still named as unanswered
   — adjacency of subject is not evidence.
4. **Given** an abstained request beside a booking outcome, **When** the reply is read, **Then**
   both the existing booking constraints and this phase's abstention constraint hold at once.

---

### User Story 3 - Staff receive the question that failed, not the message (Priority: P2)

A staff member opening an escalated conversation sees, verbatim, each request the corpus could not
serve — as the classifier restated it, which is what was actually retrieved for — rather than the
patient's whole message to re-read.

**Why this priority**: The escalation exists so a person can act. What they act on is a question;
handing them a paragraph and asking them to find it in there is work the system already did.

**Independent Test**: Drive a turn with one answerable and two unanswerable questions, then read the
staff console and confirm both unanswered requests appear verbatim, in message order, beside the
conversation that raised them.

**Acceptance Scenarios**:

1. **Given** a turn with one unanswered request, **When** the staff console shows the marked
   message, **Then** the unanswered request's text is shown verbatim.
2. **Given** a turn with two unanswered requests, **When** the console is read, **Then** both are
   shown, in message order, and one escalation was raised.
3. **Given** a turn where every request was answered, **When** the console is read, **Then** no
   unanswered request is shown and no person was called.
4. **Given** a turn whose request text differs from the patient's own wording (a restatement),
   **When** the console is read, **Then** the restatement is what is shown, because it is what was
   retrieved for.

---

### User Story 4 - The record moves onto the request (Priority: P2)

Every place that reads "this turn's verdict" and "this turn's citations" reads a list of per-request
outcomes instead: the stored message, the streamed terminal event, the console thread, the log, and
the eval harness that will consume it in Phase 2.

**Why this priority**: It is the phase's structural change, and the one that decides whether Phase 2
can compute a per-request metric at all. Without it, partial serving would be visible to the patient
and invisible in the record.

**Independent Test**: Complete one multi-request turn, then reconstruct each request's question,
verdict and citations from the stored message alone, without reading the log and without re-running
anything.

**Acceptance Scenarios**:

1. **Given** a completed FAQ turn, **When** its stored message is read, **Then** it carries one
   outcome per request, in message order, each with that request's question, answer, verdict and
   citations — and an abstained request's outcome carries no answer at all.
2. **Given** a turn with no FAQ half — a booking-only reply, a hand-off, a small-talk reply —
   **When** its stored message is read, **Then** it carries no request outcomes at all, which is
   distinct from carrying an empty list of them.
3. **Given** any stored message at all, **When** it is read after the change, **Then** it is in the
   new shape — there is no second shape to read, because the stores were emptied with the change.
4. **Given** a request answered without reranking beside one that was reranked, **When** the console
   renders them, **Then** the degraded marker is on the degraded request alone.

---

### User Story 5 - Everything that already worked still works (Priority: P3)

A single-request turn, a booking turn, a small-talk turn, a hand-off, and a turn whose every request
abstained behave exactly as they do today: the same reply, the same calls, the same escalation, the
same mark.

**Why this priority**: This phase changes the shape of a value every reply path touches. Its risk is
not that partial serving fails; it is that something unrelated quietly changes meaning on the way.

**Independent Test**: Replay the single-request inputs from 1g's evaluation sets and confirm every
reply, verdict, citation set, escalation and mark is identical.

**Acceptance Scenarios**:

1. **Given** any single-request FAQ message, **When** the turn completes, **Then** it takes the
   existing single-specialist path, streams its own reply, makes no composing call, and records one
   request outcome.
2. **Given** a booking-only message, **When** the turn completes, **Then** nothing about it changes.
3. **Given** a message asking for a human, or carrying an urgent condition, distress, or a booking
   for another person, **When** the turn completes, **Then** the fixed reply, the cause, the mark
   and the silence are exactly today's.
4. **Given** a turn whose retrieval or embedding call failed, **When** the turn completes, **Then**
   the whole turn fails as it does today — no partial reply is delivered, and the failure is not
   recorded as an abstention.
5. **Given** a turn whose two requests were both answered and whose composing call then failed,
   **When** the turn ends, **Then** it fails whole: no reply is stored, one failure escalation is
   raised, and neither answer is delivered on its own.

---

### Edge Cases

- **Every request abstained.** The reply is the existing constant abstention message, no composing
  call is made, and one escalation carries every unanswered request. Naming each question back to a
  patient who just asked them buys nothing; staff get them from the record.
- **One request answered, two abstained.** One reply, one escalation, two unanswered requests
  carried, three outcomes recorded.
- **A request answered without reranking, beside one that abstained.** The degraded answer is
  delivered as a degraded answer and recorded as one; the gap is still a gap. Neither outcome
  changes the other's verdict.
- **The same chunk answers two requests.** It appears under each request's citations. Two
  provenances are not a duplicate — this supersedes 1g's turn-wide citation deduplication (010
  FR-034), which existed only because the turn had one citation list.
- **An abstention beside a booking outcome and a not-authorized notice.** All three constraints hold
  in one composed reply; the phase adds a constraint to the composer, it does not replace any.
- **A retrieval or embedding failure on one request.** Unchanged from 1g (010 FR-037): the turn
  fails whole. A failure is not an abstention, and partial serving of an *unknown* outcome is not
  what this phase authorizes.
- **A reranking failure on one request.** Unchanged: that request is answered from the chunks that
  cleared the similarity floor and records the degraded verdict; no person is called for it.
- **A classification failure.** Unchanged: the existing fallback runs, one request, one outcome.
- **The composing call fails after both halves succeeded.** The turn fails whole (FR-026). This is
  the case partial serving makes tempting to salvage and the case where salvaging is least safe.
- **The composing call is truncated.** Recorded as it is today. A cut reply that ends inside the
  gap sentence is the case the record has to keep, because neither half can report it.
- **A conversation the assistant is silent in.** No turn runs, so no outcome is recorded — unchanged.
- **An escalation with no reply behind it.** A turn superseded by a newer message, or one that failed
  outright, can call staff without storing an assistant message — and the unserved list is derived
  from that message's outcomes (FR-031a). Staff see the mark and the patient's own message, with no
  unanswered request listed. That is exactly today's situation for every escalation, so it is a
  case this phase does not improve rather than one it breaks.
- **A message stored before this phase.** There is none. Every session is deleted as part of this
  change (FR-044), so no stored message predates the new shape and no reader needs a rule for one.

## Requirements *(mandatory)*

### Functional Requirements

**The verdict belongs to the request**

- **FR-001**: Each FAQ request MUST carry its **own** verdict, drawn from the existing six-value set
  with no value added or removed. The verdict describes one request's retrieval, which is what it
  always described.
- **FR-002**: A turn MUST NOT carry a single verdict of its own, in storage or on any wire shape.
  1g's summary rule (010 FR-041, FR-043) and the reduction that implements it are removed, not
  relaxed: a turn that answered one request and abstained on another has no single true verdict, and
  a field that is only sometimes right is worse than none.
- **FR-003**: Each FAQ request MUST carry its **own** citations — the chunks its own answer was
  generated from — so a citation says which question it supports. Deduplication applies **within** a
  request; a chunk that supported two requests is cited under each (superseding 010 FR-034).
- **FR-004**: A turn whose FAQ half did not run MUST carry **no** request outcomes, distinct from an
  empty list. A booking-only reply, a hand-off and a small-talk reply were never retrieved against.
- **FR-005**: Every request outcome MUST carry the request's **position** in the message and the
  request's **text as the classifier restated it** — what was actually retrieved for and answered,
  not the patient's original wording.

**Serving what can be served**

- **FR-010**: A turn MUST deliver every request its FAQ half answered, whatever any other request's
  outcome. The half no longer abstains as a whole (superseding 010 FR-042).
- **FR-011**: A turn MUST name every request it could not answer, in the same reply, as a gap. No
  request may be silently absent from the reply — the partial-answer failure this phase is closing
  must not be replaced by a partial reply that says nothing about the rest.
- **FR-012**: The gap MUST contribute exactly **one** part of the reply however many requests fell
  into it, naming each of them. One gap sentence per unanswered request would restate the patient's
  own message back at them.
- **FR-012a**: The gap MUST name each unanswered request by its **subject, in the composer's own
  words** — not by quoting the classifier's restatement, and not generically. The testable form: a
  reader given the patient's message and the reply alone MUST be able to say which of the requests
  went unanswered. "I couldn't help with part of your message" fails it; naming what the missing
  answer would have been about passes it.
- **FR-012b**: Naming the gap in the composer's words MUST NOT weaken FR-031: what staff receive is
  the restatement, verbatim. The two audiences get the same fact in the form each can act on, and
  neither is derived from the other's wording.
- **FR-013**: A turn whose **every** FAQ request abstained, with no other reply part beside it, MUST
  produce the existing constant abstention message and MUST make no composing call — unchanged from
  1g (010 FR-051a). A constant abstention is the one reply the design deliberately keeps model-free.
- **FR-014**: A turn with exactly one reply part MUST take the existing single-specialist path
  unchanged. Partial serving may not add a composing call to a turn that has nothing to compose.

**What the composer may not do**

- **FR-020**: The composing step MUST be given each answered request's answer with the question it
  answers, and each unanswered request's question with the fact that it has no answer. It MUST NOT
  be given a pooled text, a summary verdict, or an abstention already written for it.
- **FR-021**: The composed reply MUST NOT **soften** an abstention: it may not promise a time, claim
  the assistant will look into it, suggest the answer exists elsewhere in the reply, or hedge the
  gap into a partial answer.
- **FR-022**: The composed reply MUST NOT **extend** an answered request's claims to cover an
  unanswered one. An answer about parking is not an answer about parking fees because the two
  sentences are adjacent.
- **FR-023**: The composed reply MUST NOT introduce any claim absent from the parts it was given.
  The existing "preserve every factual claim exactly" constraint continues to hold, with an
  abstention now among the claims it protects.
- **FR-024**: The gap the reply states MUST convey what the existing abstention message conveys:
  that the information is not in the clinic's knowledge base, that the question has been forwarded
  to staff who will follow up, and that the assistant can still help with anything else. Wording is
  the composer's; those three things are the contract.
- **FR-025**: Every constraint this phase adds to the composer MUST hold **alongside** the existing
  ones — the booking outcome's wording rules, the not-authorized notice's three requirements, and
  the citation carry-through. None is replaced.
- **FR-026**: A **failure of the composing call** MUST fail the whole turn: no reply stored, one
  failure escalation, nothing partial delivered — however many of the turn's requests had already
  been answered. *(Two of the three were already true; the failure escalation was not. No path
  recorded `assistant_failed` for a pipeline failure before this phase — only the booking loop's
  tool failures did — so "exactly as it does today", as this requirement first read, described a
  behaviour that did not exist. It exists now, on every pipeline failure rather than the composing
  call alone: what the patient is owed does not depend on which step broke.)* The answers generated
  before it are a sunk cost, not a reason: nothing has checked them against each other, and the one
  path where an unchecked part could soften a gap is this one. The requests' outcomes are still
  recorded in the turn's log, as every failed turn's are.

**Escalation**

- **FR-030**: A turn MUST raise at **most one** escalation however many of its requests could not be
  answered — unchanged from today's one-collector-per-turn shape.
- **FR-031**: That escalation MUST carry **every** unanswered request, verbatim, in message order.
- **FR-031a**: "Carry" means **derived from the turn's stored outcomes**, not copied into a second
  place: the unanswered requests are the outcomes whose verdict is an abstention, read in message
  order. No requirement here may be satisfied by writing that text a second time beside the
  escalation, because two copies of one fact are two facts as soon as one write succeeds and the
  other does not.
- **FR-032**: An escalation MUST NOT be raised for a request that was answered, including a request
  answered without reranking. A degraded answer is an answer; a dependency outage is not a corpus
  gap.
- **FR-033**: The cause, the precedence, the mark, and the fact that a corpus gap does **not**
  silence the conversation are all unchanged. This phase adds no cause and no mark.
- **FR-034**: A staff member MUST be able to see each unanswered request, verbatim, from the console
  — beside the conversation and the message that raised it, without reading a log.
- **FR-034a**: A turn that raised an escalation but stored **no reply** MUST still mark the
  conversation exactly as it does today, and MUST show no unanswered request rather than an empty
  one or a guess. Nothing was recorded about what it retrieved for; the patient's message is what
  staff have, which is what they have today in every such case.

**The record: what is stored and what is sent**

- **FR-040**: The stored assistant message MUST carry an **ordered list of request outcomes** —
  position, question text, the answer generated for that request, verdict, citations — and MUST NOT
  carry a turn-level verdict or a turn-level citation list.
- **FR-040a**: An outcome's answer MUST be the text the FAQ half generated for that request, before
  any merge. An abstained request MUST carry **no** answer rather than an empty one: nothing was
  generated for it, and an empty string would be a second way of saying "abstained" that a reader
  could disagree with the verdict about.
- **FR-040b**: The merged reply MUST remain the message's content, stored once. The outcomes carry
  what went *into* the reply; the content is what the patient was shown, and the two being separately
  readable is the point — it is what lets a softened abstention be found after the fact.
- **FR-041**: The terminal event a client receives and the message shape the console reads MUST
  carry the same list, in the same shape, so one turn has one record and not two that can drift.
- **FR-042**: The patient pane MUST continue to draw neither verdicts nor citations. Both remain in
  the payload, for the reason 1g states: one session owns both panes, so omitting them would protect
  nothing while splitting one record into two shapes.
- **FR-043**: The console MUST render each request's citations under the request they support, and
  the degraded marker against the request that was answered without reranking, not against the
  message.
- **FR-044**: Every existing session MUST be **deleted** as part of this change, rather than its
  messages converted into the new shape. A session is a demonstration, not a record anyone is
  keeping: converting one would mean inventing the question text that was never stored, leaving a
  shape whose `question` is sometimes absent for every future reader to branch on — a second meaning
  in the field this phase exists to give one meaning. Deletion goes through the maintenance surface
  that already removes a session from both services' stores and the vector store together, so no
  store is left holding rows the others no longer name.
- **FR-044a**: No conversion, backfill, or dual-read of the old shape may be written. After this
  change exactly one shape exists, and code that can read two is code that has to decide which it
  is looking at.

**The log and the published contract**

- **FR-050**: The turn's completion event MUST carry each request's own outcome and MUST NOT carry a
  turn-level verdict, so the log and the stored record answer the question the same way.
- **FR-050a**: The completion event's `outcome` MUST name the **shape** of the turn — which
  specialist wrote the reply, or that it was merged, handed off, or answered as small talk — and MUST
  NOT be a verdict value on any turn, including a single-request one. Today it is the verdict string
  for a one-request FAQ turn and a shape for every other, which is the same field carrying two kinds
  of fact.
- **FR-050b**: Any log query that counted abstentions by reading `outcome` MUST be re-pointed at the
  per-request verdicts in the same change, so the record and the questions asked of it do not
  disagree for a release.
- **FR-051**: The per-request retrieval and gate events MUST be unchanged. They already name the
  request they belong to; this phase changes what is done with their outcome, not how they are
  logged.
- **FR-052**: The field that records what an abstaining turn told the patient MUST be set only when
  **every** FAQ request abstained — the case where the reply really is the abstention. Setting it
  for a partially-served turn would file an answered reply in the log as an abstention.
- **FR-053**: The published field contract for these events MUST be updated in the same change, so
  Phase 2 computes its metrics against a written contract rather than against the implementation.

**Cost and non-regression**

- **FR-060**: A turn MUST make at most one generation call **per answered request** and at most one
  composing call. An abstaining request costs no generation call, exactly as today.
- **FR-061**: A single-request turn MUST make exactly the calls it makes today — no additional
  model, retrieval, reranking or scheduling call, and no composing call.
- **FR-062**: The classifier, the segmentation rules, the cap, the retrieval pipeline, both gates,
  both floors and both caps MUST be unchanged. This phase changes what is done with a request's
  outcome, never how it is produced.
- **FR-063**: No path other than the ones this spec names may change behaviour. Booking, small talk,
  hand-off, the not-authorized notice, and the failure path produce the same replies, escalations
  and marks they produce today.

**Verification**

- **FR-070**: Every behavioural requirement here MUST be verifiable **offline against a stubbed
  classifier and stubbed retrieval**, with no live model call — a fixed segmentation and fixed
  per-request verdicts are the seam.
- **FR-071**: The composer's constraints (FR-021 through FR-023) MUST be exercised against a
  committed set of answered/abstained pairs, with the procedure and the result recorded, as Phase
  1e's calibration set and 1f's and 1g's labelled sets already are. They are the phase's one
  model-obeyed contract, so they cannot rest on stubs alone.
- **FR-072**: The stores MUST be verified **empty of sessions** before the change is considered
  done — in this service's store, in the scheduler's, and in the vector store. An emptying that
  half-happened is the one way the "exactly one shape exists" assumption above fails silently.

### Key Entities

- **Request outcome** *(new, stored)*: what one of a turn's requests produced — its position, the
  question as the classifier restated it, the answer generated for it (absent for an abstention), its
  verdict, and the citations that answer stands on. An assistant message carries one per FAQ request,
  in message order, or none at all when no FAQ half ran.
- **Retrieval verdict**: unchanged in its six values; changed in what it describes. It was the
  turn's; it becomes the request's, and no turn-level value replaces it.
- **Citation**: unchanged in shape; moves under the request whose answer it supports. Deduplicated
  within a request rather than across the turn.
- **Escalation**: unchanged in cause, precedence, mark and silencing, and gains no stored field. What
  staff read as "the unanswered requests" is derived from the turn's own outcomes — the ones whose
  verdict is an abstention, in message order (FR-031a).
- **Assistant message**: loses its single verdict field and its single citation list; gains the
  ordered list of request outcomes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a labelled set of at least 15 two-question messages built so that exactly one half
  is answerable, 100% of turns deliver the answerable half's answer with its citations, and 0%
  deliver an abstention alone. Today's figure is 0% and 100%.
- **SC-002**: Across that same set, 100% of replies state the unanswered question as unanswered —
  0 replies in which the gap is absent, softened into a promise, or covered by the answer beside it.
- **SC-002a**: Across that same set, 100% of replies let a reader holding only the patient's message
  and the reply name which request went unanswered (FR-012a), and 0 replies quote the classifier's
  restatement at the patient.
- **SC-003**: Across a set of at least 10 replies pairing an answer with a gap, 0 contain a factual
  claim about the unanswered question's subject that is not in the answered request's retrieved
  context.
- **SC-004**: For 100% of completed FAQ turns, each request's question, answer, verdict and
  citations are recoverable from the stored message alone, without reading a log and without
  re-running anything.
- **SC-004a**: For 100% of composed turns, every factual claim in a request's stored answer is
  present in the stored reply, and the reply contains no claim about an abstained request's subject
  — checkable from the record alone, which is what makes FR-021 through FR-023 observable in
  production rather than only under test.
- **SC-005**: 0 stored messages carry a turn-level verdict or a turn-level citation list after the
  change, and 0 sessions predating it remain in any of the three stores.
- **SC-006**: Every turn raises at most one escalation, however many of its requests could not be
  answered — 0 turns with two — and 100% of escalations name every unanswered request of their turn,
  verbatim.
- **SC-007**: 0 escalations are raised for a request that was answered, including one answered
  without reranking.
- **SC-008**: A turn carrying *k* FAQ requests of which *m* were answered issues exactly *m*
  generation calls and at most one composing call, for every *k* from 1 to 3 — verified by counting
  calls.
- **SC-009**: A single-request turn issues zero additional model, retrieval, reranking or scheduling
  calls compared with today, and no composing call.
- **SC-010**: A turn whose every FAQ request abstained produces the existing abstention message
  byte for byte and makes no composing call — 100% of such turns.
- **SC-011**: Replaying 1g's single-request evaluation inputs produces identical replies, verdicts,
  citations, escalations and marks — 0 differences. Any difference is a regression, not an
  improvement.
- **SC-012**: A staff member can name every unserved request from the console alone, without opening
  a log — 100% of escalated turns **that stored a reply**. A turn that stored none lists no request
  (FR-034a) and is excluded from the denominator rather than counted as a failure: there is nothing
  recorded for it to list.

## Assumptions

- **The classifier and the retrieval pipeline are untouched.** Segmentation, the cap, the gates, the
  floors, the reranking fallback and the six per-request log events are exactly as 1g shipped them.
  This phase begins where a request's outcome is already known.
- **Citations follow the verdict onto the request.** The choice to store per-request records only
  was made about the verdict (see Clarifications); citations are carried with it because they answer
  the same question — "what did *this* request produce?" — and a per-request verdict beside a
  turn-wide citation list would leave the console unable to say which chunk supported which answer.
  The turn-wide deduplication 1g introduced exists only because the turn had one list, so it goes
  with it.
- **The gap is one reply part, not one per failed request.** FR-012's rule is a copy decision with a
  behavioural consequence — it bounds what the composer is asked to produce — so it is stated as a
  requirement rather than left to the prompt.
- **A dependency failure still fails the turn whole.** 1g's FR-037 is unchanged and deliberately so:
  this phase authorizes serving a request whose outcome is *known* to be an abstention, not one
  whose outcome is unknown because something broke.
- **The frontend changes in this phase.** 1g explicitly changed none of it; this one must, because
  the wire shape it reads is the thing being replaced. The change is to what the console renders and
  where it reads it from — no new screen, no new control.
- **The existing sessions are already gone.** FR-044 was carried out on 2026-09-10, ahead of
  implementation: the 48 sessions in the dev stores were deleted through the maintenance sweep, and
  all three stores were verified empty. The requirement stays in the spec because it is what the
  single-shape record rests on, and because the same act has to be repeated on any other environment
  before the change lands there.
- **The existing sessions are thrown away, not converted** (FR-044). The stored shape changes, and
  what it held was 48 sessions of demonstration and manual-testing traffic — nothing anyone is
  keeping, and nothing that could supply the question text the new shape is built around. Emptying
  the stores buys a strictly simpler implementation: one shape, no backfill, no nullable question,
  and no reader that has to tell an old row from a new one. The schema change still ships as a
  migration, because the shape is what changes; it simply has no data to carry across.
- **The ROADMAP's Phase 2 metric has been corrected rather than reinterpreted.** It read "the share
  of escalations raised by a message that also contained an answerable question… should be zero",
  which cannot hold after this phase: a mixed message still raises one escalation, for the requests
  that failed. `docs/ROADMAP.md` now names the measurable thing instead — the share of *answerable*
  requests a turn left unserved — and its Phase 2 metrics are stated per request, computed from the
  record this phase creates rather than from a turn summary that no longer exists.
- **The labelled sets are evaluation data for this phase, not a golden dataset.** They follow 1e's
  calibration set and 1f's and 1g's labelled sets: committed data plus a written manual procedure,
  no runner, no assertions, no place in any gate.
- **Phase 2 owns the metrics.** This phase produces the per-request record they are computed from;
  it ships no harness and no dashboard.

## Out of Scope

- Any change to segmentation, the cap, the classifier's call, or the retrieval pipeline's stages,
  floors and caps.
- Any change to booking, rescheduling, cancellation, or the scheduling service.
- Any new escalation cause, mark, precedence rule, or change to which causes silence a conversation.
- Any change to what the patient pane draws: it still shows the reply, not the clinic's working
  notes underneath it.
- A new console screen or control. The console renders a shape that changed; it gains no feature.
- Per-request *streaming*: a multi-request turn still collects its parts and composes one reply, as
  1g decided.
- Serving a request whose outcome is unknown because a dependency failed.
- An automated eval runner, a live-model test tier, or a CI-gated metric over the labelled sets —
  Phase 2.
- Re-indexing, re-chunking, or any change to stored embeddings. The existing chunks are deleted
  with their sessions (FR-044), which is not the same thing: nothing is re-computed, and the corpus
  a new session starts with is the seeded one it already gets.
- Converting, backfilling or preserving anything already stored. FR-044 empties the stores instead,
  and FR-044a forbids code that can read the old shape.
