# Phase 0 Research: Grounded FAQ Chat

Confirmed by the user directly (not researched): **Claude API** for generation, **asyncpg** as the
app's async PostgreSQL driver, **a bare Claude API call for Phase 0** with LangGraph deferred to
Phase 1 (see Decision 9), and **plain SQLAlchemy 2.0 declarative models** rather than SQLModel (see
Decision 10). The decisions below resolve everything else needed to move from spec to design.

## 1. Embedding provider (Claude API has no embeddings endpoint)

**Decision**: Voyage AI (`voyage-3-lite` or `voyage-3`), via the `voyageai` Python SDK.

**Rationale**: Anthropic doesn't offer an embeddings API, so RAG over Qdrant needs a separate
embedding provider regardless of which LLM generates the answer. Voyage AI is Anthropic's own
recommended embedding partner for Claude-based RAG stacks, has strong retrieval-quality benchmarks
at low cost, and needs only one more API key (`VOYAGE_API_KEY`) — no local model runtime to manage.
Matches "route models deliberately" from `docs/ROADMAP.md`'s AI-role practices: a cheap, purpose-built
embedding model, reserving Claude for generation.

**Alternatives considered**:
- *Local embedding model (e.g. `sentence-transformers`)* — no external API dependency or per-call
  cost, but pulls in a heavy ML runtime (torch) for a portfolio-scale FAQ corpus where that cost
  isn't justified, and adds a model-loading concern to a service that otherwise has none.
- *OpenAI embeddings* — solid quality, but introduces a second unrelated LLM vendor into a project
  whose documented AI stack (`.claude/CLAUDE.md`) is Claude-centric, with no other reason to hold an
  OpenAI account/key.

## 2. Sync driver for Alembic migrations

**Decision**: `psycopg` (v3), sync mode, used only by Alembic's migration engine. The app itself
uses `asyncpg` end to end (already confirmed).

**Rationale**: Alembic's migration runner is synchronous by design; rather than wrapping it in
`asyncio.run()` to reuse `asyncpg`, pairing one sync driver for migrations with `asyncpg` for the app
is the conventional SQLAlchemy 2.0 pattern and keeps `env.py` simple. `psycopg` v3 is actively
maintained (unlike `psycopg2`, which is in maintenance mode) and needs no separate C build step
beyond its binary wheel.

**Alternatives considered**:
- *`psycopg2` for migrations* — the older, more ubiquitous choice, but effectively legacy; no
  reason to add it when `psycopg` v3 covers the same need.
- *Async Alembic (`asyncpg` everywhere via `run_sync`)* — technically possible but adds
  boilerplate to `env.py` for no benefit at this scale.

## 3. Chunking strategy

**Decision**: Fixed-size character chunking (~1,000 characters, ~150-character overlap), splitting
on paragraph/sentence boundaries where possible rather than mid-word.

**Rationale**: `docs/ROADMAP.md` calls for "defensible chunking" without mandating a specific
algorithm. Given the 20,000-character entry cap (FR-015) and expected policy-document style content,
fixed-size chunking with overlap is simple, has no extra dependency beyond what LangGraph/LangChain
already bring in, and is a defensible, explainable baseline. Semantic/recursive chunking is a
reasonable later refinement, not a Phase 0 requirement.

**Alternatives considered**:
- *Semantic chunking (embedding-similarity boundaries)* — better boundary quality, but adds
  complexity and an extra embedding pass per write; premature for this phase's scale.
- *One chunk per entire FAQ entry (no chunking)* — simplest, but defeats "defensible chunking" and
  degrades retrieval precision once entries get long, undermining citation specificity (FR-003).

## 4. Groundedness check (this phase's version)

**Decision**: A pre-generation similarity-threshold gate on the top retrieved chunk(s). If the best
match's similarity score is below a fixed threshold, the agent abstains (FR-005) and never calls
Claude. If it clears the threshold, generation proceeds and the answer is considered grounded.

**Rationale**: `docs/ROADMAP.md` lists a formal (e.g. LLM-as-judge) groundedness check under Phase 1,
but the constitution's Principle V makes grounding + abstention non-negotiable from the start. A
similarity-threshold gate satisfies that principle without the cost/latency of a second LLM call on
every turn, keeping this phase's scope minimal per Principle I. It also means abstention is nearly
free — no wasted generation call for out-of-scope questions.

**Alternatives considered**:
- *LLM-as-judge groundedness check (second Claude call scoring the draft answer against context)* —
  more rigorous, matches Phase 2's eventual eval methodology, but doubles LLM calls and latency for
  every single question; explicitly a Phase 1/2-level technique per the ROADMAP, not required yet.
- *No gate — always generate, let the prompt instruct "say you don't know if unsure"* — cheapest,
  but relies entirely on the model's self-restraint with no structural guarantee; violates the
  "MUST abstain" wording of FR-005/Principle V.

## 5. Reranking

**Decision**: Deferred to Phase 1 — this phase uses plain top-k vector retrieval (post-threshold
gate), no separate reranking step.

**Rationale**: `docs/ROADMAP.md` explicitly lists "a reranking step" under Phase 1's RAG work, not
Phase 0's walking-skeleton description. Adding it now would pull Phase 1 scope forward, which
Constitution Principle I prohibits without justification, and at this phase's expected corpus size
(single-digit to low-hundreds of entries) top-k retrieval quality is unlikely to be the bottleneck.

## 6. How citations are produced

**Decision**: Citations are derived structurally from retrieval, not extracted from the LLM's
output. The set of FAQ entries whose chunks were placed in Claude's context *is* the citation list
returned alongside the streamed answer — the system already knows this deterministically before
generation starts.

**Rationale**: Satisfies Constitution Principle IV's spirit (no free-text parsing to guess
citations) more reliably than asking the LLM to self-report sources, which risks the model citing an
entry that wasn't actually in its context (a hallucinated citation) or omitting one that was. It also
avoids a design conflict between "stream the answer as plain text" (FR-004) and "return structured
output" (Principle IV) — the answer streams as free text throughout, and the citation list is
attached as a final structured payload the frontend already has enough information to construct
independently of what the model says.

**Alternatives considered**:
- *Ask Claude to emit structured citations via tool use/forced JSON* — more "the model verified its
  own sources," but adds a second reasoning burden to the same call and a real risk of the model
  citing content not actually in context; rejected as unnecessary given the system already knows
  ground truth.

## 7. Streaming transport (chat endpoint)

**Decision**: `POST /chat` returns a `StreamingResponse` of newline-delimited JSON (NDJSON) — a
sequence of `{"type": "token", "text": "..."}` lines, followed by one
`{"type": "done", "citations": [...], "grounded": true}` line. The frontend reads it via `fetch` +
`ReadableStream`, not the browser's native `EventSource`.

**Rationale**: Native `EventSource`/SSE only supports GET requests with no custom body, but the chat
endpoint needs a POST body (the visitor's message per FR-001). NDJSON over a normal streamed HTTP
response works with a plain POST + `fetch`, needs no extra frontend library, and cleanly carries both
the token stream and the final structured citations/grounded payload (see #6) without inventing a
second channel.

**Alternatives considered**:
- *Server-Sent Events with a POST-then-GET handshake, or a POST-capable SSE polyfill* — works, but
  adds a library/pattern for no real benefit over plain NDJSON at this scale.
- *WebSocket* — bidirectional, but `docs/ROADMAP.md` only mentions WebSocket passthrough under the
  Phase 3+ API Gateway; a single request/response streamed reply doesn't need a persistent
  bidirectional channel, so this would be over-engineering for Phase 0.

## 8. Frontend test tooling

**Decision**: Vitest + React Testing Library for `services/frontend` component tests.

**Rationale**: Standard, low-friction pairing for a Vite + React project; Vitest reuses the Vite
config already needed for the app itself, so no parallel build config is introduced.

## 9. Agent orchestration without a framework

**Decision**: The Phase 0 agent step is a plain async function (`agent/answer_faq.py`) that calls
the Claude API directly — embed question, retrieve, gate on similarity threshold, generate/stream.
No LangGraph in this phase.

**Rationale**: A single linear retrieve→gate→generate path has no branching to justify a graph
framework yet — `docs/ROADMAP.md`'s Phase 0 description has been updated to match (agent step
implemented as a plain function call, LangGraph introduced in Phase 1). Phase 1 is where parallel
specialist nodes and a merge step for mixed-intent messages first exist, which is what actually
motivates adopting LangGraph (per Constitution's Technology Foundations — LangGraph remains the
fixed overall stack choice, only its introduction point moves). Keeping the function's shape
(`answer_faq(message) -> AsyncIterator[...]`) close to what a future LangGraph node would call means
Phase 1 wraps this logic rather than rewriting it.

**Alternatives considered**:
- *Adopt LangGraph now for a trivial one-node graph* — no functional benefit at this phase (a graph
  with one node and no edges besides START→END is pure overhead), and pulls Phase 1 tooling forward
  without the branching that justifies it, conflicting with Constitution Principle I.

## 10. Plain SQLAlchemy 2.0 instead of SQLModel

**Decision**: `FaqEntry` is a plain SQLAlchemy 2.0 declarative model (`domain/models.py`), not a
SQLModel class. Pydantic request/response DTOs stay in `domain/schemas.py`, as already planned.

**Rationale**: SQLModel is still pre-1.0 and lags SQLAlchemy 2.0's own typing/feature surface, and
its main selling point — one class doing double duty as both ORM model and Pydantic schema — was
never actually being used here, since the plan already keeps ORM models and API DTOs in separate
files. Plain SQLAlchemy 2.0 is the more mature, more customizable foundation, which matters once
`scheduler`'s richer schema (exclusion constraints, range types) needs it.

**Alternatives considered**:
- *SQLModel* — less boilerplate for the simplest cases, but adds a second, less mature ORM-adjacent
  dependency for a benefit (`Model = ORM row = API schema`) this codebase doesn't take advantage of.

## 11. Integer primary key instead of UUID for `FaqEntry.id`

**Decision**: `faq_entries.id` is a Postgres `IDENTITY`/`SERIAL` integer, not a UUID. `FaqEntry.id`
(the domain/API type) follows suit.

**Rationale**: UUIDs mainly buy protection against ID enumeration and safe merging of
independently-generated rows — neither applies here: `GET /faq` already lists every entry publicly
(FR-008), so there's nothing to hide by making IDs non-sequential, and `faq_entries` isn't merged
with data from another source. A plain integer PK is simpler, smaller to index, and considerably
more pleasant for demo `curl` commands (`DELETE /faq/3` vs. a UUID) — worthwhile for a portfolio
project meant to be poked at directly.

**Alternatives considered**:
- *UUID primary key* — the safer default once auth/multi-tenant scoping exists (Phase 1+), but no
  benefit at this phase's open, single-tenant, fully-listable API.

## 12. Delete support for FAQ entries

**Decision**: `DELETE /faq/{entryId}` (FR-016) performs a hard delete: remove the entry's
`FaqChunk` points from Qdrant first, then delete the `faq_entries` row. Deleting an unknown ID
returns `404`.

**Rationale**: The FAQ content API was already described as "CRUD" (plan.md Summary) but previously
omitted the D; staff need a way to retract stale or incorrect policy content, not just supersede it
via update. Deleting Qdrant chunks before the Postgres row (rather than the reverse) preserves the
"deleted content must stop being retrievable" invariant even if the operation fails partway —
worst case a row survives with no indexed chunks (harmless, fixable via update), rather than
orphaned chunks continuing to ground answers for an entry that no longer exists.

**Alternatives considered**:
- *Soft delete (tombstone flag)* — enables undo/audit trail, but spec.md has no undo requirement
  this phase (Assumptions) and adds a filter every other query must now remember to apply; deferred
  as unnecessary complexity.

## 13. Citations quote `chunk_text` verbatim; no `title` field at all

**Decision**: `FaqEntry` has no `title` field. The `Citation` returned alongside a streamed answer
is `{entry_id, chunk_index, chunk_text}` — the exact retrieved chunk text and its position within
the entry, not a human-authored label.

**Rationale**: A citation's job is to let someone verify groundedness. A title only names the
source; the chunk text *is* the source — returning it verbatim lets a reviewer or automated test
directly diff the streamed answer against the passage it was supposedly grounded in, a much
stronger and more falsifiable signal than trusting a label. It also removes an authoring-burden
field from FAQ entry submission entirely, rather than making it optional with a derived fallback.

**Alternatives considered**:
- *Optional `title` with an auto-derived fallback (e.g. first line of `content`)* — considered
  earlier in this project's design discussion, but superseded by this decision: dropping `title`
  outright is simpler than maintaining a fallback-generation rule, and chunk-text citations make a
  title mostly redundant for this phase's purposes anyway.
- *Truncated `content` excerpt instead of `chunk_text`* — `chunk_text` is already the right-sized,
  purpose-built unit (research.md #3) and is exactly what was placed in the LLM's context, so citing
  anything else risks citing text the model never actually saw.

## 14. Rejecting and filtering meaningless (whitespace/dash/label-only) content

**Decision**: Two layers, matching FR-009 and FR-017, sharing one definition of "meaningless": the
content, after stripping whitespace, dash characters, and any bare `Question:`/`Answer:` labels
(case-insensitive, per FR-014), has nothing left. At submission time, a Pydantic field validator
applies this check to the whole `content` and rejects the entry (422) if nothing meaningful
remains. At chunking/indexing time, the same check is applied per chunk; any chunk that reduces to
nothing is dropped before embedding — never upserted into Qdrant.

**Rationale**: `min_length=1` alone doesn't catch `"---"`, `"   "`, or `"Question:\nAnswer:"` — all
non-empty strings with no informative content, which would otherwise pass validation, get embedded,
and could surface as a nonsensical or empty-looking citation. Filtering degenerate chunks at index
time (rather than at retrieval/generation time) is the simplest way to guarantee FR-017: a chunk
that was never stored can never be retrieved, so there's no separate "ignore it if retrieved" check
to keep in sync elsewhere in the retrieval path. Sharing one definition between the two layers
(rather than a looser per-chunk check) also gives a useful invariant for free: since chunking
partitions `content` in full without discarding characters, and FR-009 already guarantees
`content` has meaningful text somewhere, at least one chunk is always guaranteed to survive
FR-017's filter — an accepted entry can never end up with zero retrievable chunks.

**Alternatives considered**:
- *Filter at retrieval time instead of index time* — would work, but means every retrieval call
  re-checks a property that's static per chunk; filtering once at write time is strictly simpler
  and cheaper, with the same guarantee.
- *Regex/length heuristic only, no character-stripping check* — simpler, but doesn't generalize
  (a submission of `"----------"` is 10 characters and would pass a naive minimum-length-of-3
  check, and `"Question:\nAnswer:"` is 18 characters); stripping the specific meaningless tokens and
  checking what's left is precise rather than heuristic.
- *Checking FR-009 and FR-017 against different definitions of "meaningless"* — considered, but
  rejected: a shared definition is what makes the "at least one chunk always survives" guarantee
  hold; divergent definitions would reopen the question of whether an entry could pass submission
  validation yet still end up with zero retrievable chunks.
