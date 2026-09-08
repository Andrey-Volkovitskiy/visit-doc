# Feature Specification: Small Talk and What Escalation Is For (Phase 1f)

**Feature Branch**: `009-small-talk-and-escalation`

**Created**: 2026-09-08

**Status**: Draft

**Input**: User description: "phase 1f" — `docs/ROADMAP.md`'s Phase 1f, "Small talk, and what
escalation is actually for": a patient message that asks for nothing (a greeting, an
acknowledgement, a thank-you, a reaction) is answered the way a polite receptionist would answer
it, without retrieval and without calling a human; and escalation is narrowed to the three
situations that actually need a person — the patient asked for one, the patient asked for something
the assistant cannot provide, or something failed.

## Why this exists

Today the assistant has no route for a message that requests nothing. A message classified as
`unknown` falls through to the FAQ path, which embeds it, searches the corpus, clears no gate,
abstains, and records a call to staff with an attention mark on the conversation. So "Thanks"
pages a human. The patient also gets the abstention message — an apology for being unable to answer
a question they never asked.

Two separate defects sit inside that one behaviour:

1. **The assistant cannot be polite.** Every social message is handled as a failed question.
2. **"Nothing was said to me" and "I was asked something I can't answer" are the same outcome.**
   They call for different responses and different fixes, and the system cannot tell them apart —
   the "one value, one meaning" smell the project's design principles name explicitly.

An escalation queue that fills with pleasantries teaches the one staff member watching it that the
queue is noise. That is the failure this phase prevents.

## Clarifications

### Session 2026-09-08

- Q: A message that *is* a request but matches no path the assistant has — today it falls through to
  the FAQ path, retrieves, abstains, and calls staff as a corpus gap. Keep that, escalate directly,
  or retrieve first and record the cause honestly? → A: **Escalate directly, with its own cause.**
  "I would like you to prepare a sick leave paper for my employer" is not a hole in the clinic's
  documents and no amount of re-indexing will make it answerable — it is a request the assistant is
  not authorized to serve. So it makes no search and no generation call: it tells the patient a
  person has it, and records a cause distinct from the corpus gap. The cost is accepted knowingly: a
  real FAQ question the classifier mislabels reaches a human instead of an answer, which is the safe
  direction to fail in and is what the classification-accuracy criteria exist to bound.

- Q: What does the patient see? → A: **One fixed sentence**, in the shape of: "I am not authorized to
  handle the request, so I forwarded it to our staff. They'll follow up with you shortly. Feel free
  to ask if you need help with anything else in the meantime!" Fixed rather than generated for the
  same reason the existing hand-off sentence is: there is nothing here for a model to decide, and
  the things it must not do — promise a time, or imply the assistant will handle it after all — are
  exactly what a generated one could get wrong. *(Refined below: fixed when the
  notice is the whole reply; when it accompanies a served answer the composer renders both.)*

- Q: Does that silence the assistant, as asking for a human does? → A: **No.** The sentence's own
  last clause invites the next question, and the request being out of scope says nothing about the
  next one being out of scope too. It joins the corpus gap and the failure as a cause that raises
  attention without silencing; only a patient who asked for a person is owed silence.

- Q: Are there messages that must stop the conversation outright, rather than being answered or
  merely marked? → A: **Three of them** — an urgent condition, evident distress, and a request to
  book for another person. Each calls staff under its own cause, shows that cause beside the message
  in the console, silences the assistant until a person acts, and answers the patient with one fixed
  sentence. Distress is the deliberate hole in this phase's own "no request, no escalation" rule: a
  frightened patient often asks for nothing, and that is exactly when a person is needed.

- Q: FR-022c fixes the not-authorized sentence as text no model writes, while FR-022d requires it to
  reach the patient alongside a served answer — which the existing merge step composes with a model.
  Which gives? → A: **The notice is fixed only when it is the whole reply.** Alone, it is emitted
  verbatim with no generation call. Accompanied by an answer the assistant *could* serve, the
  composer renders both, and what is required of it is the three things the notice must convey, not
  its wording. A merged turn is already paying for that call, and a reply stitched from a paraphrase
  and a verbatim sentence reads as two voices.

- Q: A conversation already stopped for one cause receives "I can't breathe". Today a message
  arriving while the assistant is silent is stored and marked "arrived while the assistant was
  silent" without being classified at all. Does this phase classify it to surface the emergency? →
  A: **No — today's behavior is unchanged.** No classification runs while the assistant is silent,
  so none of this phase's causes can be set on such a message; it carries the existing generic mark.
  The conversation is already in the staff queue waiting for a person, which is the action an
  emergency would have asked for, and buying a per-message classification for a conversation nobody
  is answering yet would spend a call on a queue position it already holds. The accepted cost is
  that the *severity* of a message arriving mid-silence is not visible until a person reads the
  thread.

- Q: The existing `call_staff` label is defined as "an urgent or staff-handled issue, e.g. a billing
  problem" and silences the conversation under the cause "the patient asked for a person". That now
  overlaps two of this phase's causes. Does it narrow? → A: **Yes — it means only an explicit
  request for a human.** The cause it sets already carries that name, so the label and the cause
  come to say the same thing. What it over-collects today moves: a billing problem becomes a
  not-authorized request and stops silencing the conversation, an urgent condition becomes the
  urgent cause and keeps silencing it. This is a deliberate behavior change for messages that
  reach `call_staff` today, and SC-010 carves it out.

- Q: Four success criteria rest on labelled message sets, and classifying them for real needs live
  model calls the unit tier cannot make. Automated suite, committed data, or defer? → A: **Committed
  data plus a written manual procedure**, exactly as spec 008's calibration set. Behavior — routing,
  replies, silence, marks — is unit-tested against a stubbed classifier and is fully deterministic
  and offline; classification *accuracy* over the labelled sets is measured by hand once, and the
  measurement is recorded beside the data so the next person can repeat it and compare. There is no
  runner, no assertion, and no place in any gate: a live-model test is Phase 2's harness, and
  building one here would start the next phase inside this one.

- Q: What trips "booking for another person", given that stopping a conversation is expensive and
  ordinary messages mention other people? → A: **An explicit statement that the appointment is for
  someone other than the person in this chat.** Mentioning a third party is not enough — "my wife
  recommended you, can I book Tuesday?" is a booking for oneself. Where it is genuinely unclear who
  the appointment is for, the turn goes to the booking path, whose existing confirmation step asks
  before anything is written. Uncertainty is resolved by a question the patient can answer, not by
  stopping the conversation and waiting for staff.

- Q: The first live measurement (evaluation/procedure.md, 2026-09-08) found three messages
  classified `small_talk` that asked for something: "Perfect. What time should I arrive?", a bare
  "ok" confirming a slot offer, and "what is the weather in Paris today?". Are all three the same
  defect? → A: **The first two are; the third is a hole in FR-003.** A clinic question and a
  confirmation must never be small talk, and the classifier prompt now says so in those words. An
  entirely off-topic question is different: it requests something, so FR-003 as written made it an
  escalation, and paging a person because a patient asked about the weather is precisely the noise
  this phase removes. FR-003 now reads "anything the clinic could act on", with FR-003a and FR-003b
  naming the two sides, and the assistant deflects an off-topic question politely without calling
  anyone.

- Q: Re-testing after the first live run found a bare "Oh no" classified `distress`, stopping the
  conversation over two words. What does FR-040's "evident distress" exclude? → A: **A brief
  exclamation, and dismay about something ordinary.** Distress is about the patient's health, their
  care, or something happening to them. Recorded as FR-040a rather than left in the classifier
  prompt, because the parallel decision about off-topic questions was written down as FR-003a/b and
  two decisions of the same kind should not be treated differently — a spec that names one boundary
  and leaves the other to be discovered in a prompt is the drift this phase keeps finding.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A pleasantry is answered, not escalated (Priority: P1)

A patient finishes a booking and writes "Thanks!". The assistant replies the way clinic reception
would — briefly, warmly, and without inventing anything. No document is searched, no human is
called, and the staff console's queue does not move.

**Why this priority**: It is the whole point of the phase and the visible defect. Shipped alone, it
already removes the assistant's most common wrong reply and the queue's most common false entry.

**Independent Test**: Send each of "Hi", "I see", "Thanks", "OMG", "Let me think a bit" into a fresh
conversation and observe: a natural reply to each, no citation, no abstention message, and an empty
staff queue afterwards.

**Acceptance Scenarios**:

1. **Given** a conversation with the assistant active, **When** the patient sends "Thanks!",
   **Then** the assistant replies with a short courteous acknowledgement, the reply carries no
   citations, and the conversation is not marked for staff attention.
2. **Given** the same conversation, **When** the patient sends "Hi", **Then** the assistant greets
   them and may offer help in general terms, without naming a policy, a price, a date, or an
   appointment.
3. **Given** any such turn, **When** it completes, **Then** no corpus search and no scheduling call
   was made during it.
4. **Given** a session whose corpus is empty, **When** the patient sends "Good morning", **Then**
   they still get a greeting — an empty corpus is irrelevant to a message that asked nothing of it.

---

### User Story 2 - A pleasantry wrapped around a request is handled as the request (Priority: P1)

A patient writes "Hi, I need to cancel tomorrow's appointment." The greeting changes nothing: the
turn is a cancellation, and answering "Hello! How can I help?" would be a regression that this
phase introduced.

**Why this priority**: It is the failure mode the new intent creates. Without it, US1 makes the
assistant worse, not better — so the two ship together.

**Independent Test**: Send a set of messages that each pair a social opener with a real request and
confirm every one is handled by the specialist the request implies, with no small-talk-only reply.

**Acceptance Scenarios**:

1. **Given** an active conversation, **When** the patient sends "Hi, do I need a referral?",
   **Then** the turn is answered from the corpus with citations, exactly as it is today.
2. **Given** an active conversation, **When** the patient sends "Thanks! Any slots on Monday?",
   **Then** the turn is handled by the booking path, and the reply is not a bare acknowledgement.
3. **Given** a message carrying a pleasantry and a request the assistant cannot serve, **When** it
   is classified, **Then** the request decides the turn and the pleasantry is discarded.
4. **Given** a message whose reading is genuinely ambiguous between social and a request,
   **When** it is classified, **Then** it is *not* treated as small talk.

---

### User Story 3 - The same word is read against the conversation (Priority: P2)

"OK" means two different things one turn apart. After "Please arrive 15 minutes early" it closes the
exchange. After "9am Monday with Dr. Vesalius — shall I book it?" it is a confirmation, and
answering "See you soon!" instead of booking loses the appointment.

**Why this priority**: Without it the new intent silently breaks the multi-turn booking confirmation
flow that Phase 1c built — a worse outcome than having no small-talk path at all.

**Independent Test**: Drive the two conversations above to the same one-word reply and confirm they
diverge: one acknowledgement, one booking.

**Acceptance Scenarios**:

1. **Given** the assistant's last message was arrival instructions, **When** the patient replies
   "OK", **Then** the turn is small talk and no appointment is created.
2. **Given** the assistant's last message offered a specific slot and asked whether to book it,
   **When** the patient replies "OK", **Then** the turn is a booking and the appointment is created.
3. **Given** the assistant's last message asked the patient a clarifying question, **When** the
   patient replies with a bare "yes", **Then** the turn continues that exchange rather than being
   answered as a pleasantry.

---

### User Story 4 - The staff queue means something again (Priority: P2)

The one staff member watching the console sees a conversation appear only when a person is genuinely
needed: the patient asked for one, the patient asked for something the assistant cannot provide, or
something failed. Nothing else raises a mark.

**Why this priority**: The queue's credibility is what the escalation feature is worth. It depends
on US1 but is a separate, separately testable claim about what does *not* happen.

**Independent Test**: Walk a conversation through a mixture of pleasantries, answerable questions,
unanswerable questions, and an explicit request for a human, then read the queue: only the last two
kinds of turn appear in it.

**Acceptance Scenarios**:

1. **Given** a conversation containing only pleasantries and answerable questions, **When** the
   staff console is opened, **Then** that conversation is absent from the escalation queue.
2. **Given** a patient asks a question the corpus cannot answer, **When** the turn completes,
   **Then** staff are called exactly as they are today, and the assistant keeps answering.
3. **Given** a patient asks for a human, **When** the turn completes, **Then** staff are called and
   the assistant falls silent from the patient's next message, exactly as today.
4. **Given** a patient writes "I would like you to prepare a sick leave paper for my employer",
   **When** the turn completes, **Then** they are told the assistant is not authorized to handle it
   and that staff will follow up, a person is called under a cause distinct from a corpus gap, no
   document was searched, and the assistant answers their next question normally.

---

### User Story 5 - Three situations stop the conversation and fetch a person (Priority: P1)

Some messages must not be answered by a machine at all. A patient describing an urgent condition, a
patient in evident distress, and a patient trying to book on someone else's behalf each need a
person — the first two because the assistant cannot triage and must not try, the third because a
chat is one patient and the assistant cannot establish who is authorized to book for whom. In all
three the assistant says one fixed thing, stops replying, and staff see the reason on the message.

**Why this priority**: It is the phase's safety half. The other stories make the assistant polite;
this one keeps politeness from being the response to "I can't breathe". It also closes the gap the
rest of the phase opens: "no request, no escalation" would otherwise let unmistakable distress
through, because distress often asks for nothing.

**Independent Test**: Send an urgent-condition message, a distress message, and a
book-for-my-mother message into three conversations and confirm each one: a fixed reply, the
assistant silent from then on, a reason on the message in the console naming which of the three,
and no appointment created anywhere.

**Acceptance Scenarios**:

1. **Given** an active conversation, **When** the patient describes an urgent condition ("my chest
   hurts and I feel faint"), **Then** the reply directs them to emergency services, tells them staff
   have been notified, and the assistant produces no further reply in that conversation until a
   staff member acts.
2. **Given** an active conversation, **When** the patient expresses distress that asks for nothing
   ("I'm terrified about my results"), **Then** a person is called and the conversation stops —
   the "no request, no escalation" rule does not apply here.
3. **Given** an active conversation, **When** the patient asks to book for another person ("can I
   book Monday for my mother?"), **Then** no appointment is created or changed, the reply explains
   that a staff member will arrange it, and the conversation stops.
4. **Given** any of those three turns, **When** the staff console is opened, **Then** the patient's
   message carries a reason naming which of the three it was, distinguishable from a corpus gap,
   a request for a person, an out-of-scope request, and a failure.
5. **Given** such a conversation, **When** the patient sends further messages, **Then** they are
   stored and left unanswered, exactly as they are today in an escalated conversation.
6. **Given** such a conversation, **When** a staff member replies in it, **Then** the assistant is
   free to answer again, exactly as it is today.

---

### User Story 6 - A small-talk turn is on the record (Priority: P3)

An operator reading a turn's record can tell it was answered as small talk, and Phase 2 can count
how many escalations were raised by turns that contained no request.

**Why this priority**: It is what makes the phase's own success measurable rather than asserted, and
it is the input to Phase 2's metric. Nothing patient-facing depends on it.

**Independent Test**: Run one small-talk turn and one FAQ turn, then reconstruct from their records
alone which was which, what produced each reply, and whether either called staff.

**Acceptance Scenarios**:

1. **Given** a completed small-talk turn, **When** its record is read, **Then** it names small talk
   as the source of the reply, carries no retrieval verdict, and shows no call to staff.
2. **Given** a set of completed turns, **When** they are counted, **Then** the number that contained
   no request and nevertheless called staff can be computed from the records alone.

---

### Edge Cases

- **A pleasantry that is really a symptom.** "Ugh, my tooth is killing me" asks for nothing
  grammatically and is not small talk. An implied need is a request.
- **Both a pleasantry and a request for a person.** "Thanks, but I'd rather speak to someone" —
  the request for a person takes the whole turn, as it does today.
- **A conversation that is already escalated.** The assistant is silent; a "Thanks" arriving in it
  is stored and left unanswered like any other message, and generates no reply.
- **Classification fails.** A failed or invalid classification MUST NOT produce a small-talk reply —
  a failure is not evidence the message was social.
- **Gibberish, an emoji alone, or a message in another language.** Judged by the same rule: does it
  ask for anything? An unintelligible message asks for nothing that can be identified, so it is
  answered with an invitation to rephrase and calls nobody (FR-022e) — escalating "asdfgh" to a
  human would refill the queue this phase is emptying.
- **An out-of-scope request alongside an answerable one.** "What are your opening hours, and can you
  write me a sick note?" — the hours are answered and the note is acknowledged as forwarded, in the
  same reply.
- **Repeated out-of-scope requests in one conversation.** Each raises attention; the conversation
  does not fall silent, and the assistant keeps serving what it can between them.
- **Urgency inside a booking request.** "My chest hurts, can I see someone today?" — the urgency
  takes the turn and nothing is booked.
- **A booking that merely mentions someone else.** "My wife recommended you, can I book Tuesday?" —
  an ordinary booking; nothing stops.
- **A booking whose beneficiary is unclear.** "Can I book Monday morning for Marcel Proust?", where
  that is the chat's own patient name — routed to booking, which asks who the appointment is for
  rather than stopping the conversation.
- **Urgency inside a third-party booking.** "I'm booking for my daughter, she can't breathe" — the
  urgent condition outranks booking for another, and the reply is the emergency one.
- **A past condition, not a current one.** "Last year I had a heart attack, do I need a referral?"
  is a question, not an emergency. Over-triggering is the safe direction to fail in, and its cost is
  real: the conversation stops until a staff member speaks, so the patient loses the assistant for
  an ordinary question.
- **Distress that also asks something answerable.** The turn stops; the question goes unanswered and
  the fixed reply says staff will follow up. FR-046 prefers a clean handover to half an answer
  followed by silence.
- **A stopping situation in an already-escalated conversation.** The assistant is already silent, so
  the message is not classified at all (FR-025a): it is stored, carries the existing "arrived while
  the assistant was silent" mark, and generates no reply. Its urgency is visible only in its text,
  to the person already owed this conversation.
- **A conversation stopped for one of the three, then resumed by staff.** The mark clears with the
  staff reply and the assistant answers the next message normally — a resumed conversation is not
  permanently suspect.
- **A long message that still asks nothing** — a patient narrating their week. Length is not the
  discriminator.
- **A pleasantry as the very first message of a conversation**, before any history exists.
- **Repeated pleasantries** — "ok", "ok", "ok" — must not accumulate into an escalation.
- **The small-talk reply generation fails.** Handled as a generation failure is handled today; no
  new failure mode is introduced.

## Requirements *(mandatory)*

### Functional Requirements

**Classification**

- **FR-001**: The intent classifier MUST gain one additional label for a message that asks for
  nothing — a greeting, an acknowledgement, a thank-you, a farewell, a reaction, or a statement that
  the patient is thinking it over.
- **FR-001a**: The existing "call staff" label MUST narrow to **an explicit request for a human**,
  and MUST stop collecting the staff-handled issues it currently absorbs. A billing problem is a
  not-authorized request (FR-022); an urgent condition is FR-040's own cause. The label then
  describes exactly the escalation cause it sets, and no message is claimed by two labels that mean
  different things.
- **FR-002**: The label MUST be produced by the same single, cheap-model, structured-output
  classification call the turn already makes. No additional model call and no second round trip may
  be introduced for it.
- **FR-003**: The discriminator MUST be **whether the message requests anything the clinic could
  act on**, explicitly or by implication — not its length, its politeness, or its punctuation.
- **FR-003a**: A message asking anything about the clinic, an appointment, a practitioner, or the
  patient's own care is never small talk, however short or polite. "What time should I arrive?" is a
  question, not a pleasantry, and a bare "ok" answering a question the assistant just asked belongs
  to whatever was asked.
- **FR-003b**: A question about something the clinic has nothing to do with — the weather, sport —
  **is** small talk, and calls nobody. It requests something, so FR-003's earlier wording would have
  escalated it; there is nothing for a staff member to do with it, and paging one is the queue noise
  this phase exists to remove (FR-021).
- **FR-004**: A message that implies a need without asking for one (a symptom, a complaint, a
  statement of difficulty) MUST NOT be classified as small talk.
- **FR-005**: A message expressing urgency or distress MUST NOT be classified as small talk. It
  takes the stopping route of FR-040 instead, whether or not it asks for anything.
- **FR-006**: Classification MUST read the message against the conversation so far, so that the same
  words can be social in one context and a request in another. The context available to it is the
  bounded history the classifier already receives; this phase does not widen it.
- **FR-007**: When both a social and a request reading are plausible, the classifier MUST NOT choose
  small talk. Answering a request with a pleasantry is the costlier error, and the existing paths
  already know how to abstain.
- **FR-008**: When any other intent applies to the message, the small-talk label MUST be discarded
  and MUST NOT route anywhere. Small talk is the label for a turn that carries *only* small talk.
- **FR-009**: A failed or invalid classification MUST NOT yield small talk, and MUST NOT take
  FR-022's escalation route either. It keeps today's fallback to the corpus path unchanged: a
  failure is not evidence about what the message was, and guessing the corpus is the cheapest guess
  that can still produce the right answer.

**Answering small talk**

- **FR-010**: A turn whose only intent is small talk MUST be answered by a dedicated path that
  performs **no corpus retrieval, no scheduling call, and no tool call of any kind**.
- **FR-011**: That path MUST make at most one model call, using the cheap model the project reserves
  for non-generation work, and MUST see the bounded conversation history so its reply fits what was
  just said.
- **FR-012**: The reply MUST NOT state or imply any fact the assistant did not verify this turn.
  Specifically it MUST NOT name a policy, a price, a date or time, an appointment, or a
  practitioner's availability, and MUST NOT promise a callback or say a human has been notified.
- **FR-013**: A greeting MAY be answered with a general offer of help ("how can I help you today?"),
  since that promises nothing and asks the patient for the request the turn lacked.
- **FR-014**: The reply MUST reach the patient the same way every other single-specialist reply
  does, and its completion record MUST carry **no retrieval verdict and no citations** — neither of
  which exists for a turn that retrieved nothing.
- **FR-015**: The reply MUST be stored in the conversation as an ordinary assistant message, visible
  in both the patient pane and the staff console, with no marker distinguishing it.
- **FR-016**: A small-talk turn MUST NOT record a call to staff, MUST NOT place an attention mark on
  the patient's message, and MUST NOT change the conversation's escalation state.
- **FR-017**: A failure of the small-talk model call MUST be handled exactly as a generation failure
  on an existing path is handled today. This phase introduces no new failure semantics.

**What escalation is for**

- **FR-020**: A person MUST be called in exactly four situations, and no others: the patient asked
  for one; the patient asked for something the assistant cannot provide; the message needs a person
  on **safety or authority** grounds (FR-040); something failed. The second is recorded as **two
  distinct causes** — the corpus could not answer it, or the assistant is not authorized to do it —
  because they call for two different fixes (FR-022a); the third is recorded as three, one per
  situation, because a staff member must see which one they are looking at. No existing cause
  changes meaning.
- **FR-021**: A turn that contains no request MUST NOT call staff — with one deliberate exception,
  FR-040's distress and urgency, where the need for a person is the message's content rather than
  its request. That exception is named here so the rule and its hole stay in one place.
- **FR-022**: A message that *is* a request but matches no path the assistant has MUST be
  escalated directly: no corpus search, no scheduling call, no generation call. The classification
  is the decision, and nothing is asked to make it a second time.
- **FR-022a**: Such a turn MUST call staff under a cause **distinct from the corpus gap**, so the
  record separates "the clinic's documents lack this entry" — which is fixed by writing one — from
  "the assistant is not authorized to do this at all", which no entry will ever fix.
- **FR-022b**: That cause MUST raise attention on the conversation **without silencing the
  assistant**, as the corpus gap and the failure do. The patient may keep asking, and the assistant
  keeps answering what it can.
- **FR-022c**: The patient MUST be told three things: that the assistant is not authorized to handle
  the request, that it has been forwarded to staff who will follow up, and that they may ask for
  anything else in the meantime. When the notice is the **whole** reply it MUST be emitted as fixed
  text, with no generation call — there is nothing there for a model to decide.
- **FR-022c1**: When the notice accompanies an answer the assistant *can* serve (FR-022d), the
  turn's existing merge step MUST render both as one reply, which MUST convey FR-022c's three things.
  The wording is the composer's; the three things are not optional, and it MUST NOT promise a time,
  imply the assistant will handle the request after all, or claim the request was served.
- **FR-022d**: When such a request arrives **alongside** one the assistant can serve, the patient
  MUST receive both: the served answer, and the acknowledgement that the rest was forwarded. The
  out-of-scope part MUST NOT be silently dropped — a reply that answers half a message and says
  nothing about the other half is the partial-answer failure the parallel-specialist design exists
  to prevent. The two arrive as one merged reply under FR-022c1, not as a served answer with a
  verbatim sentence stapled to it.
- **FR-022e**: An **unintelligible** message — gibberish, a stray character, a fragment with no
  recoverable meaning — is not a request and MUST NOT call staff. It is answered courteously with an
  invitation to rephrase, by the same path that answers a pleasantry.
- **FR-023**: Nothing else about escalation changes: which reason silences the assistant, when the
  transition is applied, and how a turn raising more than one reason resolves to a single mark all
  stay exactly as they are. The new cause takes its place in that existing precedence and in the
  existing mark set; it introduces no second mechanism.
- **FR-023a**: The mark left by an out-of-scope request MUST be **cleared by a staff reply**, like
  the mark left by a patient asking for a person and unlike the corpus gap. A staff member writing
  the sick note *is* the whole of what that mark asked for — there is no residue for the record to
  keep, where a corpus gap survives its answer because the document is still missing.
- **FR-024**: An abstention by the FAQ path MUST keep calling staff exactly as it does today. This
  phase narrows what *reaches* that path; it does not weaken what happens when a real question
  cannot be answered.
- **FR-025**: While a conversation is escalated the assistant stays silent, including for small
  talk. The new path is subject to that state like every other.
- **FR-025a**: A message arriving while the assistant is silent MUST NOT be classified, and
  therefore MUST NOT carry any cause this phase introduces. It keeps today's treatment exactly:
  stored, marked as having arrived while the assistant was silent, unanswered. No new model call is
  introduced for a conversation nobody is replying in.

**Situations that stop the conversation**

- **FR-040**: Three further situations MUST each be recognized as their own cause: **an urgent
  condition**, **evident distress**, and **a request to book for another person**. Recognition is
  the classifier's, made in the same single call as every other label.
- **FR-040a**: "Evident" is what separates distress from a reaction. A message expressing real fear,
  panic or acute upset about the patient's health, their care, or something happening to them is
  distress; a brief exclamation on its own is not — "Oh no", "ugh", "yikes" are small talk — and
  neither is dismay about something ordinary like a wait or a full calendar. Stopping a conversation
  costs the patient the assistant until a person speaks, and two words should not cost that.
- **FR-041**: Each of the three MUST call staff under its **own** cause, and the console MUST show
  that cause beside the patient's message with a label naming which of the three it was — never
  folded into a single "needs attention".
- **FR-042**: Each of the three MUST **silence the assistant** in that conversation, using the
  existing indefinite escalation state cleared by a staff reply or by the console's switch — not the
  timed pause. A pause that expires would put a machine back in front of a patient in distress with
  nobody having looked at it, which is the one outcome this requirement exists to prevent.
- **FR-043**: The patient MUST receive **one fixed sentence, a different one per cause**, and nothing
  else: no retrieval, no generation call, no tool call. The classification is the decision.
- **FR-044**: The urgent-condition reply MUST direct the patient to emergency services and say that
  staff have been notified. The assistant MUST NOT assess severity, offer clinical advice, or
  reassure — it is not a triage system, and FR-040 is a routing decision, not a clinical judgement.
- **FR-045**: A book-for-another-person turn MUST NOT create, change, or cancel any appointment, and
  MUST NOT call any scheduling capability. Its reply says a staff member will arrange it.
- **FR-045a**: This cause MUST be tripped only by an **explicit** statement that the appointment is
  for someone other than the person in this chat. Mentioning another person is not enough: "my wife
  recommended you, can I book Tuesday?" is a booking for oneself.
- **FR-045b**: When it is genuinely unclear who an appointment is for, the turn MUST route to
  booking, and the booking path MUST establish who it is for before writing anything. Ambiguity is
  resolved by asking the patient, never by stopping the conversation — a stop can only be lifted by
  a staff member, and a question can be answered in the next message.
- **FR-046**: Each of the three MUST take the **whole** turn, suppressing every other intent on it,
  as a request for a person already does. Answering half a message and then falling silent is worse
  than handing over cleanly — and booking an appointment for a patient the turn is about to stop
  talking to is worse still.
- **FR-047**: When more than one cause applies to one message, exactly one is shown, chosen by a
  fixed precedence: **urgent condition → distress → asked for a person → booking for another →
  not authorized → corpus gap → failure**. Strongest claim on a person first; a discarded cause is
  still recorded in the turn's log, as it is today.
- **FR-048**: The marks these three leave MUST be **cleared by a staff reply**, like a request for a
  person and unlike a corpus gap: a person having read the message and answered it is the whole of
  what the mark asked for.
- **FR-049**: All five fixed replies this phase introduces or inherits — asked for a person, not
  authorized, urgent condition, distress, booking for another — MUST be recorded as the same kind of
  reply, a **fixed hand-off notice**, with the *cause* carried by the escalation reason rather than
  duplicated in the reply's own classification. One fact, one field.

**Verification**

- **FR-035**: The labelled sets behind SC-001, SC-003, SC-011 and SC-015 MUST be **committed data**:
  each message with the label a human assigned it and, where it applies, the cause the turn should
  raise. They carry no runner and no assertions, and no gate depends on them.
- **FR-036**: The procedure for measuring accuracy against those sets MUST be written down and
  committed with them, together with the result the shipped implementation produced, so the same
  measurement can be repeated and its outcome compared against the recorded one.
- **FR-037**: Every behavioral requirement in this spec — routing, replies, silence, marks, causes,
  precedence, and what does *not* happen (no retrieval, no generation, no booking) — MUST be
  verifiable **offline against a stubbed classifier**, with no live model call. The classifier's
  accuracy is the only thing the manual procedure measures.

**The record**

- **FR-030**: Every turn's record MUST identify small talk as the source of the reply, distinctly
  from an FAQ answer, a booking reply, a merged reply, and a hand-off.
- **FR-031**: Every turn's record MUST make it possible to tell whether the turn contained a request
  at all, and whether it called staff — so the two can be cross-tabulated without re-running
  anything.
- **FR-032**: The classification result MUST continue to be logged as it is today, now including the
  new label, so a misclassification can be investigated from the record alone.

### Key Entities

- **Intent label**: the closed set of things a patient message can be. Gains one member for "asks
  for nothing"; its existing members and their meanings are unchanged.
- **Answer source**: what produced the reply a turn ended with. Gains one member for small talk. The
  existing hand-off member widens from "the patient asked for a person" to "a fixed notice that a
  person now has this", which is what all five fixed replies are; *why* stays in the escalation
  reason rather than being duplicated here (FR-049).
- **Escalation reason**: why a person was called. Gains four members — not authorized, urgent
  condition, distress, booking for another person. Existing members are unchanged in meaning. Which
  members silence the assistant is a property applied where the escalation is written, not
  membership in this set: silencing now covers asked-for-a-person plus FR-040's three.
- **Attention mark**: why one patient message needs a person. Gains the four matching members. The
  three from FR-040 and the not-authorized one are cleared by a staff reply (FR-023a, FR-048); the
  corpus gap and the failure stay permanent as they are today. A small-talk turn sets none.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across a labelled set of at least 30 messages that request nothing — greetings,
  acknowledgements, thanks, farewells, reactions — 100% receive a courteous reply and **0%** call a
  human or place a mark on the conversation.
- **SC-002**: On 100% of those turns, zero corpus searches and zero scheduling operations occur, and
  at most one model call is made beyond the classification the turn already made.
- **SC-003**: Across a labelled set of at least 20 messages that pair a social opener with a real
  request, 100% are handled by the path the request implies; none is answered with a bare pleasantry.
- **SC-004**: In the confirmation scenario — the assistant has offered a specific slot — a bare
  affirmative from the patient results in a booking in 100% of trials; in the closing scenario, the
  same word results in no booking in 100% of trials.
- **SC-005**: No reply produced by the small-talk path in the SC-001 set contains a date, a time, a
  price, a named practitioner, a policy claim, or a promise that a person will follow up.
- **SC-006**: A message expressing urgency or distress is never answered as small talk across the
  labelled set — 0 occurrences.
- **SC-007**: After a walkthrough mixing every kind of turn — pleasantry, answerable question,
  unanswerable question, out-of-scope request, request for a human, urgent condition, distress, and
  a booking for someone else — every conversation in the
  staff queue traces to one of the situations in FR-020, and every conversation absent from it
  contained none of them.
- **SC-008**: For any completed turn, a reader with only its record can state whether it was
  answered as small talk, and whether it called staff, without reading source code or re-running it.
- **SC-009**: A small-talk turn issues **zero** retrieval, reranking and scheduling calls and at most
  one model call beyond the classification the turn already made — so its work is a strict subset of
  the FAQ path it replaces for those messages. No turn that is *not* small talk gains a call of any
  kind. Counted in calls rather than milliseconds: the call count is what this phase controls, and a
  wall-clock target would be measuring the providers.
- **SC-010**: No existing behaviour regresses: the FAQ, booking, mixed-intent, hand-off, and
  abstention paths produce the same replies, citations, verdicts, and escalations they do today for
  the same inputs — with one deliberate exception, FR-001a. A message that today reaches the
  staff-call label without asking for a person (a billing problem, say) is expected to change cause,
  and for a not-authorized one, to stop silencing the conversation. Every such change must be
  attributable to FR-001a; any other difference is a regression.
- **SC-011**: Across a labelled set of at least 15 requests the assistant is not authorized to serve,
  100% receive the fixed forwarding sentence, 100% call a person under the out-of-scope cause, and
  **0** corpus searches and **0** generation calls occur across the whole set.
- **SC-012**: After an out-of-scope request, the patient's next answerable question is answered
  normally in 100% of trials — the conversation never falls silent on this cause.
- **SC-012a**: A solo out-of-scope reply is byte-identical across trials; a merged one varies in
  wording but conveys all three of FR-022c's points in 100% of trials, and in none of them claims
  the request was served or promises when staff will respond.
- **SC-013**: Given any escalated conversation's record, a reader can state which of the seven causes
  raised it, and no conversation raised by an out-of-scope request is recorded as a corpus gap.
- **SC-014**: A staff reply clears the mark left by an out-of-scope request, and leaves a mark left
  by a corpus gap in place — verified on one conversation carrying both.
- **SC-015**: Across a labelled set of at least 20 messages spanning the three stopping situations,
  100% call a person under the correct one of the three causes, 100% leave the assistant silent, and
  **0** corpus searches, generation calls, and scheduling operations occur across the whole set.
- **SC-016a**: Across a labelled set of booking messages that merely mention another person, 0% stop
  the conversation, and every one whose beneficiary is unclear is answered with a question rather
  than a stop.
- **SC-016**: No message in that set results in an appointment being created, changed, or cancelled
  — verified in the scheduling service's own records, not only in the reply text.
- **SC-017**: A staff member reading the console can name which of the seven causes raised any
  escalated message from its label alone, without opening a log.
- **SC-018**: After a stopping turn, no assistant reply is produced for any subsequent patient
  message in that conversation until a staff member replies or re-enables the assistant, and each
  such message carries the existing "arrived while the assistant was silent" mark.
- **SC-019**: 100% of urgent-condition replies direct the patient to emergency services, and none of
  them contains a severity assessment, a diagnosis, a reassurance about the condition, or advice on
  what to do medically.

## Assumptions

- **The classifier's existing model and call shape are reused.** One label is added to a closed
  output set; the phase assumes no change to how classification is invoked, bounded, or logged.
- **The small-talk reply is generated, not selected from fixed strings.** A canned sentence per
  category reads as a machine and cannot answer "let me think a bit" appropriately. Constraining
  what it may say (FR-012) is what makes generation safe here, and there is nothing factual for it
  to get wrong because it is given nothing factual.
- **The small-talk path has no citations and no verdict because it retrieved nothing** — those
  fields are absent rather than empty-but-present, following the hand-off path's existing shape.
- **The only frontend change is the console's mark labels.** A small-talk reply is an ordinary
  assistant message and needs nothing new; the four added causes each need a label a staff member
  can read, added to the mapping the console already keeps. No new screen, no new control.
- **"The chat is paused" means the existing indefinite escalation silence**, the one a staff reply or
  the console switch clears — not the timed pause with a countdown, which exists for a staff member
  deliberately taking the assistant out of a conversation for a while (FR-042).
- **The three stopping causes are routing decisions, not clinical ones.** The assistant does not
  triage, score severity, or decide whether a condition is genuinely urgent; it decides who handles
  the message. Recall is preferred over precision here, and the cost of a false positive — a
  conversation stopped until a person speaks — is accepted knowingly. What the classifier *does*
  name, since the first live run, is a list of red flags (chest pain, difficulty breathing, heavy
  bleeding, a suspected overdose, fainting, a child who cannot be roused, stroke signs, sudden
  severe pain): describing an urgent condition in the abstract cost recall on "my chest hurts, can
  I see someone today?". That is clinical vocabulary in a routing rule, and it is worth saying
  plainly rather than leaving a reader to find it in a prompt — the claim this assumption makes is
  that the *reply* judges nothing, not that no clinical word appears anywhere.
- **"Booking for another person" is refused rather than verified.** The system has no way to
  establish that a patient may act for someone else, and a chat is one patient by design, so a staff
  member arranges it. This phase adds no consent, relationship, or authority model.
- **No new storage, migration, or read API is introduced.** The phase adds a route and a label.
- **The staff console shows no marker for small talk.** Consistent with the rule established in
  Phase 1e: a signal is shown only where a staff member can act on it, and a courteous reply needs
  no action.
- **Sub-query extraction is not part of this phase.** A mixed message is still handled by giving each
  specialist the whole message, exactly as today; FR-008 sidesteps the question entirely by dropping
  the social fragment rather than answering it alongside the request.
- **The out-of-scope sentence is fixed text when it stands alone**, like the existing hand-off
  sentence, and is written for the patient rather than for a model reading it back. Its exact
  wording is a copy decision, not a contract; what FR-022c fixes is the three things it must convey,
  which is also what binds the composer on a merged turn (FR-022c1).
- **The out-of-scope path reuses the existing escalation machinery wholesale** — one collector per
  turn, applied once after the graph completes, one mark per message resolved by the existing
  precedence. It adds a value to closed sets, not a second way to call a person.
- **Phase 2 owns the metric.** This phase produces the record the "escalations from turns with no
  request" number is computed from; it ships no eval harness and no dashboard.
- **The labelled sets in the Success Criteria are evaluation data for this phase, not a golden
  dataset.** They are the seed Phase 2's dataset can grow from, as Phase 1e's calibration set was —
  committed data with a manual procedure (FR-035–FR-036), never an automated live-model suite.
- **A stubbed classifier is the seam every behavioral test uses.** Everything this phase adds is
  downstream of one classification result, so fixing that result makes every requirement testable
  offline and deterministically (FR-037).

## Out of Scope

- Sub-query extraction, and any change to how a mixed-intent message is split between specialists.
- Any change to the retrieval pipeline, its gates, its thresholds, its verdicts, or its citations.
- Any change to booking, rescheduling, cancellation, or the scheduling service.
- Any change to the staff console's screens, the notification behaviour, or the assistant switch.
- Out-of-band notification of escalations (email, SMS) — Phase 3+.
- An automated eval runner, a live-model test tier, or a CI-gated metric over the labelled sets —
  Phase 2.
- Detecting or handling abusive messages as a category of their own.
- Clinical triage of any kind: severity scoring, symptom checking, or advice on what a patient should
  do medically.
- Any model of consent, guardianship, or authority to act for another patient; a third-party booking
  is handed to staff, never evaluated.
- Reaching staff out of band when a conversation stops — the in-app queue and mark are the whole of
  the notification, as they are today.
- Multilingual support as a guarantee; the rule is language-agnostic, but no language beyond the
  assistant's current behaviour is promised.
