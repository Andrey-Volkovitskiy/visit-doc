# Phase 0 Research: One Message, Several Requests (Phase 1g)

Every decision here is about *where* a change lands, not whether to make it — the spec settled the
behaviour and its five clarifications settled the design questions that had two defensible answers.
What remains is fitting that behaviour into a graph, a classifier and a pipeline that already exist,
without adding a second way to do anything they already do.

---

## D1 — The fan-out lives inside the FAQ node, not in the graph

**Decision**: `answer_faq` runs one pipeline-and-generation task per FAQ segment, concurrently
(`asyncio.gather`), inside the single existing `answer_faq` node. The graph keeps exactly the nodes,
edges and conditional routing it has today.

**Rationale**: `graph.py`'s own invariant is that "every specialist writes a *disjoint* state key, so
concurrent branches need no channel reducer — that error only fires when two branches write the same
key". A per-segment fan-out at the graph level (LangGraph's `Send`) is *n* branches writing one key,
which is precisely the case that needs a reducer. Keeping the fan-out inside the node means one node,
one key, one write, and the routing table, the conditional edges and the merge detection are all
untouched. Concurrency is an implementation detail of the FAQ half, which is what it should be: the
graph's job is which *specialists* run, and there is still exactly one FAQ specialist.

**Alternatives rejected**:
- *`Send`-based map-reduce, one node instance per segment.* Would give per-segment node spans for
  free, which is genuinely attractive for the log. But it needs an `Annotated[..., operator.add]`
  reducer on the FAQ key, which retires the disjoint-key rule the graph is built on; and it makes the
  number of graph branches depend on a model's output, which is a much larger blast radius than the
  problem justifies.
- *Sequential per segment.* Violates FR-032, and would make a two-question turn feel twice as slow
  for no design benefit.

---

## D2 — The classifier returns segments; `intents` becomes a derived view

**Decision**: `IntentClassificationResult` carries `segments: list[RequestSegment]`
(`min_length=1`, `max_length=3`), where a `RequestSegment` is `{intent: IntentLabel, text: str}`.
`intents` survives as a **computed property** returning `[s.intent for s in segments]`.

**Rationale**: the spec requires the router's existing selection to keep working unchanged (FR-001).
`_handoff_reasons`, `_select_specialists`, `_HANDOFF_REASON_BY_INTENT` and the `intent.classified`
event all read `intents` and all keep reading it, so the change is confined to what produces the
value rather than spreading through everything that consumes it. It also keeps one source of truth:
the labels cannot drift from the segments they were derived from, because they *are* the segments.

**Alternatives rejected**:
- *Return both `intents` and `segments` from the model.* Two fields that must agree, decided by a
  model that can return them disagreeing — a validation problem invented for no gain.
- *Replace `intents` everywhere with segment iteration.* A larger diff across five call sites, for a
  view each of them would then have to build itself.

---

## D3 — The cap is structural, with a validation backstop

**Decision**: `maxItems: 3` in the classifier's JSON-Outputs request schema, plus a `max_length=3`
on the Pydantic field, so an over-long list is unrepresentable in the response and rejected if it
somehow arrives. An empty or whitespace-only `text` is likewise a validation failure.

**Rationale**: identical in shape to spec 009's handling of `CLASSIFICATION_FAILED` — excluded from
the request schema's enum *and* re-checked on the parsed result, "so a schema-level regression fails
loudly here instead of silently violating this function's own contract". The same reasoning gives
the same construction: the schema is what makes it not happen, the check is what makes a regression
visible. An invalid result raises `ClassificationFailedError`, which already falls back to the FAQ
path — FR-008's required behaviour, with no new branch.

**Alternatives rejected**:
- *Trim to the first three after parsing.* Silently drops a request the patient made, which FR-006a
  forbids; the segmenter combining them is a decision made with the message in view, trimming is one
  made without it.

---

## D4 — A failed classification synthesizes one segment

**Decision**: the existing `except` branch in `classify_intent_node`, which today sets
`intents = [CLASSIFICATION_FAILED]`, sets one synthetic segment instead:
`{intent: CLASSIFICATION_FAILED, text: <the trailing patient message>}`.

**Rationale**: the fallback must stay exactly what it is today — the whole message down the FAQ path
(FR-009 of spec 009, preserved here). Making the fallback a one-segment turn keeps every downstream
consumer on one shape: nothing has to ask "is this turn segmented or not?", because every turn is.
The verbatim message as the segment text is what makes the fallback byte-identical to today's.

**Alternatives rejected**:
- *A `segments: None` state for the failure path.* A second shape for everything downstream to
  branch on, standing for a situation the one shape already expresses.

---

## D5 — Input isolation reuses `replace_trailing_entry` for both specialists

**Decision**: each specialist substitutes its own segments into the trailing conversation entry via
the existing `history.replace_trailing_entry`. The FAQ node already does this (its `Question:` line
is a substitution, not an append), so the change is that the line carries `segment.text` instead of
`trailing_question(bounded)`. The booking node adopts the same call, putting its own segments where
the raw patient message is today.

**Rationale**: this is what makes FR-020's isolation **structural** rather than instructed — the
other specialist's clause is not in the prompt to be answered, because the entry that carried it was
replaced. It also reuses a function whose docstring already generalizes to this ("a specialist
replaces the trailing entry because it has a prompt of its own to put there"), including the
opening-words fold that "was twice forgotten" at call sites. One mechanism, already tested.

**Alternatives rejected**:
- *Keep the raw message and tell each specialist which part is its own.* Provenance by instruction;
  the model can read the other clause and answer it, which is failure 3 in the spec's own list.
- *A new prompt-assembly helper per specialist.* Two copies of a substitution rule that already has
  one home.

---

## D6 — One generation call per FAQ segment (settled in clarification)

**Decision**: each FAQ segment's answer is generated from its own surviving chunks alone; the
per-segment unit of concurrency is therefore *pipeline + generation*, and the composer merges the
answers.

**Rationale**: recorded in the spec's Clarifications — a single prompt carrying two questions and two
shortlists is the same shape as the single query carrying two questions this phase exists to undo,
and it leaves FR-033's provenance resting on the model's obedience. Bounded by the cap at three
generation calls plus one composing call, paid only by turns that genuinely carry several questions.

**Consequence worth naming**: a two-question turn's generation calls are concurrent, so its latency
is one generation plus the compose, not two plus the compose.

---

## D7 — `merge_required` splits into two decisions

**Decision**: the routing-time flag keeps its one honest job — telling the specialists whether to
stream or collect — and is renamed **`specialists_collect`** to say so. Whether the composer actually
*merges* is decided in `compose_answer_node` from the reply parts that exist when it runs. A turn that collected but ends up
with a single part emits that part directly, with no composing call.

**Rationale**: FR-042 creates a case the current flag cannot express. Two FAQ segments make
`merge_required` true at routing time, but if either abstains the FAQ half collapses to *one*
abstention part — and with no booking and no notice, there is then one part and nothing to merge.
Paying for a composing call to paraphrase a constant abstention message would be waste; worse, it
would put a model in front of the one reply the design deliberately keeps model-free. The two
questions ("do specialists stream?" and "is there more than one part?") were the same answer until
this phase and are now different questions, which is exactly the project's "one value, one meaning"
rule: a flag that answers two questions gets split when the answers diverge.

**Alternatives rejected**:
- *Keep one flag and let the composer merge a single part.* A generation call whose input is one
  fixed sentence, and a patient-visible paraphrase of a message that is a constant by design.
- *Decide streaming at compose time too.* Impossible: the specialist has already run by then, and
  streaming is a decision about how it runs.

---

## D8 — The verdict collapse is a pure function

**Decision**: `summarize_verdict(outcomes: list[FaqVerdict]) -> FaqVerdict` in `answer_faq.py`.
Rules, in order: any abstention → the **first abstaining segment in message order** (FR-043); else
any `answered_unreranked` → `answered_unreranked` (FR-041); else `answered`.

**Rationale**: it takes a list and returns a value, so it is testable exhaustively with no client, no
graph and no model — the cheapest possible place to prove FR-041 and FR-043. Keeping it a named
function rather than an inline `if` also means the rule has one home to change when Phase 1h moves
the verdict onto the request.

**Alternatives rejected**:
- *Collapse by pipeline-stage precedence* (empty corpus → empty pool → similarity → rerank). Reads
  as though the turn stopped at a gate that one segment never reached; message order at least
  names a segment that really did stop there.
- *A new "mixed" verdict value.* Adds a seventh value to a closed set that Phase 1h is about to
  restructure, and tells a reader nothing about what to fix.

---

## D9 — Citations are deduplicated where the FAQ half is assembled

**Decision**: dedupe on `(entry_id, chunk_index)`, keeping first appearance in segment order, at the
point `answer_faq` builds the turn's `FaqResult`. Both the single-segment path and the merged path
inherit it.

**Rationale**: one place, before the value is either streamed or handed to the composer, so the two
paths cannot disagree about what the turn cited. `(entry_id, chunk_index)` is already the identity
pair every log event and every gate uses, so no new notion of chunk identity is introduced.

**Alternatives rejected**:
- *Dedupe in the composer.* Only runs on merged turns, and the composer is explicitly not allowed to
  re-report citations — they are carried through, not regenerated.

---

## D10 — Per-segment log attribution rides on `structlog.contextvars`

**Decision**: each segment's task binds `segment=<position>` with `bound_contextvars` for its whole
run, so all six of Phase 1e's events carry it without a single call-site change inside `_run_pipeline`.

**Rationale**: this is exactly how `turn_id` and `node` already reach every event
(`core/correlation.py`, `agent/node_logging.py`), and `structlog.contextvars` is task-scoped —
`correlation.py` says so in its own module docstring — so concurrent segment tasks cannot see each
other's binding. Threading a `segment` parameter through `_run_pipeline`, `_log_retrieval`, both gate
logs and the verdict log would touch every logging call in the module to say something the context
already knows.

**Alternatives rejected**:
- *An explicit parameter through the pipeline.* Six call sites, one of which (`_log_retrieval`) is a
  helper shared with nothing, all changed to carry a value that is constant for the task.
- *An opaque per-segment id.* Rejected in the spec's clarification: position is the join key, and it
  is already the ordering the spec defines.

---

## D11 — Failure propagation is `gather`'s default

**Decision**: `asyncio.gather(*tasks)` with default `return_exceptions=False`: the first failing
segment propagates, siblings are cancelled, and `TurnPipelineError` leaves the node exactly as it
does today.

**Rationale**: FR-037 wants the turn to fail whole, and that is `gather`'s default behaviour — no
new error plumbing, no partial result to decide what to do with. The one thing to get right is that
a cancelled sibling's streaming context closes cleanly, which `async with client.messages.stream(...)`
already guarantees.

**Alternatives rejected**:
- *`return_exceptions=True` plus a manual re-raise.* Same outcome, more code, and it invites a future
  reader to "just use the successful ones", which is the partial serving FR-037 forbids.

---

## D12 — Booking runs once over its joined segments

**Decision**: `handle_booking` receives its segments and joins them into the trailing entry it
substitutes; its tool loop is entered once.

**Rationale**: settled in the spec (FR-022) — two scheduling requests are one piece of work against
one set of records, and the loop already carries the state that sequences them. Running it twice
concurrently would put two tool loops against the same patient's appointments in the same turn, which
is a write-ordering problem invented for no requirement.

---

## D13 — Segmentation quality is prompt work, measured by hand

**Decision**: the segmentation rules (standalone restatement, conservative splitting, no invented
constraints, requests only) are written into the existing classifier system prompt, and their
accuracy is measured once against committed labelled sets with a written procedure — no runner, no
gate.

**Rationale**: precedent is unambiguous. Spec 008 committed a calibration sweep; spec 009 committed
labelled sets, a manual procedure and the result each run produced. Both live under `specs/**`, which
is excluded from ruff and mypy for exactly this reason. Phase 2 is where a live-model suite belongs,
and building one here would start the next phase inside this one.

**What the sets must cover** (from the spec's success criteria): compound questions whose halves are
individually answerable (SC-001), question-plus-booking pairs (SC-002), multi-request messages with a
labelled expected segmentation (SC-003), single-request messages including long and repetitive ones
(SC-004), and multi-request messages carrying an overriding intent (SC-012).

---

## D14 — What this phase deliberately does not build

- **No per-request verdict storage, no migration, no API field.** Settled in clarification: recorded
  means logged.
- **No partial serving.** The FAQ half is all-or-nothing this phase (FR-042), which is what keeps the
  composer's contract untouched.
- **No change to the pipeline's stages, floors, caps or citation shape** — only to how many times it
  runs and what each run is asked.
- **No frontend change at all.** The wire types keep their shape, so nothing downstream of the API
  notices that a turn had three requests in it.
