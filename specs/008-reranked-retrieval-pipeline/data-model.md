# Phase 1 Data Model: Reranked Retrieval Pipeline

Three layers change: one persisted column, one wire field, and the in-process types the pipeline
passes between its stages. Qdrant's schema does not change at all.

---

## 1. Persisted — `messages` (PostgreSQL, `visitdoc_chat`)

| Column | Before | After |
|---|---|---|
| `grounded` | `BOOLEAN NULL` | **dropped** |
| `faq_verdict` | — | `VARCHAR(32) NULL` |

### `FaqVerdict` — a Python StrEnum, not a database type

The column is a plain string, matching `messages.sender` and `messages.attention_mark`. The closed
set is enforced by mypy at every call site; the database stores the value. This is what
`docs/python-style-guide.md` prescribes, and it means a further verdict costs no migration.

| Value | Meaning | Reached when |
|---|---|---|
| `answered` | Answered from a reranked shortlist | Both gates passed |
| `answered_unreranked` | Answered from the similarity survivors, reranking unavailable | Similarity gate passed, reranking failed (FR-010) |
| `abstained_empty_corpus` | No corpus to search | Session publishes no live revisions |
| `abstained_empty_pool` | Corpus searched, nothing matched at all | Live revisions exist, the search returned no chunk of them |
| `abstained_similarity_floor` | Corpus searched, nothing scored ≥ the similarity floor | Pool non-empty, no survivor |
| `abstained_rerank_floor` | Candidates existed, none scored ≥ the rerank floor | Reranker answered "none of these" |

**Nullability carries meaning**: NULL means no FAQ specialist ran (a booking-only reply, a patient
message, a staff message). FR-022 requires absent rather than defaulted, and NULL is what `grounded`
already meant in that position — so the null semantics survive the migration unchanged.

**Migration** (`alembic`, one revision):

1. `ALTER TABLE messages ADD COLUMN faq_verdict VARCHAR(32) NULL`
2. `ALTER TABLE messages DROP COLUMN grounded`

**No backfill** (FR-024): `messages` is empty in both `visitdoc_chat` and `visitdoc_chat_test`,
verified at planning time. The downgrade is the same swap in reverse, also without translation.

Both directions **discard** the column they replace, and both are correct only against an empty
table (FR-024a) — a surviving row would come out with a NULL verdict, which the schema reads as *no
FAQ specialist ran*. The migration docstring must say this outright; a migration that silently
mislabels a row is worse than one that refuses to run.

---

## 2. Wire types (`chat/domain/schemas.py`)

### `FaqVerdict` (str enum)
Mirrors the database enum, one value per row above. Serialized as its string value.

### `ChatDoneEvent`

| Field | Before | After |
|---|---|---|
| `grounded` | `bool \| None` | **removed** |
| `faq_verdict` | — | `FaqVerdict \| None` |
| `citations` | `list[Citation]` | unchanged (FR-016a — still sent on both paths) |
| `message`, `answer_source`, `type` | — | unchanged |

### `MessageOut`

| Field | Before | After |
|---|---|---|
| `grounded` | `bool \| None = None` | **removed** |
| `faq_verdict` | — | `FaqVerdict \| None = None` |
| `citations` | `list[Citation] \| None = None` | unchanged |
| `attention_mark`, `id`, `sender`, `content`, `created_at` | — | unchanged |

### `Citation`
**Unchanged.** `entry_id`, `chunk_index`, `chunk_text`. Deliberately gains no score field: FR-023g
keeps numbers out of the console, and `Citation` is what the console renders (research.md #6).

---

## 3. In-process types

### `ScoredChunk` (new — `chat/rag/pipeline.py`)

A retrieved chunk carried through both stages, accumulating scores.

| Field | Type | Notes |
|---|---|---|
| `faq_entry_id` | `int` | |
| `chunk_index` | `int` | |
| `chunk_text` | `str` | Full text; truncation is a logging concern only (FR-027a) |
| `similarity_score` | `float` | Always present — every chunk was retrieved by vector search |
| `rerank_score` | `float \| None` | `None` means *no rerank score was obtained*, never "scored zero" (FR-025a) |

Frozen dataclass. Replaces `FaqResult.chunk_scores`, a positional list zipped against `citations`
with `strict=True`; two scores per chunk cannot survive that shape (research.md #6).

### `RetrievedChunk` (existing — `chat/repositories/qdrant_repository.py`)
Unchanged. It is the repository's return type; `pipeline.py` lifts it into `ScoredChunk` at the
boundary, so the Qdrant client's shape stays out of the pipeline.

### `GateResult` (new — `chat/rag/pipeline.py`)

What one gate produces. Frozen dataclass.

| Field | Type | Notes |
|---|---|---|
| `kept` | `list[ScoredChunk]` | Survivors, in the order that gate scores by |
| `dropped_by_floor` | `list[ScoredChunk]` | Rejected for scoring below the floor |
| `dropped_by_cap` | `list[ScoredChunk]` | Cleared the floor, lost to the cap |

Two rejection lists rather than one with a reason field, because a reader tuning a threshold only
ever looks at one of them: dropped-by-floor says the bar is too high, dropped-by-cap says it is too
low (FR-028). It is also what `considered` in the retrieval log is derived from — from `kept`, never
from a candidate's position, since the two agree only while every candidate inside the cap also
clears the floor.

### `FaqVerdict.answered` (property)

`True` for `answered` and `answered_unreranked`, `False` for the four abstentions. The single
predicate every caller branches on, so "did this turn answer?" is asked in one place rather than
re-derived from a set membership test at each call site.

### `PipelineOutcome` (new — `chat/rag/pipeline.py`)

What the gates produce, and the single thing `answer_faq` branches on.

| Field | Type | Notes |
|---|---|---|
| `verdict` | `FaqVerdict` | |
| `survivors` | `list[ScoredChunk]` | Empty for every abstention; ordered by the last stage that ran |
| `considered` | `list[ScoredChunk]` | What the similarity gate passed — for the log |
| `observed` | `list[ScoredChunk]` | The full pool, in score order — for the log |

**Invariant**: `survivors` is non-empty **iff** `verdict` is `answered` or `answered_unreranked`. One
value, one meaning: nothing has to consult both fields to know whether the turn answered.

### `FaqResult` (existing — `chat/agent/compose_answer.py`)

| Field | Before | After |
|---|---|---|
| `grounded` | `bool` | **removed** |
| `verdict` | — | `FaqVerdict` |
| `chunk_scores` | `list[float]` | **removed** |
| `scored_chunks` | — | `list[ScoredChunk]` |
| `answer_text`, `citations` | — | unchanged |
| `scored_citations()` | zips two lists | rebuilt from `scored_chunks`; emits both scores (FR-025a) |

---

## 4. Configuration (`chat/core/config.py`)

| Setting | Default | Requirement |
|---|---|---|
| `RETRIEVAL_POOL_SIZE` | `25` | FR-002a |
| `SIMILARITY_FLOOR` | `0.3` | FR-003 |
| `SIMILARITY_CAP` | `5` | FR-002 |
| `RERANK_FLOOR` | `0.58` | FR-007 — the midpoint of 0.520–0.636, a band the calibration set scores identically (10/10 answerable, 9/10 unanswerable). The two classes **overlap**, so no floor separates them and this is a choice of which mistake to make. Superseded the provisional 0.4, which scored 6/10 on abstentions. See `calibration/questions.md`. |
| `RERANK_CAP` | `3` | FR-007 |
| `RERANK_TIMEOUT_SECONDS` | `5.0` | FR-010a |
| `RERANK_MODEL` | `"rerank-3"` | research.md #1 |

All five tunables of SC-006 are here, so a threshold change is an env change (SC-006). `GROUNDEDNESS_THRESHOLD`
in `rag/groundedness.py` is deleted along with the module.

---

## 5. Unchanged, stated so the blast radius is explicit

- **Qdrant**: collection, vector size, payload schema (`ChunkPayload`), payload indexes, and the
  write path are all untouched — and so is `qdrant_repository.py` itself, which already accepts
  `limit` as a parameter. What changes is the **value its caller passes** (`retriever.py`), from the
  default 5 to `RETRIEVAL_POOL_SIZE` (FR-034, FR-002a).
- **Chunking**: `chunk_content()` and its constants — size, overlap, boundary window,
  degenerate-chunk filtering — unchanged, and nothing is re-indexed (FR-034).
- **FAQ entries and revisions**: `FaqEntry`, `FaqChunk`, the additive-revision save, the publish
  commit, deletes. Untouched.
- **Escalation**: `EscalationReason.CORPUS_COULD_NOT_ANSWER` and the `corpus_could_not_answer`
  attention mark carry all four abstentions (FR-019, FR-021a). No new reason, no new mark.
