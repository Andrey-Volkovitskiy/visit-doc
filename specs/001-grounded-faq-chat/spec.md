# Feature Specification: Grounded FAQ Chat (Phase 0 Walking Skeleton)

**Feature Branch**: `001-grounded-faq-chat`

**Created**: 2026-07-29

**Status**: Draft

**Input**: User description: "I'm going to make a spec or several specs for the phase 0 described in @docs/ROADMAP.md. My goal is to create the minimal system that allows a user (auth will be added later) to ask a question and receive a grounded answer from the agent using an FAQ knowledge base. Data can be added to FAQ via API (later will be replaced with requests from the staff web-app). Let's define which systems should be developed and what the requirements are."

## Clarifications

### Session 2026-07-29

- Q: Should visitor chat messages have an enforced length limit (and should empty messages be rejected)? → A: Reject empty messages and messages over 2,000 characters, with a clear validation error.
- Q: If a visitor asks in a language different from the FAQ content, should the system attempt to answer anyway, or treat it like "no relevant content found"? → A: No special handling — retrieval naturally finds nothing relevant, so the system abstains via the existing FR-005 path.
- Q: What's the maximum acceptable time from question submitted to first streamed token appearing? → A: No specific latency target for this phase — only that the reply streams incrementally (FR-004), with no fixed time threshold to meet or test against.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask a question and get a grounded answer (Priority: P1)

A visitor opens the chat and asks a plain-language question about clinic policy or logistics (e.g.
"what should I bring to a first cardiology visit?"). The assistant looks up relevant content in the
FAQ knowledge base and replies with an answer grounded in that content, showing the specific source
passage(s) the answer came from.

**Why this priority**: This is the entire walking skeleton — without it there is no end-to-end loop
to demonstrate. Every other capability in this spec exists to support this one.

**Independent Test**: Seed the knowledge base with one FAQ document, ask a question clearly answered
by it through the chat interface, and confirm a grounded answer with a citation to that document is
returned.

**Acceptance Scenarios**:

1. **Given** the FAQ knowledge base contains an entry answering "what are your visiting hours,"
   **When** a visitor asks "when can I visit," **Then** the assistant replies with an answer derived
   from that entry and identifies the specific passage it drew from as its source.
2. **Given** the assistant is generating a reply, **When** the reply is produced, **Then** the text
   is delivered to the visitor incrementally (streamed) rather than only appearing once fully
   complete.
3. **Given** the FAQ knowledge base contains no document relevant to the visitor's question, **When**
   the visitor asks that question, **Then** the assistant states it does not have a confident answer
   instead of fabricating one, and does not present an unsupported answer as fact.

---

### User Story 2 - Add, update, and delete FAQ content via API (Priority: P2)

A staff member (today, calling the API directly; later, via the staff web-app) adds a new FAQ
entry, edits an existing one, or removes one that's no longer accurate. Added/edited content
becomes available for the assistant to retrieve and cite the next time a relevant question is
asked; removed content stops being retrievable immediately.

**Why this priority**: User Story 1 has no content to ground answers in without this. It is P2 rather
than P1 because it is a supporting/authoring capability, not the user-facing value the project
demonstrates — but the system is not usable end-to-end without it.

**Independent Test**: Call the FAQ content API to create a new entry, verify it's retrievable by
calling a lookup/list endpoint, then delete it and verify it's no longer retrievable there —
independent of the chat flow.

**Acceptance Scenarios**:

1. **Given** a valid new FAQ entry (topic/question and answer content), **When** it is submitted to
   the API, **Then** the system stores it and confirms success.
2. **Given** an existing FAQ entry, **When** it is updated via the API with new content, **Then**
   subsequent retrievals use the updated content, not the old version.
3. **Given** an FAQ entry was just created or updated, **When** a visitor asks a question that entry
   answers, **Then** the assistant's next answer reflects that entry's content.
4. **Given** a malformed or incomplete FAQ entry submission (e.g. missing required content), **When**
   it is submitted to the API, **Then** the system rejects it with a clear error and does not store
   partial data.
5. **Given** an FAQ entry submission whose content exceeds 20,000 characters, **When** it is
   submitted to the API, **Then** the system rejects it with a clear error and does not store it.
6. **Given** an existing FAQ entry, **When** it is deleted via the API, **Then** it is no longer
   returned by lookup/list operations and is no longer retrieved to ground future answers.
7. **Given** an FAQ entry ID that does not exist, **When** a delete is requested for it, **Then** the
   system responds with a clear "not found" error rather than silently succeeding.

---

### Edge Cases

- What happens when a visitor asks a question and the FAQ knowledge base is completely empty (no
  entries have been added yet)? The assistant must abstain rather than error or fabricate.
- What happens when a visitor's question is ambiguous or touches multiple topics — does the system
  still ground itself only in what it retrieves rather than guessing?
- What happens when a visitor sends an empty message or a message over 2,000 characters? The system
  rejects the message with a clear validation error before any retrieval or generation occurs.
- What happens when a visitor sends a message in a language the FAQ content isn't written in? No
  special handling is required — retrieval finds nothing sufficiently relevant, and the system
  abstains via the same path as any other no-match question.
- What happens when two FAQ entries contain overlapping or contradictory content? The specification
  does not require conflict detection in this phase, but the answer must still cite the specific
  source(s) it drew from.
- What happens when the same FAQ entry is submitted twice (duplicate content)? The system does not
  need to de-duplicate in this phase, but must not error.
- What happens when a submitted FAQ entry's content exceeds the 20,000-character limit? The
  submission is rejected outright, not truncated.
- What happens when a delete is requested for an FAQ entry ID that doesn't exist? The system returns
  a "not found" error rather than treating it as a no-op success.
- What happens to a deleted entry's indexed content? It MUST stop being retrievable — deletion is
  not just removing the entry from listings while leaving stale content groundable.
- What happens when a submitted FAQ entry's content is only whitespace and/or dashes (e.g. `"---"`
  used as a placeholder), or only the bare `Question:`/`Answer:` labels with no actual text after
  them? Rejected at submission the same as genuinely empty content (FR-009). If such near-empty
  text is ever produced as a retrievable unit from otherwise valid content (e.g. a divider line
  landing alone in a retrievable unit), it MUST NOT be used to ground an answer (FR-017).
- What happens if a visitor asks a question unrelated to the clinic entirely (e.g. "what's the
  weather")? Treated the same as "no relevant content found" — the assistant abstains.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a chat interface where a visitor can submit a free-text
  question without signing in or authenticating.
- **FR-001a**: The system MUST reject an empty visitor message and a visitor message over 2,000
  characters, with a clear validation error, before any retrieval or generation is attempted.
- **FR-002**: The system MUST generate an answer to the visitor's question that is grounded in
  content retrieved from the FAQ knowledge base, rather than the model's unaided general knowledge.
- **FR-003**: Every grounded answer MUST identify the specific FAQ entry content (the retrieved
  passage(s)) it was derived from — not merely name the entry, but show the exact source text.
- **FR-004**: The assistant's reply MUST be delivered to the visitor incrementally as it is
  generated (streamed), not only as a single completed block.
- **FR-005**: When no FAQ content sufficiently relevant to the question can be found, the system MUST
  respond with an explicit statement that it does not have a confident answer, and MUST NOT present a
  fabricated or unsupported answer as fact.
- **FR-006**: The system MUST provide an API operation to create a new FAQ entry.
- **FR-007**: The system MUST provide an API operation to update the content of an existing FAQ
  entry.
- **FR-008**: The system MUST provide an API operation to retrieve/list existing FAQ entries (so
  submitted content can be verified and this spec's independent test is possible).
- **FR-009**: The system MUST reject FAQ entry submissions whose content is missing or contains no
  meaningful text, with a clear error, and MUST NOT persist partial/invalid entries. "No meaningful
  text" includes: content consisting solely of whitespace and/or dash characters (e.g. `"   "`,
  `"\n\n"`, `"---"`); and content that reduces to nothing but the optional `Question:`/`Answer:`
  labels from FR-014 with no actual question or answer text following them (e.g.
  `"Question:\nAnswer:"`).
- **FR-010**: Newly created or updated FAQ content MUST become available for retrieval by the
  assistant without requiring a manual, undocumented step outside the API itself.
- **FR-011**: The system MUST NOT require or check visitor identity/authentication in this phase.
- **FR-012**: The FAQ content API MUST NOT require caller authentication in this phase. No auth
  system exists yet, and no interim protection (e.g. shared secret) is required either — this is an
  explicit, temporary scope decision matching the walking-skeleton phase, not a placeholder for a
  lightweight guard.
- **FR-013**: The chat interaction MUST be single-turn: each visitor question is answered
  independently, with no memory of prior messages retained across questions in this phase.
- **FR-014**: An FAQ entry submitted via the API MUST be a free-form policy document (e.g. "Working
  hours: Mon–Fri 8am–5pm"), not a rigid structured question/answer pair. Entry content MAY itself
  contain an embedded question/answer pattern as plain text (e.g. "Question: What is your
  address?\nAnswer: 15 Smith St, London") when that reads more naturally, but the API MUST NOT
  require or enforce that structure. Retrieval and citation operate over this document content
  (and, per FR-002/FR-003, may retrieve/cite a specific passage within a longer document rather than
  always the whole thing).
- **FR-015**: The system MUST reject an FAQ entry submission (create or update) whose content exceeds
  20,000 characters, with a clear error, rather than truncating or persisting it.
- **FR-016**: The system MUST provide an API operation to delete an existing FAQ entry. A deleted
  entry's content MUST stop being retrievable to ground future answers, with no separate manual
  re-indexing step required. Deleting an ID that does not exist MUST return a clear "not found"
  error.
- **FR-017**: If a stored FAQ entry produces a retrievable unit of content that itself has no
  meaningful text by the same standard as FR-009 (whitespace/dashes only, or
  bare `Question:`/`Answer:` labels with nothing after them), that unit MUST NOT be used to ground
  an answer, as a defense-in-depth backstop to FR-009's submission-time rejection. Because FR-009
  already guarantees every stored entry has meaningful text somewhere in its content, and no
  content is discarded when producing retrievable units from it, at least one such unit always
  remains usable — this backstop only ever discards a subset of an entry's units, never all of
  them.

### Key Entities

- **FAQ Entry**: A unit of clinic knowledge that can be retrieved to ground an answer. Represents a
  free-form policy document (up to 20,000 characters), plus a stable identifier so it can be cited
  back to the visitor, updated, or deleted later. Citations reference the specific retrieved passage
  itself (see FR-003) rather than a separately authored title/label — the entry has no title field.
- **Chat Exchange**: A single, self-contained visitor question and the assistant's corresponding
  grounded (or abstaining) reply, including which FAQ Entry/Entries, if any, were cited. Independent
  of any other exchange — no cross-exchange memory in this phase.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A visitor asking a question that is clearly covered by an existing FAQ entry receives
  a grounded, correctly cited answer.
- **SC-002**: A visitor asking a question with no relevant FAQ content receives an explicit
  "I don't know" response instead of a fabricated answer, every time (0% fabrication rate on
  out-of-scope questions in manual testing).
- **SC-003**: A newly added or edited FAQ entry is reflected in the assistant's answers without any
  step beyond calling the content API.
- **SC-004**: A visitor sees the assistant's reply begin appearing incrementally (partial text
  arriving before the full answer is ready), rather than only seeing the complete answer appear all
  at once. No specific response-time target is defined for this phase.
- **SC-005**: The full loop — add an FAQ entry via API, ask the matching question in chat, receive a
  cited grounded answer — can be demonstrated end to end by a person with no prior knowledge of the
  system's internals.
- **SC-006**: A deleted FAQ entry is immediately absent from FAQ listings and no longer grounds any
  assistant answer, with no step beyond the delete call itself.

## Assumptions

- Authentication/authorization for both the visitor-facing chat and the FAQ content API is out of
  scope for this phase and is deferred to a later phase, per the ROADMAP; the FAQ content API is
  intentionally left fully open in this phase (see FR-012), a scope decision, not an oversight.
- There is no staff web-app yet; "adding data via API" means direct calls to the FAQ content API
  (e.g. via an API client or script), not a UI.
- Deletion is a hard delete (row removed, indexed chunks removed from the vector store) — this phase
  has no soft-delete/undo requirement (see FR-016).
- Escalation to a human when the assistant abstains (per the ROADMAP's `escalate_to_staff`
  capability) is a Phase 1 concern; in this phase, abstention is limited to telling the visitor the
  assistant doesn't know, without a structured handoff to staff.
- Chat is single-turn in this phase (see FR-013); no conversation history is persisted or referenced
  across separate questions.
- The FAQ knowledge base starts empty; there is no bulk-import requirement for this phase beyond the
  create/update API.
- 20,000 characters is an assumed reasonable upper bound for a single policy document in this phase
  (comfortably covers realistic clinic policy pages); it is not derived from a specific technical
  constraint and can be revisited later.
