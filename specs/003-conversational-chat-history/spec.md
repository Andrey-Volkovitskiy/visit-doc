# Feature Specification: Conversational Chat History

**Feature Branch**: `003-conversational-chat-history`

**Created**: 2026-08-06

**Status**: Draft

**Input**: User description: "Change UX to chat like. A Patient can see previous conversations
turns. The agent considers previous patient messages (e.g. "I'm going to come on Tuesday", "What is
you working hours that day?"). Patient can click on a "Clear conversation" button to have the
dialog cleaned."

**Implements**: [ROADMAP.md](../../docs/ROADMAP.md) Phase 1a — Multi-turn chat state. Scope
is bounded to that sub-phase: this feature turns Phase 0's stateless, single-turn exchange into a
persisted, context-aware chat between a patient and the assistant. It does not adopt
LangGraph (Phase 1b) and does not add a staff participant or escalation (Phase 1d). Phase 1a's own
description requires the resulting chat shape to be "a flat, ordered log ... not a fixed
request/response pair" so that Phase 1d can later extend it with a staff sender without a breaking
redesign — this spec's `Message` model (see Key Entities) is written to satisfy that requirement now,
not just to display history nicely.

## Clarifications

### Session 2026-08-06

- Q: Should chat history survive a page reload / reopening the browser, given there's no
  patient login yet? → A: Persist across reloads via an anonymous per-browser identifier
  (cookie/local storage), no login required.
- Q: What should "Clear chat" do to the stored chat data? → A: Hard delete — the
  old chat's messages are permanently removed from storage and a brand-new empty
  chat begins.
- Q: How long should chat history persist before it's considered stale/expired? → A: No
  expiration — the chat persists indefinitely until the patient explicitly clears it.
- Q: If generating the assistant's reply fails partway (e.g. a streaming error), what happens to
  that message in the chat's stored history and context? → A: The patient's message stays
  in history and remains available as context for future turns; no assistant reply is stored for
  the failed attempt — the patient can simply ask again.
- Q: When a patient sends a new message while the assistant is still generating a reply to an
  earlier one in the same chat, what happens to that in-flight generation? → A: Cancel and
  restart — the in-flight generation for the earlier message is cancelled and discarded, and a fresh
  reply is generated considering every message sent so far, including the new one.
- Q: If a reply is cancelled (per the above) after it had already begun visibly streaming to the
  patient, what should happen to that partial, now-superseded text? → A: It is removed from the
  patient's view entirely — never left visible as if it were the final answer, and never shown as an
  error. Replies keep streaming live as normal right up until a cancellation removes them, so a
  message that never gets superseded is unaffected.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask a follow-up that relies on earlier context (Priority: P1)

A patient is chatting with the assistant and mentions a detail in one message (e.g. "I'm going to
come on Tuesday"), then asks a follow-up question that only makes sense in light of that detail
(e.g. "what are your working hours that day?"). The assistant's reply correctly reflects the
earlier detail without the patient having to repeat it.

**Why this priority**: This is the actual value behind "chat like" — without it, showing message
history is just a transcript with no intelligence behind it. It's the headline capability this
feature exists to deliver.

**Independent Test**: Send two messages in the same chat where the second depends on
information only present in the first (not restated), and confirm the assistant's second reply
correctly uses that earlier information. Verifiable via the chat API directly, independent of any
UI display of history.

**Acceptance Scenarios**:

1. **Given** a patient has just told the assistant "I'm going to come on Tuesday," **When** they
   then ask "what are your working hours that day?" **Then** the assistant's answer addresses
   Tuesday's hours specifically, showing it used the earlier message as context.
2. **Given** a chat has no prior messages (the very first message sent), **When** the
   patient asks a question, **Then** the assistant answers using only that message — there is no
   earlier context to draw on, and none is fabricated.
3. **Given** a multi-turn chat is underway, **When** the assistant cannot find FAQ content
   relevant to the current question even after considering prior context, **Then** it still abstains
   rather than fabricating an answer, exactly as it does in a single-turn exchange.
4. **Given** a patient sends two messages in a row before the assistant has replied to the first
   (e.g. adds a clarifying detail before there's been time to answer), **When** the assistant
   generates its next reply, **Then** it takes both patient messages into account, not just the most
   recent one.

---

### User Story 2 - See the chat so far, even after coming back (Priority: P2)

A patient chatting with the assistant can see every message exchanged so far in the current
chat, laid out like a normal chat thread — including stretches where the patient sent
several messages in a row before the assistant replied, which real chats don't avoid.
If they reload the page or close and reopen the browser, their chat is still there — nothing
is lost.

**Why this priority**: This is the visible, user-facing half of "chat like" UX. It depends on
chat state already existing (User Story 1's storage), but doesn't by itself require the
assistant to use that history intelligently — hence it can be built and verified as a distinct
slice, ranked just below the context-aware answering itself.

**Independent Test**: Send several messages in a chat, reload the page, and confirm every
prior message — patient and assistant alike, including citations or abstention — is still displayed
in the order it was sent.

**Acceptance Scenarios**:

1. **Given** a patient has exchanged several messages with the assistant, **When** they view the
   chat, **Then** every prior message is displayed in chronological order, each labeled with its
   sender (patient or assistant).
2. **Given** a patient has an ongoing chat, **When** they reload the page or reopen the
   chat in the same browser, **Then** the full prior chat is still displayed exactly as it
   was.
3. **Given** an earlier assistant message cited specific FAQ content (or abstained), **When** that
   message is displayed after a reload, **Then** its citations (or abstention message) are still
   shown, not just the reply text.
4. **Given** a patient sent two messages in a row before the assistant replied, **When** the
   chat is displayed, **Then** both patient messages appear in the order they were sent,
   followed by the assistant's reply — the display does not force a strict alternating
   patient/assistant pattern.

---

### User Story 3 - Clear the chat and start fresh (Priority: P3)

A patient who wants to start over — a new topic, or just a clean slate — clicks a "Clear
chat" button. The visible chat empties out, and the assistant has no memory of what was
discussed before.

**Why this priority**: Useful and expected once a chat persists across reloads, but the
product still delivers its core value (Stories 1 and 2) without it — it's a control for managing
state that only matters once that state exists.

**Independent Test**: With an active chat containing several messages, click "Clear
chat," confirm the chat view is empty, then send a new message referencing something only
mentioned in the cleared chat and confirm the assistant does not use it.

**Acceptance Scenarios**:

1. **Given** an active chat with several messages, **When** the patient clicks "Clear
   chat," **Then** the chat view immediately shows an empty chat.
2. **Given** a chat was just cleared, **When** the patient sends a new message, **Then** a
   new chat begins and the assistant's reply shows no awareness of anything discussed
   before the clear.
3. **Given** a chat was cleared and the page is later reloaded, **When** the chat loads,
   **Then** the cleared chat does not reappear — the patient sees only whatever new
   chat (if any) has happened since.

---

### Edge Cases

- What happens when a patient opens the chat for the very first time (no cookie/local identifier
  yet)? A new, empty chat is started automatically — this is not an error state.
- What happens when a patient's anonymous identifier is missing or unrecognized (cookies/local
  storage cleared, or a different browser/device)? The system starts a new, empty chat
  rather than erroring; the previous chat (if any) is simply not reachable from that
  browser/device anymore.
- What happens when "Clear chat" is clicked on an already-empty chat? It succeeds
  as a no-op from the patient's perspective — the chat was empty and remains empty.
- What happens if a patient has the chat open in two tabs of the same browser at once? Both tabs
  operate against the same underlying chat; normal request ordering applies and no
  additional locking/merging behavior is required.
- What happens to the existing per-message validation (empty messages, messages over the length
  limit) in a multi-turn chat? It continues to apply to every new message regardless of how
  many messages already exist.
- What happens to grounding and abstention behavior across turns? They continue to apply per reply,
  unchanged — multi-turn context changes what question is being answered, not whether an ungrounded
  answer can be presented as fact.
- What happens if generating the assistant's reply fails partway (e.g. a streaming error)? The
  patient's message is still kept in the chat's history and remains available as context for
  future turns, but no assistant reply is stored for that failed attempt — the patient can ask again
  to get a real answer.
- What happens to a chat that a patient hasn't touched in a long time? It is not
  automatically expired or cleared — it remains available indefinitely until the patient explicitly
  clears it.
- What happens when a patient sends a new message before the assistant has finished replying to an
  earlier one? The earlier message's in-flight reply generation is cancelled and no reply is stored
  for it — the same no-partial/no-fabricated-reply rule as a failed generation (FR-012, FR-015) — and
  the system generates one fresh reply considering every message sent so far, including the new one.
  Every patient message itself is still recorded in the chat in the order it was sent; only
  the superseded reply attempt is discarded. The system does not require or enforce a strict
  one-message-then-one-reply alternation.
- What happens to a partial answer the patient already saw start streaming, if its generation then
  gets cancelled by a new message (FR-015)? That partial content is removed from view (FR-016) — the
  patient never ends up seeing a stale, superseded partial answer presented as final, and no error is
  shown. If a reply doesn't get superseded, it continues streaming and completes normally.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST persist a visitor's chat (patient messages and assistant
  replies, in order) across page reloads and browser revisits, tied to an anonymous per-browser
  identifier, without requiring the visitor to sign in.
- **FR-002**: The system MUST display every previously exchanged message — patient and assistant
  alike, including any citations or abstention carried by an assistant message — in chronological
  order, labeled with its sender, when the patient returns to an existing chat. Display MUST
  NOT assume or enforce strict alternation between senders.
- **FR-003**: When generating a reply, the assistant MUST take into account the content of prior
  messages in the same chat, not only the current message, so that a follow-up question
  depending on earlier context is answered correctly in light of that context.
- **FR-004**: The system MUST provide a "Clear chat" action reachable from the chat
  interface. A confirmation should be asked "All messages in the chat will be deleted. Do you agree?" Buttons: "Clear", "Cancel".
- **FR-005**: When "Clear chat" is triggered, the system MUST immediately and permanently
  delete the current chat's stored messages, remove them from the visible chat, and MUST
  NOT use them as context for any subsequent message.
- **FR-006**: After a chat is cleared, the next patient message MUST start a new
  chat with no memory of the cleared one.
- **FR-007**: Each assistant reply within a multi-turn chat MUST continue to be grounded in
  FAQ content, and MUST continue to abstain rather than fabricate an answer when no relevant content
  is found, consistent with existing single-turn behavior.
- **FR-008**: The system MUST continue to reject an empty message or a message over the existing
  length limit, regardless of how many messages already exist in the chat.
- **FR-009**: A chat MUST be scoped to a single anonymous visitor identity (one continuous
  chat per browser/device); the system is not required to let a patient view or manage
  multiple distinct past chats in this phase. (This identity is expected to later become the
  owner of multiple managed Patients rather than a chat owner directly — see Future
  Direction.)
- **FR-010**: If the visitor's anonymous identifier is missing or unrecognized (e.g. cleared
  cookies/local storage, or a different browser/device), the system MUST start a new, empty
  chat rather than erroring.
- **FR-011**: The system MUST NOT automatically expire or delete a chat based on age or
  inactivity — a chat remains available indefinitely until the patient explicitly clears it
  (FR-005).
- **FR-012**: If generating the assistant's reply for a patient message fails (e.g. a streaming
  error), the system MUST still retain that patient message in the chat's history and MUST
  still make it available as context for future turns, but MUST NOT store a fabricated or partial
  assistant reply for the failed attempt.
- **FR-013**: The system's stored chat data MUST represent each message as an individual,
  independently ordered entry tagged with its sender, rather than pairing a patient message with a
  fixed assistant reply — so a sender can post multiple consecutive messages before another sender
  responds, and so a third sender (staff) can be added in a later phase without restructuring
  existing chat data. (Staff messages themselves are out of scope for this feature — see
  Future Direction.)
- **FR-014**: The system MUST allow a sender to post multiple consecutive messages without requiring
  a reply from another sender in between, and MUST preserve the order in which they were actually
  sent.
- **FR-015**: If a new message arrives for a chat while an earlier message's assistant reply
  is still being generated, the system MUST cancel that in-flight generation, MUST NOT store a reply
  for the cancelled attempt, and MUST generate a single fresh reply that takes into account every
  message sent so far, including the new one. The superseded patient message(s) remain in the
  chat's history unaffected.
- **FR-016**: If an assistant reply is cancelled per FR-015 after it had already begun visibly
  streaming to the patient, the system MUST remove that partial, superseded content from the
  patient's view — it MUST NOT remain visible as if it were a completed answer, and MUST NOT be
  presented as an error.
- **FR-017**: The anonymous per-browser identifier (FR-001/FR-009/FR-010) MUST be generated such
  that it cannot feasibly be guessed or enumerated — one visitor MUST NOT be able to reach another
  visitor's chat by guessing, incrementing, or otherwise deriving their identifier.

### Key Entities

- **Chat**: One continuous chat thread tied to an anonymous per-browser identifier,
  containing an ordered sequence of messages from any sender. Can be cleared, which permanently
  removes it and any future message starts a new one. (See Future Direction: this identifier is
  expected to later identify the webapp user who owns one or more Patients, with a Chat
  moving under a Patient rather than directly under this identity.)
- **Message**: A single message belonging to a specific Chat, authored by one sender —
  **patient** or **assistant** in this phase — and ordered within the chat by when it was
  sent. An assistant message that answers grounded carries its citations; one that abstains carries
  the abstention content instead. Supersedes both the "Chat Exchange" entity from the single-turn
  walking skeleton and a paired-turn framing: messages are not paired into fixed request/response
  units, and a sender MAY post several consecutive messages before another sender responds (FR-014).
  The assistant still considers all prior messages in the Chat when producing a new reply
  (see FR-003). This shape is deliberate: ROADMAP Phase 1d anticipates a third sender — **staff** —
  joining the same flat chat log once escalation lands, and the Message model is designed so
  that addition doesn't require restructuring existing chat data (see Future Direction).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A patient who asks a follow-up question referencing information given earlier in the
  same chat receives an answer that correctly reflects that earlier context, without
  needing to repeat it.
- **SC-002**: A patient who closes and reopens the chat in the same browser sees their full prior
  chat still displayed, with no messages lost.
- **SC-003**: A patient can clear their chat and immediately begin a new one, with no
  content from the previous chat visible or influencing the next answer.
- **SC-004**: Grounded-answer and abstention behavior shows no regression when used inside a
  multi-turn chat compared to the existing single-turn chat (0% fabrication rate on
  out-of-scope follow-up questions in manual testing).
- **SC-005**: A patient who sends several messages in a row before receiving a reply gets exactly
  one reply that reflects all of those messages together — never more than one reply for the same
  burst, and never a reply that only addresses the most recent message while ignoring an earlier one
  sent moments before it.
- **SC-006**: A patient never sees a partial, in-progress answer left on screen as if it were the
  final reply to a message that was superseded by a later one — a reply either completes normally
  and is shown in full, or is not shown at all.

## Assumptions

- This feature intentionally supersedes the Phase 0 scope decision in the existing single-turn chat
  spec that chat turns are independent with no cross-turn memory — multi-turn context is now
  in scope.
- Chat identity is anonymous and browser/device-scoped (e.g. via a cookie or local storage
  identifier), consistent with the project's current no-auth phase. This is a stand-in for real
  patient identity, not a form of authentication, and is expected to be revisited once patient auth
  exists.
- No fixed cap is placed on how many prior turns are considered as context in this phase; any limit
  arising from the underlying model's context window is an implementation detail for planning, not a
  functional requirement here.
- Only one active chat exists per visitor identity at a time; browsing a list of multiple
  past chats is out of scope for this phase.
- Existing per-message validation (empty/length limits) and the grounding/citation/abstention
  behavior of the single-turn chat carry forward unchanged and are not redefined by this spec.
- If a patient has the chat open in multiple tabs of the same browser, all tabs operate against the
  same underlying chat; no additional locking or conflict-merging behavior is required
  beyond normal request ordering.
- Real chats are not strictly alternating: a sender may post several messages in a row
  before another sender responds. This feature's data model and display reflect that (FR-013,
  FR-014). When a new patient message arrives while a reply to an earlier one is still generating,
  the earlier attempt is cancelled rather than left to complete or queued (FR-015) — so a burst of
  patient messages produces at most one assistant reply, generated once the burst has quieted down
  enough for a generation to finish, not one reply per message in the burst. No patient-side
  "typing/sending" affordance for a burst of messages is required this phase.
- Rate limiting or throttling on how frequently a sender can post messages (to bound generation cost
  or deter abuse) is out of scope for this phase, consistent with this spec's existing "no fixed cap"
  stance on context growth — cancel-and-restart (FR-015) already bounds concurrent generation work to
  at most one in-flight attempt per chat. Abuse/cost controls can be added later as a
  request-layer concern without requiring a redesign of the chat/message data model.
- Although a real chat may eventually involve a third participant (staff, per ROADMAP Phase
  1d), every chat built by this feature has exactly two possible senders — patient and
  assistant. The Message model's sender is treated as an open set rather than a hardcoded pair so a
  third sender can be added later (see Future Direction), but no staff-facing capability exists yet.

## Future Direction (Not Built Now — Informing This Design)

A later feature is expected to let the anonymous webapp user — the same anonymous, browser-scoped
identity this feature introduces — create and switch between multiple **Patients** (e.g. booking
for themselves and family members), where only the webapp user who created a Patient can see or
manage it. None of that is built by this feature; it's captured here so this feature's design
doesn't foreclose it.

- **Terminology note**: elsewhere in this spec, "patient" means the anonymous person operating the
  chat (the browser-scoped identity), matching the project's existing chat terminology. The future
  capability above introduces a *different*, more specific meaning: a **Patient** as a managed
  profile/record the webapp user creates, distinct from the webapp user's own identity. Once that
  capability exists, today's implicit single patient-per-chat should be read as "the default
  Patient," not the webapp user's identity itself — readers of this spec after that point should
  resolve "patient" from context accordingly.
- The anonymous per-browser identity this feature introduces (FR-001/FR-009/FR-010) is the same
  identity the future capability calls "the webapp user." Nothing about how that identity is
  established (no login, browser-scoped, no expiration) needs to change when Patient management is
  introduced — only a new ownership layer is expected to be added on top of it, not a replacement
  for it.
- This feature's one-chat-per-browser-identity behavior (FR-009) is a reasonable stand-in
  for "the single default Patient's chat" until multi-Patient support exists. The expected
  later shape is: one webapp user identity owning several Patients, each with its own chat(s)
  — i.e. a Patient is expected to slot in *between* the browser identity and its Chat(s),
  rather than the browser identity continuing to own a Chat directly.
- The future access-control rule ("only the webapp user who created a Patient can see/manage it") is
  out of scope here — no Patient entity exists yet — but it's noted because it confirms the
  browser-scoped identity this feature adopts is the right long-term authorization boundary to have
  in place already, rather than something that would need retrofitting later.
- **A third message sender — staff.** ROADMAP Phase 1d (booking, escalation, and real branching)
  introduces staff who take over a chat and post directly into the same thread the patient
  sees — not a separate, assistant-mediated channel — making a chat a flat log of messages
  from any of three senders (patient, assistant, staff), not two. None of that — staff accounts,
  escalation state, staff-authored messages, or the assistant going silent once escalated — is built
  by this feature; it's anticipated here only in that the Message entity's sender is modeled as an
  open set (patient/assistant now, staff later) rather than a hardcoded pair, so Phase 1d can add a
  staff sender without restructuring stored chat data (see FR-013).
