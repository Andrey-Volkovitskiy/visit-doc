# Feature Specification: One Message, Several Requests (Phase 1g)

**Feature Branch**: `010-multi-request-turns`

**Created**: 2026-09-09

**Status**: Draft

**Input**: User description: "Create a spec for docs/ROADMAP.md phase 1g" — Phase 1g, "One message,
several requests": a patient's sentence is a container, not a unit of work. The classifier stops
returning labels for the message and starts returning the requests inside it; each specialist reads
only its own requests; and the retrieval pipeline runs once per question rather than once per
message.

## Why this exists

Today the whole message is two things at once: it is the string the FAQ path embeds and searches
with, and it is what every selected specialist is told to answer. One fact, two jobs — and three
separate failures fall out of it.

1. **A compound question retrieves nothing.** "What's your address and what should I bring?"
   embeds to a point between the chunks that answer the address and the chunks that answer the
   preparation, and clears neither the similarity floor nor the rerank floor. The turn abstains and
   calls a human for two questions the corpus answers perfectly well one at a time.
2. **The FAQ node abstains on clauses no corpus could ever answer.** In "What's your address and
   what dentist slots are free tomorrow?", the booking clause is inside the string the FAQ node is
   asked to answer. It cannot be answered from documents, so the retrieval it drags along weakens
   the shortlist and the abstention it produces pages a person over a question that was never the
   corpus's to answer.
3. **The booking node answers policy out of nothing.** Asked to reply to "the last message", it
   sees the address question too, has no retrieval behind it, and writes an address.

The pattern is the project's own "one value, one meaning" smell one level up: *the message* is being
used as both the unit of retrieval and the unit of work, and those are not the same thing. This
phase makes the request the unit of work. The classifier already reads the message and the history
in one cheap call to decide what it is; this phase has that same call say what the requests *are*,
and everything downstream reads its own.

Two things this phase deliberately does **not** do, both assigned to Phase 1h: report a verdict per
request, and serve the requests it can while abstaining on the ones it cannot. 1g makes the turn
*see* several requests; 1h makes the turn *answer some and not others*. Splitting them keeps this
phase's change to the classifier's shape and each specialist's input, with the composer's contract
untouched.

## Clarifications

### Session 2026-09-09

- Q: With up to three retrieval pipelines running concurrently, one segment's retrieval or embedding
  call can fail while its siblings succeed. What does that turn do? → A: **It fails whole, exactly as
  a retrieval failure does today.** No reply is delivered, one failure escalation is raised, and
  nothing partial reaches the patient. Two reasons: a failure is not an abstention and must not be
  recorded as one, and serving the segments that survived is partial serving — Phase 1h's deliverable,
  with 1h's composer constraint behind it. This phase changes how many times the pipeline runs, not
  what a broken dependency means.
- Q: A turn with several answerable FAQ requests — one generation call per request, or one call over
  every request's shortlist? → A: **One call per request, then the existing merge.** Each request's
  answer is generated from its own surviving chunks alone, so FR-033's provenance is structural
  rather than an instruction: chunks belonging to another question are not in the prompt to be
  cross-wired. One prompt covering two questions is the same shape as the one query covering two
  questions this phase exists to undo. The cost is bounded by the cap — at most three generation
  calls plus one composing call, paid only by turns that genuinely carry several questions.
- Q: Does a non-request part of a message — a greeting, a thank-you — become a segment of its own?
  → A: **No. The segment list is the list of requests.** "Hi, do I need a referral?" is one segment,
  the referral question, and the greeting produces none: a part that routes nowhere must not consume
  one of the three slots, and carrying it inside a neighbour's text would put the noise back into the
  retrieval query that FR-004 exists to keep out. A message that carries no request at all yields
  exactly one segment holding it, so the small-talk path is still reached through the same derived
  intent set as every other path.
- Q: FR-044 and FR-062 require each request's own outcome to be recorded, while the phase claims no
  new storage. Which gives? → A: **Recorded means logged.** The stored message keeps its single
  verdict field exactly as today; every request's own outcome is carried by the turn's log events,
  which is where Phase 2 reads its metrics from in any case. No migration, no new stored field and no
  change to what any client receives. Moving the verdict onto the request — and the storage that
  follows it — is Phase 1h's, and a column added here would be reshaped by it a phase later.
- Q: How is a request identified on the six retrieval and gate events, so two concurrent pipelines'
  logs can be told apart? → A: **By its position in the message**, with the request's text carried
  once on the classification event. Position is already the spec's ordering rule, nothing reorders
  segments within a turn, and a reader joins the two on it. The alternatives were an opaque id, which
  costs a lookup to learn which question it was, and repeating the text on every event, which makes a
  free-text field the key a metrics consumer groups by.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Two questions in one message are two questions (Priority: P1)

A patient writes "What's your address, and what should I bring to a first visit?" Each half is
retrieved for on its own, against its own observation pool and its own gates, and the reply answers
both with the citations each half earned. Neither half is dragged below a floor by the other.

**Why this priority**: It is the phase's headline defect and the one a patient feels directly.
Shipped alone, it turns a class of message that today always abstains into a class that answers.

**Independent Test**: Take two corpus entries that each answer a different question, confirm each
question alone is answered today, then ask both in one sentence and confirm the turn answers both
rather than abstaining.

**Acceptance Scenarios**:

1. **Given** a corpus in which question A and question B are each individually answerable,
   **When** the patient sends a single message asking both, **Then** the turn answers both and
   abstains on neither.
2. **Given** that same turn, **When** its citations are read, **Then** they include the chunks that
   answer A and the chunks that answer B, each appearing once.
3. **Given** that same turn, **When** its record is read, **Then** the retrieval, the two gates and
   the scores are attributable to the question that produced them, not pooled into one list.
4. **Given** a message whose two halves are answered from the same chunk, **When** the citations are
   reported, **Then** that chunk appears exactly once.

---

### User Story 2 - A question and a booking stop damaging each other (Priority: P1)

A patient writes "What's your address, and what dentist slots are free tomorrow?" The FAQ path never
sees the slots clause, so it cannot abstain on it and cannot page a person for it. The booking path
never sees the address question, so it cannot answer it from nothing.

**Why this priority**: It is the other half of the same fix, and it is the one that produces a
*wrong* answer today rather than a missing one. A confabulated clinic address is worse than an
abstention.

**Independent Test**: Send a set of messages that each pair a corpus question with a scheduling
request, and confirm for every one: the FAQ half's abstention rate is unchanged from asking that
question alone, the booking half's reply contains no clinic fact, and no human is called for the
scheduling clause.

**Acceptance Scenarios**:

1. **Given** a mixed message, **When** the FAQ specialist runs, **Then** the text it retrieves for
   and answers contains no part of the scheduling request.
2. **Given** the same message, **When** the booking specialist runs, **Then** the text it is asked to
   act on contains no part of the corpus question, and its reply states no policy, address, price or
   preparation instruction.
3. **Given** a mixed message whose corpus question is answerable, **When** the turn completes,
   **Then** no escalation is raised by the scheduling clause.
4. **Given** a mixed message, **When** the reply is read, **Then** it addresses both halves, and
   neither half's answer is attached to the other half's question.

---

### User Story 3 - One request still costs exactly one path (Priority: P2)

The overwhelming majority of messages carry one request. They must come out of this phase making the
same calls, taking the same path, and producing the same reply they do today — one segment, one
specialist, no merge, no composing call.

**Why this priority**: Segmentation's own failure mode is over-splitting, and its cost lands on the
common case. Without this constraint the phase pays for a rare message with every ordinary one.

**Independent Test**: Send a set of ordinary single-request messages — including long, comma-heavy,
multi-sentence ones that restate a single need — and confirm each yields one segment, one specialist
and no composing call.

**Acceptance Scenarios**:

1. **Given** an ordinary question, **When** it is classified, **Then** it yields exactly one segment
   and the turn takes the existing single-specialist path.
2. **Given** a long message that circles one request, **When** it is classified, **Then** it is one
   segment — length, punctuation and repetition are not split points.
3. **Given** a message that repeats one request in two forms ("what time do you open? when can I
   come in the morning?"), **When** it is classified, **Then** it does not become two segments that
   retrieve twice for one answer.
4. **Given** any single-segment turn, **When** its calls are counted, **Then** it makes no model
   call, retrieval, rerank or scheduling call it does not make today.

---

### User Story 4 - The routing rules of the earlier phases survive, read per request (Priority: P2)

Everything Phase 1c and Phase 1f decided about a turn still holds; the only change is that each rule
is now read against the requests rather than the message. A request for a human still takes the
whole turn. An urgent condition, distress, or a booking for someone else still stops the
conversation whole. Small talk is still dropped whenever anything real is present.

**Why this priority**: It is the phase's regression surface. Segmentation touches the one value every
routing rule reads, so each rule needs re-stating against the new shape or it silently changes
meaning.

**Independent Test**: Send multi-request messages that each pair an ordinary request with one of the
overriding intents, and confirm each behaves exactly as the overriding intent alone behaves today.

**Acceptance Scenarios**:

1. **Given** a message asking a corpus question and asking for a human, **When** the turn completes,
   **Then** a person is called, the assistant falls silent, and the corpus question is not answered
   — exactly as today.
2. **Given** a message describing an urgent condition alongside a booking request, **When** the turn
   completes, **Then** the emergency reply is sent, nothing is booked, nothing is retrieved, and the
   conversation stops.
3. **Given** a message carrying a pleasantry and a real request, **When** it is classified, **Then**
   the pleasantry produces no segment at all, and the turn's segments are its requests alone.
4. **Given** a message whose every part is a pleasantry, **When** the turn completes, **Then** it is
   answered as small talk, once, calling nobody.
5. **Given** a message pairing an answerable question with a request the assistant is not authorized
   to serve, **When** the turn completes, **Then** the answer and the forwarding notice arrive as one
   reply, exactly as they do today.

---

### User Story 5 - The record says which question produced which retrieval (Priority: P3)

Two retrieval pipelines run concurrently in one turn. An operator reading the log afterwards can say
which chunks, which scores and which gate decision belonged to which question, and what segmentation
the classifier chose in the first place.

**Why this priority**: Nothing patient-facing depends on it, but without it Phase 1e's six events
become unreadable the moment two of them interleave — and Phase 2's metrics are computed from
exactly these events.

**Independent Test**: Run one two-question turn and reconstruct, from the log alone, both questions,
both observation pools, both gate decisions and both outcomes, without re-running anything.

**Acceptance Scenarios**:

1. **Given** a two-question turn, **When** its log is read, **Then** every retrieval and gate event
   names the request it belongs to.
2. **Given** any turn, **When** its classification event is read, **Then** it carries the segments
   the classifier chose — their order, their intents and their text.
3. **Given** a turn whose segmentation was limited by the cap, **When** its record is read, **Then**
   that fact is visible rather than inferred from the segment count.
4. **Given** a turn with two FAQ requests that ended in different outcomes, **When** its record is
   read, **Then** each request's own outcome is present, not only the turn's summary.

---

### Edge Cases

- **A message that is only pleasantries, several of them.** "Hi! Thanks again!" carries no request,
  so it is one segment and one small-talk reply — not two segments and not two replies.
- **Ellipsis in the second half.** "Do you have parking, and is it free?" — a second segment reading
  "is it free?" retrieves nothing on its own. It must be restated as "is parking free?".
- **A shared subject across halves.** "Can I see Dr. Vesalius on Friday, and what does he
  specialize in?" — the second segment must name the practitioner.
- **A pronoun resolved from history, not from the message.** The message says "and is that
  covered?"; what "that" is was named two turns ago.
- **The same request asked twice in one message.** Two segments that would retrieve for the same
  question are one request, not two — they are not independently answerable, they have one answer.
- **More requests than the cap allows.** A rambling message with five distinct asks yields three
  segments, with the remainder combined rather than dropped; nothing the patient asked may be absent
  from every segment. A combined segment is exactly today's behaviour for those parts.
- **Two booking requests in one message.** "Cancel Friday and book Monday instead" is one piece of
  work against one set of records — the booking specialist runs once over both, not twice.
- **Two FAQ requests, one answerable and one not.** The FAQ half of the turn abstains as a whole and
  a person is called once. Serving the answerable half is Phase 1h, and doing it here would need the
  composer constraint 1h introduces.
- **One request's search fails while another's succeeds.** A dependency failure fails the whole
  turn, as it does today — it is not an abstention, and the half that worked is not delivered on its
  own (FR-037).
- **Reranking unavailable for one request only.** The turn reports the degraded outcome — evidence
  the cross-encoder never approved must not be reported as evidence it did.
- **A segment that is not a restatement but an addition.** A segmenter that turns "and what should I
  bring?" into "what should I bring to a first cardiology visit?" when cardiology was never mentioned
  has invented the constraint that decides what is retrieved.
- **An empty or whitespace-only segment**, or a response carrying more segments than the cap: an
  invalid classification, taking the existing fallback — not a turn with a blank question in it.
- **Classification fails.** Unchanged: the existing fallback path runs, with no segmentation. A
  failure is not evidence about how many requests the message held.
- **A conversation where the assistant is silent.** No classification runs, so no segmentation
  happens — the message is stored and marked exactly as today.
- **A single-segment turn that would have been a merge.** One segment must never reach the composing
  step; a turn that streams its specialist's reply and then streams a composed one over it is the
  failure the merge step's existing guard exists to prevent.
- **A message in another language, or with no recoverable meaning.** Judged by the same rule: how
  many independently answerable requests does it carry? An unintelligible message carries none and
  keeps its existing treatment.

## Requirements *(mandatory)*

### Functional Requirements

**Segmentation**

- **FR-001**: The classification call MUST return an **ordered list of request segments**, each
  carrying an intent and the text of that request. The turn's set of intents MUST be derived as the
  set of its segments' intents, so every rule written against that set keeps working unchanged.
- **FR-002**: Segmentation MUST happen in the **same single, cheap-model, structured-output call**
  the turn already makes. No additional model call, no second round trip, and no separate
  segmentation step may be introduced.
- **FR-003**: Segment order MUST follow the order the requests appear in the patient's message.
- **FR-004**: Every segment's text MUST be a **standalone restatement** — understandable, and
  retrievable for, without the rest of the message and without the conversation. Pronouns, ellipsis
  and shared subjects are resolved against the message and the bounded history the classifier
  already reads.
- **FR-004a**: A segment's text MUST NOT be required to be a substring of the message. Restating is
  the point: "is it free?" is not a question a corpus can answer.
- **FR-004b**: A segment MUST NOT introduce a claim, constraint, specialty, date, practitioner or
  symptom that neither the message nor the history carries. A restatement that adds a constraint
  changes what is retrieved and answers a question the patient did not ask.
- **FR-005**: A message carrying **one** request MUST yield exactly **one** segment. A message MUST
  be split only where its parts are independently answerable.
- **FR-005a**: Two parts asking for the same thing are **one** request. Independently answerable
  means they have different answers, not that they are different sentences.
- **FR-005b**: A part of a message that requests nothing MUST NOT become a segment of its own when
  the message also carries a request. The segments are the requests; a greeting, a thank-you or a
  reaction beside a real request produces no segment and consumes none of the cap.
- **FR-005c**: A message that carries **no** request MUST yield exactly one segment holding it,
  labelled as small talk, so the turn's derived intent set reaches the small-talk path the same way
  every other path is reached. A pleasantry MUST NOT be folded into a request's segment text either:
  the segment is what is retrieved for, and a greeting inside it is noise in the query.
- **FR-006**: The number of segments MUST be **capped at 3**. The cap MUST be stated in the
  classifier's instructions and enforced **on arrival** by validation: an over-long list is rejected
  as an invalid classification (FR-008), never trimmed. It cannot be made unrepresentable in the
  response — the model provider rejects array bounds in a constrained-output schema — so "rejected,
  never trimmed" is what carries the guarantee that nothing the patient asked for is dropped. The
  cap is a starting value, to be revisited against Phase 2's golden set.
- **FR-006a**: When a message carries more requests than the cap allows, the segmenter MUST
  **combine** the least separable of them rather than drop any. Nothing the patient asked may be
  absent from every segment; under-splitting is today's behaviour, and losing a request is not.
- **FR-007**: A turn whose segmentation was bound by the cap MUST record that fact, so the cap can
  be argued up or down against turns that actually hit it.
- **FR-008**: A classification result that carries more segments than the cap, or a segment with
  empty text, MUST be treated as an **invalid classification** and take the existing
  classification-failure fallback unchanged — it MUST NOT produce a small-talk reply and MUST NOT
  take the not-authorized escalation route.

**Routing, read per segment**

- **FR-010**: Specialist selection MUST run over the set of segment intents and MUST otherwise be
  unchanged from today's selection over the list of labels.
- **FR-011**: An explicit request for a human on **any** segment MUST take the whole turn, exactly as
  it does today: nothing is retrieved, nothing is booked, the fixed hand-off notice is the reply, and
  the assistant falls silent.
- **FR-012**: An urgent condition, evident distress, or a booking for another person on **any**
  segment MUST take the whole turn and stop the conversation, exactly as it does today. Every other
  segment on that turn is suppressed — answering half a message and then falling silent is worse
  than handing over cleanly.
- **FR-013**: A turn is small talk only when its **single** segment is small talk (FR-005b, FR-005c),
  and such a turn MUST produce exactly one small-talk reply however many pleasantries the message
  contained. Should a small-talk segment nevertheless appear beside a request, it MUST be dropped and
  MUST NOT route anywhere — the rule holds at both ends, since the segmenter and the router fail
  independently.
- **FR-014**: An `unknown` segment MUST be escalated deliberately as a not-authorized request, with
  no retrieval and no generation for that segment, exactly as today. When it accompanies servable
  segments, the served answer and the forwarding notice MUST arrive as one merged reply, as they do
  today.
- **FR-015**: The precedence between escalation causes, which causes silence the assistant, which
  marks a staff reply clears, and the one-escalation-per-turn shape are all **unchanged**. This phase
  adds no cause, no mark and no second mechanism; it only changes what the existing rules are read
  against.

**What each specialist reads**

- **FR-020**: Each specialist MUST receive **only its own segments** — never the whole message, and
  never another specialist's segments. This is the whole of the fix for the mixed-intent failures:
  the FAQ path cannot abstain on a clause it never sees, and the booking path cannot answer a
  question it never sees.
- **FR-021**: Each specialist MUST keep the bounded conversation history it receives today. History
  is context; the segment is the request. Only the request changes hands in this phase.
- **FR-022**: The booking specialist MUST run **once** over all of its segments, not once per
  segment. Two scheduling requests in one message are one piece of work against one set of records,
  and its tool loop already carries the state that makes them one.
- **FR-023**: The booking specialist's instructions MUST state explicitly that it holds no clinic
  knowledge and answers nothing outside its own segments. The input slice and the instruction fail
  independently, so both must hold.
- **FR-024**: A specialist MUST NOT answer, acknowledge, or apologize for a request that is not in
  its segments. A request absent from a specialist's input is absent from its reply.

**Retrieval, once per request**

- **FR-030**: The retrieval pipeline MUST run **once per FAQ segment**, and each run MUST be
  independent: its own search, its own observation pool, its own similarity gate, its own reranking
  call and its own rerank gate. Both floors and both caps apply per segment.
- **FR-031**: Shortlists MUST NOT be pooled across segments at any point, before or after either
  gate. A single shortlist shared by two questions re-creates the original defect, with the stronger
  question's chunks crowding the other out under the three-chunk cap.
- **FR-032**: The per-segment runs MUST be **concurrent**, so a two-question turn's retrieval takes
  about as long as a one-question turn's rather than twice as long.
- **FR-033**: A segment's answer MUST be generated only from that segment's surviving chunks. No
  segment's answer may draw on another's.
- **FR-033a**: That isolation MUST be **structural**, not instructed: each FAQ segment is answered by
  its own generation call, given its own shortlist and no other segment's. A single prompt carrying
  several questions and several shortlists is the same shape as the single query covering several
  questions this phase exists to undo, and would leave provenance resting on the model's obedience.
- **FR-033b**: A turn MUST therefore make at most one generation call **per FAQ segment**, and at
  most one composing call, however many requests it carries.
- **FR-034**: Citations MUST be derived per segment from that segment's survivors, and
  **deduplicated** across segments before they are reported, so one chunk cited by two questions
  appears once.
- **FR-035**: The reranking-unavailable fallback MUST apply per segment: a segment whose reranking
  call failed is answered from the chunks that cleared the similarity floor, exactly as a turn is
  today, and no staff are called for it.
- **FR-036**: A turn MUST issue at most one search and at most one reranking call **per segment**,
  and therefore at most three of each. Retrieval work is bounded by the cap.
- **FR-037**: A failure of **any** segment's retrieval or embedding call MUST fail the **whole
  turn**, exactly as such a failure does today: no reply is delivered, no segment's partial result is
  used, and one failure escalation is raised. It MUST NOT be recorded as an abstention — a broken
  dependency and an empty shortlist are different situations with different fixes — and the surviving
  segments' results MUST NOT be served instead, which is partial serving and belongs to Phase 1h.

**The turn's verdict, and what is deferred to Phase 1h**

- **FR-040**: The turn MUST continue to carry exactly **one** retrieval verdict and one set of
  citations, in the shape it carries them today. Per-request verdicts are Phase 1h.
- **FR-041**: When every FAQ segment was answered, the turn's verdict MUST be the answered value —
  and MUST be the **degraded** answered value if any segment was answered without reranking. Weaker
  evidence governs: a turn resting partly on chunks no cross-encoder approved must not be recorded as
  one that rests on chunks it did.
- **FR-042**: When **any** FAQ segment abstained, the FAQ half of the turn MUST abstain **as a
  whole**: no FAQ answer is delivered, the patient receives the existing abstention message, and a
  person is called once under the existing corpus-gap cause. Serving the answerable half is Phase 1h,
  which introduces the composer constraint that makes it safe.
- **FR-043**: When several FAQ segments abstained at different gates, the turn's single verdict MUST
  be chosen by a fixed, documented rule — the first abstaining segment in message order — and MUST
  NOT be a value that no segment actually produced.
- **FR-044**: Every FAQ segment's **own** outcome MUST be recorded **in the turn's log**, so the
  turn-level verdict is a lossy summary only in the summary field and never in the record as a whole.
  This is the evidence Phase 1h needs to move the verdict onto the request.
- **FR-044a**: Nothing per-segment may be **stored**. The message keeps the single verdict field and
  the single citation list it carries today, in the same shape, and no client receives a new field.
  This phase adds no column, no migration and no read API.
- **FR-045**: A turn MUST raise at most one escalation however many segments abstained, unchanged
  from today's one-collector-per-turn shape.

**The reply**

- **FR-050**: A turn that produced more than one reply part — several specialists, several answers
  from one specialist, or an answer beside a forwarding notice — MUST produce **one** reply through
  the existing merge step, under the composer's existing constraints. This phase adds no constraint
  to the composer.
- **FR-051**: A turn **routed** as a single reply part — one segment, one specialist, no notice —
  MUST take the existing single-specialist path unchanged: the specialist streams its own reply and
  its own terminal event, and no composing call is made.
- **FR-051a**: A turn routed as several parts that **collapses** to one — the FAQ half abstaining
  under FR-042 with nothing else beside it — MUST emit that single part as the reply, also with **no
  composing call**. Its specialists were told to collect before retrieval ran, so none of them
  streamed and the part is emitted where the merge would have been. A composing call here would
  paraphrase a constant abstention message, putting a model in front of the one reply the design
  deliberately keeps model-free.
- **FR-052**: The reply MUST address every servable segment. No segment may be silently dropped —
  answering half a message and saying nothing about the other half is the partial-answer failure the
  parallel-specialist design exists to prevent.
- **FR-053**: The reply MUST keep each answer with the request it answers. A merged reply that
  attaches one question's answer to another question is a wrong answer, not a formatting problem.

**The record**

- **FR-060**: Each of Phase 1e's six retrieval and gate events MUST carry the **position of the
  segment it belongs to** in the patient's message, so events from concurrent pipelines are
  attributable to the question that produced them. Position is the join key; the request's text is
  carried once, on the classification event (FR-061), and never repeated on the six.
- **FR-061**: The classification event MUST log the segmentation chosen: each segment's position,
  intent and text, and whether the cap bound. It is the one event that carries segment text, and the
  one every other per-segment event is read against.
- **FR-062**: The turn's completion **log event** MUST be where FR-044's per-segment outcomes land,
  carrying them and the segment count alongside the turn's summary verdict.
- **FR-063**: The published field contract for these events MUST be updated in the same change, so
  Phase 2 computes its metrics against a written contract rather than against the implementation.

**Cost and non-regression**

- **FR-070**: A single-request message MUST make exactly the calls it makes today: one classification
  call, one specialist, no composing call, one search and at most one reranking call.
- **FR-071**: A turn MUST make exactly one classification call, however many requests the message
  carries.
- **FR-072**: No path other than the ones this spec names may change behaviour. The FAQ, booking,
  small-talk, hand-off and abstention paths produce the same replies, citations, verdicts and
  escalations they do today for the same single-request inputs.

**Verification**

- **FR-080**: The labelled sets behind the segmentation criteria MUST be **committed data**: each
  message with the segmentation a human assigned it — the expected number of segments, their order,
  and each one's intent.
- **FR-081**: The procedure for measuring segmentation against those sets MUST be written down and
  committed with them, together with the result the shipped implementation produced, so the same
  measurement can be repeated and compared — as Phase 1e's calibration set and Phase 1f's labelled
  sets already are.
- **FR-082**: Every behavioural requirement in this spec — routing, specialist input, per-segment
  retrieval, citation deduplication, the verdict rules, the merge, and what does *not* happen — MUST
  be verifiable **offline against a stubbed classifier** returning a fixed segmentation, with no live
  model call. Segmentation *quality* is the only thing the manual procedure measures.

### Key Entities

- **Request segment** *(new)*: one thing the patient asked for, carrying its position in the message,
  its intent, and its standalone text. A turn holds between one and three of them, in message order.
  A part of the message that asks for nothing is not one of them; a message that asks for nothing at
  all holds exactly one, labelled as small talk (FR-005b, FR-005c).
  It is a turn artifact: nothing is persisted, and the patient's message is stored and displayed
  verbatim as it is today.
- **Classification result**: was an unordered set of labels for the message; becomes an ordered list
  of segments. The label set every existing rule reads is derived from it rather than returned
  directly, which is what lets those rules survive unchanged.
- **Retrieval outcome**: gains a per-segment existence. The turn-level verdict keeps its existing
  value set and its existing single-field shape, and becomes a summary of the segments' outcomes —
  the thing Phase 1h replaces.
- **Citation**: unchanged in shape. Derived per segment and deduplicated across the turn.
- **Answer source**: gains no member. A turn whose several segments went to the same specialist is
  composed, so it is recorded as merged — which continues to mean "the composing model wrote this
  reply", not "two different specialists ran".
- **Retrieval log events**: unchanged in shape; each gains the segment it belongs to.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a labelled set of at least 10 compound questions built so that **each half alone is
  answered today**, 100% of turns answer both halves and 0% abstain. This is the headline defect: the
  per-half pipeline is identical to the single-question pipeline, so a half that clears the gates
  alone must clear them inside a compound message.
- **SC-002**: Across a labelled set of at least 20 messages pairing a corpus question with a
  scheduling request, **0** escalations are raised by the scheduling clause and **0** booking replies
  contain a clinic fact — an address, a price, a policy, or a preparation instruction.
- **SC-003**: Across a labelled set of at least 25 multi-request messages, at least 90% produce the
  expected number of segments with the expected intent on each, measured by hand against the
  committed labels.
- **SC-004**: Across a labelled set of at least 20 single-request messages — including long,
  comma-heavy and repetitive ones — at least 95% produce exactly one segment. Under-splitting is
  today's behaviour; over-splitting is a new failure, and this is the criterion that bounds it.
- **SC-005**: Across every labelled set, 0 segments contain a constraint, date, practitioner,
  specialty or symptom absent from the message and its history.
- **SC-006**: No turn produces more than three segments — 0 occurrences across every set.
- **SC-007**: A single-request turn issues zero additional model, retrieval, reranking or scheduling
  calls compared with today, and no composing call — verified by counting calls, not by timing them.
- **SC-008**: A turn carrying *k* FAQ requests issues exactly *k* searches and at most *k* reranking
  calls, for every *k* from 1 to 3.
- **SC-008a**: A turn carrying *k* FAQ requests issues at most *k* generation calls and at most one
  composing call, for every *k* from 1 to 3 — and every generated answer is traceable to the one
  shortlist it was given.
- **SC-009**: A patient asking two questions in one message waits about as long for the reply as a
  patient asking one — the two retrievals overlap rather than queue.
- **SC-010**: For any completed multi-request turn, a reader with only its log can state which chunks,
  scores and gate decisions belonged to which request — by joining each event's segment position
  against the classification event — and what segmentation the classifier chose, without re-running
  anything: 100% of turns.
- **SC-011**: The citations reported for any turn contain no duplicate chunk — 0 occurrences.
- **SC-012**: Across a labelled set of at least 15 multi-request messages that each also carry an
  overriding intent — a request for a human, an urgent condition, distress, or a booking for another
  person — 100% behave exactly as that intent alone behaves today: the same reply, the same cause, the
  same silence, and nothing retrieved or booked.
- **SC-013**: Every turn raises at most one escalation, however many of its requests failed — 0
  turns with two.
- **SC-014**: No existing behaviour regresses: for single-request inputs, the FAQ, booking,
  small-talk, hand-off and abstention paths produce the same verdicts, citations, escalations and
  marks they produce today. Any difference is a regression, not an improvement.

## Assumptions

- **The classifier's model, call shape and history window are unchanged.** What changes is the shape
  of what it returns — a list of segments rather than a list of labels — and the instruction that
  produces it. Nothing about how it is invoked, bounded, or logged changes.
- **The cap of three is a starting value.** It is chosen to bound fan-out, not because messages carry
  at most three requests; FR-007 records when it binds so Phase 2 can argue it up or down against
  data.
- **Segments are a turn artifact, not stored data.** No migration, no new table, no new column and no
  change to what the patient pane or the staff console displays. The message is stored verbatim, and
  everything per-segment — the segmentation itself, each request's retrieval and each request's
  outcome — lives in the turn's log (FR-044a).
- **Partial serving and per-request verdicts are Phase 1h**, per `docs/ROADMAP.md`. This phase keeps
  one verdict for the turn and abstains on the FAQ half as a whole (FR-042). For a compound FAQ
  question this is not a regression — such a message abstains entirely today — and for the common
  case, splitting is what makes both halves answerable in the first place.
- **Escalation carrying the specific unanswered request, verbatim, is also Phase 1h.** This phase's
  escalations keep today's shape and today's payload.
- **The composer's contract is untouched.** It already merges an FAQ half with a booking half and an
  answer with a forwarding notice; several answers from one specialist are the same job. The
  additional constraint an abstention-beside-an-answer needs is 1h's, which is why 1g does not
  produce that combination.
- **The booking specialist is asymmetric with the FAQ specialist, deliberately.** Retrieval is
  per-segment because its gates are per-query; booking runs once over its segments because its
  invariants are per-patient and its tool loop already sequences several operations.
- **A stubbed classifier is the seam every behavioural test uses.** Everything this phase adds is
  downstream of one classification result, so fixing that result makes every requirement testable
  offline and deterministically (FR-082).
- **The labelled sets are evaluation data for this phase, not a golden dataset.** They follow Phase
  1e's calibration set and Phase 1f's labelled sets: committed data plus a written manual procedure,
  with no runner, no assertions and no place in any gate. An automated live-model suite is Phase 2's.
- **Phase 2 owns the metrics.** This phase produces the per-segment record that segmentation accuracy
  and the mixed-intent metrics are computed from; it ships no harness and no dashboard.

## Out of Scope

- Per-request verdicts, partial serving of a turn's requests, and escalations carrying the specific
  unanswered question — all Phase 1h.
- Any change to the retrieval pipeline's stages, floors, caps, verdict values, or citation shape. The
  pipeline is unchanged; only how many times it runs and what each run is asked changes.
- Any change to booking, rescheduling, cancellation, or the scheduling service.
- Any change to escalation causes, marks, precedence, silencing, or the staff console — including any
  new indicator for a multi-request turn.
- Any change to the frontend: no new screen, no new control, no new field rendered.
- Any change to chunking or indexing; nothing is re-indexed and no stored embedding changes.
- An automated eval runner, a live-model test tier, or a CI-gated metric over the labelled sets —
  Phase 2.
- Widening the conversation history any specialist or the classifier reads.
- Splitting a message across turns, or asking the patient to split it themselves.
