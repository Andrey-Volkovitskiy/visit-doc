# Data Model: Structured Logging for App/AI Behavior

Log entries are operational/diagnostic output, not persisted domain data (spec.md Key Entities) —
there is no database table here. This document defines the structured shape every entry MUST take
(FR-009) and the specific event types the rest of the spec requires.

## Log Entry (common shape)

Every entry, regardless of event type, carries:

| Field | Type | Notes |
|---|---|---|
| `timestamp` | `datetime` (UTC) | When the entry was emitted |
| `level` | `"info" \| "error" \| "critical"` | Severity tier (research.md #3): `info` = routine turn outcome or successful FAQ operation step; `error` = turn-scoped error (FR-005) or failed FAQ operation (FR-007); `critical` = non-turn-scoped critical event (FR-015) |
| `event` | `str` | Event-type name, one of the fixed set below |
| `turn_id` | `str` (ULID, research.md #2) \| absent | Present on every entry belonging to a chat turn (FR-006); absent for FAQ-operation events; present on a critical event only when FR-018-correlated to a turn |
| `operation_id` | `str` (ULID, research.md #6) \| absent | Present on every entry belonging to a FAQ management operation (FR-021); absent for turn-scoped events; present on a critical event only when FR-018-correlated to a FAQ operation |

`turn_id` and `operation_id` are mutually exclusive on any single entry — an entry belongs to at
most one chat turn or one FAQ operation, never both.

Every string-valued field across every event type below is subject to FR-013 (truncated to 2,000
chars + `"..."` if longer) and FR-017 (secret values redacted) — enforced generically by the shared
processor chain (research.md #4), not per event type.

## Turn-scoped events (`turn_id` always present)

Emitted by `search_faq`/`answer_faq` (`services/chat/src/chat/rag/retriever.py`,
`services/chat/src/chat/agent/answer_faq.py`) and the `/chat` endpoint, one per pipeline step (User
Story 1, User Story 3):

| `event` | `level` | Fields | Corresponds to |
|---|---|---|---|
| `turn.message_received` | `info` | `message: str` | FR-001 |
| `turn.message_embedded` | `info` | *(no fields beyond the common shape — confirms the embedding sub-step completed)* | FR-020 |
| `turn.retrieval_completed` | `info` | `retrieved_chunks: list[{entry_id: int, chunk_index: int, score: float, chunk_text: str}]`, ordered highest `score` first, one list entry per candidate retrieved (no cap) | FR-002 |
| `turn.groundedness_verdict` | `info` | `grounded: bool` | FR-003 |
| `turn.completed` | `info` | `outcome: "grounded" \| "abstained"`; when `grounded`: `answer_text: str`, `citations: list[{entry_id, chunk_index, chunk_text, score: float}]` (each citation's RAG similarity score, FR-004); when `abstained`: `abstention_message: str` | FR-004 |
| `turn.error` | `error` | `pipeline_step: "embedding" \| "retrieval" \| "groundedness" \| "generation"`, `error_detail: str` | FR-005 |

`turn.completed` is emitted once per turn regardless of outcome — never one entry per streamed
token (FR-004, edge case). `turn.message_embedded` is emitted before `turn.retrieval_completed`,
distinct from it (FR-020) — embedding is a sub-step of retrieval, but its own log entry, since it's
an independently-failing step (`turn.error`'s `pipeline_step: "embedding"` attributes a failure to
it specifically).

## FAQ management events (`operation_id` always present)

Emitted by the FAQ CRUD endpoints and the indexing pipeline they call
(`services/chat/src/chat/api/faq.py`, `services/chat/src/chat/rag/indexing.py`,
`services/chat/src/chat/rag/chunking.py`, `services/chat/src/chat/rag/embeddings.py`), User Story 4:

| `event` | `level` | Fields | Corresponds to |
|---|---|---|---|
| `faq.content_chunked` | `info` | `chunk_count: int` | FR-022 |
| `faq.chunks_embedded` | `info` | `chunk_count: int` (all chunks from the preceding `faq.content_chunked` were embedded) | FR-022 |
| `faq.entry_created` | `info` | `entry_id: int` | FR-007 |
| `faq.entry_updated` | `info` | `entry_id: int` | FR-007 |
| `faq.entry_deleted` | `info` | `entry_id: int` | FR-007 |
| `faq.operation_failed` | `error` | `operation: "create" \| "update" \| "delete"`, `entry_id: int \| None` (unknown if the failure happened before the ID was resolved), `failed_step: "chunking" \| "embedding" \| "persist" \| None`, `error_detail: str` | FR-007 |

`faq.content_chunked`/`faq.chunks_embedded` are only emitted for create/update (a delete operation
has nothing to chunk/embed) and are summarized per operation — one entry each, covering however many
chunks resulted, not one entry per chunk (research.md #6).

## Critical events (`turn_id` or `operation_id` present only when correlated per FR-018)

Emitted from `main.py`'s `lifespan` (startup dependency check) or from a dependency call's existing
exception handler inside a turn/FAQ operation (research.md #5):

| `event` | `level` | Fields | Corresponds to |
|---|---|---|---|
| `critical.dependency_unreachable` | `critical` | `dependency: "qdrant" \| "postgres" \| "anthropic_api"`, `error_detail: str`; carries `turn_id` when FR-018-correlated to a turn, or `operation_id` when FR-018-correlated to a FAQ operation — neither, at startup | FR-015, FR-018 |

## Relationships

- A chat turn (User Story 1/3) produces exactly one `turn_id`, shared by 4–6 `turn.*` entries
  (`message_received`, `message_embedded`, `retrieval_completed`, `groundedness_verdict`, and either
  `turn.completed` or `turn.error`).
- A FAQ create/update operation produces exactly one `operation_id`, shared by up to 4 `faq.*`
  entries (`content_chunked`, `chunks_embedded`, then either `entry_created`/`entry_updated` or
  `faq.operation_failed`); a delete operation produces one `operation_id` shared by exactly one entry
  (`entry_deleted` or `faq.operation_failed`, since deletion has no chunking/embedding step).
- A dependency failure that occurs *during* a turn/FAQ operation produces two entries — the
  turn/operation's own `error`-level entry, and a `critical.dependency_unreachable` entry, linked by
  `turn_id` or `operation_id` (whichever applies) when the failure was turn-/operation-scoped
  (FR-018). A dependency failure at startup (no active turn or operation) produces only the
  `critical.dependency_unreachable` entry, with neither identifier.

No state transitions — every entry is a single, immutable, append-only fact about something that
already happened; entries are never updated or deleted after being emitted.
