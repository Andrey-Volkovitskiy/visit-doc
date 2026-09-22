# Contract: tracing request headers and the per-turn sampler

How a caller of `POST /chat` chooses whether its turn is traced, and how the chat service keeps that
choice to exactly that turn. The harness is the one caller that sends these; a browser sends none of
them and is always traced (when the service traces at all).

## Headers on `POST /chat`

| Header | Values | Effect |
|---|---|---|
| `X-VisitDoc-Trace` | `off` | this turn exports nothing |
| `X-VisitDoc-Eval-Run` | a run id (26-char ULID) | trace metadata `eval_run_id`; environment `eval` |
| `X-VisitDoc-Eval-Case` | a case id (`G-<family>-<nn>`) | trace metadata `eval_case_id` |

Validation, before anything else the route does with the request:

- `X-VisitDoc-Trace` with any value other than `off` (case-insensitive, trimmed) → **422**.
- Exactly one of `X-VisitDoc-Eval-Run` / `X-VisitDoc-Eval-Case` → **422**.
- A malformed run or case id → **422**.

A 422 stores nothing and runs no turn, like every other request-validation failure on the route.

The headers are not part of `ChatRequest` and do not appear in the body schema.

## The sampler invariant

The chat service's `TracerProvider` is built with a sampler that returns `DROP` when the untraced
flag is set in the current context, and otherwise defers to `ParentBased(ALWAYS_ON)`. The flag is
checked **before** the parent's sampled bit.

Stated as clauses, each with its own test:

- **S1** — a turn sent with `X-VisitDoc-Trace: off` exports zero spans, including its generations and
  tool calls.
- **S2** — a turn sent without the header, **concurrently** with an `off` turn on another chat against
  the same app, exports its full trace. (The flag never leaks across tasks.)
- **S3** — two turns on the same chat, the first `off` and the second not: the second is traced.
  (The flag does not persist past the turn that set it.)
- **S4** — a turn superseded by a staff post, sent `off`, exports nothing for either the cancelled
  turn or anything its cancellation path runs.
- **S5** — with tracing disabled in `Settings`, a turn sent without the header exports nothing, and
  `turn.traced` is not logged. (The header can only turn tracing off, never on.)
- **S6** — the flag is set before `asyncio.create_task(run_pipeline())` and only there; a test fails
  if any other module sets it.

## `turn.traced`

Logged once per turn **only** when the turn's root observation is recording, carrying `trace_id`.
Its absence from a turn's log group means the turn was not exported — which is the harness's only
source for a case's trace ids.
