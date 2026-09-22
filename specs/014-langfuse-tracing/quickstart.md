# Quickstart: Tracing with Langfuse (Phase 2d)

How to turn tracing on locally and check, by hand, that the phase does what the spec says. The unit
tier (`make test-unit`) proves the contracts with an in-memory exporter; this guide is what that
cannot prove — a real trace in the real Langfuse UI. Scenarios 1–4 spend live Claude and Voyage
calls, and every traced scenario spends Langfuse units (a few dozen per turn).

## Setup (once)

1. Create a Langfuse Cloud account and a project on the free **Hobby** plan at
   <https://cloud.langfuse.com>. Project settings → API keys → create a key pair.
2. Add to the repo-root `.env` (never commit it):

   ```dotenv
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   # LANGFUSE_BASE_URL=https://cloud.langfuse.com     # the default; US region is https://us.cloud.langfuse.com
   # LANGFUSE_ENVIRONMENT=development                  # the default
   ```

3. Start the stack with JSON logs, which the harness needs:

   ```bash
   make migrate
   LOG_FORMAT=json make services-up
   grep service.configured .run/chat.log | tail -1 | jq .tracing_enabled   # → true
   ```

## Scenario 1 — one turn, one trace (US1, SC-001, SC-002)

```bash
scripts/dev-chat.sh   # usage block; mint a session, then post:
# "What should I bring to a first visit, and what cardiology slots are free tomorrow?"
```

Expect, in Langfuse → Tracing, filtered to environment `development`:

- One trace named `turn`, its session = the chat id, holding `classify_intent`, `answer_faq`,
  `handle_booking` and `compose_answer` ([contracts/trace-shape.md](./contracts/trace-shape.md) C3).
- Under `answer_faq`, a `request[0]` branch with `faq.embed` → `faq.verdict` and an
  `answer_faq.model` generation.
- Under `handle_booking`, `tool:list_practitioners` first, then `handle_booking.model[1]` (numbered
  from 1, as the log's `iteration` is) and
  `tool:check_availability` with its arguments and result.
- Every generation shows its model id and input/output tokens.
- Pick the `faq.rerank_gate` span and compare it to the log:
  `grep '"faq.rerank_gate"' .run/chat.log | tail -1 | jq` — the same floor, cap, kept and dropped.

Post a second message in the same chat: the Sessions view shows both turns, in order.

## Scenario 2 — a failing turn is marked failed (US1 scenario 5)

Stop the scheduler (`make services-down`, then start only chat with `make run-chat-dev` in another
terminal) and ask to book. The turn's `tool:check_availability` is level `WARNING` with status
`unavailable`; the root's `turn_outcome` is `completed` (the patient got a reply). To see `failed`,
send a turn with an invalid `ANTHROPIC_API_KEY` in a throwaway shell: the generation is `ERROR`, the
root's `turn_outcome` is `failed`, and **the key does not appear anywhere in the trace** (C5).

## Scenario 3 — an eval case leads to its trace (US2, SC-006)

```bash
make eval-run CASES=G-a-01,G-j-03
RUN=$(ls -t .run/evals | grep -v -e comparisons -e bands | head -1)
jq .tracing .run/evals/$RUN/run.json                     # → "traced"
jq .traces .run/evals/$RUN/cases/G-a-01.json             # → {"<message id>": "<trace id>"}
```

Open `https://cloud.langfuse.com/project/<project id>/traces/<trace id>`: environment `eval`,
metadata `eval_run_id` = `$RUN`, `eval_case_id` = `G-a-01`. Filtering the trace list to environment
`eval` shows these two cases' turns and none of Scenario 1's.

## Scenario 4 — an untraced run beside a traced session (US3, SC-003, SC-007)

In one terminal, keep chatting by hand (Scenario 1). In another:

```bash
make eval-run TRACE=0 CASES=G-a-01
jq .tracing .run/evals/<run>/run.json                    # → "untraced_by_request"
jq .traces .run/evals/<run>/cases/G-a-01.json            # → {}
grep -c '"turn.traced"' .run/chat.log                    # grew only for the hand-driven turns
make eval-compare BASE=<the Scenario 3 run> NEW=<this run>   # condition delta: none
```

No trace with environment `eval` appears for this run; the hand-driven turns sent during it are traced.
`make eval-run TRACE=yes` stops before driving anything, naming `0` and `1`.

## Scenario 5 — Langfuse unreachable (SC-004)

```bash
# in .env: LANGFUSE_BASE_URL=http://127.0.0.1:9    then restart chat
```

Send a turn: the reply streams as usual. Within a few seconds `.run/chat.log` has
`tracing.export_failed`. Restore the URL.

## Scenario 6 — no keys, no tracing (SC-005)

Comment out both keys, restart chat: `service.configured` says `tracing_enabled: false`, turns work,
no `turn.traced` is logged, nothing appears in Langfuse. An eval run taken now records
`"tracing": "untraced_service_off"`.

## The phase's record (FR-030, SC-009)

With tracing on and nothing else sending traces, note the project's unit count (Settings → Usage),
take one full traced run (`make eval-run`), note it again, and read per-turn-shape observation counts
from a sample trace of each shape (FAQ ×1 request, FAQ ×several, booking, small talk, a stopping
reason, merged). Commit them under `specs/014-langfuse-tracing/evaluation/units.md`, with the build
and the date. The roadmap's "15–25 per turn" and "roughly 3k per run" are replaced by those numbers.

*(Done differently, by the user's choice: the per-shape counts were measured offline from the test
exporter, and the full-run figure is weighted from them — see `evaluation/units.md`. The before/after
unit count of one real full run is T084.)*

## Walk-through record (T069, 2026-09-22)

All six scenarios were walked against the project's Langfuse Cloud Hobby project on branch
`014-langfuse-tracing`, and all six behaved as described. Traces were read back through Langfuse's
public API rather than the UI. Divergences from the text above, none of them a defect in this phase:

- **Reading traces by API.** An organization created on or after 2026-09-16 gets `410
  LEGACY_API_UNAVAILABLE_FOR_NEW_ORGANIZATION` from `GET /api/public/traces/<id>`. Use
  `GET /api/public/v2/observations?traceId=<id>&fromStartTime=…&toStartTime=…&fields=core,basic,io,metadata,model,usage,time`
  (basic auth with the key pair). `completionStartTime` is only returned with the `time` field group.
- **The root has a parent id.** Because the trace id is seeded from the turn id, the `turn`
  observation carries a `parentObservationId` that names no exported span; Langfuse still marks it
  `isRootObservation: true`, and the UI shows it as the root.
- **Streamed vs created calls.** `completionStartTime` is set on the three streamed generations and
  absent on `classify_intent.model` and `handle_booking.model[<n>]`, which are `messages.create` calls
  — as C3 says.
- **A failed generation shows input tokens.** When the call itself fails (Scenario 2's bad key), the
  generation has no usage from the provider, and Langfuse fills `input` with its own tokenizer
  estimate. That number is Langfuse's, not the SDK response's.
- **Scenario 2, scheduler down.** Stopping only the scheduler is enough: `kill -TERM -- -$(cat
  .run/scheduler.pid)`, then `LOG_FORMAT=json make services-up` restarts it later.
- **Measured shapes agree with `evaluation/units.md`.** Scenario 1's merged turn exported 19
  observations (20 units, as the per-shape formula predicts); small talk exported 6 (7 units).

Observed: Scenario 1 — the tree of C3 exactly, each FAQ step's output equal to its `.run/chat.log`
event; Scenario 2 — `tool:list_practitioners` `WARNING unavailable` with the root `completed`, and
with a bad Anthropic key the generations `ERROR`, the root `failed`, the key absent from the export;
Scenario 3 — `tracing: "traced"`, one trace id per case, environment `eval`, run and case ids in the
metadata; Scenario 4 — `untraced_by_request`, `traces: {}`, only the hand-driven turn traced
(environment `development`), an empty condition delta with `[traced]`/`[untraced_by_request]` beside
the run ids, and `TRACE=yes` stopping Make; Scenario 5 — the reply unaffected and
`tracing.export_failed` logged for the retries and the dropped batch; Scenario 6 —
`tracing_enabled: false`, no `turn.traced`, and an eval run recorded as `untraced_service_off`.
