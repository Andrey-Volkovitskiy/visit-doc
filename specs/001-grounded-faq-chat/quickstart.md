# Quickstart: Validating Grounded FAQ Chat

Runnable validation for the two user stories in [spec.md](./spec.md). Assumes the `chat` service is
running locally (`make run-chat`, per the root `Makefile`) against a local PostgreSQL and Qdrant, and
that `ANTHROPIC_API_KEY` / `VOYAGE_API_KEY` are set. Endpoint shapes are defined in
[contracts/openapi.yaml](./contracts/openapi.yaml); entity shapes in [data-model.md](./data-model.md).

## Prerequisites

- `chat` service running with its DB migrations applied (`alembic upgrade head` from
  `services/chat/`)
- Qdrant reachable at the configured `QDRANT_URL`, with an empty (or existing) `faq_chunks`
  collection
- No auth headers needed anywhere in this phase (FR-011, FR-012)

## Scenario 1 — User Story 2: add an FAQ entry via API (P2, prerequisite for Scenario 2)

```bash
curl -s -X POST http://localhost:8000/faq \
  -H "Content-Type: application/json" \
  -d '{"content": "Visiting hours are Monday to Friday, 8am to 5pm."}'
```

**Expected**: `201`, response body is a `FaqEntry` (per contracts/openapi.yaml) with a generated
`id`. Then confirm it's listed:

```bash
curl -s http://localhost:8000/faq
```

**Expected**: `200`, a JSON array containing the entry just created (FR-006, FR-008).

## Scenario 2 — User Story 1: ask a grounded question (P1)

```bash
curl -s -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "when can I visit?"}'
```

**Expected**: a stream of NDJSON lines — zero or more `{"type": "token", ...}` lines whose
concatenated `text` forms an answer derived from the entry created in Scenario 1, followed by one
`{"type": "done", "grounded": true, "citations": [{"entry_id": 1, "chunk_index": 0, "chunk_text":
"Visiting hours are Monday to Friday, 8am to 5pm."}]}` line — the `chunk_text` is the exact source
passage, so it can be diffed directly against the streamed answer. Confirms FR-002, FR-003, FR-004,
SC-001.

## Scenario 3 — abstention on an unanswerable question

```bash
curl -s -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "what is the weather like today?"}'
```

**Expected**: no `token` events, then a single `{"type": "done", "grounded": false, "citations": [],
"message": "..."}` line stating the assistant doesn't have a confident answer. Confirms FR-005,
SC-002.

## Scenario 4 — update reflected in retrieval

```bash
curl -s -X PUT http://localhost:8000/faq/<id-from-scenario-1> \
  -H "Content-Type: application/json" \
  -d '{"content": "Visiting hours are now 7 days a week, 9am to 6pm."}'
```

Then repeat Scenario 2's request. **Expected**: the streamed answer now reflects the updated hours,
with no manual re-indexing step beyond the `PUT` call itself. Confirms FR-007, FR-010, SC-003.

## Scenario 5 — validation rejections

```bash
# Empty message
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" -d '{"message": ""}'
# Oversized FAQ entry (20,001 'a' characters)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/faq \
  -H "Content-Type: application/json" \
  -d "{\"content\": \"$(python3 -c 'print("a"*20001)')\"}"
# Whitespace/dash-only FAQ entry content (non-empty string, no meaningful text)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/faq \
  -H "Content-Type: application/json" -d '{"content": "---\n   \n---"}'
# Label-only FAQ entry content (Q&A scaffolding with no actual answer)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/faq \
  -H "Content-Type: application/json" -d '{"content": "Question:\nAnswer:"}'
```

**Expected**: all four return `422`. Confirms FR-001a, FR-009, FR-015.

## Scenario 6 — delete an FAQ entry

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE http://localhost:8000/faq/<id-from-scenario-1>
```

**Expected**: `204`. Then confirm it's gone:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/faq/<id-from-scenario-1>
# Deleting again returns 404, not a silent success
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE http://localhost:8000/faq/<id-from-scenario-1>
```

**Expected**: both `404`. Repeat Scenario 2's request and confirm the assistant now abstains
(`grounded: false`) instead of citing the deleted entry. Confirms FR-016.

## End-to-end demo (SC-005)

Scenarios 1 → 2 in sequence, run by someone with no prior knowledge of the system's internals,
constitute the full walking-skeleton demo: add knowledge via API, ask a matching question in chat,
get a cited grounded answer.
