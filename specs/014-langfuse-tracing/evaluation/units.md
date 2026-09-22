# Langfuse units per turn and per run (FR-030, SC-009)

Measured 2026-09-22 on branch `014-langfuse-tracing` (uncommitted build that became the phase's
commit). A Hobby **unit** is one trace, one observation (span or generation), or one score; this phase
writes no scores, so a turn costs **1 + the number of spans it exports**.

## How it was measured

Offline, with no model call and nothing sent to Langfuse. A throwaway test reused the scripted turns of
`services/chat/tests/test_tracing_non_interference.py` (`_SCENARIOS`, `_run`, `seeded_entry`), plus a
two-request answered FAQ turn and a three-iteration booking turn, ran each through the app into an
`InMemorySpanExporter`, and counted the exported spans by name. The test was deleted afterwards; to
re-measure, recreate it the same way and print `len(exporter.get_finished_spans()) + 1` per scenario.

One correction applies to every FAQ request that reaches reranking: the unit tier's autouse
`_reranking_keeps_what_it_is_given` fake replaces `rerank_chunks`, which is where the `faq.rerank`
span opens, so a real run exports **one more span per such request** than the test counted.

## Per turn shape

| Turn shape | Units (real path) | Where they come from |
|---|---|---|
| Every turn | 5 | trace, `turn`, `classify_intent` + its generation, `compose_answer` |
| Stopping reason (hand-off) | 6 | base + `hand_off` |
| Small talk | 7 | base + `small_talk` + its generation |
| FAQ, one request, answered | 14 | base + `answer_faq` + 8 (request, embed, search, similarity gate, rerank, rerank gate, verdict, generation) |
| FAQ, two requests, both answered | 23 | base + `answer_faq` + 2 × 8 + the composing generation |
| FAQ request abstaining at the similarity floor | 5 per request | request, embed, search, similarity gate, verdict |
| FAQ request abstaining at the rerank floor | 7 per request | as answered, minus the generation |
| Booking | 7 + 2 per tool-using iteration + 1 for the closing one | base + `handle_booking` + roster read, then per loop iteration one generation and one span per tool call |
| Booking, read (1 tool, 2 iterations) | 10 | measured |
| Booking, 3 iterations, 2 tools | 12 | measured |
| FAQ + booking (merged) | 18 | measured 17, + `faq.rerank` |

So a turn costs **6–23 units**, and the old estimate of 15–25 per turn was high for everything except
multi-request FAQ turns.

## Per full golden-set run — expected, not yet observed

Weighted by the golden set's own mix (97 cases, 117 labelled requests; a write case adds its scripted
confirmation turn, 105 turns in all), with these assumptions: an unanswerable FAQ request costs 6 (the
midpoint of stopping at either floor), a booking request runs one iteration per labelled tool plus a
closing one, a `not_authorized`-only turn costs what a hand-off does, and planted history costs
nothing (it is written, not driven).

**Expected: ~1.3k units per full traced run** (13 per case on average; 6 at the least, 32 at the
most). A noise band's five runs is ~6.3k, and the month's 50k covers **~39 full traced runs** — the
old "roughly 3k per run" overstated it by more than half.

| | Value | Status |
|---|---|---|
| Full traced run, expected | ~1.3k units | estimated from the measured shapes above |
| Full traced run, observed | — | **to fill in** after the next full `make eval-run`: note the project's unit count (Settings → Usage) before and after, with nothing else sending traces |

The observed number replaces the expected one here and in `README.md` / `docs/ROADMAP.md` once it
exists; if it lands far from 1.3k, the booking-iteration assumption is the first to check.
