# Feature Specification: Metrics Over the Golden Set (Phase 2b)

**Feature Branch**: `012-golden-set-metrics`

**Created**: 2026-09-12

**Status**: Draft

**Input**: User description: "Create a spec for @docs/ROADMAP.md phase 2b" — Phase 2b, "Metrics over
the labeled set": run the golden set through the real system, score each **request** against its
label, and report every metric with the denominator it was computed over. No CI gate (2c), no
tracing (2d).

## Why this exists

Phase 2a shipped 135 labelled messages carrying 190 labelled requests, and said so plainly: *"It is
data. There is no runner here, and nothing imports it yet."* That was the right place to stop, and
it leaves the project with three things that are asserted and nothing that is measured.

1. **A label nothing scores is a claim nobody checks.** The set's own `README.md` ends by naming
   G020 as the likeliest label in it to be wrong and instructing a reader to *re-measure it*. There
   is nothing to re-measure it with. The same is true of the other 189: consolidation re-checked
   labels wherever the corpus text moved, by hand, and every label that did not move was inherited
   from the phase that wrote it. Until something runs them, the set is a well-documented opinion.
2. **The per-request record has never been read in aggregate.** Phase 1h moved the verdict, the
   answer and the citations onto the request precisely so that a turn answering one question and
   abstaining on another could be described truthfully. One turn at a time, in the console, that
   works today. Nobody has ever asked the record the question it was shaped for — *across 190
   requests, how often is each one served?* — because nothing reads more than one message.
3. **1e's logs were declared 2b's input and have never been consumed.** Phase 1e's field contract
   (`specs/008-reranked-retrieval-pipeline/contracts/log-events.md`) says in its own header that it
   is *"the input contract Phase 2 will compute metrics from, so field names are part of the
   contract, not an implementation detail"*. Two phases later, no reader exists. A contract with no
   consumer has never been wrong, which is not the same as being right.

This phase builds the one thing that closes all three: a harness that drives the golden set through
the running system, stores what each turn actually did, and computes the metrics `docs/ROADMAP.md`
names — per request wherever a request is what the system decides about.

**Two things it deliberately is not.** It does not gate anything: failing a build on a metric
regression is 2c, and a gate needs a baseline that only a first run can produce. And it does not
tune anything: 2b measures the system as it stands, so a metric that comes back poor is a finding,
not a licence to move a floor inside this phase. The thresholds a run measured under are recorded
*with* the numbers, which is what makes a later comparison mean anything.

## Clarifications

### Session 2026-09-12

- Q: How does the harness drive a golden case through the system — in-process through the agent
  graph, or over the running HTTP API? → A: **Over the HTTP API.** The roadmap's own words for what
  2b computes from are "the per-request record 1h moved onto the message", and that record is a
  stored row, reached by posting a turn and reading the thread back. An in-process call to
  `run_turn` would score an event the graph yielded, which is the same data one layer before anyone
  persisted it — so a bug between the graph and the row would be invisible to every metric. The
  HTTP seam also exercises the escalation writes and the silencing rules, which several families of
  the set exist to produce. The cost is that the five cases carrying `history` cannot be planted
  through any published surface — three of them are assistant-role, and the console posts as staff
  — so the harness writes those rows into the chat database directly, which is stated as a
  capability rather than discovered later as a workaround.
- Q: End-to-end task success needs an expected database state, and the golden set carries none: 9 of
  its 19 booking requests presuppose an appointment nothing creates ("cancel tomorrow", "move my
  Thursday appointment"). What does this phase do? → A: **Add scheduling fixtures to the golden
  set.** The case schema gains a per-case precondition — the appointments to plant before the turn
  — and a per-case expected post-state, and the 19 booking requests are labelled by hand. Scoring
  the metric over only the four requests that need no precondition would report "end-to-end task
  success: 100%" from a denominator of four, which is the shape of value this project removed from
  the turn's verdict in 1h: a number that is right about the fraction it covers and read as though
  it covered the whole. The fixture is data, it belongs beside the labels it makes scorable, and it
  extends 2a's artifact rather than duplicating it.
- Q: A full pass spends live Claude, embedding and rerank calls on 135 cases. What run shapes does
  the harness offer? → A: **One shape — every case is run as a full turn.** A cheap classifier-only
  tier was considered and rejected: it would have had to call the classification step directly,
  since a posted turn always runs its whole pipeline and a paused conversation classifies nothing,
  and that makes the classification metrics a measurement of a *different execution path* from every
  other metric in the report. A segmentation number taken from a step invoked in isolation cannot be
  set beside a retrieval number taken from a real turn and treated as describing the same system.
  Uniformity is worth more than the saving: every case goes through the same path, every metric is
  computed from the same run, and the only lever on cost is running fewer cases (FR-008). What the
  cheap tier was really buying — looking at classification without paying again — is bought instead
  by FR-044: scoring is a pure function of a stored run, so classification can be re-scored against
  runs already on disk for nothing.
- Q: What happens to a request whose produced segmentation does not line up with the labelled one?
  → A: **It is reported as unaligned, never scored and never dropped.** Per-request metrics align by
  position, and position only means something when the counts agree. A run accounts for every
  labelled request in exactly one of three ways — aligned, unaligned, or excluded with a named reason
  — and the counts are published beside every metric. Quietly scoring a mismatched pair measures
  phrasing; quietly dropping it flatters the denominator.
- Q: What does a scheduling fixture's expected post-state compare? → A: **The complete set of the
  patient's appointments after the turn.** Each expected appointment is matched on the fields the
  label names — practitioner, date, optionally an exact time, status — and the case passes only if
  every expected appointment matched exactly once and no appointment is left over. Two cases name no
  exact time ("the earliest slot you have", "book one Friday"), which is why a label states the
  fields it knows rather than a full instant. Listing individual claims and ignoring everything else
  would let an appointment the agent invented but never mentioned pass silently, and a single
  changed/unchanged flag could not say what was wrong when it failed. "Nothing changed" needs no
  second rule under this one: it is the precondition set restated.
- Q: Are cases driven one at a time, or several at once? → A: **Strictly sequential.** All cases
  share one session, and the scheduler enforces its exclusion constraints per practitioner across
  that whole session — so two concurrent booking cases can contend for one practitioner's slot and
  record a scheduling conflict as an end-to-end failure the booking loop had nothing to do with.
  Rate limiting does the same thing to retrieval and generation. A metric that can come out
  differently because of how fast the harness ran is not measuring the system, and the saving in
  wall-clock time is not worth buying that with.
- Q: What happens when a single case fails part-way through a run? → A: **Retry only what stopped
  the measurement from happening.** A transport failure, a rate limit or an unavailable service
  means no turn was measured, so re-taking it is legitimate — bounded, and recorded as an attempt
  count on the case. A turn that *completed* carrying `assistant_failed` is never retried: the
  system broke while answering a patient, which is a result, and re-rolling it would launder a
  failure out of the record. It is the same line `agent/escalation.py` already draws between a
  request that was served badly and one that never reached the server. *(Narrowed on 2026-09-14 for
  cases carrying a scheduling fixture, and for a failed turn that still replied — see that session.)*
- Q: Does any run's report enter the repository? → A: **The first full run's report is committed
  under `specs/012-golden-set-metrics/evaluation/`, and nothing else is.** It is this phase's frozen
  record — what the system measured when the harness was built — which is the same artifact 1e's
  calibration sweep and 1f, 1g and 1h's evaluation inputs each are, kept in the same place for the
  same reason. It is deliberately not installed as a baseline: nothing has measured run-to-run
  variance yet, and a single sample promoted to the thing later runs are judged against would be a
  threshold chosen by one roll of the dice. What a baseline is, and where it lives, is 2c's to
  decide.

### Session 2026-09-14

Nine questions the cross-artifact analysis of spec, plan and tasks raised.

- Q: A handed-off turn classifies before it hands off. Is its classification scored? → A: **Yes.**
  It is excluded from the retrieval and serving metrics, which have nothing to score on it, and
  stays in every classification metric. Excluding it everywhere would leave `urgent_condition`,
  `distress`, `booking_for_another` and `call_staff` — the labels the `safety-and-authority` family
  exists to check — without a single scored request. Every other turn-level exclusion still removes a
  case from everything (FR-018a).
- Q: A booking case's stream drops after the request was sent. Is it retried? → A: **Not if the
  request may have reached the pipeline.** For a case carrying a scheduling fixture, only a failure
  that proves the pipeline never ran is retried: the connection was never made, or the service
  answered with a status instead of a stream. Anything else may have left an appointment behind that
  nothing can un-book and that the next attempt would contend with for its slot, so the case is
  recorded as *outcome unknown* and not retried (FR-007d). A case with no fixture writes nothing a
  retry can collide with, and is retried as before.
- Q: The reranker only sees what the similarity gate kept. What do rerank-stage metrics measure? →
  A: **The reranker's own ranking, and nothing the gate did.** They are scored only over requests
  whose cited chunk reached the reranker, and what the gate lost is published as its own number,
  similarity-gate survival (FR-029a) — so a chunk dropped by the floor is charged to the floor.
- Q: Where do a run's model identifiers and thresholds come from? → A: **From the running chat
  service, which states them at startup** (FR-047c). A value read from the harness's own environment
  would describe the harness, not the process that answered, and the two can disagree without
  anything noticing. This is the second, and last, change FR-051 permits.
- Q: Creating the run's session seeds its corpus, which spends embedding calls, before the corpus can
  be checked. Does FR-011 forbid that? → A: **No — the guarantee is about the turns.** A mismatched
  corpus stops the run before the first turn's model call. The embeddings that seed a session are the
  price of having a live corpus to compare, and they measure nothing.
- Q: A booking tool failure marks the patient message `assistant_failed` but still stores a reply.
  Is that turn a run error? → A: **No — a run error is a turn that failed without a reply.** A turn
  that replied is scored on what it left behind, so a loop that apologised for a failed tool and
  booked nothing is an end-to-end failure, not an exclusion. The report lists such turns (FR-007e),
  so a run taken through a scheduler outage shows as one rather than as a booking defect.
- Q: A stored run is re-scored against whatever labels are on disk. Does the run record which labels
  it was taken against? → A: **Yes, and scoring refuses a mismatch** (FR-044a). Re-scoring a
  committed run against labels edited afterwards would produce numbers about a set nobody ran.
- Q: Are the eighteen hand-written scheduling fixtures reviewed before the first full run spends on
  them? → A: **Yes — by a person, before that run** (FR-038a). They are the ground truth end-to-end
  task success is scored against, and a wrong one would be committed with the phase's record.
- Q: Cases run one at a time, but they share one session, so an appointment one case leaves
  standing still holds its slot for every later case. What stops that from moving a later case's
  result? → A: **The harness cancels what each case left standing, once its post-state is stored**
  (FR-041b). A cancelled appointment stops occupying its slot at the datastore, the stored record
  still says what the turn did, and the session is never deleted. Labelling fixtures apart was
  rejected because a turn asked for "the earliest slot you have" chooses its own time, and a session
  per booking case because it would give up the one verified corpus FR-003 exists for.

### Session 2026-09-14, after the first implementation

- Q: The booking loop confirms before it writes — "NEVER call cancel_appointment without an explicit
  confirmation from the patient given in the CURRENT turn" — so a single turn asked to cancel or
  book correctly writes nothing. What do the seven cases labelled with a write measure? → A: **A
  scripted patient reply, posted as a second full turn in the same chat** (FR-037b). After the first
  turn the calendar must still be what was planted; after the reply it must match `expect`. Labelling
  the seven "unchanged" was rejected: no case would then write anything, and the one metric that
  reads on a write path could never catch an agent that confirms and then fails to write. Tool
  selection is scored across both turns, which makes the 2a `tools` labels correct as they stand;
  classification and retrieval read the first turn only, since the reply is not a labelled message.
- Q: `no_search` was used both for a turn whose corpus was empty and for a labelled FAQ request the
  classifier produced under another intent. → A: **Two reasons** (FR-018a): `no_search` keeps the
  empty corpus, and a request never routed to the FAQ half is excluded from retrieval as
  `not_routed_to_faq`.
- Q: Several booking messages leave the practitioner open ("any slots on Monday?", "the earliest slot
  you have") or name the clock's own weekday ("Monday at 9" on a Monday-08:00 clock). What do the
  labels do? → A: **Reword the messages** (FR-038b): a message whose tool needs a practitioner names
  one, since `check_availability` and `book_appointment` take a practitioner id and the loop may not
  choose for the patient; and no booking message uses the clock's weekday, since a label can only
  mean one of its two readings. G044, G062, G088, G094, G095 and G102 name William Osler; "Monday"
  became Wednesday. G094 stays read-only.
- Q: G062 asks to book but names no time and carries no reply, so its `book_appointment` label can
  never be met in one turn. → A: **Give it a reply** — "The earliest Wednesday time is fine, yes
  please book it." — and expect a standing Osler booking on Wednesday, any time. It is the eighth
  case whose write is measured over a reply turn (FR-037b).
- Q: A reply turn can itself hand off. Is that a booking failure, or a handed-off turn? → A:
  **A handed-off turn** (FR-037b): the case records `handed_off_turn`, which excludes it from the
  retrieval and serving metrics as well — the first turn's included — and leaves it scored for
  classification and booking (FR-018a). A first turn that hands off is not changed by this: no reply
  is posted and the case is scored on booking against `expect`.
- Q: When a turn's answer times out, the harness releases the patient straight away — but the turn
  may still be running on the server and book after the cleanup, leaving an appointment in a later
  case's way. → A: **Wait for the turn to settle** (FR-041c). Within a bound, the harness polls the
  chat until the turn has visibly ended — a stored reply to it, or the `assistant_failed` mark — and
  only then reads the calendar and releases; a turn that never settles stops the run, since the
  calendar can no longer be vouched for. This is *a timeout never proves the server did nothing*
  applied to the cleanup.
- Q: A restart with other settings stops the run before a posted booking case is written, and resume
  posts that case again — which FR-007d otherwise forbids. → A: **Re-posting is allowed for this one
  stop** (FR-007d). The restart ended the process running the old turn, so nothing of it can land
  later, and anything it did write belongs to a patient the resume sweep cancels before driving
  again; FR-007d's reason does not apply, and the case is measured under the run's own settings.
- Q: How does the settle wait behave in detail? → A: **Poll every 5 seconds for at most 1 minute**;
  a stream that breaks the service's contract mid-answer waits too, since that turn may also still
  be running; a turn that visibly finishes while being waited on is scored as a completed turn
  rather than recorded as outcome unknown, because its outcome is then known; and every poll checks
  for a service restart, so a restart stops the run at once instead of after the full wait
  (FR-041c).
- Q: A turn that settles while waited on never delivered its terminal event, so two things are lost:
  whether it handed off, and any sign that its stream broke the service's contract. → A: **Read the
  hand-off from the log, and list the broken streams** (FR-041d). A settled turn without a terminal
  event is taken to have handed off when its own `turn.completed` carries `outcome: handed_off`; and
  the report lists by id, with the turn, every case whose stream broke the contract and then
  settled, so a service emitting a malformed stream is visible in a run that otherwise finishes
  cleanly.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Score what the classifier decided (Priority: P1)

A developer who has just changed the classification prompt runs the golden set. Every case is posted
as a real turn, the harness stores everything that turn produced, and the report says how often the
number of requests was right, how often their order and intents were right, and which cases
disagreed — by id, with the labelled segmentation beside the produced one.

**Why this priority**: It carries the whole measurement loop — drive, record, align, score, report —
and every later story is a second reading of the runs it already stored. Segmentation is also the
thing with the least evidence behind it today: 1g's cap of 3 rests on a single message, and
`PROVENANCE.md` records that 010's segment intents had to be labelled by hand here rather than
lifted from committed model output, *because promoting model output to a label would make
segmentation accuracy unfalsifiable*. This story is what makes it falsifiable.

**Independent Test**: Run the whole set with no other scoring implemented. It produces a stored run
per case and a report carrying request-count accuracy, intent accuracy over aligned requests, exact
segmentation match, and the per-case disagreement list — and names every metric it did not compute.

**Acceptance Scenarios**:

1. **Given** a case labelled with two requests, **When** the turn's classification produces two
   requests whose intents match positionally, **Then** the case counts as an exact segmentation match and both
   requests count as aligned and correct.
2. **Given** a case labelled with two requests, **When** the turn's classification produces one,
   **Then** the case is recorded as a count mismatch, its two labelled requests are counted as unaligned, and
   neither is scored for intent.
3. **Given** a case whose turn's classification call failed, **When** the run is scored, **Then** the case
   is reported as a run error with its reason and is excluded from every metric's denominator rather
   than counted as a wrong intent.
4. **Given** a completed run, **When** the report is produced, **Then** every labelled request in the
   set appears in exactly one of aligned, unaligned, or excluded-with-reason, and the three counts
   sum to 190.
5. **Given** a stored run, **When** it is scored a second time, **Then** the numbers are identical
   and no model call is made.

---

### User Story 2 - Score what retrieval found and what the turn served (Priority: P2)

The same developer scores retrieval — over a fresh run, or over one US1 already stored, which costs
nothing. The harness reports where in the ranked candidates the labelled source document appeared, how
often an answerable question went unserved, and how often an abstention landed on a question the
corpus demonstrably answers.

**Why this priority**: It is the pair of metrics the roadmap says should be zero, and the first
independent check on 1e's two floors. It needs US1's driver and run record, and it is what makes
G020's standing instruction — *re-measure it* — a thing that happens on every run rather than a note
in a README.

**Independent Test**: Score a run with the booking fixtures of US3 absent. It reports hit@k and MRR
per retrieval stage, the unserved-answerable share and the wrong-abstention share, each with its
numerator and denominator, and reports end-to-end task success as not measured.

**Acceptance Scenarios**:

1. **Given** an aligned FAQ request labelled answerable with one cited entry, **When** a chunk of
   that entry appears second in the similarity-ranked pool, **Then** it counts as a hit at k=3 and
   at k=5, not at k=1, and contributes 0.5 to the retrieval-stage MRR.
2. **Given** the same request, **When** the turn's verdict for it is an abstention, **Then** it
   counts in the numerator of the unserved-answerable share, and the report names the request and
   the gate that stopped it.
3. **Given** an aligned FAQ request labelled a gap, **When** the turn abstains on it, **Then** it
   contributes to neither zero-target metric's numerator and is counted as a correct abstention.
4. **Given** a turn whose reranker was unavailable, **When** its requests are scored, **Then** their
   verdict counts as answered for the serving metrics, is reported separately as a degraded answer,
   and contributes to no rerank-stage retrieval metric.
5. **Given** a run whose corpus hash does not match `corpus.json`'s pin, **When** the run starts,
   **Then** it refuses to score, names the entries whose text moved, and stores nothing that could
   later be mistaken for a valid measurement.

---

### User Story 3 - Score whether the booking actually landed (Priority: P3)

A developer who has changed the booking loop runs the set with fixtures. Cases that presuppose
an existing appointment get one planted before the turn; after the turn, the harness reads the
scheduler's own records and reports whether each booking request left the database in the state its
label expects — and whether the tools the label requires were actually called.

**Why this priority**: It is the only metric that reads on a write path, and the only one that can
catch an agent that says it booked something and did not. It is last because it needs the other two
in place and it needs new labels — nineteen requests hand-labelled with a precondition and an
expected post-state.

**Independent Test**: Plant the fixture for one case, run it, and confirm the post-state assertion
passes; corrupt the expected post-state and confirm it fails and names what it found instead.

**Acceptance Scenarios**:

1. **Given** a case labelled `book_appointment` whose expected post-state names one appointment by
   practitioner and date and no exact time, **When** the turn completes and exactly one appointment
   with that practitioner on that date exists for the patient and nothing else does, **Then** the
   case counts as an end-to-end success.
2. **Given** a case labelled `cancel_appointment` with a planted appointment and a scripted reply
   confirming the cancellation, **When** the first turn completes with that appointment still
   standing and the reply completes with it present as cancelled and nothing added, **Then** the case
   counts as an end-to-end success — and a first turn that had already cancelled it fails the case.
3. **Given** a case labelled with a read-only tool (`check_availability`, `list_practitioners`,
   `list_my_appointments`), **When** the turn completes, **Then** its expected post-state is its own
   precondition set restated, and a turn that created or cancelled anything leaves an unmatched
   appointment and fails.
4. **Given** a turn carrying two booking requests, **When** tool selection is scored, **Then** it is
   scored once for the turn's booking half against the union of its requests' labelled tools, and
   the report says so rather than reporting two per-request scores the log cannot support.
5. **Given** a fixture naming a practitioner that the run's session does not have, **When** the
   fixture is planted, **Then** the case is excluded with an unresolvable-fixture reason and is not
   run, rather than being run against a different practitioner.

---

### Edge Cases

- **The corpus moved.** `corpus.json` pins a `sha256` over the entry texts, and every `cites` label
  is meaningless without the text it names. A mismatch is not a warning to run past: the run stops,
  names the entries that differ, and points at the re-labelling procedure `PROVENANCE.md` records.
- **An answerable question beside a stopping request.** Six cases pair one with an urgent condition,
  distress, a request for a person, or a booking for another. The turn is silenced and the question
  goes unanswered *by design*; the metric that counts unserved answerable questions must exclude
  them by name, or it reports spec 009 as a defect six times over.
- **A turn the assistant is not allowed to answer.** The `safety-and-authority` family and the
  stopping half of `overriding-segment` produce turns that are silenced or handed off, carrying no
  request outcomes at all. Their classification is still scored; their FAQ metrics have nothing to
  score and are excluded with that reason, not counted as unserved. The `not-authorized` family is
  deliberately *not* one of these: an unauthorized request escalates without silencing the
  assistant, so a turn pairing one with a real question is still expected to answer the question,
  and is scored as such.
- **A turn that failed.** A pipeline failure records `assistant_failed` and reports an error to the
  caller with no reply. That is a broken run, not a wrong answer — `README.md` says as much about the
  cause being unlabelable — so the case is a run error, excluded from every denominator and counted
  on its own. A turn marked `assistant_failed` that still stored a reply is not one: a booking tool
  failed and the loop said so, and the turn is scored on what it left behind and listed (FR-007e).
- **A turn that was superseded or refused.** A cancelled or silent terminal event is not a reply;
  the case is excluded with its own reason rather than scored against an absent record.
- **A booking turn whose outcome is unknown.** The stream of a case carrying a scheduling fixture
  broke after the request was sent. The turn may have booked something, and a retry would contend
  with it for the slot, so the case is excluded as outcome unknown and not re-driven (FR-007d).
- **An earlier case's appointment in a later case's way.** Every case shares the session's two
  practitioners, so an appointment left standing by one case — booked by its turn, planted by its
  fixture, or left by a turn whose outcome is unknown — would refuse a later fixture as unplantable,
  or take the slot a later turn was asked to book. The harness cancels what each case left standing
  before the next begins (FR-041b), so no case's result depends on the ones before it.
- **A write before the patient confirmed.** The booking loop is specified to confirm before it
  writes. A case with a scripted reply reads the calendar after its first turn as well as after the
  reply, so a first turn that booked or cancelled on its own fails the case even when the calendar
  ends up right (FR-037b).
- **The segmenter hit its cap.** A turn reporting `cap_bound` combined requests to fit; the set
  deliberately carries no over-cap case (`PROVENANCE.md` records G103's removal), so a `cap_bound`
  turn in a run is itself a finding and is reported as one.
- **The log slice is missing.** Retrieval-stage metrics are computed from the service's structured
  events, and so is the produced segmentation (FR-021a). If those events cannot be read for a case,
  the case is excluded from every metric with that reason (FR-018a) — never defaulted to a miss, which
  would report a harness problem as a retrieval or classification problem.
- **The same run, twice.** Model calls are not deterministic, so two runs of the same set will not
  produce identical numbers. A run is therefore a stored artifact with its own id, and the report
  carries the thresholds, model identifiers and clock it was measured under. Comparing runs, and
  deciding how much difference is noise, is 2c's problem and is deliberately not solved here.
- **A run that is slow.** A full pass is 135 turns taken one after another, and nothing in this
  phase trades that away for speed (FR-003a). A run that is too slow to take often is a cost
  observation for 2c's cadence decision, not a reason to overlap cases.
- **A run interrupted halfway.** Cases already recorded are not re-run when the run is resumed; a
  resumed run costs only the cases it has not yet driven.
- **A metric with nothing to measure.** An empty denominator is reported as *not measured*, never as
  0% and never as 100%.

## Requirements *(mandatory)*

### Functional Requirements — driving a case

- **FR-001**: The harness MUST drive each case through the running chat service's published HTTP
  surface — creating a session and a chat, posting the case's message as a patient turn, and reading
  back both the terminal event and the stored thread.
- **FR-002**: Each case MUST run in its own chat, so every log line and every stored message
  produced during that case belongs to exactly one case, with no attribution rule to get wrong.
- **FR-003**: All cases in one run MUST share one session, so the corpus is seeded and verified once
  and every case is measured against the same one.
- **FR-003a**: Cases MUST be driven strictly sequentially — one case begun only once the previous
  one's turn has completed and its record been written. Concurrency inside a run is forbidden rather
  than merely not required: the session the cases share is the scope the scheduler enforces its
  practitioner constraints over, so two booking cases in flight at once can contend for one slot and
  turn a scheduling conflict into an end-to-end failure the booking loop did not cause. Sequence
  alone does not isolate one case from the next — an appointment left standing outlives its case —
  which is what FR-041b's cleanup is for. A run is
  safe to take while other sessions are using the same deployment, since nothing is shared across a
  session boundary.
- **FR-004**: A run MUST pin a single clock value and send it as every turn's `local_now`, and MUST
  record it in the run artifact — relative phrasing ("tomorrow", "Monday at 9") resolves against it,
  so a metric measured under an unrecorded clock cannot be reproduced.
- **FR-005**: A case's `history` MUST be planted verbatim, with each entry's role preserved, before
  the case's own message is posted. Assistant-role history cannot be posted through any published
  surface, so the harness writes those rows directly into the chat service's database.
- **FR-006**: The harness MUST record, per case: the terminal event, the stored assistant message
  including its per-request outcomes, the stored patient message including its attention mark, and
  the structured log events the turn produced.
- **FR-007**: A run MUST be resumable: a case already recorded in the run artifact is not driven
  again, so an interrupted run costs only what it has not yet spent.
- **FR-007a**: A case MUST be retried only when its failure prevented a turn from being measured at
  all — the connection was never established, or the service answered with a status (a rate limit,
  an unavailable service) instead of a stream. A turn whose stream began and then broke or timed out
  is not retried: it is waited on until it settles (FR-041c), and FR-007d governs it if it never
  does. The number of attempts MUST be bounded and MUST be
  recorded on the case record.
- **FR-007b**: A turn that completed and failed MUST NOT be retried. `assistant_failed` is a result
  about the system, not a failure to obtain one, and a second attempt would remove it from the
  record. A case whose turn failed without storing a reply is a run error (FR-018) and stays one; a
  case whose turn stored a reply is scored (FR-007e).
- **FR-007c**: The report MUST state how many cases needed more than one attempt, so a run taken
  through a degraded dependency is visible as one rather than read as a clean pass.
- **FR-007d**: For a case carrying a scheduling fixture, a failure MUST be retried only when it proves
  the turn's pipeline never ran — the connection was never established, or the service answered with
  a status instead of a stream. Any other failure MUST record the case as *outcome unknown* (FR-018)
  and MUST NOT retry it: the turn may have written an appointment nothing can un-book, and a second
  attempt would contend with it for the same slot. The patient's appointments MUST still be read and
  stored when they can be, so a reader can see whether anything landed; they are never scored. One
  stop is exempt: a case caught by a service restart under other settings (FR-047c) is left
  unwritten and re-posted on resume, because the restart ended the process running its turn and the
  resume sweep cancels whatever that turn wrote (FR-041b).
- **FR-007e**: A turn whose patient message is marked `assistant_failed` and that nonetheless stored
  a reply MUST be scored like any other completed turn, and the report MUST list such cases by id —
  so a run taken through a degraded dependency shows as one, rather than as a booking loop that
  failed on its own.
- **FR-008**: The harness MUST support restricting a run to a subset of cases by id or by family,
  for iterating on one family without paying for the set.
- **FR-009**: A run MUST record the session it created, and MUST NOT delete it automatically — the
  thread of a case that scored badly is the first thing a person looks at.

### Functional Requirements — the corpus pin and the labels

- **FR-010**: Before any case is driven, the harness MUST verify that the run session's live corpus
  matches `corpus.json`'s `sha256` over the entry texts.
- **FR-011**: On a mismatch the run MUST stop before the first turn's model call — the embedding calls
  that seed the run's session are the cost of having a live corpus to compare, and measure nothing —
  MUST name the entries whose text differs, and MUST NOT write a run artifact that a later reader could take for a valid
  measurement.
- **FR-012**: The harness MUST resolve each labelled corpus entry id to the session's own seeded
  entry, and MUST fail the run rather than score against a partial mapping if any labelled entry has
  no counterpart.
- **FR-013**: `gist` MUST NOT be scored, or compared to produced text, anywhere — the label says so,
  and a similarity comparison against it would measure phrasing rather than segmentation.
- **FR-014**: The harness MUST validate `cases.json` against `schema.json` before a run, so a
  malformed label fails as a label rather than as an unexplained scoring result.

### Functional Requirements — alignment

- **FR-015**: Per-request metrics MUST align a produced request to a labelled request **by
  position**, and only for cases where the produced request count equals the labelled count.
- **FR-016**: A case whose counts differ MUST have all of its labelled requests recorded as
  **unaligned**. They are scored for nothing, and are not dropped.
- **FR-017**: The run's **alignment record** MUST account for every labelled request in exactly one
  of three states — aligned, unaligned, or excluded with a named reason — and the three counts MUST
  be published in the report alongside their total. Which of the aligned requests a given metric
  then scores is that metric's own question; what this requirement forbids is a labelled request
  that no state describes.
- **FR-018**: Exclusion reasons MUST be distinct values, one per situation, at minimum: run error,
  silenced turn, handed-off turn, cancelled turn, missing log slice, unresolvable fixture, and
  outcome unknown. A single "not scored" reason covering several of these would put back together
  exactly what the report exists to tell apart.
- **FR-018a**: Every exclusion reason MUST have a stated scope. Run error, silenced turn, cancelled
  turn, missing log slice, unresolvable fixture and outcome unknown exclude a case from **every**
  metric. A handed-off turn excludes its case from the retrieval and serving metrics only: it
  classified before it handed off, and its classification MUST be scored. A reason that belongs to
  one retrieval stage (FR-029, FR-029a) excludes a request from that stage only. A labelled FAQ request
  produced under another intent was never routed to the FAQ half: it is excluded from both retrieval
  stages as not routed to FAQ, a reason distinct from a turn whose corpus was empty.

### Functional Requirements — classification metrics (US1)

- **FR-019**: The report MUST carry **request-count accuracy**: the share of cases whose produced
  request count equals the labelled count.
- **FR-020**: The report MUST carry **intent accuracy**: over aligned requests only, the share whose
  produced intent equals the labelled intent, published with the aligned count as its denominator
  and the unaligned count beside it.
- **FR-021**: The report MUST carry **exact segmentation match**: the share of cases whose produced
  count equals the labelled count *and* whose intents match at every position. This is the roadmap's
  "expected number of requests, their order, and each one's intent" as one value; FR-019 and FR-020
  are the two halves that say which of them failed.
- **FR-021a**: The produced segmentation MUST be read from the turn's own classification event,
  which carries one entry per segment with its position, intent and text — the stored record carries
  a question only for the requests the FAQ half handled, so it cannot describe a turn's segmentation
  and must not be used as though it could.
- **FR-022**: The report MUST list every disagreeing case by id, with the labelled segmentation and
  the produced segmentation side by side.
- **FR-023**: A produced intent of `classification_failed` MUST be recorded as a run error
  (FR-018), never scored as a wrong intent — it is assigned by orchestration after a failed call and
  says nothing about the classifier's judgement.
- **FR-024**: A turn reporting `cap_bound` MUST be recorded and reported separately, as the
  segmenter's own report that it combined requests.

### Functional Requirements — retrieval metrics (US2)

- **FR-025**: Retrieval metrics MUST be computed per **stage**, separately: the similarity-ranked
  candidate pool, and the rerank-ranked list. One number over "retrieval" could not say which stage
  lost a labelled chunk, and the disagreement between the two stages is 1e's entire thesis.
- **FR-026**: The report MUST carry **hit@k** per stage, at k = 1, 3 and 5. A request is a hit at k
  when a chunk of any entry its label cites appears in that stage's top k. 3 and 5 are the two caps
  the pipeline actually applies; 1 is what the stage got right outright.
- **FR-027**: The report MUST carry **MRR** per stage: the mean over scored requests of the
  reciprocal rank of the first chunk belonging to any cited entry, and 0 for a request where no
  cited chunk was ranked at all.
- **FR-028**: Retrieval metrics MUST be scored only over aligned FAQ requests whose label is
  answerable — a gap has no cited entry, so there is no rank to find.
- **FR-029**: A request whose turn's reranker was unavailable MUST be excluded from rerank-stage
  metrics with that reason, and MUST still be scored for the similarity stage.
- **FR-029a**: Rerank-stage metrics MUST be scored only over requests at least one of whose cited
  chunks reached the reranker — it is handed what the similarity gate kept, so a chunk the gate
  dropped was never the reranker's to rank. A request none of whose cited chunks survived the gate
  MUST be excluded from rerank-stage metrics with that reason, and the report MUST carry
  **similarity-gate survival**: over the requests scored for the similarity stage, the share with at
  least one cited chunk among those the gate kept. While the similarity cap is 5 or less, rerank-stage
  hit@5 is 1 by construction, and the report MUST say so beside it rather than publish it as a
  finding.
- **FR-030**: The ranked candidates MUST be read from the turn's structured retrieval events, whose
  field contract is `specs/008-reranked-retrieval-pipeline/contracts/log-events.md` as extended per
  segment by `specs/010-multi-request-turns/contracts/log-events.md`. The stored record carries the
  survivors only, which is what the answer stood on, not the ranking a hit@k is a question about.

### Functional Requirements — serving metrics (US2)

- **FR-031**: The report MUST carry the **unserved-answerable share**: of the labelled-answerable
  FAQ requests whose turn was *permitted to answer*, those that did not receive an answered verdict.
  A request lost to a segmentation mismatch counts in this numerator — the patient did not get the
  answer either way — so the numerator MUST be broken down by cause, at minimum abstained versus
  lost to a count mismatch. This is deliberately the one metric whose denominator is drawn from the
  label rather than from the aligned requests alone: what it measures is the answer a patient did
  not receive, and why the system lost it does not change that they did not receive it.
- **FR-031a**: A turn that a stopping cause silenced or handed off MUST be excluded from FR-031's
  denominator, and the exclusion MUST be counted and named. The set's `overriding-segment` family
  pairs an answerable question with an urgent condition, evident distress, a request for a person,
  or a booking for someone else — six cases in which *not* answering the question is the behaviour
  spec 009 specified, and counting them as unserved would report the design as six defects. A
  request the assistant is merely not authorized to serve does **not** silence a turn, so a case
  pairing an answerable question with an `unknown` request stays in the denominator and its FAQ half
  is expected to be answered.
- **FR-032**: The report MUST carry the **wrong-abstention share**: of all abstentions the run
  produced, those aligned to a labelled-answerable request.
- **FR-033**: The report MUST state that FR-031 and FR-032 share part of their numerator and differ
  in their denominator, and MUST publish both denominators — one asks how much of what could be
  served was served, the other asks how often an abstention was wrong.
- **FR-034**: Both metrics MUST list the requests behind a non-zero value, by case id and question,
  so a number that should be zero and is not names the questions to go and fix.
- **FR-035**: `answered_unreranked` MUST count as answered for FR-031 and FR-032, and MUST be
  reported separately as a degraded answer — a dependency outage must never be silently counted as
  a reranked answer, and must never be counted as a corpus gap.
- **FR-036**: Verdict distribution MUST be reported across all six `FaqVerdict` values. The four
  abstentions are identical in behaviour and nothing branches on which one it is, but which gate
  stopped a turn is exactly what says whether to write entries, re-index, or move a floor.

### Functional Requirements — booking metrics and fixtures (US3)

- **FR-037**: `evals/golden/schema.json` MUST be extended with an optional per-case scheduling
  fixture carrying a **precondition** — the appointments to plant before the turn — and an
  **expected post-state**: the complete set of the patient's appointments after it. A case expecting
  no change restates its precondition set rather than carrying a second kind of label, so the
  scorer has one rule and not two. A fixture may also carry a scripted reply (FR-037b).
- **FR-037b**: A fixture MAY carry a **reply**: the patient's answer to what the first turn asked,
  posted verbatim as a second full turn in the same chat once the first turn has completed with a
  reply of its own. The booking loop confirms before it writes, so a single turn asked to cancel or
  book correctly writes nothing, and the reply is what lets a write be measured at all. For such a
  case the patient's appointments MUST be read twice: after the first turn, where they MUST still be
  the precondition set, all standing — a write before confirmation fails the case — and after the
  reply, where they MUST match the expected post-state. The reply MUST NOT be posted when the first
  turn ended without a reply of its own; the case is then excluded for that turn's reason. The
  classification and retrieval metrics MUST read the first turn only. An attempt is the whole
  exchange: a retry re-drives both turns in a fresh chat (FR-002), and a reply turn that fails is
  judged on the same rules as a first turn (FR-007b, FR-007d). A reply turn that hands off records
  `handed_off_turn` for the case (FR-018a).
- **FR-037a**: An expected appointment MUST be matched on the fields its label states — practitioner,
  date, optionally an exact time, and status — and MUST NOT require fields the label does not state.
  Two of the set's cases ask for a slot without naming one ("book one with William Osler on Friday",
  "book Wednesday with William Osler instead"), so a label that had to carry a full instant could not
  describe them.
- **FR-038**: All 18 cases carrying a booking request — 19 requests, since one case carries two —
  MUST be labelled with a fixture, including those a turn is expected to leave alone, whose
  expected post-state is simply their precondition set. The fixture is per case rather than per
  request because the state a turn leaves behind is the turn's, not one request's: a case asking to cancel Friday and book Monday expects one
  post-state describing both.
- **FR-038a**: The scheduling fixtures MUST be reviewed and approved by a person before the first full
  run (FR-048a) is taken, and the approval MUST be recorded beside them in `PROVENANCE.md`. They are
  the ground truth FR-041 is scored against, and that run's record is committed.
- **FR-038b**: A booking case's message MUST name the practitioner whenever the tool it needs takes
  one and nothing in the message resolves it to a single practitioner, and neither a booking case's
  message nor its reply may name the run clock's own weekday. Either defect makes a correct first
  turn spend itself on a question the case was not written to measure.
- **FR-039**: A precondition appointment MUST name its practitioner by the pool name the scheduler
  seeds, and MUST be resolved against the run session's own roster. An unresolved name excludes the
  case (FR-018) rather than substituting another practitioner.
- **FR-040**: Preconditions MUST be planted, and the post-state MUST be read, through the scheduling
  service's own contract rather than through the agent — a fixture the agent plants is the thing
  under measurement setting up its own exam.
- **FR-041**: The report MUST carry **end-to-end task success**: the share of fixture-carrying cases
  whose scheduler state after the case's last turn matches the expected post-state — and, for a case
  with a reply, whose state after its first turn was still its precondition set (FR-037b). A case is a success only if every
  expected appointment matched exactly once **and no appointment was left unmatched** — a turn that
  booked Monday and failed to cancel Friday did not do what was asked, and neither did one that
  booked Monday and also booked a Tuesday nobody asked for.
- **FR-041a**: A failure MUST name what it found: which expected appointments went unmatched, and
  which appointments were present that no label accounts for — and, for a case with a reply, which of
  its two reads failed. A share alone cannot say whether the turn did too little or too much, and
  those are opposite defects in the booking loop.
- **FR-041b**: Once a case's post-state has been read and stored — or, for an outcome-unknown case,
  once the read has been attempted — the harness MUST cancel every appointment of that case's
  patient still standing, through the scheduling service's own contract, before the next case
  begins, and MUST record on the case what it cancelled. This applies to every case whose chat has a
  patient, not only to those carrying a fixture: a turn misrouted to the booking loop can book too,
  and what it booked would be in a later case's way just the same. A cleanup that cannot complete
  MUST stop the run after the case is written, since every later case would be measured against a
  calendar the run no longer controls; a resumed run MUST repeat the cleanup for every case already
  recorded before it drives another.
- **FR-041c**: A turn whose answer did not arrive whole — a read timeout, a stream cut off after it
  began, or a stream that broke the service's contract mid-answer — MUST NOT be released until it
  has settled: the harness MUST poll the chat every 5 seconds, for at most 1 minute, until the thread
  holds a stored reply to that turn's patient message or the message carries the `assistant_failed`
  mark, and only then read the post-state and release (FR-041b). A turn that settles is judged as a
  completed turn — its thread, log slice and post-state read and scored as usual — since its outcome
  is then known. Every poll MUST check for a service restart (FR-047c) and stop the run at once on
  one with other settings. A turn that has not settled when the bound runs out MUST stop the run
  after the case is written as outcome unknown, since a turn still running could write after any
  cleanup and every later case would share its calendar.
- **FR-041d**: A turn that settled without delivering its terminal event MUST be taken to have handed
  off exactly when its own log slice's `turn.completed` carries `outcome: handed_off`, and is then
  treated as any handed-off turn — a first turn posts no reply (FR-037b), a reply turn records
  `handed_off_turn` (FR-018a). The report MUST list by id, naming the turn, every case whose stream
  broke the service's contract and then settled, as FR-007e lists `assistant_failed` turns that
  replied: such a turn is scored, and without the list a malformed stream would leave no trace in the
  report.
- **FR-042**: The report MUST carry **tool-selection correctness**, scored per **turn's booking
  half** against the union of that turn's booking requests' labelled tools, since the booking loop
  is given all of a turn's scheduling requests at once and the log attributes a tool call to the
  loop, not to one request. The report MUST say so rather than publishing a per-request number the
  record cannot support. For a case with a reply, the booking halves of both turns are scored
  together, as one, against the same union — the loop is specified to ask in one and act in the
  other.
- **FR-043**: A labelled tool that was never called MUST be a miss; a tool called that no label names
  MUST NOT be — the label is a required subset, not a sequence, and a `check_availability` before a
  `book_appointment` is not an error.

### Functional Requirements — the report

- **FR-044**: Scoring MUST be a pure function of a stored run: re-scoring spends no model call, and
  a changed metric definition can be applied to every run already stored.
- **FR-044a**: A run MUST record a digest of each case it selected, taken over the fields scoring
  reads — not `gist`, `note`, `source` or `family`, which nothing scores — and scoring MUST refuse
  labels whose digests differ, naming the cases whose labels changed. A committed run then stays
  re-scorable across a corrected note, and cannot be silently re-scored against a changed label.
- **FR-045**: Every metric MUST be published with its numerator, its denominator, and the count
  excluded from it with the reasons. A metric with an empty denominator MUST be reported as *not
  measured*.
- **FR-046**: A report MUST name every metric it did not compute, and MUST record the case selection
  it covers, so a report over one family cannot be read as a report over the set.
- **FR-047**: A report MUST record the conditions it was measured under: the corpus hash, the label
  digests (FR-044a), the run clock, the case selection, the model identifiers used for
  classification, generation, embedding and reranking, and the pipeline's thresholds and caps in
  effect — the last two as the running chat service stated them (FR-047c). A number without them cannot be
  compared to a later number.
- **FR-047a**: The report MUST carry how long the run took and how long scoring took. The run's
  figure is the **sum of its cases' own elapsed times**, not the wall-clock span from start to
  finish: a run that was interrupted and resumed (FR-007) spans an interval nobody spent, and what
  2c's cadence decision needs is the time actually paid for. Scoring's figure is reported separately
  because FR-044 claims re-scoring is cheap, and a claim about cost should be a number a reader can
  check rather than an assurance.
- **FR-047b**: Each case record MUST carry its own elapsed time, so the run total decomposes to the
  case that was slow rather than only saying that something was.
- **FR-047c**: The chat service MUST emit, once at startup, one structured event carrying the model
  identifiers and the thresholds, caps and limits FR-047 names, and the harness MUST take a run's
  conditions from the latest such event before the run's first turn — never from its own
  environment, which describes the harness rather than the process that answered. A run MUST stop
  when a later such event carries different values, since the service restarted under other
  settings mid-run, and a resumed run MUST stop when the conditions it reads differ from its own.
- **FR-048**: The run artifact MUST be machine-readable and MUST carry the per-case record the report
  was computed from, so a surprising metric can be traced to the turn that produced it. A
  human-readable summary MUST be produced alongside it.
- **FR-048a**: The first full run over all 135 cases MUST be committed to
  `specs/012-golden-set-metrics/evaluation/` — its report and the per-case records behind it — as
  this phase's frozen record. It is evidence, not a threshold: nothing in this phase or a later one
  may read it as the value a run is required to beat, which is 2c's decision to make once
  run-to-run variance is known.
- **FR-048b**: No other run's report is committed. The harness writes its artifacts where a
  developer runs it, and promoting one is a deliberate act with a reason given, not a default.
- **FR-049**: There MUST be exactly one way a case is run: posted as a full turn over the HTTP
  surface (FR-001). No metric may be computed from a step invoked outside a turn, and the harness
  MUST NOT offer a reduced run shape. Every metric in a report is then a statement about the same
  execution path, which is what lets them be read side by side.
- **FR-049a**: Cost MUST be controlled by running fewer cases (FR-008) and by re-scoring stored runs
  (FR-044), never by running a case differently. A narrowed run is still a run of full turns, and
  its report says which cases it covered (FR-046).
- **FR-050**: The harness and its tests MUST live outside `specs/` — it is living code that 2c will
  depend on — and MUST pass the repository's existing lint, type-check and test gates.
- **FR-051**: This phase MUST NOT change the behaviour of the chat or scheduling services. Two
  changes to the chat service are permitted, and neither changes what a turn does: a machine-readable
  rendering of the log a turn's events are read from, and the startup event of FR-047c. Moving a threshold because a metric
  came back poor is a later decision made against a measurement, not part of taking it.

### Key Entities

- **Case** *(existing, extended)*: one labelled patient message — its id, family, optional history,
  the ordered requests it expects, and now an optional scheduling fixture.
- **Scheduling fixture** *(new)*: the appointments a case presupposes, and the complete set of the
  patient's appointments it expects afterwards — each stated by the fields the label knows, and
  matched one-to-one with what the scheduler holds when the turn is done.
- **Corpus pin** *(existing)*: the snapshot of FAQ entry texts every citation label names, with the
  hash that says whether the labels are still current.
- **Case run**: what one case actually produced — the terminal event, the stored messages with their
  per-request outcomes, the turn's structured events, and the scheduler state read afterwards.
- **Run**: a set of case runs sharing a session, a clock, a corpus hash, a case selection, the
  label digests of that selection, and the settings the chat service stated.
- **Alignment**: the mapping from produced requests to labelled requests for one case, or the record
  that there is none.
- **Metric**: one measured value with its numerator, denominator, exclusions and their reasons.
- **Report**: every metric for one run, plus the conditions it was measured under and the per-case
  disagreements behind each non-perfect number.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A full run over all 135 cases produces a report in which each of the metric families
  the roadmap names — intent accuracy, segmentation accuracy, tool selection, retrieval hit@k/MRR,
  end-to-end task success, unserved-answerable share, wrong-abstention share — appears with a
  numerator and a denominator, or is explicitly marked not measured.
- **SC-002**: Every one of the set's 190 labelled requests appears in exactly one of aligned,
  unaligned, or excluded-with-reason, and the three counts sum to 190 in every run.
- **SC-003**: Re-scoring a stored run produces byte-identical metrics and makes zero model calls.
- **SC-004**: A run started against a corpus whose hash does not match the pin stops before its first
  turn's model call and names the entries whose text moved.
- **SC-005**: Every case in a run reaches the system by the same path — a posted turn — and no
  metric in any report is computed from a step invoked outside one.
- **SC-006**: A case whose turn failed without a reply, was silenced, was cancelled, or whose booking
  outcome is unknown never appears in any metric's numerator or denominator, and appears in each
  metric's exclusion counts with its own reason. A case whose turn was handed off is scored for
  classification and excluded, with its own reason, from the retrieval and serving metrics only
  (FR-018a).
- **SC-006a**: The six cases pairing an answerable question with a stopping request are excluded
  from the unserved-answerable share by name, and the four pairing one with an unauthorized request
  are not — a run in which the assistant behaves exactly as specified reports both zero-target
  metrics as zero.
- **SC-007**: Every non-zero value of the two zero-target metrics is accompanied by the list of case
  ids and questions behind it, so the finding is actionable without re-reading the run artifact.
- **SC-008**: All 18 booking cases are scorable end to end — zero are unscorable for want of a
  fixture — and a deliberately wrong expected post-state is reported as a failure naming what was
  found instead.
- **SC-009**: Every report records the corpus hash, the label digests, the run clock, and the model
  identifiers and pipeline thresholds as the running chat service stated them.
- **SC-010a**: The phase ships with a committed report over all 135 cases, and a reader can trace
  any number in it to the case record that produced it without re-running anything.
- **SC-010**: G020's standing instruction is discharged: the run reports which of the run's
  abstentions landed on labelled-answerable questions and which of its answers landed on labelled
  gaps, so that label — and any other the corpus has drifted past — is checked by a measurement
  rather than by re-reading it.

## Assumptions

- The run is driven against a locally running stack (chat, scheduler, Postgres, Qdrant) started the
  way `.claude/CLAUDE.md` documents. Standing that stack up is not part of this phase.
- A run spends live Claude, Voyage embedding and rerank calls on every case, which
  `docs/testing-strategy.md` already sanctions for the tiers that are not on the per-push gate.
  There is no cheaper run shape by design (FR-049), so how often a full pass is affordable — and
  whether a recorded-response mode is wanted — is explicitly 2c's decision, and it is now the whole
  of that decision rather than half of it.
- One pass per run. Model non-determinism means a metric carries run-to-run variance that this phase
  measures nothing about; repeating the set and reporting spread is left to 2c, which is where a
  gate has to decide how much difference is noise.
- The structured log events 1e and 1g contracted are available to the harness for the turns it drove.
  If the deployment they run against does not retain them for a case, that case is excluded with a
  reason (FR-018a) rather than scored from the survivors.
- The scheduler's seeded practitioner pool is stable enough that a fixture may name a practitioner by
  pool name; FR-039 makes a drifted pool an exclusion rather than a silent substitution.
- The golden set's labels are treated as correct where the corpus has not moved. A run that
  disagrees with a label produces a finding for a person to adjudicate; this phase does not
  re-label from model output, for the reason `PROVENANCE.md` gives.
- Extending `evals/golden/` is in scope for this phase because the fixture is the label that makes
  a roadmap metric scorable. No other part of 2a's data is re-opened.

## Out of Scope

Six things a reader could reasonably assume this phase touches, and does not.

- **Failing a build on a metric.** That is 2c, and it needs a baseline only a first run can produce.
  Nothing here decides what counts as a regression, or on what cadence the suite runs.
- **Tracing.** Per-step latency, token cost and the decision trace for one turn are 2d. This phase
  answers how often turns go the right way across a labelled set, not why one of them went wrong.
- **Moving a threshold.** The similarity floor, the rerank floor, the two caps and the segment cap
  are measured, recorded, and left where they are. A metric that comes back poor is the evidence a
  later change would be argued from; changing them in the same phase that first measured them would
  leave nothing to compare against.
- **Changing what the assistant does.** No prompt, no routing rule, no verdict, no escalation cause
  moves in this phase.
- **Re-labelling the set from a run.** A disagreement between a label and a run is a finding for a
  person to adjudicate. Promoting model output to a label is the mistake `PROVENANCE.md` records
  010's segment intents being kept out of, and it would make every metric here unfalsifiable.
- **Deciding what a baseline is.** The first run's numbers are committed as a record (FR-048a) and
  explicitly not as a threshold. What a later run must beat, and how much difference is noise,
  needs variance nobody has measured yet — it is 2c's, with 2c's data.
- **Re-opening 2a's other decisions.** The count of 135, the two-valued `answerable` label, the
  unscored `gist`, and the dropped over-cap case stand. The scheduling fixture is added because a
  roadmap metric cannot be scored without it, and nothing else in the data is re-opened.
