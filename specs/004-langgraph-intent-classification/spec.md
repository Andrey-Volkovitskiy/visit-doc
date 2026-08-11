# Feature Specification: Adopt LangGraph + Intent Classification (Phase 1b)

**Feature Branch**: `004-langgraph-intent-classification`

**Created**: 2026-08-08

**Status**: Draft

**Input**: User description: "Make a spec for phase 1b of @docs/ROADMAP.md."

## Clarifications

### Session 2026-08-08

- Q: How should the size of the "bounded trailing context window" for intent classification be
  defined? → A: Fixed turn/burst count — a turn = one patient message-burst followed by one
  response burst; the window is the last 5 turns (per prior discussion), not a raw message count or
  token budget.
- Q: When should intent classification actually run, relative to a burst of consecutive patient
  messages? → A: Once per individual message — classification fires as each patient message arrives
  (even mid-burst), rather than waiting for the burst to complete.

### Session 2026-08-09

- Q: Should the classification log/trace record (FR-005) include the raw patient message text, or
  only the classified label(s) plus a reference back to the conversation turn? → A: Label +
  reference only — the record holds the turn ID and the classified intent label(s), never a copy of
  the message text; reviewers join back to the primary conversation store to see the actual message.
- Q: The Classified Intent entity mentions "whatever marks a low-confidence/fallback assignment"
  (FR-007's fallback case) — what should that marker actually be? → A: A simple boolean fallback
  flag — true when the recorded label(s) came from FR-007's default fallback rather than a normal
  classification result, false otherwise. No numeric confidence score.
- Q: Follow-up refinement — instead of a separate boolean, can the fallback case be recorded as a
  dedicated intent label value (e.g., a "classifier failed" label) alongside the real intent
  categories? → A: Yes — replaces the boolean flag. The recorded intent label is drawn from a
  5-value set (FAQ, booking, escalation, catch-all/"doesn't fit any category", and "classifier
  failed"), where the last value is assigned by the calling code on a failed/invalid classification
  call, never something the classifier itself outputs. This is orthogonal to which response path
  handles the message — FR-004 still always uses the FAQ-answering path in this phase regardless of
  the recorded label.
- Q: Follow-up correction — when a patient message's turn is itself superseded/cancelled by a
  follow-up message (an existing behavior from Phase 1a), should that message's classification still
  be separately completed and recorded? → A: No. Classifying a message whose turn is about to be
  cancelled has no value on its own: the message isn't lost, it becomes part of the *next*, surviving
  message's own classification context (FR-006 already folds the current in-progress burst into
  that call). Intent classification shares the same cancel-and-restart lifecycle as the FAQ-answering
  pipeline it accompanies — when a turn is superseded, its classification attempt is abandoned along
  with its FAQ reply, exactly like no assistant reply is ever recorded for a superseded turn. This
  supersedes the "each message's own result" framing in the Edge Cases entry on rapid bursts, and
  narrows FR-005/SC-002 to messages whose turn actually completes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - FAQ answers keep working after the internal swap (Priority: P1)

A patient asks a clinic policy/FAQ question in chat, the same way they could before this change.
They still get a grounded, correct answer streamed back, with their prior conversation turns still
taken into account.

**Why this priority**: This phase's chief risk is regressing the one thing that already works
(grounded FAQ answering) while restructuring how messages are processed internally. If FAQ answers
break, nothing else in the system matters yet.

**Independent Test**: Send a known FAQ question that previously worked, confirm the answer is still
correct and grounded, and confirm a same-topic follow-up question still resolves using earlier
turns of the conversation.

**Acceptance Scenarios**:

1. **Given** a patient has an existing conversation, **When** they ask a clinic policy/FAQ
   question, **Then** they receive the same quality of grounded answer as before this change.
2. **Given** a patient already asked an FAQ question in the conversation, **When** they ask a
   related follow-up, **Then** the answer still accounts for the earlier turn.

---

### User Story 2 - Non-FAQ and mixed-intent messages are still classified and handled gracefully (Priority: P2)

A patient sends a message that isn't purely an FAQ question — for example, "I'd like to book an
appointment for next Tuesday," "I need to talk to someone about a billing problem," or a message
that mixes intents ("I'd like to book a visit to a cardiologist on Friday — what should I bring?").
No booking or escalation capability exists yet at this stage, but the message is still classified
(capturing every intent present, not just one) and still gets a coherent response rather than an
error, a blank reply, or a wrong-looking answer that pretends to book something.

**Why this priority**: This is the first proof that intent classification actually works and
produces a usable signal, ahead of it being wired into real routing decisions in a later phase. It
also guards against the system confidently fabricating an action (like a booking) it can't actually
perform.

**Independent Test**: Send a clearly booking-flavored message, a clearly escalation-flavored
message, and a message that mixes an FAQ question with a booking request; confirm each gets a
coherent response (not an error) and that every intent present was recorded for each. Separately,
with the classification step forced to fail, confirm the response is still coherent and the outcome
is recorded as "classification failed."

**Acceptance Scenarios**:

1. **Given** a patient sends a booking-flavored message, **When** it is processed, **Then** the
   system responds coherently (without fabricating a booking) and records the booking intent for
   the message.
2. **Given** a patient sends an escalation-flavored message (e.g., describing an urgent problem),
   **When** it is processed, **Then** the system responds coherently and records the escalation
   intent for the message.
3. **Given** a patient sends a single message that mixes an FAQ question with a booking request,
   **When** it is processed, **Then** both the FAQ and booking intents are recorded for that
   message, and the patient still receives one coherent response.
4. **Given** the classification step itself fails or returns an invalid result for a patient
   message, **When** the message is processed, **Then** the system still responds coherently via
   the FAQ path exactly as if classification had succeeded, and the message's outcome is recorded
   as "classification failed" rather than a fabricated real intent or no record at all.

---

### User Story 3 - Classified intents are reviewable before they're trusted for routing (Priority: P3)

A developer or maintainer reviewing recent conversations can see which intent was assigned to each
patient message, so they can spot-check classification quality before later phases start using it to
actually route conversations to booking or escalation handling.

**Why this priority**: Lower priority than the patient-facing behavior, but necessary groundwork —
without a way to review classifications, nobody can tell whether the classifier is trustworthy
enough to build real branching on top of in the next phase.

**Independent Test**: After sending a handful of messages with obviously different intents, confirm
each message's classified intent can be looked up/reviewed without re-running the conversation.

**Acceptance Scenarios**:

1. **Given** several patient messages with different apparent intents have been sent, **When** a
   maintainer reviews them afterward, **Then** each message's classified intent is available to
   inspect.

---

### Edge Cases

- What happens when a single message mixes intents (e.g., an FAQ question and a booking request in
  the same sentence)? Every intent present is recorded, but the message is still answered as one
  unified response in this phase — decomposing the response per intent is out of scope until real
  branching is added later.
- What happens when the classification call itself fails or returns something invalid (timeout,
  error, output that doesn't match the defined label set)? The system must still answer the message
  via the FAQ path and record the dedicated "classification failed" label, rather than the request
  erroring out or hanging.
- What happens with a short, context-dependent message like "yes," "Tuesday works," or "what about
  tomorrow" that only makes sense in light of earlier turns? Classification must have enough recent
  conversation context to resolve it correctly, not just the message in isolation.
- What happens with a message unrelated to FAQ, booking, or escalation entirely (off-topic or
  nonsensical)? This is a normal, successful classification, not a failure — the classifier
  confidently assigns the catch-all category (FR-003) rather than crashing or defaulting.
- What happens when a patient sends several messages in quick succession before the assistant
  responds (e.g., "I want to book Friday" followed immediately by "actually, make it Monday")? Each
  message still starts its own classification attempt as it arrives, but an earlier message's turn is
  superseded/cancelled the moment a later message arrives (the same behavior FAQ generation already
  has) — that earlier attempt is simply abandoned, not recorded. Only the final, surviving message's
  turn completes and gets a recorded classification, using context that already includes the earlier,
  cancelled messages of the same burst (FR-006) — mirroring exactly how only one FAQ reply is ever
  produced per settled burst, not one per message.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST classify every incoming patient message against the defined intent
  categories, capturing **every** intent present rather than forcing a single label onto messages
  that mix intents.
- **FR-002**: Intent classification MUST use structured output restricted to a fixed, closed set of
  intent labels — never inferred by parsing the free-text of a generated reply.
- **FR-003**: The set of intent categories MUST include at least FAQ, booking, and escalation, plus
  a catch-all category for messages that fit none of them.
- **FR-004**: Regardless of the classified intent(s), the system MUST continue answering every
  message through the existing FAQ-answering path in this phase, as one unified response, since no
  booking or escalation capability — and no per-intent response decomposition — is implemented yet.
- **FR-005**: The system MUST record the classified intent(s) for each patient message whose turn
  actually completes (is not itself superseded/cancelled by a later message), in a way that can be
  reviewed afterward without re-running the conversation. This record MUST reference the
  conversation turn (e.g., its ID) rather than duplicate the message's raw text, so patient message
  content isn't copied into a second, separately-governed log/trace store. A message whose turn is
  cancelled gets no classification record, the same way it gets no assistant reply.
- **FR-006**: Classification MUST start for each incoming patient message as soon as it arrives —
  not batched until a message-burst finishes — using as context the 5 most recent prior conversation
  turns (a turn being one patient message-burst followed by one response burst) plus any earlier
  not-yet-answered messages already sent in the current, in-progress burst. This must be enough to
  correctly resolve short, context-dependent replies (e.g., "yes," "what about tomorrow") without
  reprocessing the entire conversation history on every message. Classification shares the same
  cancel-and-restart lifecycle as the FAQ-answering pipeline it accompanies (FR-005): if a message's
  turn is superseded before classification completes, the attempt is abandoned, not recorded — the
  superseded message's content still reaches the surviving message's own classification call via
  this same context window.
- **FR-007**: When a classification attempt actually completes but the call failed or returned
  something invalid (rather than the classifier confidently choosing the catch-all category from
  FR-003, which is itself a normal, valid result, or the attempt simply being abandoned because its
  turn was superseded per FR-006, which is not a failure either), the system MUST NOT let the
  request fail outright. It MUST still answer the message via the FAQ-answering path per FR-004, and
  MUST record a dedicated "classification failed" label for that message — distinct from every real
  intent category, including the catch-all one, and distinct from a superseded attempt (which gets
  no record at all) — so failed classification attempts can always be told apart from both genuine
  classification results and abandoned ones during review.
- **FR-008**: The message-handling flow MUST be restructured as a sequence of discrete steps
  (classify, then respond) rather than a single monolithic operation, so that later phases can add
  new response paths without reworking the existing FAQ path.

### Key Entities *(include if feature involves data)*

- **Conversation Turn**: An existing entity (from Phase 1a) representing one message in a
  conversation; gains an associated set of classified intents in this phase.
- **Classified Intent**: One label assigned to a given patient message whose turn actually completed
  (FR-005) — a message whose turn was superseded/cancelled has no Classified Intent at all, the same
  way it has no assistant reply. A message may have more than one label. Drawn from either a real
  intent category (FAQ, booking, escalation, or the catch-all category for messages that fit none of
  them — all four being valid, confident classification outcomes) or, when the classification call
  itself failed or was invalid (FR-007), a dedicated "classification failed" label instead — never
  both, and never silently recorded as one of the real categories. Used for review in this phase, and
  for real routing decisions starting in the next phase.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every FAQ question that previously received a grounded answer continues to receive an
  equivalent grounded answer after this change — no regression in the existing FAQ experience.
- **SC-002**: A classification record — either real intent label(s) or an explicit
  "classification failed" marker (FR-007) — is produced for at least 99% of patient messages whose
  turn actually completes; messages whose turn is itself superseded/cancelled by a follow-up are
  excluded from this measure, since no classification is expected for them (FR-005). This measures
  whether the recording mechanism itself is reliable (it doesn't silently lose results for a message
  that got a real answer) — it is **not** a measure of classification quality; a message can count
  toward this 99% whether its classification succeeded or explicitly failed. Whether classification
  actually produces correct intents is SC-003's job, not this one.
- **SC-003**: On a small hand-labeled sample of representative patient messages — including some
  that mix more than one intent and some that only make sense with prior context — the recorded
  intent(s) match the expected intent(s) at least 80% of the time. A "classification failed" result
  never matches an expected intent, so this is also where a classifier that fails routinely (even if
  each failure is honestly recorded, satisfying SC-002) would actually be caught.
- **SC-004**: Adding intent classification does not add more than 1–2 seconds, on average, to the
  time a patient waits before their answer starts streaming back.

## Assumptions

- Per the roadmap, this phase proves the internal processing-flow swap and adds intent
  classification ahead of it being acted on — it delivers no new patient-facing capability
  (booking/escalation) yet. Every message is still answered via the existing FAQ path regardless of
  its classified intent.
- The intent taxonomy targets the three categories used through the rest of Phase 1 (FAQ, booking,
  escalation), plus a catch-all category. Classification captures every intent present in a message
  (multi-label), matching the plural "expected intent(s)" already anticipated by Phase 2's golden
  dataset — but decomposing the response per intent is deferred to the phase that adds real
  branching; in this phase every message still gets one unified FAQ-style response regardless of how
  many intents were detected.
- Classification starts for each individual patient message as it arrives (not batched per burst),
  using the 5 most recent prior turns (patient burst + response burst) plus any earlier not-yet-
  answered messages of the current burst as supporting context — not the full history, to keep the
  step cheap and fast. This fixes the window's unit/size and the trigger timing at the spec level so
  acceptance tests (e.g., SC-003's hand-labeled sample) can be built deterministically; grouping
  messages into bursts/turns is planning's job to implement, not to redefine.
- Classification shares the FAQ-answering pipeline's existing cancel-and-restart lifecycle rather
  than running independently of it: starting an attempt per message (rather than waiting for a burst
  to settle) trades some attempts that get abandoned mid-flight under a rapid burst for lower latency
  on the common, non-burst case — the same tradeoff already accepted for FAQ generation itself. An
  abandoned attempt costs no correctness, since the superseded message's content reaches the
  surviving message's own classification call via the same context window (FR-006).
- Classified intents are recorded via logs/traces for later review, not as a new field persisted on
  the conversation data model — formal tracing infrastructure arrives in a later phase. These
  records store the conversation turn ID and the classified label(s) only, never a copy of the
  message text, since application logs/traces are typically less access-controlled than the primary
  conversation database and this is a medical-clinic assistant handling potentially sensitive
  patient messages.
- Classification uses a fast, low-cost model, distinct from the model used to generate FAQ answers,
  consistent with routing models deliberately by cost/capability.
- When a classification call fails or is invalid, the message is still answered via the FAQ path
  (the only implemented response path in this phase, per FR-004) — but the *recorded* intent is the
  dedicated "classification failed" label (FR-007), not "FAQ." Which path answers the message and
  what gets recorded as its intent are independent: the former is fixed for every message in this
  phase; the latter honestly reflects what the classifier actually produced (or that it produced
  nothing at all).
- A classifier that confidently assigns the catch-all category (FR-003 — the message genuinely fits
  no defined intent) is a normal, successful classification, not a failure — it is recorded as the
  catch-all label, never confused with the dedicated "classification failed" label above.
- Three distinct outcomes exist per patient message, and only one of them produces a record: a
  completed, successful classification (real category label(s), possibly the catch-all); a
  completed, failed classification attempt (the "classification failed" label, FR-007); and an
  abandoned attempt whose turn was superseded before it completed (FR-006) — which gets no record at
  all, the same as a superseded turn gets no assistant reply. The first two count toward SC-002; the
  third is excluded from it by definition, not a shortfall against it.
