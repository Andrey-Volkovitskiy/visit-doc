# CLAUDE.md — services/chat

Code style guide for this service:

@../../../docs/python-style-guide.md

Testing convention for this service's `tests/` directory:

@../../../docs/testing-strategy.md

## Key design decisions to preserve

Moved here from the root `.claude/CLAUDE.md`, which keeps the cross-cutting principles.

- Intent classification uses **structured output**, not free-text parsing, and a cheap/fast model —
  reserve the stronger model for generation. The small-talk reply is the one patient-facing text
  written on the cheap model (009 FR-011: it is given nothing factual to state), under its own
  `SMALL_TALK_MODEL` rather than `CLASSIFICATION_MODEL`, so changing the classifier - which the
  golden set measures - does not also change how the assistant chats. Since 009 the label set says what a message *is*
  before anything decides who handles it: `faq_question`, `booking`, `small_talk` (asks for nothing
  that can be acted on), `urgent_condition`, `distress`, `booking_for_another`, `call_staff` (an
  explicit request for a human, and nothing else), `not_authorized` (a request the assistant is
  not authorized to serve). `not_authorized` no longer falls through to the FAQ path, and it is
  named for what it means rather than for the classifier being unsure.
- **The `booking`/`faq_question` boundary is decided from the booking side, because only that side
  is enumerable.** The live records answer a closed set of five requests — the roster and its
  specialties, a practitioner's free slots, making an appointment, changing or cancelling one, and
  this patient's own appointments — while the FAQ corpus is open and gains entries at runtime, so
  no list of *its* subjects stays complete. The prompt therefore states the five and makes
  `faq_question` the residual: not one of the five means `faq_question`, whatever the subject.
  Two near-misses are named explicitly rather than left to keyword overlap, because both would
  otherwise route to records that hold no answer — a question about what a visit *requires or
  costs* (a referral, what to bring, the price), and a question about the *rules governing* one of
  the five ("what is your cancellation policy?" against "cancel my Friday appointment"). Three of
  the five are acts on records; the terms those acts are subject to are in the clinic's documents.
  `test_classify_intent.py` compares the prompt's list against `SCHEDULING_TOOLS` for **equality**,
  so a tool added to the booking node fails the suite rather than silently making "exactly five"
  untrue.
- **The membership test is "where is the answer kept", never "does the message reach for an
  appointment"** — added after measuring, not reasoned in advance. The first run of the
  `single-booking` family (2026-09-20) scored 12/14 on intent: "do you have a dentist?" and "what
  does <practitioner> specialise in?" both went to `faq_question`, searched a corpus that names no
  practitioner, abstained, and paged a person under `corpus_could_not_answer` — a cause that says
  "write an FAQ entry" about live records data no entry should ever assert. The run isolated the
  discriminator the model had actually adopted: "do you have a cardiologist **I could see**?" was
  `booking` while "do you have a dentist?" was not, so four words of appointment-seeking flipped it,
  and every one of the 12 passes named a time, a slot or an existing appointment. Enumerating the
  five had quietly invited surface matching, with the residual catching whatever did not look like
  an appointment. The prompt now says outright that the roster item asks for no appointment and is
  still `booking`, and that wanting to be seen is not what makes a message `booking` — it is one of
  the five things that happen to be.
- Capabilities reach the agent through an **in-process tool registry, not MCP** (005):
  `ToolRegistry` in `agent/tools/registry.py` holds `(name, description, JSON schema, handler)`
  records rendered straight into the Messages API's `tools=` — `list_practitioners`,
  `check_availability`, `book_appointment`, `list_my_appointments`, `reschedule_appointment`,
  `cancel_appointment`, `escalate_to_staff`. The agent knows only names and schemas, so a handler
  can change transport without touching agent code. FAQ retrieval is not a tool: it is the
  `answer_faq` graph node. MCP is a transport for a consumer in *another* process, and nothing
  consumes one yet — `docs/ROADMAP.md` defers it until a second consumer justifies it. Putting an
  MCP server and client between the agent and handlers in one process is the rejected option
  (`specs/005-scheduling-and-booking/research.md` #1), not a pending upgrade.
- RAG must include defensible chunking (since 018: one chunk per heading section, prefixed with
  its heading path; headless entries fixed-size as before), a reranking step, citations to source
  documents — derived structurally from what was actually retrieved and placed in context, never
  self-reported by the LLM (avoids hallucinated citations) — and an explicit **abstention path**.
  Since 008 the "groundedness check" is **two gates before generation**, not a boolean after it:
  a per-chunk similarity floor, then a cross-encoder rerank floor, either of which abstains without
  spending a generation call. Since 018 the similarity floor defaults to -1.0, so the first gate
  is its cap alone and the rerank floor is the one that decides an abstention: measured on the
  extended corpus, a 0.25 floor dropped chunks dense search had ranked first. The floor that
  remains is `UNRERANKED_SIMILARITY_FLOOR`, applied only when the reranker is unavailable, so a
  fallback never answers from a shortlist nothing has judged
  (`specs/018-hybrid-retrieval/evaluation/`). `rag/groundedness.py` and the `grounded` flag are
  gone; a turn now carries a five-value `FaqVerdict` naming which gate stopped it, because "an
  answered turn is grounded" made `true` uninformative while `false` covered three situations
  needing three different fixes.
  Per-turn *post-generation* groundedness verification is deliberately not done — `docs/ROADMAP.md`
  assigns answer groundedness to Phase 2's offline eval harness.
- **Postgres decides what Qdrant may answer from.** For any entity with both a Postgres row and a
  derived Qdrant index (e.g. `FaqEntry`/`FaqChunk`), the row is the sole authority on which indexed
  content is live, and retrieval carries that as a predicate on the search itself. Points the row
  does not vouch for — superseded by a later write, orphaned by a failed one, left behind by a
  delete — must be unreachable: never retrieved, never cited, never counted toward groundedness.
  Removing them is housekeeping that may fail, retry, or lag; it must never be what makes them
  unanswerable. Leaked points are an accepted cost, an answer drawn from one is not.
  Concretely, since 007: a save is **additive**. It writes its chunks under a new revision, deletes
  nothing, and publishes with one local commit whose `WHERE` carries the revision it expects to
  supersede — so a failure at any step leaves the entry answering the text it was answering a
  moment ago, and there is no compensating write to half-succeed. The delete-then-upsert ordering
  this bullet used to prescribe is superseded by that, and a delete now removes the **row first**,
  which is what makes the entry unanswerable at that instant.
- **A new session is created holding the starter corpus.** `chat.rag.default_corpus` declares it and
  `chat.api.provisioning.seed_default_corpus` plants it as part of session creation, superseding
  spec 007's FR-039b ("a new session's corpus MUST start empty"). It is planted the way a save is —
  chunks written first under revisions nothing names live, one commit publishing all of them — so a
  failure plants none of it, and it never fails chat creation: the session simply starts empty, and
  that is logged rather than raised. Every other property of a session's corpus is unchanged, and
  a seeded entry is an ordinary entry the session may edit or delete.
- **The request, not the message, is the unit of work** (010). The classifier returns an ordered list
  of `{intent, text}` segments rather than a list of labels — `IntentClassificationResult.intents`
  survives as a *derived* property, which is what let every routing rule keep reading the value it
  read before. Each specialist is handed only its own segments and substitutes them into the trailing
  conversation entry via `history.replace_trailing_entry`, so isolation is structural: the other
  half's clause is not in the prompt to be answered. The FAQ answerer is the exception: it is shown
  **no conversation at all** — one `user` message: the clinic information, the request's `query`
  labelled as what the question refers to (only when it differs from `text`), then `text` as the
  question. The `query` alone is not enough: it is a search wording and can drop a condition —
  G-k-15's "…for the same visit, splitting the cost between them?" restated as "Do you accept
  UnitedHealthcare and Blue Cross Blue Shield?", which an answerer given only that answered.
  Shown the conversation, it let it decide the answer: with an earlier question in view that had
  gone unanswered, it declined questions the entry in front of it answered — 5 in 5 on replay of a
  live turn, still 5 in 5 with the clinic's replies removed and only the patient's earlier messages
  kept, and 3 in 3 answered with no conversation; an instruction to disregard earlier answers changed
  nothing. The classifier still reads the whole window (silent window included) and writes `query`
  to stand on its own, so that is where a follow-up is resolved. Golden case `G-r-01` holds that
  turn. A segment carries two wordings: `query`, the classifier's standalone restatement, which is
  what retrieval searches for; and `text`, what the specialist answers and what a stored outcome
  records. They are the same restatement when the message carried several requests, but a message
  the classifier found **one** request in is answered in the patient's own words
  (`_in_patient_wording` in `agent/graph.py`) — with no other clause to keep out, a restatement can
  only lose something - the "yes" in "yes, please go ahead", which is what tells the booking loop
  that a change it described is now chosen. The retrieval pipeline runs once per FAQ
  segment, concurrently, **never pooled** — a shared shortlist under the 3-chunk cap lets the
  stronger question crowd the other out, which is the defect splitting exists to remove — and each
  segment's answer is generated from its own shortlist alone. The fan-out lives *inside*
  `answer_faq`, not in the graph: one node still writes one state key, so LangGraph needs no channel
  reducer. Two rules bound it: the cap of 3 cannot be expressed in the request schema (the API
  rejects array bounds in a constrained-output schema), so it is stated in the prompt and an
  over-long result is *rejected*, never trimmed; and each request's own outcome is what the turn
  reports, since 011 — see the entry below.
- **The verdict, the answer and the citations live on the request** (011). There is no turn-level
  verdict anywhere: `messages.faq_verdict` and `messages.citations` are gone, replaced by one
  `messages.request_outcomes` JSONB column holding `{position, question, answer, verdict, citations}`
  per request in ascending position order — and the same list is what `ChatDoneEvent`, `MessageOut`
  and `turn.completed` carry. NULL means no FAQ half ran; `[]` is never written. `summarize_verdict`
  is deleted and no equivalent reduction may reappear under another name: a turn may answer one
  request and abstain on another, and any single value describing both is the "one value, two
  meanings" defect this phase removed. `RequestOutcome` enforces the pair that follows from a
  verdict — `answer` is None exactly when the verdict is an abstention, and `citations` is empty
  exactly then — so no reader has to decide which of two fields to believe. Citations are
  deduplicated **within** a request, so a chunk that supported two requests is cited under each.
  On the reply itself: the FAQ half contributes one part per answered request plus exactly one for
  the gap however many requests fell into it, which keeps `_actual_parts` bounded by the
  routing-time `_expected_parts`; a turn whose every request abstained still takes the collapse
  path and gets `_ABSTENTION_MESSAGE` byte for byte with no composing call. The composer gains a
  gap block and three constraints — never soften a gap, never extend an answer to cover one, name
  each gap in your own words rather than by quoting the classifier's restatement — and, being
  model-obeyed, their effect is measured by hand against `specs/011-answer-what-you-can/evaluation/`
  and re-checkable afterwards from the stored parts beside the stored reply. `turn.completed`'s
  `outcome` names the turn's **shape** (`faq`, `booking`, `small_talk`, `handed_off`, `merged`),
  never a verdict, and `answer_source` left that event once the two said one thing;
  `abstention_message` is set only when *every* request abstained. `agent/escalation.py` is
  unchanged and stores nothing new — what staff read as "the unserved requests" is derived from the
  reply's outcomes at render time.
- **A turn that ends without a reply calls a person** (011, FR-026). Any pipeline failure — the
  composing call, a generation, an embedding, a retrieval, or a bug this build cannot name —
  records `assistant_failed` into the turn's own collector and applies it before the error
  propagates (`_settle_the_failure` in `chat/api/turn.py`). Before 011 only the booking
  loop's tool failures did, so a broken turn left the patient with an error and nobody looking at
  it. **Without a reply** is the whole of the condition, and it is answered by a latch rather than
  inferred: `run_pipeline` sets `reply_delivered` when — and only when — the reply's insert has
  committed and its `done` has been taken by the stream, and a turn that got that far records no
  failure however it breaks afterwards. Only the escalation's own writes can fail past that point,
  so the alternative was paging a person to a conversation the patient was answered in. Such a
  turn still re-attempts the escalation the failure interrupted, so a corpus gap raised before the
  write broke still gets its mark. Three properties make it safe to put on every failure path: it
  is the **weakest** precedence,
  so a turn that already called staff for a corpus gap or an unauthorized request keeps that cause
  and that mark; it does **not** silence, because the thing that broke may already be working
  again; and a cancellation never reaches it — `CancelledError` is a `BaseException`, so neither
  `except` clause catches one, and a superseded turn is not a failure. It deregisters the turn
  before its write queues on the chat's lock, for the same reason the success path does: a staff
  post takes that lock before it asks for a cancellation, and `pg_advisory_lock` has no timeout.
  If the staff call itself fails it is logged as
  `turn.staff_call_failed` and swallowed: the original error is what the turn has to report.
- **What the assistant did to the schedule is a booking act, its own shape** (016). One act per
  attempt to book, reschedule or cancel, in the `booking_acts` table and on the wire as
  `MessageOut.booking_acts` — beside `request_outcomes`, **never** inside it and never a
  `FaqVerdict` value: those describe a FAQ answer the corpus grounded, an act is a performed change.
  `null` means nothing was attempted; `[]` is never sent. An act hangs off the **patient** message
  the turn answered, never the reply, because a reply is missing in exactly the turns a person
  needs the record for — failed after its write, taken over by staff, cancelled by a newer message.
  It is **written before the scheduler is called** and committed, and **settled at most once** by
  an `UPDATE` whose `WHERE` carries `outcome IS NULL`; an act that could not be written is never
  sent (the tool answers `unavailable`), so a write that reached the scheduler always has a row. A
  handler that raised, a cancelled turn or a failed settle leaves `outcome` NULL, and **NULL is read
  exactly as unknown** — rendered "Outcome unknown", never as nothing having changed — while staying
  distinct from a stored `unknown` (the tool's own answer). Settled rows are never touched again and
  store the practitioner's *name* as known at the time, so the record is a snapshot the schedule's
  later changes do not rewrite. A table rather than a JSONB column because acts are inserted then
  updated by id, and the booking loop dispatches tool calls under `asyncio.gather`. Recording is
  enforced in `ToolRegistry` (`_run_recorded`), not in handlers: a write tool declares `plan_act`,
  which reads its arguments with the handler's own helpers, and the registry records around the
  handler through `BookingActRecorder` — a port with **no read method**. The agent never reads acts
  (FR-021a): no prompt, history or tool result carries them, and `test_booking_acts.py` walks
  `chat/agent/` so that only the recording path may import the port. The console learns of a
  settle through `booking_acts_version` on the listing — count of acts plus count settled, a
  version compared for equality, never a clock — and the staff thread re-reads when the pair
  `(last_message_at, booking_acts_version)` changes, since a turn can settle an act without writing
  a message.
- **The staff connector is a staff-scoped surface, separate from the agent's registry** (017).
  `/mcp` is a remote MCP server in the chat service with two read-only tools - conversations
  needing attention, and appointments booked, cancelled and rescheduled in a window - and
  `/oauth/*` is the OAuth 2.1 sign-in server Claude connects through, both under
  `chat/connectors/`. Nothing in `chat/agent/` reaches either, and the agent's in-process
  `ToolRegistry` is unchanged: its tools are patient-scoped, and this is not the MCP transport
  005 deferred for them. **The session a tool answers for comes only from the verified token**
  (`AccessToken.subject`, set from the grant); no tool takes a session, chat or grant argument, so
  nothing a caller sends can point one at another session. The identity is a **pairing code** the
  console issues, keyed by the session, spent by one conditional `UPDATE`, and stored, like every
  token, only as a SHA-256 digest. Grants cascade from the session, so deleting one through
  `/admin` ends its pairings. The tools return counts and nothing else. **`PUBLIC_BASE_URL` is the
  single source of every public address** - the issuer, the connector address, the host the MCP
  transport accepts - read once through `chat.connectors.public_address.current_connector()`, and
  anything but a bare `https://` origin makes the connector unavailable (`503` on its routes, the
  reason in the tab) rather than publishing an address nobody can reach. The MCP transport is
  built per lifespan, because the SDK's session manager runs once per instance, and it is routed
  to on its two paths rather than mounted at `/`, so every other path keeps FastAPI's own `404`.
- **A person is called in four situations and no others** (009): the patient asked for one; the
  patient asked for something the assistant cannot provide; the message needs a person on safety or
  authority grounds; something failed. A message that requests nothing calls nobody — before 009,
  "Thanks" took the FAQ path, abstained, and paged a human. The middle two are recorded as two
  causes each (`corpus_could_not_answer` vs `not_authorized`; `urgent_condition` vs `distress` vs
  `booking_for_another_person`) because they call for different fixes. `agent/escalation.py` holds
  the structures that decide what each cause does: `_PRECEDENCE` orders them, `_SILENCING` says
  which stop the assistant, `_MARK_BY_REASON` derives the message mark, and `HANDOFF_TEXT` gives a
  constant to each of the five that *end* a turn — a corpus gap and a failure have none, because
  neither ends one. Four causes
  silence the conversation; `not_authorized`, a corpus gap and a failure do not. The four texts are
  constants, not generated: a stopping reply makes no claim about the clinic, which is the only
  thing retrieval could have grounded. The assistant does not triage — `urgent_condition` is a
  routing decision whose reply points at emergency services rather than judging anything.
- **A turn is traced through `chat.observability` and nothing else** (014). It is the only module
  that may import `langfuse` or `opentelemetry` (`test_tracing_boundary.py` checks, and the harness's
  `scoring/` and `comparison/` purity tests forbid both), so call sites speak in turns, steps,
  generations and tool calls. Every Anthropic call sits inside `generation(...)`, every tool call
  inside `ToolRegistry.dispatch`'s `tool_call(...)`, and each graph node inside `node_span`, which
  opens the node's observation — not Langfuse's callback handler, which runs on an executor thread
  and cannot parent anything a node opens. A retrieval decision that is also a log event goes
  through `record(...)`, which logs the payload and sets it as the span's output: **one dict, two
  sinks**, so building a second dict "with the same fields" for the trace is the defect it
  prevents. Untraced is a per-turn sampling decision: the `UNTRACED` flag is set in exactly one
  place, `launch` in `api/turn.py`, on the copied context handed to the turn's own
  `create_task(..., context=)` — later misses spans, earlier or elsewhere leaks into other turns,
  and `test_tracing_headers.py` pins the single setter. Masking is the log's redaction, not a second
  rule, in two stages: the key-name rule runs on structured values as they are recorded, and the
  export-time mask replaces configured secret values in every string attribute. Strings are
  recorded verbatim, so a patient message that happens to read as JSON is never re-encoded. Blank
  keys build no client at all, and the test `conftest.py` blanks them because `.env` holds real
  ones. On the harness side, `run.json`'s `tracing` lives on `Run`, **never on `RunConditions`**:
  a traced and an untraced run measure the same thing, so it must not enter a condition delta or a
  band. A restart that changes `tracing_enabled` stops a traced or service-off run and a resume
  refuses it; neither applies to a run that asked to be untraced.
- **`service.configured` is a data contract with the golden harness** (012): the harness takes a
  run's conditions from the field names of that startup event, so renaming one stops runs rather
  than silently recording less. See `evals/harness/.claude/CLAUDE.md`.
