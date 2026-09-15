# Quickstart — taking a run

How to run the golden set and read what comes back. Validation scenarios first in cost order, so the
cheap ones fail before a paid pass starts.

## What this is, and what it is not

**It is a script, run on demand.** `make eval-run` drives every case, records what happened and
computes the report without a person in the loop. The judgement is the scorer's, not a reader's —
which is the whole difference between this and what came before it: 009, 010 and 011 each shipped a
`drive.py` under `specs/` that posted turns and dumped `*.out.json`, and then a person read the
outputs against a written `procedure.md`. The driving was already automatic; the *judging* was not,
because there were no labels to judge against. 2a wrote the labels, so 2b can make the judgement a
program.

**It is not a test tier.** It asserts nothing and nothing fails. It spends live Claude and Voyage
calls on all 135 cases, and its output is non-deterministic, so a number is a measurement rather
than a verdict. Turning a measurement into a gate needs a baseline and a rule for how much
difference is noise — that is 2c's, deliberately (FR-048a, and the spec's Out of Scope).

Three separate things exist once this ships, and they are easy to confuse:

| | What it checks | Deterministic | Cost | Runs in CI | Who judges |
|---|---|---|---|---|---|
| `make test-unit` | the harness's own code, against recorded fixtures and stubbed seams | yes | free | yes | assertions |
| `make eval-run` | the assistant, against the golden set | no | live calls | no, until 2c | the scorer |
| `specs/<n>/evaluation/procedure.md` | what a label cannot capture | no | live calls | no | a person reading |

**The third one survives this phase.** The golden set labels segmentation, intents, source documents,
verdicts, tools and database state — it labels no reply *wording*. So 011's composer constraints
(never soften a gap, never extend an answer to cover one, name a gap in your own words) stay a human
reading a reply, because a model obeys them and no scorer here scores prose. Anything of that kind
in a later phase stays manual for the same reason.

## Prerequisites

1. **The stack, with JSON logs.** `make services-up` starts chat, scheduler and the frontend and
   writes `.run/chat.log`. The chat service must be running with `LOG_FORMAT=json` — the harness
   refuses to start otherwise rather than discovering it 40 cases in
   ([`contracts/log-access.md`](./contracts/log-access.md)). The service states its settings in a
   `service.configured` event when it starts, and that event is where a run's conditions come from.
2. **Databases migrated**: `make migrate`.
3. **Qdrant and the model providers reachable.** A run spends live Claude, Voyage embedding and
   Voyage rerank calls on every case — there is no cheaper run shape by design (FR-049).

## Commands

```bash
make eval-run                     # drive the whole set, write a run artifact
make eval-run CASES=G001,G042     # drive only those cases
make eval-run FAMILY=partial-serving
make eval-score RUN=<run_id>      # re-score a stored run; spends nothing
```

`eval-run` scores what it drove and prints the report; `eval-score` re-reads an artifact and scores
it again. The second is the one to reach for while the scorer is being worked on.

## Validation scenarios

### 1. The label loads (free)

Run the harness's own unit tier: `make test-unit`. It validates `cases.json` against `schema.json`,
checks that `scheduling` is present on exactly the 18 booking cases, that every `given` entry is
plantable against the run clock, and that the corpus hash recomputes from `DEFAULT_FAQ_ENTRIES`
under the construction `corpus.json` now names.

**Expected**: green, with no live call made.

### 2. The pin refuses a drifted corpus (free)

Edit one character of a `DEFAULT_FAQ_ENTRIES` entry and start a run.

**Expected**: it stops **before** the first turn — the only calls spent are the embeddings that seeded
the run's session (FR-011) — names the entry whose text moved, and writes
no run artifact (FR-011). Revert the edit.

### 3. One case, end to end (a few cents)

`make eval-run CASES=G001`.

**Expected**: a run directory holding `run.json` and `cases/G001.json`; the case file carries the
terminal event, both stored messages with `request_outcomes`, the produced segmentation, the turn's
log events, and no exclusion. The printed report shows every metric with a denominator of 1 or with
`not_measured`, and names the metrics it did not compute.

### 4. A case with history (a few cents)

`make eval-run CASES=G034` — its history is a single **assistant** turn, which no published surface
can post.

**Expected**: the thread holds the planted assistant message before the patient's "Sure", and the
turn is classified as small talk against it. This is the case that proves FR-005 works.

### 5. A booking case with a fixture (a few cents)

`make eval-run CASES=G042` — *"Hi, I need to cancel tomorrow"*, with one appointment planted for
tomorrow and the scripted reply *"Yes, please go ahead."* posted as a second turn.

**Expected**: the appointment is still standing after the first turn (`scheduling_before_reply`) and
is `cancelled` after the reply — as the case file's
`scheduling_after` records it, read before the harness cancels whatever the case left standing
(FR-041b); end-to-end task
success is 1/1. Then change the fixture's expected status to `standing` and run the case again
(`make eval-run CASES=G042`). Re-scoring the first run would not do: `scheduling` is a scored field, so
its digest no longer matches and scoring refuses that run (FR-044a). The new run's report must
fail the case and name both halves — the unmatched expectation and the appointment nothing accounts
for (FR-041a).

### 6. The silenced family is excluded, not counted (a few cents)

`make eval-run FAMILY=overriding-segment`.

**Expected**: six of the family's seven cases pair an answerable question with a stopping request and
are excluded from the unserved-answerable share by name; only G115 — which pairs one with an
`unknown` request, and so is *not* silenced — remains in the denominator, and its question is
expected to be answered. The metric reads 0 over a denominator of 1, not 6 over 7 (FR-031a,
SC-006a). A run that reports those six as unserved has the metric definition wrong, not the
assistant.

### 7. The full set (a full pass)

Only once a person has approved the 18 scheduling fixtures (FR-038a). Then `make eval-run`.

**Expected**: 135 case files; alignment totals summing to 190 labelled requests (SC-002); every
metric published with numerator, denominator and exclusions. This is the run committed under
`evaluation/` as the phase's record (FR-048a).

### 8. Re-scoring costs nothing (free)

`make eval-score RUN=<the run from 7>`.

**Expected**: byte-identical metrics, zero model calls (SC-003). Run it with the stack **down** to
prove the scorer needs nothing but the files.

## Reading the report

Three things to look at before any metric value:

1. **The conditions block** — corpus hash, clock, model ids, floors and caps. A number measured
   under different thresholds is not comparable to one measured under these.
2. **The alignment totals** — a large unaligned count means the classification metrics are over a
   smaller set than it looks, and every per-request metric with them.
3. **The exclusions** — by reason. A run with many `missing_log_slice` exclusions is a harness or
   deployment problem wearing a retrieval problem's clothes.

Only then the metrics. The two with a target of zero list the requests behind any non-zero value, so
a finding arrives with the questions to go and fix already attached.
