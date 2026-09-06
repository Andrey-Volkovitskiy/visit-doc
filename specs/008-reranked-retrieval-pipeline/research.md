# Phase 0 Research: Reranked Retrieval Pipeline

Six unknowns, each closed to a decision. The one the spec deliberately left open — the rerank
floor's default — is closed here only to a *starting* value and the procedure that fixes it, which
is what the spec asked for (Assumptions; SC-008).

---

## 1. Which reranker

**Decision**: **Voyage AI `rerank-3`**, called through the `AsyncClient` this service already
holds for embeddings.

**Rationale**: Voyage is already the embedding provider — same vendor, same API key
(`VOYAGE_API_KEY`), same client object, same pooled `aiohttp` session bound per request by
`api/dependencies.py:get_voyage_client`. `voyageai>=0.5.0` is already in `services/chat`'s
dependencies and its `AsyncClient` already exposes `rerank(query, documents, model, top_k,
truncation)`. So the entire dependency cost of this phase's headline feature is **zero new packages,
zero new credentials, zero new client lifecycle**. That is not a small thing against a constitution
whose first principle is scope discipline.

`rerank-3` is the current generation and is what this feature ships on. The model id is a setting
(`RERANK_MODEL`), so moving to a lighter or newer variant is a config change, not a code change —
which is deliberate, because the one number this phase cannot pin in advance (the rerank floor,
below) is a property of the model, and a model swap has to be cheap enough that re-calibrating is the
only real cost.

**Two things about the model to confirm at implementation, not assume**: that `rerank-3` is spelled
exactly that way in the provider's API, and that its `relevance_score` is normalized to [0, 1] as
earlier Voyage rerank models are. The first is a one-line check the first live call makes for you;
the second is what §3's starting floor rests on, so if the scale differs, the starting value changes
with it and the calibration sweep is what settles it either way.

**Alternatives considered**:
- **Cohere Rerank** — a strong cross-encoder, but a second vendor, a second key, a second client, and
  a second failure mode to document, to buy nothing this corpus can measure.
- **A local cross-encoder** (e.g. a sentence-transformers MiniLM) — no API cost and no network
  failure mode, but it drags `torch` into a service whose image is currently pure-Python, and it
  would make the fallback path (FR-010) nearly unreachable, i.e. untested in practice.
- **An LLM-as-reranker prompt** to Claude — no new dependency at all, but it is a generation call
  wearing a scoring hat: slower, more expensive, non-deterministic scores, and it would put a
  model's free-text judgment where the spec asks for a number to threshold. Rejected on the same
  grounds the constitution rejects free-text parsing for routing.

**Wire shape** (confirmed by inspection, not documentation): `rerank()` returns a `RerankingObject`
with `.results: list[RerankingResult]`, each carrying `index` (into the documents list passed in),
`document`, and `relevance_score`. Results come back **sorted by relevance, already truncated to
`top_k`**. `relevance_score` is normalized to [0, 1].

---

## 2. How the 5-second deadline is imposed

**Decision**: `asyncio.wait_for(...)` around the whole `rerank()` coroutine, at
`RERANK_TIMEOUT_SECONDS` (default 5.0). The client is constructed with `max_retries=0`.

**Rationale**: FR-010a requires the deadline to bound *obtaining the scores*, not one attempt at
them, and the spec's Assumptions say so explicitly. `voyageai`'s client-level `timeout=` parameter is
per-HTTP-request, so with any internal retry the wall-clock bound would be `timeout × attempts` —
the exact failure mode FR-010a forbids. `wait_for` bounds the whole thing including retries, and it
is provider-agnostic, so swapping the reranker does not re-open the question.

The existing shared client is **not** reused for this: it is constructed once at startup in
`main.py` for embeddings. A separate `AsyncClient(api_key=..., max_retries=0)` for reranking keeps
the embedding path's behavior untouched, and costs one more object at startup.

**Why the timeout is not treated as "unknown outcome"**: `.claude/CLAUDE.md`'s rule that a timeout
never proves the server did nothing governs **writes**. Reranking is a pure read with no side effect;
abandoning it leaves nothing half-done, and the fallback is a complete, correct answer rather than a
guess about state. This is recorded here because the rule is load-bearing elsewhere in this codebase
and a reader is entitled to see that it was considered rather than overlooked.

**Alternatives considered**: the client's own `timeout=` (rejected above); no deadline at all
(rejected in spec clarification — a hung dependency stalls the turn indefinitely).

---

## 3. The rerank floor's starting value

**Decision**: **`RERANK_FLOOR = 0.58`**, the midpoint of the band the calibration set cannot
distinguish within. The first version of this section shipped `0.4` as an explicitly provisional
starting point; the calibration replaced it. `0.4` was too low — 10/10 answered but only **6/10**
abstained, failing SC-008.

Two things the calibration established that matter more than the digit:

- **The classes overlap.** The worst unanswerable question reranks at 0.6953, *above* the weakest
  correct answer at 0.6367. No threshold separates them, so this setting does not choose between
  right and wrong — it chooses which kind of mistake to make.
- **A wide band scores identically.** Anything from 0.520 to 0.636 gives 10/10 and 9/10, so there is
  no optimum in the data. 0.58 is that band's midpoint, which is the only defensible way to pick
  inside it: it maximizes the distance to the nearest error on both sides at once.

*(A first pass shipped 0.55 and justified it by margin to the weakest correct answer alone — an
argument that ignored the 0.030 of margin it left on the other side, and which would have selected
the midpoint had it been applied to both. The sweep in `calibration/questions.md` is the record.)*

**Rationale**: Voyage's `relevance_score` is normalized to [0, 1] (confirm for `rerank-3`, §1) and is
*not* comparable to a cosine similarity — a cross-encoder's mid-range is genuinely mid-range, where cosine's is not. 0.4 is chosen
as a starting point that is high enough to reject the vocabulary-overlap chunks this phase exists to
catch and low enough not to abstain on a good answer, which makes it a defensible place to begin
sweeping from — not a claim to be right. The spec is unusually explicit that this number is unset
until measured; shipping it undocumented as if it were chosen would be the failure mode.

**How it gets fixed**: run `calibration/questions.md`'s 20 questions against the default corpus,
sweeping the floor, and take the value satisfying SC-008 (≥9 of 10 answerable questions answered with
a correct citation, ≥9 of 10 unanswerable abstained). Record the chosen value and the measurement
beside the question set.

**Alternatives considered**: deriving the floor from a percentile of observed scores (adaptive, and
therefore a threshold that changes meaning as the corpus changes — untunable and unreproducible);
shipping no default and requiring configuration (a checkout that does not run is worse than one that
runs at a provisional setting).

---

## 4. Where the observation pool is bounded

**Decision**: `qdrant_repository.search()`'s `limit` becomes the pool size (25). The similarity floor
is applied **in application code**, not pushed into Qdrant's `score_threshold`.

**Rationale**: FR-028 requires the log to distinguish candidates dropped *by the floor* from those
dropped *by the cap*, and FR-027 requires every pool member to be logged with its score. A
`score_threshold` on the search would delete the below-floor candidates before anything could record
them — the floor would become unobservable and therefore untunable, which is the exact thing
FR-002a exists to prevent. Filtering after the fact costs nothing at this scale: 25 points with
short payloads.

The session/revision filter stays exactly as it is — a `Filter` on `session_id` and `revision`,
index-backed by the payload indexes `_create_payload_indexes` already builds. Widening `limit`
changes no scoping (`.claude/CLAUDE.md`: every query carries its session predicate).

**Alternatives considered**: `score_threshold` in the search (rejected above); two searches, one wide
one narrow (two round trips for data one already returned).

---

## 5. How the verdict is stored

**Decision**: a **plain nullable `VARCHAR(32)`** column `messages.faq_verdict`, with the closed set
enforced in Python by the `FaqVerdict` StrEnum, replacing `messages.grounded` in one migration that
**drops and adds, with no backfill**.

**Rationale**: *(corrected during implementation — the first version of this section claimed the
repo uses native PostgreSQL enums for closed sets like spec 007's attention marks, and that a
VARCHAR would be inconsistent. Both halves were wrong: `messages.attention_mark` is `String(32)`,
`messages.sender` is `String(16)`, and the database contains no enum types at all.)*

`docs/python-style-guide.md` prescribes exactly this shape and says why: the enum is "a *Python-level*
constraint, not necessarily a database one — a field can and often should stay a plain DB column (no
native SQL `ENUM` type) so a future legal value never needs a migration, while every call site is
still required to pass an enum member, never a raw string, in application code." A sixth verdict — a
new gate, a second degradation mode — would otherwise need an `ALTER TYPE` before it could be
written. Type safety is not lost: mypy checks every assignment against `FaqVerdict`, which is what
actually prevents a typo, since a native enum would only catch it at write time in production.

Nullable because FR-022 requires *absent*, not defaulted, for a turn with no FAQ half — and NULL is
what `grounded` already means there, so the semantics of the column's null carry across unchanged.

**No backfill**, because there is nothing to fill: `messages` is empty in both `visitdoc_chat` and
`visitdoc_chat_test`, checked at planning time rather than assumed. A mapping from `grounded` to a
verdict would be code no row ever executes, asserting a correspondence nobody can verify — and the
one it would have to assert (`false` → `abstained_similarity_floor`) is a guess about which gate a
pre-phase turn failed, in a phase whose whole point is that those gates are now distinguishable.

The consequence is named rather than left implicit: this migration is correct **only** against an
empty table. A surviving row would come out with a NULL verdict, which the schema reads as *no FAQ
specialist ran* — a false statement about a turn that had one. The migration says so in its own
docstring, and the downgrade is a plain column swap carrying the same caveat, so neither direction
pretends to preserve something it does not.

**Alternatives considered**: keeping `grounded` alongside the verdict (two sources of truth for one
fact, which FR-023 forbids outright); a **native PostgreSQL enum** (rejected above — it would be the
codebase's first, against its own written style guide, and would make every future verdict a
migration); a backfill written defensively for rows that might exist somewhere (untested code whose
only effect would be to make a mislabelled row look deliberate).

---

## 6. What replaces `FaqResult.chunk_scores`

**Decision**: a `ScoredChunk` domain type carrying the chunk, its similarity score, and its rerank
score (`None` when unreranked). `FaqResult` holds `list[ScoredChunk]`; `Citation` (the wire type)
stays exactly as it is.

**Rationale**: `chunk_scores` is a positional list zipped against `citations` with `strict=True` — a
correspondence held together by an assertion at the point of use. One score per chunk survived that;
two will not, and FR-025a needs both, with the rerank one genuinely absent (not zero, not the
similarity score) on an unreranked turn. A record with two optional-by-meaning fields says that
directly, and the zip disappears.

`Citation` deliberately does **not** gain the scores: FR-023g keeps every number out of the console,
and `Citation` is the type the console renders. Scores travel to the log record from `ScoredChunk`,
which never leaves the server.

**Alternatives considered**: a second parallel list `rerank_scores` (three lists to keep aligned);
scores on `Citation` (would put numbers on the wire that FR-023g says nothing may render, inviting
exactly the second copy FR-023g was written to prevent).
