# Feature Specification: Tracing with Langfuse (Phase 2d)

**Feature Branch**: `014-langfuse-tracing`

**Created**: 2026-09-22

**Status**: Draft

**Input**: User description: "Create a spec for phase 2d" — the Phase 2d section of
`docs/ROADMAP.md` and the "Tracing with Langfuse: technology choices" section of `README.md`, both
binding: Langfuse Cloud's free Hobby tier rather than a self-hosted instance; traces exported over
OpenTelemetry by the Langfuse SDK while structlog and `.run/chat.log` stay unchanged; LangGraph
nodes spanned by the SDK's callback handler, Claude calls recorded as generations with model id and
token usage, explicit spans for the retrieval steps carrying what each decided; masking designed in
as one declaration applied to every span; eval runs traced by default, with `make eval-run TRACE=0`
turning it off per run rather than per stack, the run recording whether it was traced, and tracing
never changing what a turn does.

## Why this exists

Phase 2b and 2c answer *how often* the assistant gets a turn right, and *what moved* between two
builds. Neither can answer *why* one turn went the way it did. Today that question is answered by
reading `.run/chat.log`: a turn's classification, its per-request retrieval events, its gates and
its verdict are all there, but as flat lines interleaved with every other turn's, with no timing
between steps, no token counts, and none of the prompts the models were actually sent. A case that
surprises in a run's report sends a developer grepping for its chat id and reconstructing the turn
by hand — and still leaves them guessing what the model saw.

This phase adds the missing view: one trace per turn, in which every step the turn took is a nested,
timed span carrying what that step decided, every model call carries its prompt, its output, its
model id and its token usage, and the whole turn can be opened from the case that produced it.

**Three things it deliberately is not.** It does not replace the log: structlog and
`.run/chat.log` stay exactly as they are, because the harness reads the log and scoring must stay a
pure function of a stored run — no metric may ever come from a trace. It does not evaluate
anything: Langfuse's datasets, scores, prompt management and model-graded evaluation are out of
scope, since 2b and 2c already own evaluation and a second, parallel copy of it would drift. And it
does not run Langfuse locally: the free Cloud tier is the destination, for the reasons the roadmap
records.

## Clarifications

### Session 2026-09-22

- Q: What does the masking declaration hide — secrets only, or patient text as well? → A:
  **Secrets only, matching the log.** API keys, connection URLs, the admin secret and the Langfuse
  keys themselves are masked; patient-authored text, prompts, model output and display names are
  not. A trace exists to show what the model saw and said, and masking the text would remove most
  of its value. `.run/chat.log` already carries patient text under the same rule, and every
  message and name in this deployment is synthetic — the golden set's messages and 1c's seeded
  pools.
- Q: The roadmap says graph nodes are spanned by Langfuse's callback handler, which cannot parent
  anything opened inside a node. Keep the roadmap's mechanism, or span nodes from the graph's own
  `node_span`? → A: **`node_span`.** Retrieval steps, generations and tool calls nest under their
  node, and no `langchain` dependency is added. The roadmap's 2d bullet is rewritten to match
  (plan.md Complexity Tracking, research R2).
- Q: Should SC-002's trace-equals-log guarantee be verified over a whole traced golden-set run? →
  A: **No — SC-002 states what the design guarantees.** The trace and the log receive the same dict
  by construction, a unit test asserts it for each FAQ event, and a hand spot-check confirms it
  against a real trace. A full-run comparison would need a reader over Langfuse's API that nothing
  else in this phase needs.
- Q: OpenTelemetry's batch processor drops an identical export-error record repeated within 20
  seconds, so "each lost export is logged" (SC-004) does not hold as written. Log every loss, or
  restate SC-004? → A: **Restate it: export failures are logged.** Logging every loss would mean
  building the OTLP exporter ourselves — the SDK wires no URL or credentials into an exporter it is
  handed — and copying SDK internals to make a line count exact buys nothing a developer acts on:
  one `tracing.export_failed` line already says traces are being lost.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open the trace of a turn and see why it went that way (Priority: P1)

A developer chats with the assistant by hand — or drives a turn with `scripts/dev-chat.sh` — and
the reply is not what they expected. They open Langfuse and find that turn's trace: the
classification and the requests it split the message into, each specialist that ran, each FAQ
request's retrieval — what was searched for, how many candidates came back, what each gate kept and
dropped and at what scores, what the reranker decided — every model call's prompt, output, model
and token usage, and the time each step took. The turn sits in its conversation, next to the turns
before and after it.

**Why this priority**: This is the phase. Everything else — the eval switch, the budget, the masking
— exists to make this view safe and affordable to have on by default.

**Independent Test**: With the stack running and Langfuse configured, send one mixed FAQ-and-booking
message. Its trace shows the classification, both specialists, both FAQ requests' retrieval steps
with their decisions, the booking loop's tool calls, and every model call with its token usage, and
each step's recorded decision matches the corresponding event in `.run/chat.log`.

**Acceptance Scenarios**:

1. **Given** a running stack with tracing configured, **When** a patient sends a message, **Then**
   exactly one trace is recorded for that turn, grouped with the other turns of the same
   conversation.
2. **Given** a turn whose message the classifier split into two FAQ requests and one booking
   request, **When** its trace is opened, **Then** each request's retrieval appears as its own
   branch, attributable to that request's position, and the booking specialist's tool calls appear
   under the booking branch.
3. **Given** any model call made during a turn, **When** the trace is opened, **Then** that call
   appears with the model identifier it was made with, its input and output token counts, and its
   duration.
4. **Given** an FAQ request that abstained at the rerank floor, **When** its trace is opened,
   **Then** the rerank step shows the floor, each candidate's score, what was dropped by the floor
   and what by the cap, and the verdict the request ended with — the same values the log's
   `faq.rerank_gate` and `faq.verdict` events carry.
5. **Given** a turn that failed and called staff under `assistant_failed`, **When** its trace is
   opened, **Then** the trace is present, the step that failed is marked as failed, and the error it
   raised is recorded on it.
6. **Given** a turn superseded by a staff post and cancelled, **When** its trace is opened, **Then**
   it is shown as cancelled rather than failed.

---

### User Story 2 - From a surprising case in an eval run, open its trace (Priority: P2)

A developer runs `make eval-run`, and one case in the report moved or failed unexpectedly. Without
re-running anything, they go from that case to its trace: the case's stored record names the trace
of each turn it drove, and each trace names the run and case it belongs to. Traces from eval runs
are distinguishable from traces of hand-driven sessions, so neither buries the other.

**Why this priority**: It is the reason eval runs are traced by default — the roadmap turned the
default around precisely so that a surprising case already has a trace. It is second because it
depends on the P1 trace existing.

**Independent Test**: Run `make eval-run CASES=<two case ids>`. Each case's record names the traces
of its turns; opening each one shows the run id and case id on it; and filtering Langfuse to eval
traces shows these and not the ones from a hand-driven session taken beside them.

**Acceptance Scenarios**:

1. **Given** an eval run with tracing left at its default, **When** a case is driven, **Then** its
   stored case record holds the identifier of each turn's trace.
2. **Given** a trace produced by an eval run, **When** it is opened, **Then** it carries the run id
   and the case id, and is marked as coming from an eval run.
3. **Given** an eval run and a hand-driven session tracing at the same time, **When** Langfuse is
   filtered to eval traces, **Then** only the run's traces appear, and the reverse filter shows only
   the hand-driven ones.

---

### User Story 3 - Take an untraced run when traces would be wasted (Priority: P3)

A developer is about to take the five full runs of a noise band — roughly 15k of the month's 50k
units, spent on traces nobody will open. They run `make eval-run TRACE=0`, and that run sends
nothing to Langfuse, while a teammate — or they themselves — tracing the app by hand in another
tab keeps tracing throughout. The run records that it was untraced, and a comparison between it and
a traced run of the same build treats them as the same conditions.

**Why this priority**: It protects the budget, which is what makes the P2 default affordable. It is
last because nothing breaks without it except the monthly allowance.

**Independent Test**: With a hand-driven session tracing, take `make eval-run TRACE=0 CASES=<id>`.
No trace appears for the run's turns, the hand-driven session's next turn is traced, the run
records itself as untraced, and `make eval-compare` against a traced run of the same case reports
no condition delta.

**Acceptance Scenarios**:

1. **Given** `make eval-run TRACE=0`, **When** the run drives its cases, **Then** none of its turns
   produces a trace, and its run record states that it was not traced.
2. **Given** an untraced run in progress, **When** a turn is sent from a hand-driven session against
   the same chat service, **Then** that turn is traced.
3. **Given** a traced and an untraced run of the same build, **When** they are compared, **Then**
   the condition delta is empty and every scored field of every case is computed exactly as it
   would have been without tracing.
4. **Given** `make eval-run` with tracing left at its default but a chat service that has no
   Langfuse credentials configured, **When** the run completes, **Then** its record states that it
   was not traced — the run records what happened, not what it asked for.

---

### Edge Cases

- **Langfuse is unreachable, slow, or rejects the export** — the network is down, the keys are
  wrong, or the month's Hobby allowance is spent. The turn is unaffected: the reply streams as it
  would have, with the same latency the patient would otherwise see, and the failure is logged, not
  raised. A trace lost this way is lost; it is not retried into a later turn's budget.
- **The chat service starts with no Langfuse credentials** — the default for a fresh checkout and
  for CI. The service starts and answers normally, says at startup that tracing is off, and sends
  nothing. Tracing is never a dependency of a turn.
- **A turn that ends with no model call** — small talk is generated, but a message whose every FAQ
  request abstained takes the constant-abstention path with no composing call, and a stopping
  reason (`urgent_condition`, `distress`, `booking_for_another_person`) answers with fixed text. The
  trace still exists and shows the classification and the route that ended the turn.
- **A paused conversation** — the message is stored and no reply is generated. No trace is
  recorded, because no turn ran; nothing is spent on it.
- **A trace of an eval turn whose run is later resumed** — the resumed run's cases carry the traces
  of the turns they actually drove, and a case re-driven on resume does not keep a trace id from its
  abandoned attempt.
- **A secret appearing in a span** — an API key in an error message, a connection URL in a failed
  retrieval's exception. It is masked before it leaves the process, exactly as the same value would
  be in the log.
- **A very long input or output** — a full retrieved shortlist in a prompt, a long conversation
  history. The span carries it; the trace is a debugging view and a truncated prompt would hide the
  thing a developer opened it to see. The Hobby unit count is per span, not per byte, so size costs
  nothing against the allowance.

## Requirements *(mandatory)*

### Functional Requirements

#### What a trace contains

- **FR-001**: Every turn the chat service runs MUST produce exactly one trace, and every trace MUST
  belong to exactly one turn.
- **FR-002**: A turn's trace MUST be grouped with the other turns of the same conversation, so a
  conversation reads in order.
- **FR-003**: Every step of the agent graph a turn passes through MUST appear in the trace as its
  own span, nested as the graph executed it, including specialists that ran concurrently.
- **FR-004**: Every model call MUST be recorded as a generation carrying the model identifier it was
  made with, its input, its output, its input and output token counts, and its duration. This
  covers the classifier, each FAQ answer, the booking loop's calls, small talk and the composing
  call.
- **FR-005**: Each FAQ request's retrieval MUST appear as its own branch of the trace, attributable
  to the request's position, with a span for each step — embedding the query, the vector search,
  the similarity gate, the rerank call and the rerank gate — carrying what that step decided: the
  candidates it received, their scores, what it kept, and what it dropped by the floor and by the
  cap separately.
- **FR-006**: The values a retrieval span records MUST be the same values the corresponding log
  event carries for the same step. The trace is a second view of one decision, never a second
  computation of it.
- **FR-007**: A turn's trace MUST record, at its top level, the classification's requests and their
  intents, the route the turn took, each request's final verdict, and the turn's outcome shape — the
  values `intent.classified` and `turn.completed` carry.
- **FR-008**: The booking specialist's tool calls MUST each appear as a span carrying the tool's
  name, its arguments and its result, including a call whose outcome was unknown.
- **FR-009**: A step that failed MUST be marked as failed in the trace with the error it raised, and
  a turn cancelled by a staff post MUST be shown as cancelled, not failed — the same distinction
  `_settle_the_failure` already draws.
- **FR-010**: A paused conversation's stored message, which runs no turn, MUST NOT produce a trace.

#### Tracing never changes what a turn does

- **FR-011**: Enabling or disabling tracing MUST NOT change any turn's classification, routing,
  retrieval, verdict, reply, citations, escalation, stored record or log output (other than log
  lines reporting tracing's own state or its failures).
- **FR-012**: A failure to export a trace — unreachable destination, rejected credentials, exhausted
  allowance, or a timeout — MUST NOT fail, delay or alter the turn. It MUST be logged, and it MUST
  NOT be raised into the turn.
- **FR-013**: Exporting MUST NOT hold up the patient's reply: the reply MUST finish streaming
  without waiting for any trace data to be sent.

#### Masking

- **FR-014**: Before any span leaves the process, it MUST be passed through one masking declaration
  that applies to every span and every generation, with no per-span opt-in. It masks secrets —
  API keys, connection URLs, the admin secret and the Langfuse keys themselves — by the same rules
  `shared-logging` applies to the log: a value under a secret-named key, and any occurrence of a
  configured secret's value wherever it appears. Patient-authored text, prompts, model output and
  display names are **not** masked (see Clarifications).
- **FR-015**: The masking declaration MUST be tested directly: a span carrying each masked kind of
  value MUST reach the exporter with that value masked, and the test MUST fail if any span path
  bypasses the declaration.
- **FR-016**: The Langfuse credentials MUST join the chat service's secret settings, so the log's
  existing redaction covers them as it covers the Anthropic and Voyage keys.

#### Configuration and the destination

- **FR-017**: The destination and credentials MUST reach the tracer through the chat service's
  settings, like every other endpoint and key. No instrumentation code may name the destination.
- **FR-018**: With no credentials configured, the chat service MUST start and run normally with
  tracing off, send nothing, and say at startup that tracing is off. Credentials present MUST mean
  tracing on.
- **FR-019**: Whether tracing is on MUST be stated in the chat service's startup configuration
  event, as a fact about the process, and MUST NOT become one of the run conditions the harness
  compares.
- **FR-020**: Only the chat service is traced. The scheduling service is not instrumented; a call to
  it appears as the calling tool's span in the chat service's trace.

#### Eval runs

- **FR-021**: `make eval-run` MUST trace the turns it drives by default, when the chat service has
  tracing on.
- **FR-022**: `make eval-run TRACE=0` MUST drive a run none of whose turns is traced. The choice MUST
  travel with the run's own requests, never through a setting of the chat service, so other
  sessions against the same running service keep tracing throughout the run.
- **FR-023**: The switch MUST NOT restart, reconfigure or otherwise touch the running chat service,
  and MUST NOT count as a service restart to the harness's restart check.
- **FR-024**: A chat service MUST honour the untraced choice for exactly the turns that carry it —
  no turn from any other session may be untraced by it, and no turn carrying it may be traced.
- **FR-025**: A run's record MUST state whether its turns were traced, as a fact about what
  happened: traced only when the run asked for tracing **and** the service stated tracing was on.
  A run taken before this phase MUST read as untraced, since nothing could have traced it.
- **FR-026**: A traced turn of an eval run MUST carry the run id and the case id, and MUST be
  distinguishable from a hand-driven session's trace by a filter in Langfuse.
- **FR-027**: A traced case's stored record MUST carry the identifier of each turn's trace, so a
  case in a report leads to its traces without a search.
- **FR-028**: Whether a run was traced MUST NOT appear in a comparison's condition delta, and MUST
  NOT stop, qualify or alter any comparison or band. It MAY be shown beside each run's identity.
- **FR-029**: Scoring and comparison MUST NOT read a trace or import the tracing SDK. The purity
  rule 2b enforces by test MUST extend to it.

#### The phase's record

- **FR-030**: The phase MUST measure, and commit under this feature's `evaluation/` directory, how
  many Langfuse units a turn of each shape actually costs (FAQ with one request, FAQ with several,
  booking, small talk, a stopping reason, a merged turn), and what a full traced golden-set run
  costs in total — replacing the roadmap's "roughly 15–25 per turn" and "roughly 3k" with measured
  numbers.
- **FR-031**: The documentation the constitution requires MUST be updated in the same change: the
  README's tracing section loses its "nothing is built yet", the commands gain `TRACE=0`, and the
  settings a developer must set to enable tracing are documented.

### Key Entities

- **Trace**: one turn's record in Langfuse — the conversation it belongs to, the classification and
  route at its top, its spans and generations nested beneath, and, for an eval turn, the run id and
  case id it came from.
- **Span**: one step of a turn — a graph node, a retrieval step or a tool call — with its duration,
  its inputs, what it decided, and whether it failed or was cancelled.
- **Generation**: one model call — model identifier, input, output, input and output token counts,
  and duration.
- **Masking declaration**: the single rule every span and generation passes through before it
  leaves the process.
- **Run tracing state**: the fact on a stored run saying whether its turns were traced, derived from
  what the run asked for and what the service stated.
- **Case trace references**: the trace identifiers a stored case record carries, one per turn it
  drove.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any turn, a developer can open one trace that shows every step the turn took, the
  time each took, and every model call's prompt, output and token usage — without reading the log.
- **SC-002**: Every retrieval decision shown in a trace matches the corresponding log event's
  values exactly: the two are written from the same payload, a unit test asserts the equality for
  each of the six FAQ events, and a hand check of one real trace against `.run/chat.log` agrees.
- **SC-003**: A traced and an untraced run of the same build produce identical scored fields for
  every case up to the run-to-run noise 2c's band describes, and their comparison reports an empty
  condition delta.
- **SC-004**: With the tracing destination unreachable, every turn completes with the same reply
  behaviour and no added wait the patient can perceive, and export failures are logged — though
  not one line per lost export (see Clarifications).
- **SC-005**: A chat service with no tracing credentials starts, answers every turn, and sends
  nothing — so the unit tests, CI and a fresh checkout need no Langfuse account.
- **SC-006**: From any case in an eval run's report, a developer reaches that case's traces in one
  step, and every eval trace names its run and case.
- **SC-007**: An untraced run sends zero traces for its own turns, while a hand-driven session
  against the same service during it is traced on every turn.
- **SC-008**: No masked kind of value appears in any exported span, verified by test for every kind
  the masking declaration covers.
- **SC-009**: The measured unit cost per turn shape and per full run is committed, and a full
  traced golden-set run fits within one month's Hobby allowance with room for at least ten such runs
  or states plainly that it does not.

## Assumptions

- A Langfuse Cloud Hobby project exists, and its keys are available to set in the chat service's
  environment; creating the account is a manual step, not part of this phase.
- The golden set, its labels, and 2b's and 2c's scoring and comparison stay unchanged, apart from
  the run record gaining its tracing state and each case record gaining its trace references.
  Both are new optional fields, so every run stored before this phase, including the committed
  baseline, still reads and scores.
- Tracing covers turns only. The FAQ admin routes (whose saves embed text), the console's polling,
  the admin surface and session provisioning are not traced; a turn is the only unit the question
  "why did it go that way" is asked about.
- Traces are disposable: Hobby keeps 30 days, and nothing in the eval chain needs one older than
  that, because 2b and 2c read stored runs, never traces.
- The data every trace carries is synthetic: the golden set's messages and the seeded names from
  1c's pools. No real patient uses this deployment.
- Controlling spend is done the way the roadmap states — per run with `TRACE=0` — and not by
  sampling. A sampled trace is one a developer cannot count on being there.

## Out of Scope

- **Self-hosting Langfuse.** The Cloud Hobby tier is the destination; standing up its six-container
  stack is not a plan of this project.
- **Evaluation inside Langfuse.** Datasets, scores, experiments, model-graded evaluators and
  annotation queues. 2b and 2c own evaluation; a second copy in another system would drift from it.
- **Prompt management.** Prompts stay in the code, versioned with the build that uses them.
- **Tracing the scheduling service**, and distributed trace propagation across the gRPC boundary.
- **Tracing the frontend** or any browser-side timing.
- **Replacing or reshaping the log.** structlog, its events and `.run/chat.log` are unchanged, and
  the harness keeps reading the log.
- **Alerting or dashboards** built on traces.
