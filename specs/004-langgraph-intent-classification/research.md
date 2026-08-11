# Research: Adopt LangGraph + Intent Classification (Phase 1b)

## 1. LangGraph adoption: `classify_intent_node -> answer_faq_node -> END`, sequential

**Decision**: Introduce a `StateGraph` (`agent/graph.py`) with two nodes, sequential:
`START -> classify_intent_node -> answer_faq_node -> END`. `classify_intent_node` runs first, calls
`classify_intent()` (research.md #3), and logs the result — it has nothing to stream, so it never
touches the stream writer. `answer_faq_node` runs second: a thin wrapper that calls the existing
`answer_faq()` async generator unchanged and forwards each `ChatTokenEvent`/`ChatDoneEvent` it
yields via LangGraph's custom stream-writer mechanism (`get_stream_writer()` inside the node; the
caller consumes them via `graph.astream(state, stream_mode="custom")`). `answer_faq.py`'s retrieve →
gate → generate/stream logic itself is not modified — its one small change is losing its
`turn.message_received` log call, which moves earlier in the pipeline for reasons unrelated to the
graph wrapping itself (research.md #8). The whole graph invocation lives inside the same
`asyncio.Task` that `api/chat.py`'s existing `generation_registry.py` already tracks and cancels per
chat (research.md #2) — cancelling that task cancels whichever node is currently running.

**This reverses this research item's own prior version** (the two nodes running in parallel),
corrected on review: sequential ordering is what actually sets up the graph shape Phase 1d needs.
Phase 1d's real branching ("parallel specialist nodes with a merge step," ROADMAP) has to make its
routing decision — which specialist node(s) to launch — *based on* the classified intent(s); that
decision can only exist as a graph edge if classification has already completed by the time the
graph reaches it. A parallel `classify_intent_node`/`answer_faq_node` shape has no such decision
point at all — there's nowhere in that topology for 1d to attach a routing edge without restructuring
the graph from scratch. The sequential shape already has exactly the edge 1d will need
(`classify_intent_node -> ...`); 1d's job becomes replacing its single unconditional continuation to
`answer_faq_node` with a conditional edge to one or more specialist nodes, not inventing a decision
point that didn't exist before. This also matches FR-008's own plain-English framing ("classify,
then respond") literally, which the rejected parallel design had quietly diverged from.

**Rationale**: ROADMAP Phase 1b's own framing is "prove the framework swap on its own before adding
new capabilities on top of it" — the lowest-risk way to prove a swap is for it to change nothing
observable about the FAQ path itself, while still building the graph in the shape its own stated
purpose (setting up for 1d) actually needs. Today's token-by-token NDJSON streaming (`api/chat.py`'s
`run_pipeline`, an `async for event in answer_faq(...)` loop pushing onto a queue) is a hard
requirement to preserve (spec 001 FR-004, unchanged by this feature) — LangGraph nodes normally
return a state update on completion rather than yielding incrementally, so naively "porting"
`answer_faq`'s logic into a node body would either lose incremental streaming or require rewriting
the retrieve → gate → generate/stream logic to fit a different shape, for no behavioral gain and
real regression risk. The custom-stream-writer mechanism is LangGraph's documented way to let a node
push arbitrary intermediate values while it runs, which is exactly "a node body that happens to be
an existing async generator, unchanged." `classify_intent_node`'s own internal error handling
(research.md #3 — it never lets a classification failure propagate as a raised exception) is what
keeps this simple even sequentially: the edge to `answer_faq_node` is a single unconditional edge,
not a conditional one gated on classification's success — FR-007's "must not fail the request" is
what makes that unconditional edge safe.

Classification now genuinely adds to time-to-first-token, spending part of SC-004's 1-2s budget
rather than costing ~0 by construction (as the rejected parallel design did) — this is an accepted
tradeoff, not an oversight: SC-004's budget itself already anticipates this ("does not add more than
1-2 seconds"), and Haiku 4.5 (research.md #4) keeps the actual cost well inside it.

**Alternatives considered**:
- *Rewrite `answer_faq`'s retrieve/gate/generate steps as separate graph nodes now*: this is
  the natural shape for Phase 1d's eventual branching, but building it before there's any branching
  to justify it is exactly the complexity Constitution Principle I/VII block — Phase 1b's own text
  says the graph "still has only one real path (FAQ) at this point." Deferred to whenever 1d
  actually needs multiple specialist nodes to merge.
- *Parallel nodes, both from `START`, both feeding `END`* (this research item's own prior
  decision): kept classification latency off the critical path entirely, but doesn't set up the
  routing decision point 1d actually needs — see above. Rejected once that was the deciding factor,
  not latency.
- *Use LangGraph's built-in LLM-message streaming (`stream_mode="messages"`) instead of a custom
  writer for `answer_faq_node`*: that mode is shaped for streaming a chat model's own token stream
  directly from a graph node calling an LLM inline; `answer_faq` already has its own generator
  producing typed `ChatTokenEvent`/`ChatDoneEvent` objects (including abstention and citation logic,
  not just raw tokens) — the custom-writer mode is the better fit for forwarding an existing typed
  event stream unchanged, without needing to shoehorn abstention/citations into an LLM-message-shaped
  event.

## 2. Classification shares the existing cancel-and-restart lifecycle — it is not decoupled from it

**Decision**: Classification is *not* a separately-managed task. It runs as a node in the same
LangGraph graph invocation as FAQ generation (research.md #1), inside the same `asyncio.Task` that
`agent/generation_registry.py` already tracks per chat and cancels when a follow-up message
supersedes it. When a message's turn is cancelled, its classification attempt is cancelled along
with its FAQ reply — no separate background-task mechanism is introduced.

**This reverses this plan's own original decision of the same section number** (a decoupled,
never-cancelled `background_tasks.fire_and_forget()` task), corrected after review: that design
optimized for "classify every message, even ones about to be superseded," but a superseded message's
classification has no value on its own — the message isn't lost, its content already reaches the
*surviving* message's own classification call via FR-006's context window (research.md #5), the
same way its content already reaches that call's FAQ generation today (spec 003 research.md #6,
merged-burst retrieval). Spending a classification call on a message about to be superseded, only to
throw the result away unused, is wasted work for a phase whose stated goal is proving the simplest
possible framework swap — not a correctness requirement. spec.md's FR-005/FR-006/FR-007/SC-002 and
its rapid-burst Edge Case were corrected to match (spec.md Clarifications, follow-up correction):
a message whose turn is superseded gets **no** classification record at all, the same way it already
gets no assistant reply (spec 003) — this is a third, expected outcome, not a shortfall against
SC-002's 99% target, which is scoped to messages whose turn actually completes.

**Rationale**: This is simpler than the rejected original design in every dimension: no new
concurrency-safety module (`background_tasks.py` is no longer needed at all — see plan.md's revised
Project Structure), no risk of an orphaned task outliving the request it was spawned for, and no
new runtime-state section in data-model.md. It also reuses `generation_registry.py`'s existing,
already-tested cancellation mechanism for a second purpose rather than inventing a parallel one
(Constitution Principle VII) — one lifecycle mechanism now governs the whole turn (both what answers
it and what classifies it), not two independently-reasoned-about ones.

SC-004 (added latency budget) is a separate concern from this decision — research.md #1's sequential
node ordering does spend part of that budget on classification now, but that's an accepted tradeoff
of the graph-shape decision made there, not something this decision (which task lifecycle
classification shares) affects either way.

**Alternatives considered**:
- *The original decoupled-task design (this plan's prior version of this decision)*: rejected on
  review — see above. It satisfied a stronger, incorrect reading of FR-005/SC-002 (classify literally
  every message, including ones about to be discarded) that spec.md itself has since been corrected
  to not require.
- *`classify_intent_node`'s critical section wrapped in `asyncio.shield()` so it alone survives
  cancellation while `answer_faq_node` doesn't*: this was the shape the decoupled-task alternative
  was trying to approximate from inside a single graph; rejected for the same reason the decoupled
  task itself now is — there's no requirement left that classification survive its turn's
  cancellation, so there's nothing left to shield.
- *Serialize classification for a chat, like a lightweight version of the generation registry*:
  unnecessary — nothing requires classification calls for the same chat to run one-at-a-time, and
  serializing them would only add latency for no correctness benefit. (Unchanged from the prior
  version of this research item — still rejected, for the same reason.)

## 3. Structured output: native JSON Outputs, closed label set, failure sentinel assigned by the caller

**Decision**: `classify_intent()` calls the Claude Messages API with `output_config.format` set to a
`json_schema` constraining the response to `{"intents": [...]}`, where `intents` is an array of a
4-value `enum` (`faq_question`, `booking`, `call_staff`, `unknown`) — Claude's own native JSON
Outputs mechanism (constrained decoding against the schema), not a tool call. `IntentLabel` (a
5-member `StrEnum` in `domain/schemas.py`) additionally has a `CLASSIFICATION_FAILED` member — but
the request's schema `enum` list is built by excluding it (`[label.value for label in IntentLabel if
label is not IntentLabel.CLASSIFICATION_FAILED]`), so the model can never produce it; it's a value
only orchestration code assigns, when `classify_intent()` raises (any API error, timeout, or a
response that fails to validate against the schema) and the caller catches that exception.

**This corrects this research item's own prior version** (forced tool-use,
`tool_choice={"type": "tool", ...}`), which claimed no native JSON-mode mechanism existed — verified
against Anthropic's own structured-outputs documentation and found to be wrong: **JSON Outputs**
(`output_config.format`) is a native, independent mechanism from tool use, guaranteed schema-
compliant via constrained decoding, and Anthropic's own documentation names classification as its
intended use case ("Use JSON Outputs when: You need Claude's final response in a specific JSON
structure — extraction, **classification**, formatting"), distinct from Strict Tool Use, which is
framed for agentic tool-calling workflows. Classification isn't invoking an action — nothing is being
"called" — so routing it through the tool-use API was a workaround for a gap that no longer exists,
not a real fit for what this step does. `enum` remains a supported schema construct under JSON
Outputs, so the failure-sentinel-exclusion design carries over unchanged — only the mechanism
(`output_config.format` vs. a forced tool call) changes, not the schema shape or the reasoning behind
excluding `CLASSIFICATION_FAILED` from it.

**Rationale**: Directly implements spec.md's Clarifications follow-up: "the fallback case [is]
recorded as a dedicated intent label value... where the last value is assigned by the calling code
on a failed/invalid classification call, never something the classifier itself outputs." One
`StrEnum` for both "what the model can say" and "what gets logged" avoids two near-duplicate enums
(Constitution Principle VII) while still making it structurally impossible for the model's own
response to produce the failure sentinel — the schema-level `enum` exclusion, not application-level
validation after the fact, is what enforces this. Native JSON Outputs (rather than a forced tool
call, or free-form JSON parsed out of response text) is the more accurate reading of "structured
output" for this step — Principle IV requires it explicitly for any routing/classification step, and
this is the mechanism Anthropic's own docs point to for exactly this kind of task.

**Alternatives considered**:
- *Forced tool-use (this research item's own prior decision)*: works, and was the only reliable
  mechanism before JSON Outputs existed, but models the step as an action being "called" rather than
  a structured judgment being produced — a semantic mismatch for classification specifically, on top
  of no longer being necessary now that a native mechanism exists for this exact use case.
- *Two separate enums (`ClassifierIntentLabel`, 4 members; `RecordedIntentLabel`, 5 members)*:
  gives the type checker an even stronger guarantee (the classifier's own return type could
  literally never mention the failure member), at the cost of two types tracking what's really one
  concept plus one orchestration-only extra value. Rejected as unnecessary duplication for a
  guarantee the schema-level enum exclusion already provides at the API boundary, where it actually
  matters (there's no code path where the *model's* structured-output result is deserialized
  straight into `IntentLabel` without first checking it against the request's own restricted
  `enum` list).

## 4. Model routing: Haiku 4.5 for classification, unchanged Sonnet 5 for generation

**Decision**: `classify_intent.py` calls `claude-haiku-4-5-20251001`, a module-level constant
(mirroring `answer_faq.py`'s existing `_MODEL = "claude-sonnet-5"` pattern). Generation is
unchanged.

**Rationale**: Constitution Principle IV requires "the cheapest model capable of the task" for any
routing/classification step, reserving stronger models for generation — this is closed-set,
short-input classification, squarely within a small/fast model's capability, and Haiku 4.5 is the
current cheap/fast model in the Claude 5 family this codebase already targets (`answer_faq.py`
already uses Sonnet 5, not Opus, for the same cost-consciousness reason). No new `Settings` field or
API key is needed — the same shared `AsyncAnthropic` client (`app.state.anthropic_client`) is reused
with a different `model=` argument, identical to how `answer_faq.py` already does it.

**Alternatives considered**:
- *Reuse Sonnet 5 for classification too, one fewer model to reason about*: rejected — directly
  contradicts Constitution Principle IV's explicit cost-routing requirement, and spec.md's
  Assumptions already commit to "a fast, low-cost model, distinct from the model used to generate
  FAQ answers."

## 5. Classification context window: `last_n_turns` lives in `history.py`, not a classifier-only module

**Decision**: `history.py` gains a new function, `last_n_turns(history: list[Message], n: int = 5)
-> list[Message]`, alongside its existing `build_history_messages`. It walks `history` (oldest-first)
backward from the end, grouping consecutive same-sender runs into bursts, and keeps only the rows
belonging to the last `n` complete patient-burst-then-response-burst pairs (a trailing,
still-unanswered patient burst at the very end doesn't itself count as a "turn" yet — it's not
paired with a response). `classify_intent_node` calls `last_n_turns(history_rows, n=5)` and passes
the result into `build_history_messages(..., current_message, current_message_id)` — the same
function `api/chat.py` already calls for FAQ generation — to get both the alternating message list
for the classifier call and the trailing burst ids.

**This corrects this research item's own prior version** (a new, classifier-only
`agent/intent_context.py` module), on review: `last_n_turns` isn't a classification-specific
concern, it's a general "how much of this chat's history counts as recent" capability — the kind of
thing Phase 1d's specialist nodes (or a future eval/observability consumer) are just as likely to
need as the classifier is, once they're not all forced to consume the full, unbounded history the
way `answer_faq`'s generation call does today. `history.py` is already this codebase's one place
that knows how to interpret `Message` rows as turns/bursts (`build_history_messages`'s merge logic,
its `trailing_ids` in-progress-burst detection); putting `last_n_turns` there instead of splitting it
into a second, narrower module keeps that ownership consolidated in one place rather than fragmenting
"turn/burst interpretation" across two modules for no structural reason.

**Rationale**: FR-006's "5 most recent turns... plus any earlier not-yet-answered messages already
sent in the current, in-progress burst" turns out to already be half-solved: `build_history_messages`
already computes the trailing in-progress-burst inclusion (its `trailing_ids` return value,
research.md #5 of spec 003) — that's precisely "any earlier not-yet-answered messages already sent
in the current burst." The only genuinely new piece of logic is the upstream truncation to 5 turns,
since `build_history_messages` is currently always called with the *full* history. Adding
`last_n_turns` as a sibling function in the same module that already owns burst-interpretation —
rather than a new module that would need to import and depend on `history.py` anyway — is both reuse
over duplication (Constitution Principle VII) and the more cohesive home for it: any future caller
that needs a bounded window gets it from the same place it already gets merge logic, without needing
to know a second module exists.

**Alternatives considered**:
- *A separate `agent/intent_context.py` module* (this research item's own prior decision): would
  still have called into `history.py`'s `build_history_messages`, so it bought no independence — just
  an extra file and an extra import edge, for logic that's about interpreting `Message` history in
  general, not about classification specifically. Rejected once a plausible second caller (Phase 1d)
  was considered.
- *A second, independent burst-merging implementation, wherever it lives*: rejected regardless of
  which module — duplicates `history.py`'s existing, tested logic for no behavioral difference.
- *Bound by raw message count or token budget instead of turns*: rejected in spec.md's
  Clarifications session already (turn/burst count was the user's explicit, deliberate choice over
  both alternatives) — not reopened here.

**This corrects this research item's own prior version** (`last_n_turns` + `build_history_messages`,
the shape described above), corrected on review: that two-function split is superseded by a
four-function split in the same module — `split_into_bursts` (promoted from a private
`_group_into_bursts` helper the old shape already had internally), `derive_reply_to_message_ids`,
`bound_to_last_n_turns`, and `to_claude_messages`. The trigger is `api/chat.py`'s own call site:
folding the current patient message into `history_rows` *before* calling into `history.py` (via
`history_rows = [*history_rows, patient_message]`, right after `chat_repository.create_message`
returns it) makes the turn's trailing burst always patient-sided by construction, for every caller —
not just something `build_history_messages` had to special-case internally by threading
`current_message`/`current_message_id` through as separate parameters. Once that's true, "bound to
the last n turns" and "format as alternating Claude messages" stop needing to be one combined
operation parameterized by which node is calling it (`classify_intent_node` needed the bounded
version, `answer_faq_node` needed the unbounded one, and the old `build_history_messages` signature
had no clean way to express "format this, but don't also decide how much of it to include") — they
become two small, separately reusable, directly composable functions any caller can chain for
itself: `bound_to_last_n_turns(bursts, n=5)` then `to_claude_messages(...)`, or just
`to_claude_messages(bursts)` alone for the unbounded case. `derive_reply_to_message_ids` becomes its
own function for the same reason: it's no longer a second return value `build_history_messages`
computes as a side effect of formatting, it's the trailing burst's ids, period — computable directly
from `split_into_bursts`'s output without formatting anything. This also fixes a latent consistency
risk the old shape had: `graph.py`'s `_GraphState` used to carry `merged_history`
(`build_history_messages`'s formatted output) and `reply_to_message_ids` as two independently
precomputed fields with no structural link between them — nothing prevented them from silently
drifting out of sync if a future edit changed one without the other. The new shape carries `bursts`
itself in `_GraphState` instead, and derives both `reply_to_message_ids` (via
`derive_reply_to_message_ids`) and the formatted history (via `to_claude_messages`) from that one
source whenever they're actually needed — there's no longer a second, separately-computed copy of
"which messages this turn answers" that formatting could disagree with. See research.md #9 for the
`_GraphState`/`run_turn` interface-shrinking decision this enables.

## 6. Log event: turn id + label(s) only — narrower than this codebase's existing pattern, deliberately

**Decision**: The new `intent.classified` event (emitted via the existing `get_logger()`, inside the
already-bound `bind_turn_id()` context so `turn_id` is attached automatically by the existing
`merge_contextvars` processor — no new parameter needs to be threaded through) carries only
`intents` (the recorded `IntentLabel` value(s)) — no `message`/`content` field. It is only ever
emitted from inside `classify_intent_node` once that node actually completes (research.md #1/#2) —
a message whose turn is cancelled before the node finishes never reaches the `logger.info` call, so
no `intent.classified` line exists for it, mirroring `message.persisted`'s own precedent of never
appearing for a cancelled assistant reply (spec 003).

**Rationale**: Directly implements spec.md's Clarifications privacy decision: classification
records must reference the conversation turn, never duplicate the patient's raw message text, since
application logs are typically less access-controlled than the primary conversation database and
this is a medical-clinic assistant. This is narrower than `answer_faq.py`'s own existing
`turn.message_received` event, which *does* log `message=message` in full — that's a pre-existing
pattern from an earlier phase, out of this feature's scope to change (Constitution Principle I —
this feature's job is Phase 1b's own scope, not retroactively hardening unrelated logging from
Phase 0/1a). `turn_id` already equals the patient message's own `id` (spec 003 research.md #4), so
no new identifier is needed to satisfy "reference the conversation turn (e.g., its ID)" — the
existing correlation-id mechanism already provides it for free. (`turn.message_received`'s
*content* is unaffected by this feature per the above — only *when* it fires moves, and only because
this feature inserts a stage ahead of where it used to fire; see research.md #8.)

**Alternatives considered**:
- *Also retrofit `turn.message_received` to stop logging raw text, in the same change*: out of
  scope — that's an existing behavior from a prior, already-shipped phase, not something Phase 1b's
  spec asks for; flagging it here as a known, separate, pre-existing inconsistency rather than
  silently leaving it undocumented (Constitution Principle VI).

## 7. No new database table — classification stays log-only, per spec.md Assumptions

**Decision**: No SQLAlchemy model, no Alembic migration. `IntentLabel`/`IntentClassificationResult`
are Pydantic/`StrEnum` types in `domain/schemas.py`, never persisted as a `Message` column or a new
table.

**Rationale**: spec.md's Assumptions are explicit that classified intents are "recorded via
logs/traces for later review, not as a new field persisted on the conversation data model — formal
tracing infrastructure arrives in a later phase [Langfuse, Phase 2, ROADMAP]." Adding a persisted
table now would be scope beyond what this phase's User Story 3 ("reviewable... without re-running
the conversation," satisfied by grep-able structured logs) actually requires — Constitution
Principle I.

**Alternatives considered**:
- *A `classified_intents` table, FK to `messages.id`*: would make review queryable via SQL instead
  of log-grepping, but is explicitly out of scope per spec.md's own Assumptions — deferred to
  whenever formal tracing infrastructure (Langfuse) lands in Phase 2.

## 8. `turn.message_received` moves ahead of `classify_intent_node` — a consequence of adding a stage in front of `answer_faq`

**Decision**: The existing `turn.message_received` log call — currently the first line
`answer_faq()` logs, carrying the merged/unified current-turn message (`message`) and the burst ids
it covers (`message_ids_unified`) — moves out of `answer_faq()` and into `api/chat.py`'s
`_event_stream`, logged immediately after `build_history_messages(history_rows, message, turn_id)`
computes `merged_history`/`reply_to_message_ids` and *before* the graph invocation (and therefore
before `classify_intent_node`) begins. Its fields are unchanged; only where it fires moves.

**Rationale**: `turn.message_received` represents "this is the unified message the whole turn is
about to process" — a statement about the *turn* as a whole, not about the FAQ-answering step
specifically. Before this feature, that distinction didn't matter: `answer_faq()` was the only
processing step, so "the start of `answer_faq()`" and "the start of the turn" were the same moment.
This feature breaks that equivalence by inserting `classify_intent_node` ahead of `answer_faq_node`
(research.md #1) — if `turn.message_received` stayed inside `answer_faq()`, it would now fire only
once classification has *already finished*, leaving a log reader with no way to tell, while
classification is still in flight, what message the turn is even about. That's a real regression
this feature would otherwise introduce, not a pre-existing issue like `research.md #6`'s privacy
point (which is about the event's *content*, unrelated to this). Logging it once, before either
node runs, restores "a reader can tell what's being processed regardless of which stage is
currently active" — the property the single-step pipeline had for free and this feature's own
restructuring is responsible for preserving.

This also simplifies `answer_faq()` slightly: it no longer needs to reconstruct `message` from
`history[-1]["content"]` for logging purposes — that value now only needs to exist once, at the
point it's already computed in `_event_stream`.

**A side effect worth stating precisely**: `build_history_messages` runs entirely before
`generation_registry.py`'s cancellation logic and the graph task even exist (`api/chat.py`,
confirmed in the current source — the merged-history computation sits above where the task is
created and registered). Logging `turn.message_received` at that point means it now fires
**unconditionally, once per incoming message** — including one whose turn is cancelled moments
later by a follow-up. This is a deliberate contrast with `intent.classified`/`turn.completed`
(research.md #2/#6), which only ever appear for a turn that actually completes: `turn.message_received`
answers "what message did the system receive and start processing," `intent.classified`/
`turn.completed` answer "what did processing that message actually produce" — different questions,
correctly allowed to have different presence rules. This also makes an existing, previously racy
behavior deterministic: before this move, whether a rapidly-superseded message's
`turn.message_received` line appeared depended on whether its task got scheduled and ran far enough
into `answer_faq()` before being cancelled; now it always appears, every time, before any
cancellation-relevant code runs at all.

**Alternatives considered**:
- *Leave it inside `answer_faq()`, add a second, similar log line at the top of
  `classify_intent_node`*: rejected — two events carrying near-identical information (the same
  merged message) for one turn is confusing noise, not the one canonical "here's what's being
  processed" line a reader should be able to rely on.
- *Emit it from inside `graph.py`, at the top of the graph's execution, rather than from
  `api/chat.py` before the graph starts*: would also satisfy "before either node runs," but
  `api/chat.py`'s `_event_stream` is where `merged_history` is actually computed — logging it there
  avoids threading that value into the graph's entry point just to log it a moment later, and keeps
  the graph itself free of a concern (this log line) that isn't really about graph execution at all.
  It would also lose the "fires even for a message whose turn is cancelled before the graph task is
  even created" property above, since the graph itself never starts for such a message.

**This corrects this research item's own prior version**'s *mechanism* description (not its
decision), corrected in the same pass as research.md #5/#9's refactor: `merged_history`/
`reply_to_message_ids` are no longer computed by a single `build_history_messages(history_rows,
message, turn_id)` call. `_event_stream` now appends the just-persisted patient message onto
`history_rows` itself (`history_rows = [*history_rows, patient_message]`, using
`chat_repository.create_message`'s own return value — previously discarded), then calls
`history.split_into_bursts(history_rows)` followed by `history.derive_reply_to_message_ids(bursts)`
to get `reply_to_message_ids`. `turn.message_received`'s own `message=message` field is computed
independently, from the original request payload, same as before. The *decision* this research item
records — that the event fires from `_event_stream`, unconditionally, before the graph task is
created — is unchanged; only the functions that compute its `message_ids_unified` field changed
names and shape. This also fixes the stale `reply_source_ids` name above (already renamed to
`reply_to_message_ids` in code well before this refactor; the research doc just hadn't caught up).

## 9. `_GraphState`/`run_turn` shrink to `bursts` + `reply_to_message_ids` — decoupling `api/chat.py` from node internals

**Decision**: `graph.py`'s `_GraphState` TypedDict shrinks from five fields
(`history_rows`, `message`, `turn_id`, `merged_history`, `reply_to_message_ids`) to two: `bursts:
list[list[Message]]` and `reply_to_message_ids: list[str]`. `run_turn(...)`'s signature shrinks to
match: `(qdrant_client, voyage_client, anthropic_client, bursts, reply_to_message_ids)`, dropping
`history_rows`/`message`/`turn_id`/`merged_history` as separate parameters. `classify_intent_node`
now calls `bound_to_last_n_turns(state["bursts"], n=5)` then `to_claude_messages(...)` itself, rather
than receiving a pre-bounded, pre-formatted `merged_history` computed outside the graph;
`answer_faq_node` calls `to_claude_messages(state["bursts"])` (unbounded) itself, rather than
receiving `merged_history` as a ready-made argument. `api/chat.py` no longer computes
`merged_history` at all — it only ever produces `bursts` (via `history.split_into_bursts`) and
`reply_to_message_ids` (via `history.derive_reply_to_message_ids`), the two pieces of information
that are actually about *this turn* rather than about how any one node happens to want its context
formatted.

**Rationale**: The old five-field `_GraphState` leaked each node's own formatting choice
(`classify_intent_node`'s bounded window vs. `answer_faq_node`'s unbounded one) up into
`api/chat.py`, which had no reason to know either node's internal context-shaping logic — it just
needed to hand the graph "the conversation so far" and "what this turn answers." With
`bound_to_last_n_turns`/`to_claude_messages` now separable, reusable operations (research.md #5/#9's
split), each node can call exactly the operations it needs directly, and `_GraphState` only has to
carry the one shared input (`bursts`) both nodes format differently, plus the one value neither node
derives on its own (`reply_to_message_ids` - since `answer_faq_node` needs it but doesn't need to
recompute it from `bursts`, having already been computed once in `api/chat.py` off the exact same
`bursts`). This is a direct application of Constitution Principle VII (SRP/encapsulation): a caller
one layer up from the graph shouldn't need to know *how* a node built its own Claude-format context,
only what raw material the turn is made of.

**Alternatives considered**:
- *Raw-history-only state (`bursts` alone, each node independently calling
  `split_into_bursts`/`derive_reply_to_message_ids`/`bound_to_last_n_turns`/`to_claude_messages` as
  needed, `api/chat.py` passing only the unbounded `history_rows`)*: rejected — `reply_to_message_ids`
  is exactly the same computation (`derive_reply_to_message_ids` over the *same* `bursts`) for both
  `api/chat.py`'s own `turn.message_received` log call and `answer_faq_node`'s `reply_to_message_ids`
  argument; computing it twice from scratch (once in `api/chat.py` to log it, once again inside the
  graph) is needless duplicate work for a pure function whose input doesn't change between the two
  call sites, and reintroduces exactly the consistency risk research.md #5/#9 fixed - two separately
  computed values that are supposed to describe the same trailing burst, with nothing tying them
  together structurally.
- *Two separately-computed fields for "ids" vs. "content" (e.g. `_GraphState` carrying
  `reply_to_message_ids` *and* a separately precomputed, unbounded `formatted_history:
  list[MessageParam]`, with only the bounding left to `classify_intent_node`)*: closer to the final
  shape, but still asks `api/chat.py` to make a node-shaped decision (calling `to_claude_messages`
  itself, on `answer_faq_node`'s behalf) that isn't really its concern - `api/chat.py` doesn't care
  what Claude-message shape either node needs, only what conversation this turn is part of. Rejected
  for the same leaked-node-internals reason as the original five-field state, just less of it.
- *Keep the five-field state, but rename `merged_history`/derive it more clearly*: doesn't address
  the actual problem (`api/chat.py` computing a node-internal value on the graph's behalf), only its
  naming - rejected as a non-fix.
