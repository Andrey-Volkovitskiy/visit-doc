# Quickstart: Validating Structured Logging for App/AI Behavior

Runnable validation for the four user stories in [spec.md](./spec.md). Assumes the `chat` service
is running locally (`make run-chat`) with its terminal visible, against a local PostgreSQL and
Qdrant (`make db-up`), migrations applied, and `ANTHROPIC_API_KEY`/`VOYAGE_API_KEY` set. Event
shapes are defined in [data-model.md](./data-model.md) and
[contracts/log-events.md](./contracts/log-events.md).

## Prerequisites

- `chat` service running (`make run-chat`) with its terminal output visible in this window
- At least one FAQ entry seeded (per `specs/001-grounded-faq-chat/quickstart.md` Scenario 1) so a
  grounded answer is possible

## Scenario 1 — User Story 1 + 2: full per-turn trace, readable in the terminal

```bash
curl -s -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "when can I visit?"}'
```

**Expected in the terminal**: five `INFO`-level lines sharing one `turn_id` — in order,
`turn.message_received` (the question verbatim), `turn.message_embedded` (confirms the embedding
sub-step ran, FR-020), `turn.retrieval_completed` (every retrieved candidate, highest-scoring
first, each with its score — not just the ones cited), `turn.groundedness_verdict`
(`grounded=true`), and `turn.completed` (`outcome=grounded`, the final answer text and its
citations, each with its own RAG similarity score). Every field is readable directly — no external
tool needed to make sense of it. Confirms FR-001–FR-004, FR-006, FR-009, FR-011, FR-020, SC-001,
SC-006, SC-012.

## Scenario 2 — User Story 1: abstention traced, not visually flagged as a problem

```bash
curl -s -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "what is the weather like today?"}'
```

**Expected**: the same five-line trace shape as Scenario 1, but `turn.groundedness_verdict` shows
`grounded=false` and `turn.completed` shows `outcome=abstained` with the abstention message. All
five lines render at the unflagged `INFO` tier — an abstention is a routine result, not something
visually called out (2026-08-05 clarification, FR-012). Confirms FR-003, FR-004.

## Scenario 3 — User Story 3: concurrent turns stay distinguishable

```bash
curl -s -N -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "when can I visit?"}' &
curl -s -N -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "what is the weather like today?"}' &
wait
```

**Expected**: the terminal shows both turns' entries interleaved, but every line carries a
`turn_id`; grep-filtering by either turn's ID isolates exactly that turn's own five-line trace, in
order, with none of the other turn's lines mixed in. Confirms FR-006, SC-002.

## Scenario 4 — User Story 4 + FR-018: dependency failure mid-turn logs twice, at different prominence

Stop Qdrant while the service keeps running (`docker compose stop qdrant`), then:

```bash
curl -s -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "when can I visit?"}'
```

**Expected**: two lines for the same `turn_id` — a `CRITICAL`-level `critical.dependency_unreachable`
(`dependency=qdrant`) and an `ERROR`-level `turn.error` (`pipeline_step=retrieval`) — not merged into
one. The `CRITICAL` line is visibly more prominent than the `ERROR` line. Neither line contains the
Qdrant connection string/credentials. Confirms FR-005, FR-015, FR-017, FR-018, FR-019, SC-009,
SC-010, SC-011. Restart Qdrant afterward (`docker compose start qdrant`).

## Scenario 5 — User Story 4: FAQ operation failure is logged

With Qdrant still stopped (from Scenario 4, before restarting it):

```bash
curl -s -X POST http://localhost:8000/faq \
  -H "Content-Type: application/json" \
  -d '{"content": "Parking is available in the garage on Elm Street."}'
```

**Expected**: an `ERROR`-level `faq.operation_failed` line (`operation=create`, `failed_step`
identifying whether it failed during chunking/embedding/persisting) carrying an `operation_id`
(not a `turn_id`, since this isn't a chat turn — FR-021) — plus a `CRITICAL`
`critical.dependency_unreachable` line correlated to that same `operation_id`, for the underlying
Qdrant outage. Confirms FR-007, FR-015, FR-018, FR-021.

## Scenario 8 — User Story 4 + FR-020/FR-022: chunking/embedding sub-steps, correlated by operation

With Qdrant running normally again:

```bash
curl -s -X POST http://localhost:8000/faq \
  -H "Content-Type: application/json" \
  -d '{"content": "Parking is available in the garage on Elm Street."}'
```

**Expected**: three `INFO`-level lines sharing one `operation_id` — `faq.content_chunked`
(`chunk_count` matching however many chunks the content split into), `faq.chunks_embedded` (the
same `chunk_count`, confirming every chunk was embedded), and `faq.entry_created` (`entry_id`).
Confirms FR-021, FR-022, SC-013.

## Scenario 6 — FR-013: long field truncated, not shown in full

Create an FAQ entry near the 20,000-character limit (per `specs/001-grounded-faq-chat`'s own
constraint), then ask a question it grounds:

```bash
curl -s -X POST http://localhost:8000/faq -H "Content-Type: application/json" \
  -d "{\"content\": \"Parking policy. $(python3 -c 'print("Details. " * 2500)')\"}"
curl -s -N -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "where can I park?"}'
```

**Expected**: `turn.retrieval_completed`'s `chunk_text` field appears truncated at 2,000 characters
plus a trailing `"..."`, never the full chunk. Confirms FR-013, SC-007.

## Scenario 7 — FR-017: secrets never appear, even in error detail

With a wrong `DATABASE_URL` password set temporarily (or by stopping Postgres), trigger a FAQ list
call:

```bash
curl -s http://localhost:8000/faq
```

**Expected**: the resulting `ERROR`/`CRITICAL` log lines describe the failure (e.g. "connection
refused" or similar) but never contain the actual database password/connection string, even though
the underlying exception's message would naturally include it. Confirms FR-017, SC-010.
