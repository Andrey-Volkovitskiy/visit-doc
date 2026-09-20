# golden-harness

Drives the golden set (`evals/golden/`) through the running chat service as real turns, one case at
a time, stores everything each turn left behind, and scores each **request** against its label —
publishing every metric with its numerator, its denominator and the exclusions it set aside.

Spec: [`specs/012-golden-set-metrics/`](../../specs/012-golden-set-metrics/). The metric definitions
are in its [`contracts/metrics.md`](../../specs/012-golden-set-metrics/contracts/metrics.md), and
[`quickstart.md`](../../specs/012-golden-set-metrics/quickstart.md) walks the validation scenarios in
cost order.

## What it is, and what it is not

**It is a script, run on demand.** `make eval-run` drives the cases, records what happened and
computes the report with no person in the loop: the judgement is the scorer's.

**It is not a test tier.** It asserts nothing and nothing fails. It spends live Claude and Voyage
calls on every case it drives, and the assistant's output is non-deterministic, so a number is a
measurement rather than a verdict. Comparing one build's measurement against another's — and
measuring how much difference is noise before calling any of it a regression — is what `compare`
and `band` below do, offline, over runs already taken.

Four things exist side by side, and they are easy to confuse:

| | What it checks | Deterministic | Cost | Runs in CI | Who judges |
|---|---|---|---|---|---|
| `make test-unit` | the harness's own code, against recorded fixtures and stubbed seams | yes | free | yes | assertions |
| `make eval-run` | the assistant, against the golden set | no | live calls | no | the scorer |
| `make eval-compare` | what moved between two stored runs | yes | free | no | a person reading |
| `make eval-band` | how much the numbers move on their own | yes, given the runs | free (the five runs are not) | no | a person reading |
| `specs/<n>/evaluation/procedure.md` | what a label cannot capture | no | live calls | no | a person reading |

The third survives this harness: the golden set labels no reply *wording*, so a constraint a model is
asked to obey in prose (011's composer rules, for one) is still read by a person.

## Prerequisites

1. **The stack, logging JSON, started by the dev-services script:**

   ```bash
   LOG_FORMAT=json make services-up
   ```

   The harness reads a turn's events out of `.run/chat.log`, which is the file `make services-up`
   writes; `make run-chat-dev` logs to its terminal and leaves nothing to read (point `--log`
   elsewhere if the log lives somewhere else). The console format is for people and cannot be
   parsed as data, so a chat service not started with `LOG_FORMAT=json` stops the run before its
   first turn. Every event the harness reads is logged at INFO, so `LOG_LEVEL` must also be `INFO`
   (the default) or `DEBUG`: above it those events are never written, and the refusals that name
   `LOG_FORMAT` name `LOG_LEVEL` beside it.
2. **Databases migrated:** `make migrate`.
3. **Qdrant and both model providers reachable.** Every case is a full turn — there is no cheaper
   run shape.

## Commands

```bash
make eval-run                          # drive the whole set, then score it and print the report
make eval-run CASES=G-a-01,G-j-03      # only those cases
make eval-run FAMILY=single-faq-gap    # only one family
make eval-score RUN=<run_id>           # re-score a stored run; spends nothing, needs no stack
make eval-build-set                    # re-render evals/golden/cases.json from its declaration
```

`RUN` is a run id under `.run/evals/` or a path to a run directory. Two invocations have no
Makefile target because they take arguments the targets do not pass:

```bash
# continue a run that stopped, in its own session, clock and selection
uv run --package golden-harness -- python -m golden_harness run --resume <run_id>

# score against a labels file other than evals/golden/cases.json
uv run --package golden-harness -- python -m golden_harness score --run <run_id> --labels <path>
```

`run` also takes `--clock` (the local time every turn is sent; default Monday 2026-03-02 08:00, so
the whole working week lies ahead of every fixture), `--artifacts`, `--log` and `--base-url`.
`--resume` refuses `--cases`, `--family` and `--clock`, since a resumed run takes all three from its
`run.json`. A failure the harness has a name for — a run that does not exist, a label that
changed, a run that stopped, a chat service that could not be reached — exits non-zero with its type
and message on stderr rather than a traceback.

## The artifact

```text
.run/evals/<run_id>/          # run_id is a ULID; .run/ is gitignored
├── run.json                  # the conditions: session, clock, selection, corpus check,
│                             #   slug -> entry id map, label digests, the service's settings
├── cases/<case_id>.json      # one per driven case, written whole before the next case begins
├── report.json               # scoring's output, sorted keys
└── report.md                 # the same report, in the order it should be read
```

A case file holds everything a metric could need: the terminal event, the stored patient message and
reply with their `request_outcomes`, the produced segmentation, the turn's own log events, the
patient's appointments after the turn, what the cleanup cancelled, the attempt count, the elapsed
time, the turn-level exclusion if there is one, and — for an `unresolvable_fixture` — why the fixture
would not plant. Every file is written atomically, so an
interrupted write leaves the previous file whole rather than half of a new one. A field left out of
the case file would be a re-run later, at full cost.

A case whose fixture carries a scripted `reply` holds two more fields: `reply_turn` — the reply's own
terminal event, stored patient message and answer, and log events, each on the same rules as the
first turn's fields — and `scheduling_before_reply`, the patient's appointments read after the first
turn and before the reply was posted. `scheduling_after` is then read after the reply. The first
turn's own fields stay the first turn's.

`drive_seconds` is the sum of the cases' own elapsed times, not the span from start to finish, so an
interrupted and resumed run reports the time actually spent.

## Driver, scorer, reporter

- **The declaration** (`golden_set.py`) *is* the golden set: `evals/golden/cases.json` is rendered
  from it by `make eval-build-set`, and `tests/test_golden_set.py` fails byte-for-byte when the two
  disagree, so the set is changed here and re-rendered rather than edited as JSON. It lives in this
  package because `evals/golden/` holds data and no code, and because the invariants JSON Schema
  cannot state — a `cites` id that exists in the corpus pin, a fixture start inside the named
  practitioner's own working hours, an id that agrees with its family — are checked as the set is
  built, naming the case at fault. It is the one module here that no run reads: the driver and the
  scorer both go through `cases.load_cases` against the rendered file, exactly as an outside caller
  would.
- **The driver** (`driver/`) takes a run: it opens one session, verifies the corpus, and for each
  case makes a fresh chat, plants its history and its scheduling fixture, posts the turn, reads back
  what the turn stored and takes the turn's slice of the log — and, for a case with a scripted
  reply, does the same again for the reply (see [The reply turn](#the-reply-turn)).
- **The scorer** (`scoring/`) computes alignment and the classification, retrieval, serving and
  booking metrics. Alignment groups the produced requests onto the labelled ones in order: each
  labelled request takes one, except that a labelled `faq_question` may take several consecutive
  produced `faq_question`s — the classifier split one question, and the reply still answers both
  halves. Such a request counts once everywhere: classified right, retrieved at the best rank either
  half reached, and served only when every half was answered. A split that could be grouped two ways
  leaves the case unaligned rather than guessing which question was split.
- **The reporter** (`report.py`) reads a run directory, calls the scorers and writes the report.

**Scoring is a pure function of a stored run.** It reads `run.json`, the case files and the labels,
and nothing else: no scoring module imports a network client, a database engine or anything from
the driver (`tests/scoring/test_purity.py`), `score` runs with every socket, HTTP client, gRPC
channel and database engine refused (`tests/test_cli.py`), and scoring one run twice produces
reports identical apart from `score_seconds`. Scoring never writes into the stored run. The payoff is
that the arithmetic most likely to be wrong — alignment, denominators, exclusions, MRR — is tested
against small hand-written runs with known answers, and that a metric definition changed later can
be re-applied to every run already on disk, including one recorded weeks earlier, for free.

## Where a run's conditions come from

From the chat service itself. At startup it logs one `service.configured` event carrying its model
identifiers, thresholds, caps and limits, and the harness takes a run's conditions from the latest
such event written before the run began. It never reads them from its own environment, which
describes the harness rather than the process that answered — a service started with an overridden
floor, or still running last week's build, would otherwise be recorded under settings it was not
using. (The harness does read the chat service's `Settings` for *where* to connect: the database URL
and the scheduler's gRPC address.)

A later `service.configured` event means the service restarted mid-run. The harness reads every line
the log gains after the conditions were read — before each turn is posted, on every poll of a turn it
is waiting on to settle (see [Cleanup between cases](#cleanup-between-cases)), and again before each
case is written, so a restart while a chat is set up, between two attempts, during a cleanup or in an
attempt that recorded no slice is seen as surely as one inside a turn, and one during a settle wait
stops the run on the poll that sees it rather than once the wait runs out. With the same settings the
run carries on; with different ones it stops, and the case it was driving is not written — even a
fixture case whose turn was already posted. Resuming posts that case again, in a fresh chat: the restart ended
the process running its turn, so nothing of it can land later, and the resume sweep releases that
chat's patient, cancelling whatever the turn did book, before any case is driven. This is the one
stop after which a posted fixture case is driven again. A resumed run reads the conditions again and
stops if they differ from `run.json`'s.

`make services-up` restarts the chat service with `> "$log"`, which empties the log file in place
rather than replacing it, so after a restart the log can grow back past the byte the harness last
read to. The harness therefore keeps the bytes just before that offset beside it: a log that is now
shorter, or that holds other bytes there, has been replaced and is read from its start, and a turn
whose slice was taken across such a replacement is a `missing_log_slice`. A replacement holding the
very same bytes before the offset is not told apart.

Those field names are a data contract with this harness — see
[`contracts/log-access.md`](../../specs/012-golden-set-metrics/contracts/log-access.md).

## The reply turn

The booking loop acts on an instruction and offers on a question: "please cancel my Friday
appointment" is cancelled in one turn, while "can I book Wednesday at 9?" gets the availability and
an offer, and writes nothing. It also offers when the patient's words leave the choice open - a day
with no time. Where a message sits between the two, either is a correct first turn, so the scripted
`reply` a fixture carries is **optional to the case**: it is posted only when the first turn did not
already do the task. The harness drives such a case as an exchange:

1. The first turn is posted, read back and sliced exactly as any other.
2. Only if it ended with a reply of its own — a stored reply, with a `done` terminal event that is
   not a hand-off or, for a turn whose answer did not arrive whole and that settled with a stored
   reply, with no terminal event at all and no hand-off in its own `turn.completed` (see below) —
   the patient's appointments are read and stored as
   `scheduling_before_reply`. A first turn that handed off, went silent, was cancelled or failed
   without a reply keeps its own exclusion, and no reply is posted.
3. If those appointments already match the fixture's `expect`, the first turn did the task: no reply
   is posted, the case records `reply_skipped: true`, and it continues at step 5 with nothing
   further driven.
4. Otherwise the reply is posted verbatim in the same chat, with the run clock. Its slice is taken by the same
   byte offset and `turn_id` rule, selected by the reply's own patient message, and its thread read
   passes over the history and the first turn's two messages. The log is checked for a restart
   before it is posted, as before every turn.
5. The appointments are read again, as `scheduling_after`; then the patient is released, once, and
   the case written. `elapsed_seconds` covers every turn driven.

**An attempt is the whole exchange.** A reply that provably never reached the pipeline (no
connection, a 429 or 5xx) drives both turns again in a fresh chat, once the patient is released, and
is a `run_error` when the attempts run out. A reply whose answer did not arrive whole — its stream
broke off, timed out or broke the contract — is waited on to settle exactly as a first turn is, below:
one that settles is measured, and one that does not is `outcome_unknown` and stops the run. A reply
whose thread could not be read back or whose segmentation broke the record's shape is written as
`outcome_unknown` and then stops the run, as for a first turn. A measured reply turn is judged by the
first turn's rules — silent, cancelled, `assistant_failed` without a stored answer,
`classification_failed` or no selectable slice exclude the case — unless the first turn was already
excluded, whose reason is kept. That includes a hand-off: a reply turn that ends `done` with
`answer_source == "hand_off"` records `handed_off_turn` on the case, which sets the whole case aside
from retrieval and serving — the first turn's requests included — and leaves it scored for
classification and booking, both reads stored as usual. A first turn that hands off is unchanged: no
reply is posted, and the case is scored on booking against `expect`. A read after the first turn
that fails stops the run before the reply is posted, with the case unwritten.

**How it is scored.** End-to-end task success reads the appointments after the case's last driven
turn - the first turn when the reply was skipped, the reply otherwise - and needs them to match
`expect` under the exhaustive matching. `scheduling_before_reply` is what decided whether to post the
reply, and is not itself scored: a first turn that wrote part of the task, or all of it, before a
reply that finished it is a success. A failure lists both halves of that one read. Tool selection
counts the `booking.tool_called` events of every driven turn together, as one booking half, so a case
the loop finished in one turn is scored on that turn's calls. Classification and retrieval read the
first turn only: the reply is not a labelled message.

## Cleanup between cases

A run's cases share one session, and so its two seeded practitioners. An appointment one case left
standing could refuse a later case's precondition, or take the slot a later turn asks for. So once a
case's post-state is stored, the harness lists that patient's standing appointments and cancels each
one through the scheduler's own `CancelAppointment`, recording what it cancelled on the case. This
applies to **every case whose chat has a patient**, not only those with a fixture — a turn misrouted
into the booking loop can book too. An attempt about to be driven again is cleaned up first, and an
attempt that stops the run once its chat's patient has been read releases that patient before it
stops (see [What stops a run](#what-stops-a-run)) — except a turn that was not seen to end, below.

**A turn whose answer did not arrive whole settles before it is released.** When a turn's stream broke
off, timed out, or broke the service's contract mid-answer (a `TurnProtocolError` while reading it),
and its thread holds neither a reply nor the `assistant_failed` mark, the turn may still be running on
the server, and could book after a cleanup, in a later case's way. So for that turn — a first turn or
a reply, with a fixture or without — the harness polls the chat's thread every
`SETTLE_INTERVAL_SECONDS` (5 s) until the thread holds a stored reply to that turn's patient message
or the message carries `assistant_failed`, and only then reads the patient's appointments and
releases the patient. The thread does not say which patient message a reply answers, so the reply is
recognised by the chat's shape: each poll passes over every message the chat held before the turn,
and a thread holding more than one patient message or reply besides those, or a reply with no patient
message, is refused — so the one reply a poll can return answers this turn. The wait is bounded by
`SETTLE_TIMEOUT_SECONDS` (60 s), measured on the run's timer.

**A turn that settles is judged as a completed turn**, since its outcome is then known: its thread is
the one the poll read, its log slice is taken by the usual offset and `turn_id` rule, and its
exclusion is derived as for any turn — a stored reply is scored, `assistant_failed` without a reply
is a `run_error` — its appointments read and its patient released as usual, and a reply case goes on
to its reply. It is never retried and never `outcome_unknown`. Its `terminal` is `error` when its
stream broke off or timed out, and absent when the stream broke the contract, since no ending was
read whole; `stream_broke_contract` is `true` exactly in the second case, so the two are never told
apart by guessing from what else the record holds.

**Whether such a turn handed off is read from its own log slice** (FR-041d), since only a terminal
event says so and none was delivered. A turn that settled — or whose thread already showed it ended
on its own read — with no terminal event is taken to have handed off exactly when its selected slice
holds one `turn.completed` and that event's `outcome` is `handed_off` (the value the chat service's
own `_OUTCOME_BY_SOURCE` gives `AnswerSource.HAND_OFF`, imported rather than copied). It is then
treated as any handed-off turn: a first turn posts no reply and records `handed_off_turn`, still
scored on booking against `expect`; a reply turn records `handed_off_turn` on the case. Another
outcome, no `turn.completed`, several of them (the service logs one per turn, so several say
nothing), or no selectable slice do not make it a hand-off. A turn that did deliver its terminal
event is judged by that event alone, whatever its `turn.completed` says.

A turn still unsettled at the bound writes its case as `outcome_unknown` — with or without a fixture,
its appointments read and stored when it has one — does **not** release its patient, since a release
could not stop a turn still running from writing afterwards, and stops the run with
`TurnUnsettledError`; the resume sweep releases that patient before the next case. A thread read that
fails for such a turn — its own read straight after the stream, or a poll — stops the run with that
error, the case written as `outcome_unknown` when it has a fixture and left unwritten when it has
none, and the patient again not released. Every poll also checks the log for a restart: one with
other settings stops the run on that poll, the case unwritten and its patient released, since the
restart ended the process running the turn (a poll whose read failed beside it stops as the restart,
chained to the read's error); one with the same settings is passed over.

**That is why the scheduler shows nothing standing after a run**, and why every appointment the run
touched is `cancelled` rather than gone: a cancelled appointment leaves the partial exclusion constraint
(`WHERE status = 'standing'`), so its slot is free at the datastore, while the row stays. A case's
`scheduling_after` is read *before* the cleanup, so it is what the turn left behind. The run never
deletes its session.

A cleanup that cannot complete writes the case and then stops the run: every later case would be
measured against a calendar the run no longer controls. A resumed run repeats the cleanup for every
case already recorded, and for every other patient of the run session's chats — read from the chat
database, scoped by the session, so an attempt that stopped the run unwritten is covered even when
its own release failed — before it drives another, and drives nothing if that fails.

## What stops a run

Before anything is spent on a turn:

- a selected fixture that could not plant on the run clock (checked before the session opens);
- no `service.configured` event in the log, or a service not logging JSON;
- a session that cannot be opened or whose corpus cannot be read — `POST /chats` or `GET /faq`
  answering an unexpected status, or `POST /chats` minting no session (`SessionError`), or the
  service not reached or not answering (an `httpx` error) — with no `run.json` written;
- a live corpus that does not match `evals/golden/corpus.json`'s pin — named by the entry that
  moved, with no `run.json` written — or a pinned text with no single live entry
  (`UnmappedCorpusEntryError`). Opening the session seeds its corpus, so the embedding calls that
  seeding makes are the only thing spent;
- on resume: a selected case whose scored fields changed, other service settings, a corpus that no
  longer matches the pin or whose pinned texts now map to other entry ids, or a recorded case's
  patient, or any other patient of the session's chats, that cannot be released;
- a restart with other settings after the conditions were read (`run.json` is written by then).

Between cases, with the stopping case left unwritten unless said otherwise. Once the case's chat's
patient has been read, each of these stops releases that patient first — except the two that come
for a turn whose answer did not arrive whole and that was not seen to end — and a release that fails
then stops the run as a `CleanupFailedError` chained to the original error.

- a harness fault — the service answered the turn with a status other than 429 or a 5xx (a 404
  or a 422, say), which is the harness's own mistake and is never retried;
- a restart with other settings at any point before the case is written — while its chat is set
  up, during its turn, while its turn is waited on to settle, between two attempts, or during its
  cleanup;
- a measured fixture case whose appointments cannot be read afterwards — after its last turn, or
  after the first turn of a case with a reply, which is then not posted;
- a cleanup that cannot complete (the case *is* written first — unless the service also restarted
  with other settings, when it is not);
- a case chat that cannot be made or found — `POST /chats` answering an unexpected status or minting
  a new session (`SessionError`), or the chat not found in the run's session when its patient is
  read or its history planted (`ChatNotFoundError`);
- a thread that cannot be read back — `GET /chats/<id>/messages` answering other than 200, or
  answering 200 with a body that is not JSON or breaks the thread's schema (`ThreadReadError`), or
  holding what one turn could not have left (`TurnProtocolError`);
- the service not reached, or not answering, while a case chat is created or its thread read back
  (an `httpx` error). The same failure while *posting* the turn is not a stop: before a connection it
  is an attempt that measured nothing, and once the stream began it is a turn waited on to settle,
  below;
- a turn whose logged segmentation breaks the record's shape (a pydantic `ValidationError`) — a
  disagreement with the log contract;
- a turn whose answer did not arrive whole that has not settled at the bound (`TurnUnsettledError`)
  — the case *is* written first, as `outcome_unknown`, and its patient is not released — or whose
  thread cannot be read before it settles (a `ThreadReadError`, `TurnProtocolError` or `httpx`
  error), with the patient not released either (see [Cleanup between cases](#cleanup-between-cases)).

A stream breaking the service's contract — a line that is not JSON, an unknown event, an invalid or a
second terminal event (`TurnProtocolError`) — is not a stop of its own: the turn is waited on to
settle like one whose stream broke off.

For a case carrying a fixture, a thread that cannot be read back, the service not answering while it
is read back, or a logged segmentation breaking the record's shape *does* write the case, as
`outcome_unknown`: its turn was posted and may have booked, so its appointments are read (and stored
when they can be) and released — unless its answer did not arrive whole, above — the case is
written, and only then does the run stop, so a resumed run never posts that turn again. It is left
unwritten only when the service also restarted with other settings, and the restart then stops the
run, chained to the original error. A case with no fixture is still left unwritten and posted again
on resume, in a fresh chat, with its patient released under the same exception.

**What is retried and what is not.** Only an attempt that provably never reached the pipeline — no
connection, or a 429 or 5xx instead of a stream — is driven again in a fresh chat, at most three
attempts in all, and a case still unmeasured after three is a `run_error`. A turn that completed is a
result and is never retried, including one that failed. A stream that began and then broke off, timed
out or broke the contract is never retried either, with a fixture or without: it is waited on to
settle and then judged as a completed turn, or, unsettled at the bound, recorded as `outcome_unknown`
and the run stopped — a timeout never proves the server did nothing.

## Exclusion reasons

Each situation has its own reason, and each reason its own scope. Seven are decided per turn and
stored on the case; four are decided per request at scoring time and never stored.

| Reason | Set when | Excluded from |
|---|---|---|
| `run_error` | every attempt measured nothing — for a reply case, no attempt measured the whole exchange; a cleanup failed between two attempts; a turn marked `assistant_failed` that stored no reply; a produced `classification_failed` | every metric |
| `silenced_turn` | the stream ended `silent` — the conversation was already silenced, so nothing classified | every metric |
| `cancelled_turn` | the stream ended `cancelled` | every metric |
| `missing_log_slice` | the turn's events could not be read or selected, or hold no single classification | every metric |
| `unresolvable_fixture` | the fixture would not plant, or the chat has no patient; no turn was posted | every metric |
| `outcome_unknown` | a fixture case's turn — the first or the reply — was posted and its thread could not be read back (for a turn whose answer did not arrive whole, before it settled) or its logged segmentation broke the record's shape; or any case's turn whose answer did not arrive whole and that did not settle within the bound | every metric |
| `handed_off_turn` | the stream ended `done` with `answer_source == "hand_off"`, or — for a turn that ended with no terminal event — its own `turn.completed` has `outcome == "handed_off"`; the first turn's, or a reply turn's when the first turn has no reason of its own | retrieval and serving only, the first turn's requests included — it classified before it handed off, so classification scores it, and booking scores it too |
| `no_search` | the turn's corpus was empty, so no search was issued (`turn.retrieval_skipped_empty_corpus`) | both retrieval stages |
| `not_routed_to_faq` | a labelled `faq_question` request aligned to one produced request under another intent, so it never reached the FAQ half | both retrieval stages |
| `not_reached_reranker` | none of the request's cited chunks was kept by the similarity gate | the rerank stage only |
| `reranker_unavailable` | the reranker was unavailable for the request | the rerank stage only |

The report lists every `unresolvable_fixture` case with why it would not plant — a name not on the
roster, a roster that could not be read, a booking the scheduler refused (with its failure reason and
message), a booking not answered or answered unreadably, or a chat with no patient — so a label
problem is told apart from a scheduler that was down. A case recorded before that was stored is
listed as not recorded.

A turn marked `assistant_failed` that still stored a reply is **not** excluded: it is scored on what
it left behind, and the report lists its case by id with which turn it was — `first`, or `reply` for
a scripted reply's turn (`assistant_failed_with_reply`, and `assistant_failed_with_reply_turns` beside
it). A turn whose stream broke the service's contract and that then settled — stored a reply, or was
marked `assistant_failed` — is scored like any settled turn, and listed the same way
(`contract_broken_then_settled`, and `contract_broken_then_settled_turns` beside it), so a service
emitting a malformed stream shows in a run that otherwise finishes cleanly (FR-041d). A stream that
only broke off or timed out is not listed, and neither is a broken stream that never settled, which is
already `outcome_unknown`. An exclusion is never defaulted to a miss — a
`missing_log_slice` counted as a retrieval failure would report a harness or deployment problem as
retrieval getting worse.

## Label digests, and why re-scoring can refuse

`run.json` records a sha256 per selected case over the fields scoring reads: `id`, `message`,
`history`, each request's `intent`, `answerable`, `cites` and `tools`, and `scheduling` (its `reply`
included). `gist`,
`note`, `source` and `family` are left out, because nothing scores them.

Scoring recomputes the digests from the labels it is given and **refuses** a run whose digests
differ, naming the cases. The stored turns answered those labels and no others, so a run re-scored
against a changed intent or fixture would publish numbers nobody measured. A corrected `gist` or
`note` leaves every run re-scorable. The consequence to plan around: once a scored field of a case
changes, runs taken before the change cannot be re-scored against the new labels, and measuring that
case again means a new run. A resumed run refuses a changed label the same way.

## Comparing two runs, and measuring the noise (spec 013)

Two more commands, both **offline**: they read stored runs and the labels and nothing else, so they
work with the whole stack down and spend no model call.

```bash
make eval-compare BASE=<run> NEW=<run>              # what moved between two stored runs
make eval-compare BASE=<run> NEW=<run> BAND=<band>  # ... and whether it is outside the noise
make eval-band RUNS=<id>,<id>,<id>,<id>,<id>        # measure the noise from five full runs
```

`BASE` and `NEW` each take a run id under `.run/evals/` **or** a directory path, so the run
committed under `specs/012-golden-set-metrics/evaluation/` is usable as a baseline where it sits.
It is the *documented* baseline a comparison starts from, and naming it is the caller's job: neither
command ever defaults to a path under `specs/`.

### What a comparison is

The report is printed and written, in this order, which is the order it should be read in: both
runs, the condition delta, the coverage restriction, the band, alignment and exclusions, every
metric, then the case movements beneath them and the cases that moved under a metric that did not.

- **It re-scores both runs.** Its inputs are each run's `run.json` and `cases/*.json`; it never
  reads either run's stored `report.json`. That is what lets this phase add a field without the
  committed 2b record becoming unparseable, and what makes a narrowed comparison honest — a metric
  is recomputed over the cases both runs recorded, never quoted from a published report.
- **It writes into neither input run.** The record goes to
  `.run/evals/comparisons/<base>__<new>/comparison.json` and `comparison.md`. A read does not add
  files to what it read, and the new run may be the committed baseline or someone else's future one.
- **It always exits zero.** A finding is in the report, never in the exit code, and there is no flag
  that changes that. Refusing to compare at all is a different thing and exits 1.
- **It is reproducible.** Comparing the same pair twice produces the same record apart from
  `compared_at` and `compare_seconds`.

### What a comparison is not

- **Not a verdict on a build.** There is no overall score and no `regression` flag. Without a band
  the report describes movement and nothing more; the words *regression*, *better* and *worse* do
  not appear about a metric.
- **Not a gate.** Nothing here fails a build, in CI or anywhere else.
- **Not a statement about the whole set when it was narrowed.** A comparison over four common cases
  says so, with the count, before any metric.

### What stops a comparison

| Situation | What happens |
|---|---|
| A label's scored fields moved since a run was taken | stops, naming the cases — 2b's own refusal, inherited rather than restated |
| The two runs recorded no case in common | stops, saying so |
| A run directory is missing or unreadable | stops, naming it |
| Conditions differ — a floor, a model, the corpus hash, the run clock | **reported, not refused**: a threshold change is usually the change under test |
| One run recorded fewer cases than it selected | **reported as incomplete**, with the count |

### What a band is

The spread of the golden set's metrics across **five full runs of one unchanged build** — the
answer to "how much do these numbers move when nothing changes?". It records every metric's five
values in run order with its lowest and highest, and per case that varied, each state with how many
of the five produced it. `make eval-band` refuses anything that contradicts "these five differ only
by chance": a count other than five, one run named more than once, a run that drove part of the
set, a run that stopped partway and recorded fewer cases than it selected, or any difference in
conditions, corpus hash, run clock, label digests or case set — each naming what differs. The incomplete-run refusal is the one asymmetry
worth knowing: a *comparison* reports such a run as incomplete and carries on, because a narrower
case set is still comparable, while a band cannot — its value would sit in a range beside four
measured over a different population.

A band is **not a threshold**: no run is required to beat it, and nothing reads a band value as a
pass mark. It is **not a statistical interval**: five observations give a range and a frequency
count, not a standard deviation or a significance test, and every rendering citing it says so. A
movement inside the range is *not shown to be noise*; it is *not shown to be more than noise*.

A band applies to a comparison only when its conditions, corpus hash, run clock and case set match
**both** runs; otherwise the comparison says the band was measured under other conditions and marks
nothing.
A metric the band never observed is reported unmarked, never assumed stable.
