# Contract: Reading a turn's log events

How the harness obtains the ranked candidates hit@k and MRR are questions about, and the
segmentation the classification metrics are scored against. This is the contract the spec left to
planning (FR-030, FR-021a).

## Why anything is needed here

Two of the inputs live only in the log. The stored record carries each request's **survivors** —
what the answer stood on — which is the right thing for a citation and the wrong thing for a rank:
a chunk that placed 7th of 25 never reaches the record at all. And the produced **segmentation** is
on `intent.classified`, since `request_outcomes` carries a question only for the requests the FAQ
half handled and so cannot describe a booking-only or a small-talk turn.

## The change to `shared-logging`

`configure_logging` gains a `log_format` argument, and `chat`'s settings gain a matching
`LOG_FORMAT`. Two values:

| Value | Renderer | Default |
|---|---|---|
| `console` | today's `structlog.dev.ConsoleRenderer`, colours and all | yes |
| `json` | one JSON object per line | no |

**The renderer is the last processor in the chain and the change adds nothing before it.** Redaction
(`make_redact_secrets_processor`) and truncation (`_truncate_long_strings`) run first and are
untouched, so a JSON line is rendered from an already-redacted event dict. A test asserts exactly
that: a log call carrying a live secret renders with the placeholder under both formats.

Non-serializable values are rendered with `default=str` rather than dropped — a line that loses a
field silently is worse than one carrying a repr, and the harness validates the fields it needs.

## The line

Every line is one JSON object carrying at least:

| Field | From |
|---|---|
| `event` | the event name — `intent.classified`, `faq.retrieval_completed`, … |
| `level`, `timestamp` | the shared chain |
| `turn_id` | bound by `bind_turn_id()` for the whole turn |
| `node`, `segment` | bound per node and per request, where the emitting code binds them |

Field contracts for the events themselves are unchanged and are not restated here:
[`specs/008-reranked-retrieval-pipeline/contracts/log-events.md`](../../008-reranked-retrieval-pipeline/contracts/log-events.md)
and [`specs/010-multi-request-turns/contracts/log-events.md`](../../010-multi-request-turns/contracts/log-events.md).
This phase is their first consumer; where the log disagrees with those documents, that is a finding
about the log or the contract, and is reported rather than accommodated.

## The settings event

The second change FR-051 permits, and the source of a run's conditions (FR-047c). The chat service
emits one event at INFO from its lifespan startup, before it builds a client:

| Field | From |
|---|---|
| `event` | `service.configured` |
| `classification_model`, `generation_model`, `rerank_model` | `Settings` |
| `embedding_model` | `chat.rag.embeddings.EMBEDDING_MODEL` |
| `retrieval_pool_size`, `similarity_floor`, `similarity_cap`, `rerank_floor`, `rerank_cap`, `context_turns` | `Settings` |
| `max_segments` | `chat.domain.schemas.MAX_SEGMENTS` |

Field names are part of the contract, as 008's are. No secret-bearing setting is on the list, and
redaction runs upstream of the renderer regardless.

**How the harness reads it.** A run's conditions are the fields of the latest `service.configured`
line before the offset the run began at; with none, the run stops before its first turn. A
`service.configured` inside a case's slice means the service restarted mid-run: the run stops unless
its values equal the run's, and that case is not written — even a booking case whose turn was
posted. Resume re-posts it: the restart ended the process running that turn, so nothing of it can
land later, and the resume sweep cancels whatever it did write (spec FR-007d, FR-041b). A resumed
run reads the event again and stops when it differs from `run.json`.

## Taking a case's slice

The run is strictly sequential (FR-003a), which is what makes this exact:

1. Record the log file's size **before** posting the case's turn.
2. Post the turn and wait for its terminal event.
3. Read from the recorded offset to the new end, parsing every line that begins with `{` as one JSON
   object. Lines that do not are **foreign** and skipped: the file is the process's whole stdout
   and stderr, so uvicorn's own startup and access lines (`INFO:     127.0.0.1:… "POST /chat
   HTTP/1.1" 200 OK`, one per request) and any traceback land in it too, through the standard
   library's logger rather than the shared chain. A line that begins with `{` and does not parse is
   not foreign — it makes the slice unreadable.
4. Group the parsed events by `turn_id`.
5. Select the group whose `turn.message_received` carries the case's own patient message id in
   `message_ids_unified`.

Steps 4 and 5 are not redundant with steps 1–3. The offset window is exact against this run, since
no second case is in flight; it is **not** exact against a person using the same deployment from a
browser, which the spec explicitly permits. The `turn_id` selection costs nothing and turns a silent
mixing of two turns' candidates into a loud failure.

If the slice cannot be read, or the selection finds no group or more than one, the case is excluded
as `missing_log_slice` (FR-018). It is never defaulted to a retrieval miss: that would report a
harness problem as a corpus problem, which is the one thing every metric in this phase exists to
tell apart.

**A slice that was read but breaks this contract is not an exclusion** (decided 2026-09-14). A FAQ
request with neither a retrieval event nor an empty-corpus skip, one event twice for a segment, a
kept cited chunk with no rerank outcome, or a stored record that contradicts its own invariants
makes scoring fail, loudly and for the whole run, naming the case. Such a record is not a
measurement of the assistant but evidence that the log, the contract or the harness is wrong, and
excluding it would let a report be read as valid over a defect nobody looked at. The run stays on
disk; once the cause is fixed — or, where the log was right, the contract amended — it re-scores
for free (FR-044).

## What the harness requires of a deployment

The chat service must be running with `LOG_FORMAT=json` and its output captured to a file the
harness can read — which is what `make services-up` already does, writing `.run/chat.log`. The
harness cannot judge that from the file's first line, which is uvicorn's in either format. It
judges it from what the service itself wrote: once the run's session has been opened, the bytes
appended since the run began must hold at least one line parsing as a JSON object with an `event`
key. If they hold none, the run stops before its first turn and writes no case file, rather than
discovering it 40 cases and several dollars in.
