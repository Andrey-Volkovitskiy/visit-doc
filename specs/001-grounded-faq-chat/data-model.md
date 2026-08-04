# Data Model: Grounded FAQ Chat

## FaqEntry

Source-of-truth record for a unit of clinic knowledge. Stored in PostgreSQL
(`services/chat`'s database, table `faq_entries`), owned entirely by the `chat` service.

| Field | Type | Rules |
|---|---|---|
| `id` | `int` (primary key, `IDENTITY`/`SERIAL`) | Generated server-side on create; immutable (research.md #11) |
| `content` | `str` | Required, 1–20,000 characters (FR-015); free-form policy document (FR-014); may itself contain embedded `Question:`/`Answer:` text with no enforced structure |
| `created_at` | `datetime` (UTC) | Set on create, immutable |
| `updated_at` | `datetime` (UTC) | Set on create, refreshed on every update |

No `title` field — an entry has no separately authored label. Citations reference the retrieved
chunk's own text instead of a title (research.md #13).

**Validation**: `content`'s length constraint (`Field(min_length=1, max_length=20000)`) is enforced
at the Pydantic schema layer, but length alone doesn't catch whitespace/dash-only submissions (e.g.
`"---"` is 3 characters, non-empty, still meaningless) — a Pydantic field validator additionally
strips whitespace and dash characters and rejects the result if nothing remains, and separately
rejects content that, after stripping the literal `Question:`/`Answer:` labels (case-insensitive)
and surrounding whitespace, has nothing left (FR-009).

**Lifecycle**:
- **Create**: insert row → chunk `content` → embed each chunk (Voyage AI) → upsert resulting
  `FaqChunk` points into Qdrant, tagged with this entry's `id`.
- **Update**: update row (`content`, `updated_at` refreshed) → delete all existing `FaqChunk` points
  for this entry's `id` from Qdrant → re-chunk and re-embed the new `content` → upsert fresh points.
  Re-chunking from scratch (rather than diffing) is the simplest correct approach at this phase's
  scale (research.md #3) and guarantees chunk boundaries never go stale relative to the current
  content.
- **Delete** (FR-016): delete all `FaqChunk` points for this entry's `id` from Qdrant, then delete
  the row from Postgres. Hard delete, no soft-delete/tombstone (spec.md Assumptions,
  research.md #12). Deleting an unknown `id` returns 404, no row/point changes.

No state machine — an entry is simply "exists" from create until update or delete; there's no
draft/published distinction in this phase.

## FaqChunk (Qdrant, not a relational table)

A retrievable slice of a `FaqEntry`'s content, embedded as a vector. Lives in Qdrant only — never
queried by anything other than the retriever, and reconstructible in full from `FaqEntry` (Qdrant is
a derived index, not a second source of truth).

| Field (Qdrant point) | Type | Notes |
|---|---|---|
| `id` | UUID (point ID) | Generated per chunk |
| `vector` | `float[]` | Voyage AI embedding of `chunk_text` |
| `payload.faq_entry_id` | `int` | Foreign reference back to `FaqEntry.id` |
| `payload.chunk_index` | `int` | Position of this chunk within the entry's content (0-based) |
| `payload.chunk_text` | `str` | The chunk's raw text — included both as generation context and, verbatim, as the citation shown to the visitor (research.md #13), so a citation can be rendered without a Postgres round-trip |

**Degenerate-chunk filtering (FR-017)**: after chunking, any chunk whose text is meaningless by the
same standard as FR-009's entry-level check (whitespace/dashes only, or bare `Question:`/`Answer:`
labels with nothing after them) is dropped before embedding — it is never upserted into Qdrant, so
it can never be retrieved. This is a defense-in-depth backstop for content that passes FR-009's
entry-level validation but happens to isolate a meaningless chunk during chunking (e.g. a divider
line landing alone at a chunk boundary). Since chunking partitions an entry's full content without
discarding any of it, and FR-009 already guarantees that content contains meaningful text
somewhere, at least one chunk always survives this filter — an entry can never end up with zero
retrievable chunks as a result of it.

**Relationships**: many `FaqChunk` points → one `FaqEntry`, via `payload.faq_entry_id`. All points
for a given entry are replaced atomically (delete-then-upsert) on every `FaqEntry` update.

## ChatExchange (transient, not persisted)

A single visitor question and the assistant's reply — modeled purely as request/response DTOs for
the `/chat` endpoint, not stored anywhere (spec.md Assumptions: no conversation persistence
required this phase).

| Field | Type | Notes |
|---|---|---|
| `message` | `str` | Visitor's question; 1–2,000 characters (FR-001a), rejected via Pydantic `Field` constraints outside that range |
| `answer` (streamed) | `str` (token stream) | Emitted incrementally as NDJSON `token` events (FR-004) |
| `citations` (final event) | `list[{entry_id, chunk_index, chunk_text}]` | Empty when abstaining; otherwise the exact chunk(s) placed in the LLM's context (research.md #6), quoted verbatim with their position within the entry so the answer can be directly diffed against its source (research.md #13) — not self-reported by the model |
| `grounded` (final event) | `bool` | `false` when the pre-generation similarity gate rejected retrieval and the agent abstained without calling Claude; `true` otherwise |

No identifier, no timestamps, no persistence — this is purely the shape of one HTTP request/response
cycle.
