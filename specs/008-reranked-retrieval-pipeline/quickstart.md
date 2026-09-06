# Quickstart: Validating the Reranked Retrieval Pipeline

How to prove this feature works, by hand and by suite. Contracts are referenced, not repeated —
see [`contracts/`](./contracts/) and [`data-model.md`](./data-model.md).

## Prerequisites

```bash
make sync                                   # deps (voyageai is already present; nothing new)
docker compose up -d postgres qdrant        # datastores
make migrate                                # includes this feature's grounded -> faq_verdict revision
```

`VOYAGE_API_KEY` must be set — it now pays for embeddings **and** reranking. The new settings all
have defaults (see [data-model.md §4](./data-model.md)); an unconfigured checkout runs.

## 1. Automated tiers

```bash
make test-unit          # the whole gate/verdict/port contract; no network, no spend
make lint typecheck
```

The unit tier is where this feature is actually proven: `pipeline.py` is pure, so every row of the
[decision table](./contracts/pipeline-gates.md) and every invariant is asserted without a client.

**Two checks that must pass before anything else is trusted**:

```bash
uv run pytest services/chat/tests/test_paid_api_guard.py -q
```

This must include a case proving `voyageai.client_async.AsyncClient.rerank` is blocked. Until it
does, a test that forgets to mock the reranker bills a live call and still passes green.

```bash
uv run pytest services/chat/tests/test_groundedness.py -q
```

Must report **no such file** — `groundedness.py` and its test are deleted, not left asserting a
threshold nothing reads.

**A third check, which the implementation added**: `services/chat/tests/conftest.py` must carry the
autouse `_reranking_keeps_what_it_is_given` fixture. Without it the paid-API guard does not protect
the reranking call at all — `rerank_chunks` converts *every* failure to `None` by requirement
(FR-010), including `PaidAPICallInTestError`, so a test that reaches a live reranker does not fail;
it quietly answers `answered_unreranked`. The guard entry and the boundary fake are both needed, and
neither substitutes for the other.

## 2. Bring the stack up

```bash
make services-up && make services-status    # stop with `make services-down`, never pkill
```

## 3. Walk the six verdicts

`scripts/dev-chat.sh` mints a session, posts turns, and reads the thread and the staff console. Each
scenario below names what to look for in the turn's log (`.run/chat.log`) — filter by the turn's
correlation id.

| # | Setup | Ask | Expect |
|---|---|---|---|
| 1 | Fresh session (seeded corpus) | A question the corpus answers | `faq.verdict` = `answered`; ≤3 citations; `faq.rerank_gate` present |
| 2 | Delete every FAQ entry in the session | Any FAQ question | `abstained_empty_corpus`; **no** embedding and **no** search event; staff called |
| 3 | Fresh session | Something entirely off-topic | `abstained_similarity_floor`; `faq.retrieval_completed` present, no reranking call |
| 4 | Raise `RERANK_FLOOR` to `0.99`, restart chat | A question the corpus answers | `abstained_rerank_floor` — reranking ran and rejected everything. This is the row that proves the two abstentions are distinguishable. |
| 5 | Point `RERANK_MODEL` at a nonexistent model, restart | A question the corpus answers | `answered_unreranked`; **ERROR** `faq.reranking_unavailable`; the patient still gets a cited answer |
| 6 | Point `QDRANT_COLLECTION_NAME` at a name that does not exist, restart (the session's entries stay in Postgres) | A question the corpus answers | `abstained_empty_pool`; `faq.retrieval_completed` with `pool_returned` = 0, `faq.similarity_gate` with `kept` = `[]`, no reranking call; staff called. The row that proves an index behind the rows is not read as a floor set too high. |

Scenarios 2–4 and 6 spend no generation call at all — the abstention is reached before it. That is
[SC-002](./spec.md), and it is visible as the absence of a generation event.

## 4. Observation pool and log volume

On any answered turn, `faq.retrieval_completed` must show:

- up to `pool_size` (25) candidates, in descending similarity order
- at most 5 with `considered: true`, each carrying **full** `chunk_text`
- the rest with `considered: false` and `chunk_text` cut to 200 characters, `text_truncated: true`
  **only where it was actually cut**

Then confirm the pool changes nothing (SC-006a): ask the same question with `RETRIEVAL_POOL_SIZE=25`
and again with `=10`. Same citations, same verdict, same answer — only the log differs. If the answer
moves, the pool is leaking into the pipeline and FR-002a is broken.

## 5. Both panes

With one conversation holding an answered turn and a scenario-5 (unreranked) turn:

- **Patient pane** — no citation list on any message, no verdict marker anywhere (FR-023b/d).
- **Staff console, same conversation** — citations on every assistant message that has them; the
  verdict marker on the unreranked one **only**; hovering it explains why. No numbers anywhere
  (FR-023g).
- Both panes' message elements carry `data-faq-verdict`; `data-grounded` appears nowhere in the built
  app.

Driving the patient pane costs real Claude and Voyage calls (see `.claude/CLAUDE.local.md`); a paused
conversation is the free way to check the marks.

## 6. Migration, both directions

The migration drops `grounded` and adds `faq_verdict` with **no translation between them**, which is
correct only because the message table is empty (FR-024, FR-024a). So confirm that first — before
migrating, not after:

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat \
  -tAc "select count(*) from messages"                         # must be 0
make migrate                                                   # up
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat \
  -c "\\d messages"                                              # faq_verdict present, grounded gone
```

If that count is **not** 0, stop: those rows would come out with a NULL verdict, which the schema
reads as "no FAQ specialist ran" — a false statement about every turn that had one. Clear them, or
write the backfill this plan deliberately left out.

Then downgrade one revision and confirm `grounded` returns and `faq_verdict` is gone. Both directions
discard the column they replace; neither pretends otherwise.

## 7. Calibration — done, and re-runnable

[`calibration/questions.md`](./calibration/questions.md) carries the 20 questions, the sweep, and the
result. `RERANK_FLOOR` shipped at **0.58**: 10/10 answerable questions answered citing the right
entry, 9/10 unanswerable abstained. The provisional 0.4 scored 6/10 on abstentions and **failed**
SC-008, which is what the calibration was for.

Read the "what the numbers do not settle" section there before moving it. The two classes overlap,
so no floor separates them, and every value from 0.520 to 0.636 scores the same — the digit is the
midpoint of that band, not an optimum.

Re-run it after any corpus edit, and after Phase 1f, which changes the text these scores are measured
against.

## Done when

- [X] `make test` and `make precommit` pass
- [X] Paid-API guard blocks `rerank`; no `groundedness` test collects
- [X] Five of the six verdicts reproduced against the live stack (§3 rows 1–5), and all six as
  automated cases in `test_answer_faq.py`/`test_pipeline.py`
- [ ] `abstained_empty_pool` (§3 row 6) walked against the live stack
- [X] Pool size provably does not change answers (§4)
- [X] Citations staff-only, marker on the unreranked turn only (§5)
- [X] Migration verified up and down (§6) — `test_migrations.py`
- [X] Calibration run and recorded; `RERANK_FLOOR` set to the measured value (§7)
- [X] README gains the reranker with its tradeoff; `docs/ROADMAP.md`'s 1e reconciled with what shipped


## Recorded live walk — 2026-09-06

Run against live Claude, `voyage-4-lite` and `rerank-3`, on the seeded 9-entry corpus.

| Question | Verdict | Note |
|---|---|---|
| "What should I bring to my first appointment?" | `answered` | 2 citations; the rerank gate cut 4 survivors to 2 |
| "What is your cancellation and no-show policy?" | `abstained_similarity_floor` | nothing cleared 0.3 |
| "What are your Sunday opening hours?" | `abstained_rerank_floor` | cleared 0.3 against the Mon–Sat hours entry; the cross-encoder rejected it. **The case a bi-encoder alone answers wrongly** |
| "Which insurance plans do you accept?" *(with `RERANK_MODEL` pointed at a nonexistent model)* | `answered_unreranked` | ERROR `faq.reranking_unavailable` with `reason=unusable_response`; no rerank gate; **no staff call** (FR-013) |
| "What should I bring?" *(corpus emptied)* | `abstained_empty_corpus` | no embedding, no search, no generation |

**One behaviour the automated tier does not show.** "I have chest pain radiating to my arm, what
should I do?" never reaches the FAQ path at all: the intent classifier routes it to `call_staff`, so
the turn ends `answer_source=hand_off` with `faq_verdict=null`. The clinical-safety case is handled a
stage earlier than this phase's gates. `calibration/questions.md` measures it as a rerank-floor
rejection because that sweep bypasses the classifier — worth knowing before reading U7's score as
what the system does.

**Log contract, on a live answered turn**: `pool_size=25`, `pool_returned=9`, `considered: 4 true /
5 false` — matching `similarity_gate kept=4` exactly, which is what the positional-flag defect
(T084) got wrong; `text_truncated: 4 true` (the fifth excluded chunk is under 200 chars and is logged
whole); `rerank_gate floor=0.58 cap=3 kept=2`; `reranking_completed duration_ms=282.69`.

**Both panes, in Chromium**: patient pane — 0 citation blocks, 0 verdict marks, 0 `data-grounded`.
Staff pane, same conversation — 1 citation block, `data-faq-verdict="answered"`, no marker (correct:
it was reranked), and no `0.xx` anywhere in the thread (FR-023g).
