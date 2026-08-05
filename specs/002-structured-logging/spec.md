# Feature Specification: Structured Logging for App/AI Behavior

**Feature Branch**: `002-structured-logging`

**Created**: 2026-08-04

**Status**: Draft

**Input**: User description: "Lets add a struct logging to the project in order to be able debug
app/AI behaviour and ingest it later with Langfuse. The app isn't supposed to manage PII so we are
feel free to logg user messages and user names. Ask me questions to build a decent logging."

## Clarifications

### Session 2026-08-04

- Q: What needs to be captured for the logs to actually be useful for debugging AI behavior? → A:
  Full per-turn decision trace — every pipeline step (incoming message, retrieved content,
  groundedness verdict, final answer/abstention, errors) as structured events, enough to fully
  reconstruct why a given answer was produced without re-running anything.
- Q: Should log entries belonging to one turn share a common identifier so they can be grouped? →
  A: Yes, correlate by a shared identifier. (Since the system has no conversation/session concept
  yet — see Assumptions — this correlates entries within a single turn/request, not across
  multiple turns of a conversation.)
- Q: Which services does this feature cover right now? → A: Chat service only — it's where the
  agent/RAG decisioning lives; Scheduler is still a placeholder with no real logic to log.
- Addendum (same session): Langfuse ingestion is now expected to land much later than originally
  assumed. Until then, logs MUST be viewable directly in the terminal in an easy, human-readable
  form — an interim way to actually use the trace this feature captures, not just a machine-format
  dump waiting for a future consumer.
- Addendum (same session): Any logged text longer than 2,000 characters (e.g., a retrieved FAQ
  chunk, which can natively be up to 20,000 characters) MUST be truncated in the log to 2,000
  characters plus an ellipsis ("..."), to bound individual log entry size.
- Addendum (same session): The translation from a structured log entry to its human-readable
  terminal rendering MUST live in a single, centralized place, not be duplicated at every point in
  the code that produces a log entry — so that switching the primary output representation later
  (e.g., once Langfuse ingestion is built) is a one-place change, not a rewrite of every logging
  call site.
- Addendum (same session): Errors and critical events that fall outside a single chat turn's
  pipeline (e.g., a failed FAQ content management operation, or a failure reaching a dependency the
  service needs to run) MUST also be logged, not just the per-turn errors already covered by
  FR-005 — otherwise a class of failures would go unlogged entirely.
- Addendum (same session): Secrets, credentials, tokens, and passwords MUST NEVER be logged, in any
  log entry — including within error/critical-event details, where a naive dump of an exception
  could otherwise leak one. This is a distinct concern from FR-010 (which governs visitor-submitted
  content and user-identifying fields, not system credentials) and takes priority over it wherever
  the two could otherwise overlap.

### Session 2026-08-05

- Q: When a dependency failure (e.g., Qdrant unreachable) happens mid-turn, causing that turn's
  answer generation to fail, should it produce one combined log entry, or two related entries? →
  A: Two related entries — a turn-scoped error (FR-005), preserving that turn's own trace for User
  Story 1, and a separate critical event (FR-015), for service-health visibility — correlated
  (e.g., via the turn identifier where applicable) rather than merged into one entry.
- Q: Should critical events (FR-015) get the same terminal visual-distinction treatment as
  abstentions/errors (FR-012), or something more prominent? → A: Abstention is a routine outcome
  and should NOT be emphasized — grounded answers and abstentions are both normal turn results.
  Turn-scoped errors should be visually distinguishable. Critical events should be more
  distinguishable than turn-scoped errors, since they signal degraded service health affecting
  every visitor, not just one turn's outcome.
- Q (from checklist CHK001): Does FR-002's retrieval logging depend on whether the turn ends up
  grounded or abstains? → A: No — retrieval is logged the same way regardless of outcome.
- Q (from checklist CHK002): Is there a cap on how many retrieved candidates are logged per turn? →
  A: No cap — every retrieved candidate is logged, each with its relevance score, ordered
  highest-scoring first (e.g., if five candidates were retrieved, all five are logged with scores).
- Q (from checklist CHK003): Do FAQ management operations need a correlating identifier analogous to
  a chat turn's (FR-006)? → A: No — `entry_id` (already part of "what changed," FR-007) plus each
  entry's timestamp is sufficient; no additional correlation identifier is required.
- Q (from checklist CHK004): Is a dedicated step-ordering field needed for a turn's log entries, or
  does the timestamp already required per entry (FR-009) suffice? → A: Timestamp is enough — no
  separate sequence-number/step-order field is required.
- Q (from checklist CHK005): If a log entry is lost to a logging-mechanism failure (FR-008), must
  that loss itself be surfaced anywhere? → A: No — it's not a big deal if an event isn't logged;
  servicing the visitor's request takes priority over guaranteeing delivery of any single log entry,
  and a dropped entry is not itself reported or retried.
- Q (from checklist CHK006): What is "scores" in FR-004's "citations and scores"? → A: The RAG
  similarity score — the same relevance score already captured per retrieved candidate (FR-002).
- Q (from checklist CHK010): Does FR-001's "in full" wording read as contradicting FR-013's
  truncation rule? → A: Yes — remove "in full" from FR-001; it adds nothing FR-013 doesn't already
  govern, and only invites the apparent conflict.
- Q (from checklist CHK012, expanded beyond the original terminology question): Should retrieval's
  and FAQ content management's internal sub-steps (embedding, chunking) be logged too, not just
  their outcomes? → A: Yes. For a chat turn, the embedding sub-step of retrieval is now its own log
  entry. For FAQ content management, the chunking and embedding sub-steps are now logged too — and
  (reversing the CHK003 answer above) a FAQ operation now gets its own correlating identifier,
  analogous to a chat turn's (FR-006), so an operation's chunking/embedding entries can be tied
  together the same way a turn's steps are.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reconstruct why the assistant answered the way it did (Priority: P1)

A developer investigating a wrong, confusing, or abstained answer needs to find out, after the
fact, exactly what happened during that turn: what the visitor asked, what FAQ content was
retrieved, whether the groundedness check passed, and what final answer (with citations) or
abstention was returned — without having to reproduce the request live.

**Why this priority**: This is the entire reason for the feature. Without it, the only way to
debug AI behavior is trying to reproduce the exact same request live, which for an LLM-based
system is often not reliable since outputs aren't fully deterministic. Every other capability in
this spec exists to support this one.

**Independent Test**: Ask the chat endpoint a question, then inspect the emitted logs and confirm
every step of that turn (question, retrieval, groundedness verdict, final answer) is present and
attributable to that one turn.

**Acceptance Scenarios**:

1. **Given** a visitor asks a question that receives a grounded answer, **When** the turn
   completes, **Then** the logs contain the visitor's message, that the message was embedded for
   retrieval, every candidate the retrieval step returned (each with its relevance score, highest
   first — not just the ones ultimately cited), the groundedness verdict, and the final answer with
   its citations and their scores — all identifiable as belonging to that one turn.
2. **Given** a visitor asks a question with no relevant FAQ content, **When** the assistant
   abstains, **Then** the logs show the retrieval attempt, the groundedness verdict that triggered
   the abstention, and the abstention message returned — not just a blank "no answer" gap.
3. **Given** an unexpected error occurs partway through a turn (e.g., a failure calling the
   language model), **When** the error occurs, **Then** the logs capture the error together with
   which turn and which pipeline step it happened in.

---

### User Story 2 - Read a chat turn's trace straight from the terminal (Priority: P1)

A developer running the service needs to read a chat turn's captured trace directly in the
terminal where the service is running, in a form a person can scan and understand at a glance —
without needing any external log viewer, query language, or tool to make sense of it.

**Why this priority**: Langfuse ingestion (`docs/ROADMAP.md` Phase 2) is now expected to land much
later than originally planned. Until it does, the terminal is the *only* place these logs are ever
seen — a trace that's captured (User Story 1) but not readable is not actually usable for
debugging. This is a temporary/interim solution: it can be superseded once Langfuse ingestion
exists, but it's what makes the feature deliver value in the meantime.

**Independent Test**: Run the chat service locally, ask it a question, and confirm the terminal
output for that turn can be read and understood directly — what was asked, what was retrieved,
the groundedness verdict, and the final answer/abstention — without piping it through any parser
or external tool.

**Acceptance Scenarios**:

1. **Given** a chat turn completes, **When** its log entries are written, **Then** they appear in
   the terminal in a clearly readable form (readable prose/labels per step) rather than as a raw
   machine-oriented serialization a person would need to parse mentally.
2. **Given** a developer is watching the terminal while testing the chat endpoint, **When** a turn
   produces an error, **Then** that error is easy to spot in the terminal output without scrolling
   through unrelated detail to find it — while a turn that simply abstains reads as a routine
   result, not something visually flagged as a problem.
3. **Given** a developer is watching the terminal, **When** a critical event occurs (FR-015), **Then**
   it stands out more prominently than a turn-scoped error does, since it signals the service
   itself is degraded rather than one visitor's turn having an issue.

---

### User Story 3 - Tell concurrent visitors' turns apart (Priority: P2)

A developer investigating an issue needs to isolate the log entries for one specific request, even
while many other visitors' requests are being logged around the same time.

**Why this priority**: The chat endpoint serves many visitors concurrently; without a way to tell
one turn's entries apart from another's, logs from different requests interleave and become
unreadable at any real traffic volume. P2 because a single turn's trace (User Story 1) already
delivers debugging value in isolation, but this becomes essential once there's meaningful
concurrent traffic, and it's also a prerequisite for cleanly mapping onto Langfuse traces later.

**Independent Test**: Send two chat requests concurrently and confirm each request's log entries
can be filtered down to just that request's entries, in order, regardless of interleaving with the
other request's entries.

**Acceptance Scenarios**:

1. **Given** a chat turn produces multiple log entries across its pipeline steps, **When** those
   entries are emitted, **Then** every entry for that turn carries the same identifier.
2. **Given** two visitors send requests at the same time, **When** their turns' log entries
   interleave, **Then** each turn's identifier makes it possible to view only that turn's entries,
   in the order its steps occurred.

---

### User Story 4 - Catch errors and critical events outside a single chat turn (Priority: P2)

A developer/operator needs to know when something goes wrong that isn't scoped to one visitor's
chat turn — e.g., a FAQ content management operation fails partway through, or the service can't
reach a dependency it needs to run — rather than that failure going completely unlogged and only
surfacing later as "something's broken" with no trail to explain why.

**Why this priority**: Chat-turn-scoped errors already have a home under User Story 1 (FR-005).
This closes the remaining gap: failures that happen outside any single turn's lifecycle would
otherwise be invisible in the logs entirely. P2 because it's a completeness/reliability concern
alongside the turn-scoped debugging value, not the primary flow itself.

**Independent Test**: Trigger a failure that isn't part of a chat turn — e.g., make a FAQ content
operation fail, or simulate the service being unable to reach a required dependency — and confirm
it appears in the logs with enough detail to identify what failed and why.

**Acceptance Scenarios**:

1. **Given** a FAQ content management operation (create/update/delete) fails, **When** the failure
   occurs, **Then** the logs record that it failed and why, not just successful changes.
2. **Given** a critical event occurs that isn't tied to a specific chat turn (e.g., the service
   can't reach a dependency it needs to operate), **When** that event occurs, **Then** the logs
   capture it, so it isn't silently lost.
3. **Given** a FAQ entry is created or updated, **When** its content is chunked and each chunk
   embedded, **Then** the logs show those chunking and embedding sub-steps as distinct entries, all
   carrying that operation's own identifier (FR-021) — separate from that identifier a concurrent
   FAQ operation might be using at the same time.

---

### Edge Cases

- What happens if the logging mechanism itself fails (e.g., can't write output)? Logging failures
  must not prevent the visitor from receiving their answer — the chat response takes priority over
  the log record. The lost entry itself is not reported or retried; silently missing one log entry
  is an accepted tradeoff, not a condition that needs its own handling (FR-008).
- What happens for text fields that could exceed 2,000 characters, such as a retrieved FAQ chunk
  (natively up to 20,000 characters) or the assembled final answer? They are truncated in the log
  to 2,000 characters plus an ellipsis, regardless of the field's own length limit elsewhere in the
  system. The visitor's own message never actually needs truncation in practice, since it's already
  capped at 2,000 characters by existing input validation — but it's subject to the same rule if
  that cap ever changes.
- What happens with the streamed answer — is every streamed token logged individually? No: the
  final assembled answer is logged as a single record per turn, not one entry per token, so the
  trace stays readable and log volume stays proportional to traffic rather than answer length.
- What happens when FAQ content is created, updated, or deleted via the management API? These
  operations change what future answers can be grounded in, so they must also produce a basic
  structured log record (what changed, when) even though they aren't part of the per-turn AI
  decision trace — and if the operation fails instead of succeeding, that failure is logged too
  (User Story 4). A create/update operation additionally logs its chunking and embedding sub-steps
  (FR-022), all sharing that operation's own correlating identifier (FR-021).
- What happens when something fails outside of any single chat turn or FAQ operation — e.g., the
  service can't reach a dependency it needs at startup or during normal operation? That is logged
  as a critical event in its own right, so it isn't lost simply because it isn't attached to a
  visitor-facing request.
- What happens when an error's natural detail (e.g., an exception message or stack trace) would
  otherwise include a secret — for example, a database connection failure whose exception embeds
  the connection string, password included? The secret portion is still excluded from the log even
  though it's part of the error's natural detail; enough of the rest of the error is still logged
  to identify what failed (FR-016) without the secret itself ever appearing.
- What happens when a dependency failure occurs while a chat turn or FAQ operation is in progress
  (e.g., the vector store becomes unreachable mid-turn)? It is logged twice, deliberately: once as
  that turn's/operation's own error (FR-005/FR-007, so its individual trace stays complete) and
  once as a critical event (FR-015, so the broader service-health issue is visible on its own) —
  correlated rather than collapsed into a single record (FR-018).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST log, for every chat turn, the visitor's incoming message text.
- **FR-002**: The system MUST log the outcome of the retrieval step for every chat turn, regardless
  of whether the turn ends up grounded or abstains: every retrieved candidate — not just ones
  ultimately cited — together with its relevance score, ordered highest-scoring first. No cap is
  placed on how many retrieved candidates are logged; however many were retrieved are logged.
- **FR-003**: The system MUST log the groundedness verdict for every chat turn (whether the
  retrieved content was judged sufficient to answer, or the turn should abstain).
- **FR-004**: The system MUST log the final result of every chat turn as a single record: either the
  assembled answer text with its citations and their RAG similarity scores (the same relevance score
  captured per retrieved candidate, FR-002), or the abstention message — not the individual streamed
  tokens.
- **FR-005**: The system MUST log unhandled errors that occur during a chat turn, including which
  pipeline step (embedding, retrieval, groundedness check, or answer generation) the error occurred
  in.
- **FR-006**: Every log entry produced during a single chat turn MUST carry a shared identifier
  unique to that turn, so entries belonging to one request can be distinguished from entries
  belonging to any other concurrent request.
- **FR-007**: The system MUST log FAQ content management operations (create, update, delete) with
  what changed and when, including when such an operation fails instead of succeeding, together
  with why it failed.
- **FR-008**: The system MUST NOT block or delay the visitor-facing chat response on account of a
  logging failure — logging is best-effort relative to serving the request. A log entry lost to such
  a failure is not itself reported, retried, or otherwise surfaced — silent loss is acceptable, since
  servicing the visitor's request takes priority over guaranteeing delivery of any single log entry.
- **FR-009**: Log content MUST be structured (organized into consistent, identifiable fields per
  entry — e.g., turn or operation identifier, step/event type, timestamp, and step-specific detail)
  rather than free-form prose, so entries can be filtered, grouped, and machine-parsed without
  custom text parsing.
- **FR-010**: The system MUST NOT apply any redaction, masking, or exclusion to visitor message
  content or user-identifying fields when logging — this project does not manage PII, so full
  message text (and user names, once user identity exists — see Assumptions) may be logged as-is.
  This does not extend to secrets or credentials (see FR-017), which are a different category of
  data and are never exempted from exclusion.
- **FR-011**: The system MUST display log entries in the terminal in an easy-to-read, human-legible
  form (readable step-by-step as prose/labeled fields) rather than as a raw machine-oriented
  serialization the reader has to parse themselves. This is the default/primary way logs are
  consumed today, as an interim solution while Langfuse ingestion (see Assumptions) remains
  unbuilt — it does not conflict with FR-009's structuring requirement, since the same structured
  entry can be rendered in a human-readable way when printed to the terminal.
- **FR-012**: The system MUST make turn-scoped errors easy to visually distinguish from routine
  turn outcomes in the terminal output, so a developer watching the terminal can spot them without
  reading every entry in detail. Both a grounded answer and an abstention count as routine turn
  outcomes for this purpose — abstention is expected, normal behavior (see Assumptions), not a
  problem to flag.
- **FR-013**: Any text value included in a log entry (e.g., the visitor's message, retrieved
  content, the final answer text) that exceeds 2,000 characters MUST be truncated to 2,000
  characters followed by an ellipsis ("...") before being logged, regardless of that field's own
  length limit elsewhere in the system (e.g., FAQ content may natively be up to 20,000 characters).
- **FR-014**: The mapping from a structured log entry to its human-readable terminal rendering
  (FR-011) MUST be implemented in a single, centralized place, not duplicated at each point in the
  code that produces a log entry — so that switching the primary output representation later (e.g.,
  once Langfuse ingestion is built) requires changing only that one place.
- **FR-015**: The system MUST log critical events that occur outside of any single chat turn or FAQ
  management operation — in particular, failures reaching a dependency the service needs to run
  (e.g., the vector store or database becoming unreachable) — so that such failures are never
  silently lost simply because they aren't attached to a specific visitor-facing request.
- **FR-016**: Every logged error and critical event (whether turn-scoped per FR-005, a failed FAQ
  operation per FR-007, or a non-turn-scoped critical event per FR-015) MUST include enough detail
  to identify what failed and why, without requiring the developer to reproduce the failure to find
  out.
- **FR-017**: The system MUST NOT log secrets, credentials, tokens, or passwords (e.g., the
  language model API key, database connection credentials, or any future authentication tokens)
  under any circumstances — including when logging errors or critical events (FR-005, FR-007,
  FR-015) about the systems those secrets protect, where the natural detail of the failure (e.g.,
  an exception message) might otherwise include one.
- **FR-018**: When a single underlying failure affects both a specific chat turn or FAQ operation
  and represents a broader critical event (e.g., a dependency becoming unreachable mid-turn), the
  system MUST log both: the turn-scoped error (FR-005) or failed FAQ operation record (FR-007) for
  that request's own trace, and a separate critical event (FR-015) for service-health visibility.
  The two MUST be correlated (e.g., via the turn identifier where applicable) rather than merged
  into a single entry, so neither the per-turn trace (User Story 1) nor service-health visibility
  (User Story 4) loses information to the other.
- **FR-019**: Critical events (FR-015) MUST be visually more prominent in the terminal output than
  turn-scoped errors (FR-012) — a developer watching the terminal should be able to tell a
  service-wide critical event apart from an individual turn's error at a glance, not just by
  reading each entry's label.
- **FR-020**: The system MUST log the embedding sub-step of retrieval for every chat turn — that the
  visitor's message was embedded for retrieval — as its own log entry, distinct from the retrieval
  outcome (FR-002), carrying that turn's identifier (FR-006).
- **FR-021**: Every FAQ content management operation (create, update, delete) MUST carry a shared
  identifier unique to that operation, analogous to a chat turn's identifier (FR-006), so every log
  entry belonging to one operation — including its chunking and embedding sub-steps (FR-022) — can
  be correlated together and distinguished from any other concurrent operation.
- **FR-022**: The system MUST log the chunking and embedding sub-steps of a FAQ content management
  create or update operation — how the content was split into chunks, and that each chunk was
  embedded (don't make it too verbose, number of chanks and report that embeded successful is enough) — as distinct log entries from that operation's final create/update/delete record
  (FR-007).

### Key Entities

- **Log Entry**: A single structured record representing one step of a chat turn (e.g., "message
  received," "message embedded," "content retrieved," "groundedness verdict," "answer produced,"
  "error"), a step of a FAQ content management operation (e.g., "content chunked," "chunk
  embedded," "entry created/updated/deleted," including failures), or a critical event not tied to
  either (e.g., a required dependency becoming unreachable). Carries a timestamp, the step/event
  type, and details specific to that step or event — plus a shared turn identifier when it belongs
  to a chat turn (FR-006), or a shared operation identifier when it belongs to a FAQ management
  operation (FR-021). Not part of the application's persisted domain data (not stored in Postgres
  or Qdrant) — it's operational/diagnostic output.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any completed chat turn, a developer can determine — from the logs alone, without
  reproducing the request — what was asked, what was retrieved, whether it was judged grounded, and
  what final answer or abstention was returned.
- **SC-002**: Under concurrent traffic from multiple visitors, 100% of log entries can be correctly
  grouped back to the single turn that produced them.
- **SC-003**: When a chat turn fails partway through, the resulting log entries identify which
  pipeline step failed, without the developer needing to add temporary debug output to find out.
- **SC-004**: Visitors experience no perceptible slowdown in the streamed chat response as a result
  of logging being added.
- **SC-005**: FAQ content changes (create/update/delete) are each traceable to a log record showing
  what changed and when, for 100% of such operations.
- **SC-006**: A developer can read and understand a chat turn's full trace directly from the
  terminal output alone — no external log viewer, query tool, or manual parsing required.
- **SC-007**: No single log entry ever displays more than 2,000 characters of any one text field —
  longer values consistently appear truncated with a trailing ellipsis instead of in full.
- **SC-008**: Changing how logs are presented (e.g., moving from terminal display to a future
  ingestion-ready format) requires modifying only one part of the system, not every individual
  place that produces a log entry.
- **SC-009**: 100% of failed FAQ content management operations and critical events (e.g., a
  required dependency becoming unreachable) produce a log record identifying what failed and why —
  none go unlogged, and when a single failure spans both a specific turn/operation and a broader
  critical event, both records appear rather than one silently replacing the other.
- **SC-010**: A review of log output finds zero occurrences of a secret, credential, token, or
  password value, across normal operation and every error/critical-event path.
- **SC-011**: A developer watching the terminal can tell apart, at a glance and without reading
  full entry text, three distinct tiers: routine turn outcomes (grounded answers and abstentions
  alike), turn-scoped errors, and critical events — with critical events the most visually
  prominent of the three.
- **SC-012**: For any completed chat turn, a developer can determine from the logs that the
  visitor's message was embedded as part of retrieval, not just the retrieval outcome that followed
  it.
- **SC-013**: For any completed FAQ content management create/update operation, a developer can
  determine from the logs alone — correlated to that operation's own identifier — how the content
  was chunked and that each chunk was embedded (don't make it too verbose, number of chanks and report that embeded successful is enough).

## Assumptions

- No PII-handling constraints apply: per explicit product decision, this system does not manage
  PII, so visitor message content may be logged in full, and no masking/redaction is required.
- The system currently has no authentication or persisted conversation/session concept (per
  `docs/ROADMAP.md`, auth is future work) — there is no "user name" field to log yet, and each chat
  request is an independent, stateless turn rather than part of a tracked multi-turn conversation.
  This feature therefore correlates log entries at the turn/request level; correlating entries
  across multiple turns of the same conversation is deferred until conversation/session tracking
  exists.
- This feature produces the structured log output itself; actually shipping/ingesting that output
  into Langfuse is separate, later work (`docs/ROADMAP.md` Phase 2 — Evaluation & observability),
  now expected considerably further out than originally planned. This feature's success is that
  the logs are already shaped in a way that later ingestion doesn't require reworking what's
  captured, and that a human-readable terminal view (FR-011) covers the gap until then. The
  terminal view is explicitly a temporary/interim solution — it's not required to survive
  unchanged once Langfuse ingestion is built; a different or additional export shape MAY be added
  at that point.
- Scheduler is excluded from this feature's scope because it is still a placeholder service with no
  real logic to log; the same logging approach should extend to it once it has real behavior worth
  tracing.
- Log retention and storage/rotation policy follow whatever the hosting/deployment environment
  already provides by default — no bespoke retention requirement is introduced by this feature.
- The 2,000-character truncation bound (FR-013) is a log-entry-size control, independent of any
  field's own validation limit elsewhere in the system — e.g., FAQ content can be authored up to
  20,000 characters (see `specs/001-grounded-faq-chat`), but only 2,000 characters of it ever
  appear within a single log entry.
- "Critical event" (FR-015) means a failure in the service's own operation or its ability to reach
  a dependency it needs — not a normal per-turn business outcome like an abstention (which is
  expected behavior, not a failure). The concrete list of what counts as a dependency the service
  needs is bounded by what the chat service actually depends on today (Postgres, Qdrant, the
  language model API) rather than anything hypothetical.
- "Secrets, credentials, tokens, and passwords" (FR-017) is a distinct category from the visitor
  content and user-identifying fields FR-010 permits logging in full — it covers the system's own
  credentials for its dependencies (e.g., the Anthropic API key, the Postgres/Qdrant connection
  credentials), not anything a visitor submits. Today's concrete scope is bounded by what the chat
  service actually holds — its configured API key(s) and database/vector-store connection
  credentials — rather than a hypothetical broader secret inventory.
- FAQ management operations (FR-007) DO carry a dedicated correlation identifier, analogous to a
  chat turn's (FR-006) — see FR-021. (Supersedes an earlier answer, checklist CHK003, that
  `entry_id`/timestamp alone would be sufficient; revisited and reversed once chunking/embedding
  sub-step logging (FR-022) was added, per checklist CHK012 — with more than one log entry per
  operation, correlating them the same way a turn's steps are correlated was judged necessary.)
- A chat turn's log entries are ordered by their timestamp alone (already required per entry, FR-009)
  — no separate sequence-number or step-order field is required to reconstruct pipeline-step order
  (checklist CHK004). The same applies to a FAQ operation's entries.
- Retrieval's embedding sub-step (FR-020) and FAQ content management's chunking/embedding sub-steps
  (FR-022) are logged as their own entries because they are meaningful, independently-failing steps
  in the pipeline — not because every internal function call warrants its own log entry; the bar
  remains "a step whose outcome or failure a developer would need to see to debug the trace."
