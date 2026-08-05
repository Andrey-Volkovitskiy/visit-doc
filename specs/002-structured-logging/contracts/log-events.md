# Contract: Log Events

This feature's "interface" isn't an HTTP API — it's the structured event shape every log call
produces (consumed today by the terminal renderer; consumed later, unchanged, by whatever replaces
it per FR-014) and the terminal rendering that shape drives. Event field definitions are in
`data-model.md`; this document fixes the two consumable contracts derived from them.

## 1. Structured event contract

Every log call MUST produce an event matching this shape (illustrated as JSON; the actual in-process
value is a `structlog` event dict — JSON is just the interchange shape a future non-terminal
renderer would emit unchanged from the same processed event):

```json
{
  "timestamp": "2026-08-05T14:03:21.114Z",
  "level": "info",
  "event": "turn.retrieval_completed",
  "turn_id": "01J8Z3K9QAF7VXP9T6E9T3RZ9B",
  "retrieved_chunks": [
    {"entry_id": 12, "chunk_index": 0, "score": 0.83, "chunk_text": "Visiting hours are..."}
  ]
}
```

`turn_id` is a ULID (research.md #2) — no hyphens, so it's one double-click-selectable token in a
terminal, unlike a hyphenated UUID4. A FAQ management operation carries the analogous `operation_id`
instead (research.md #6) — never both on the same entry.

Rules that apply to every event, regardless of type (enforced by the shared processor chain,
research.md #4, not by each call site):

- `timestamp`, `level`, `event` are always present. `turn_id` is present iff the event is turn-scoped
  or is an FR-018 critical event correlated to a turn; `operation_id` is present iff the event
  belongs to a FAQ management operation or is an FR-018 critical event correlated to one.
- No string value exceeds 2,000 characters (FR-013) — longer values are truncated with a trailing
  `"..."`.
- No string value ever contains a secret, credential, token, or password (FR-017) — matched values
  are replaced with a fixed redaction placeholder before the event leaves the process.
- Field names and event-type strings are exactly as listed in `data-model.md` — stable so a future
  consumer (e.g. a Langfuse exporter) can map them without renegotiating the shape.

## 2. Terminal rendering contract

The human-readable terminal view (FR-011) is a *rendering* of the same event, not a different data
source — every field above is representable in it, just laid out for a person instead of a machine.
Three severity tiers (research.md #3) are visually distinct, most-to-least prominent:

```text
[CRITICAL] 14:03:22 dependency_unreachable  dependency=qdrant turn_id=01J8Z3K9QAF7VXP9T6E9T3RZ9B
    error_detail: Connection refused (redacted: connection string)

[ERROR]    14:03:22 turn.error  turn_id=01J8Z3K9QAF7VXP9T6E9T3RZ9B step=retrieval
    error_detail: Connection refused (redacted: connection string)

[INFO]     14:03:21 turn.message_received  turn_id=01J8Z3K9QAF7VXP9T6E9T3RZ9B
    message: "What are your visiting hours?"

[INFO]     14:03:21 turn.message_embedded  turn_id=01J8Z3K9QAF7VXP9T6E9T3RZ9B

[INFO]     14:03:21 turn.completed  turn_id=01J8Z3K9QAF7VXP9T6E9T3RZ9B outcome=abstained
    abstention_message: "I don't have a confident answer to that."
```

Contract points this example fixes:

- `CRITICAL` is the most visually prominent tier; `ERROR` is distinguishable but less so; `INFO`
  (covering both grounded answers and abstentions) carries no problem-signaling styling at all — an
  abstention is a routine outcome, not something flagged (2026-08-05 clarification, FR-012).
- A dependency failure mid-turn renders as **two** lines sharing the same `turn_id`, not one merged
  line (FR-018) — visible above as the `CRITICAL` and `ERROR` lines for the same `turn_id`.
- `turn.message_embedded` (FR-020) is its own line, distinct from `turn.message_received` and
  `turn.retrieval_completed` — visible in the trace even though it carries no fields beyond the
  common shape.
- Every line is readable top-to-bottom without a parser or query tool (SC-006): event type, the
  correlation id, and step-specific detail are all inline, in prose/labeled-field form, not a raw
  serialized blob.

A FAQ content management operation renders the same way, correlated by `operation_id` instead of
`turn_id`:

```text
[INFO]     15:10:04 faq.content_chunked  operation_id=01J8Z3M2XG6E5N3F8H1K7P0Q2R chunk_count=4

[INFO]     15:10:04 faq.chunks_embedded  operation_id=01J8Z3M2XG6E5N3F8H1K7P0Q2R chunk_count=4

[INFO]     15:10:04 faq.entry_created  operation_id=01J8Z3M2XG6E5N3F8H1K7P0Q2R entry_id=12
```
