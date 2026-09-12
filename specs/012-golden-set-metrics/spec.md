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
  the harness offer? → A: **Two tiers — classifier-only and full.** Intent accuracy and segmentation
  accuracy are decided entirely by one call to the cheap classifying model, and making them cost a
  generation call each buys nothing. The classifier tier runs the whole set for the price of the
  cheapest thing in it; the full tier runs the whole pipeline and scores everything. Every report
  declares its tier and names the metrics it did **not** compute, so a partial run cannot be read as
  a full one — the same rule as the metric denominators below, applied to the report as a whole.
- **A consequence of those two answers together, recorded rather than assumed**: the classifier tier
  cannot go over HTTP. A posted turn runs its whole pipeline — there is no published way to ask the
  service to classify a message and stop, and a paused conversation classifies nothing at all. So
  the cheap tier calls the classification step directly while the full tier drives HTTP, and the two
  are kept as separate kinds of run (FR-049, FR-049a) rather than as one run with fewer metrics.
- Q: What happens to a request whose produced segmentation does not line up with the labelled one?
  → A: **It is reported as unaligned, never scored and never dropped.** Per-request metrics align by
  position, and position only means something when the counts agree. A run accounts for every
  labelled request in exactly one of three ways — aligned, unaligned, or excluded with a named reason
  — and the counts are published beside every metric. Quietly scoring a mismatched pair measures
  phrasing; quietly dropping it flatters the denominator.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Score what the classifier decided (Priority: P1)

A developer who has just changed the classification prompt runs the golden set's cheap tier. Each
case's history and message go to the classification step and nothing else; the harness records the
segmentation it produced for each, and reports how often the number of requests was right, how often
their order and intents were right, and which cases disagreed — by id, with the labelled
segmentation beside the produced one.

**Why this priority**: It is the whole of the measurement loop in miniature — drive, record, align,
score, report — and it is the tier that makes the other two affordable to build against. Segmentation
is also the thing with the least evidence behind it today: 1g's cap of 3 rests on a single message,
and `PROVENANCE.md` records that 010's segment intents had to be labelled by hand here rather than
lifted from committed model output, *because promoting model output to a label would make
segmentation accuracy unfalsifiable*. This story is what makes it falsifiable.

**Independent Test**: Run the classifier tier over the whole set with no other tier implemented. It
produces a report carrying request-count accuracy, intent accuracy over aligned requests, exact
segmentation match, and the per-case disagreement list — and names every metric it did not compute.

**Acceptance Scenarios**:

1. **Given** a case labelled with two requests, **When** classification produces two requests whose
   intents match positionally, **Then** the case counts as an exact segmentation match and both
   requests count as aligned and correct.
2. **Given** a case labelled with two requests, **When** classification produces one, **Then** the
   case is recorded as a count mismatch, its two labelled requests are counted as unaligned, and
   neither is scored for intent.
3. **Given** a case whose classification call failed, **When** the run is scored, **Then** the case
   is reported as a run error with its reason and is excluded from every metric's denominator rather
   than counted as a wrong intent.
4. **Given** a completed run, **When** the report is produced, **Then** every labelled request in the
   set appears in exactly one of aligned, unaligned, or excluded-with-reason, and the three counts
   sum to 190.
5. **Given** a stored run, **When** it is scored a second time, **Then** the numbers are identical
   and no model call is made.

---

### User Story 2 - Score what retrieval found and what the turn served (Priority: P2)

The same developer runs the full tier. Now every FAQ request is retrieved for, gated and answered,
and the harness reports where in the ranked candidates the labelled source document appeared, how
often an answerable question went unserved, and how often an abstention landed on a question the
corpus demonstrably answers.

**Why this priority**: It is the pair of metrics the roadmap says should be zero, and the first
independent check on 1e's two floors. It needs US1's driver and run record, and it is what makes
G020's standing instruction — *re-measure it* — a thing that happens on every run rather than a note
in a README.

**Independent Test**: Run the full tier with the booking fixtures of US3 absent. It reports hit@k and
MRR per retrieval stage, the unserved-answerable share and the wrong-abstention share, each with its
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

A developer who has changed the booking loop runs the full tier with fixtures. Cases that presuppose
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

1. **Given** a case labelled `book_appointment` with an expected appointment, **When** the turn
   completes and an appointment matching the expected practitioner and start exists for that
   patient, **Then** the request counts as an end-to-end success.
2. **Given** a case labelled `cancel_appointment` with a planted appointment, **When** the turn
   completes and that appointment's status is cancelled, **Then** the request counts as an
   end-to-end success.
3. **Given** a case labelled with a read-only tool (`check_availability`, `list_practitioners`,
   `list_my_appointments`), **When** the turn completes, **Then** the expected post-state is that
   the session's appointments are unchanged, and a turn that wrote anything fails the assertion.
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
  caller. That is a broken run, not a wrong answer — `README.md` says as much about the cause being
  unlabelable — so the case is a run error, excluded from every denominator and counted on its own.
- **A turn that was superseded or refused.** A cancelled or silent terminal event is not a reply;
  the case is excluded with its own reason rather than scored against an absent record.
- **The segmenter hit its cap.** A turn reporting `cap_bound` combined requests to fit; the set
  deliberately carries no over-cap case (`PROVENANCE.md` records G103's removal), so a `cap_bound`
  turn in a run is itself a finding and is reported as one.
- **The log slice is missing.** Retrieval-stage metrics are computed from the service's structured
  events. If those events cannot be read for a case, its retrieval metrics are excluded with that
  reason — never defaulted to a miss, which would report a harness problem as a retrieval problem.
- **The same run, twice.** Model calls are not deterministic, so two runs of the same set will not
  produce identical numbers. A run is therefore a stored artifact with its own id, and the report
  carries the thresholds, model identifiers and clock it was measured under. Comparing runs, and
  deciding how much difference is noise, is 2c's problem and is deliberately not solved here.
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
- **FR-008**: The harness MUST support restricting a run to a subset of cases by id or by family,
  for iterating on one family without paying for the set.
- **FR-009**: A run MUST record the session it created, and MUST NOT delete it automatically — the
  thread of a case that scored badly is the first thing a person looks at.

### Functional Requirements — the corpus pin and the labels

- **FR-010**: Before any case is driven, the harness MUST verify that the run session's live corpus
  matches `corpus.json`'s `sha256` over the entry texts.
- **FR-011**: On a mismatch the run MUST stop before spending any model call, MUST name the entries
  whose text differs, and MUST NOT write a run artifact that a later reader could take for a valid
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
  silenced turn, handed-off turn, cancelled turn, missing log slice, unresolvable fixture, and tier
  not run. A single "not scored" reason covering several of these would put back together exactly
  what the report exists to tell apart.

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
  fixture carrying a **precondition** (the appointments to plant before the turn) and an **expected
  post-state** (what the scheduler's records must show after it).
- **FR-038**: All 18 cases carrying a booking request — 19 requests, since one case carries two —
  MUST be labelled with a fixture, including those whose expected post-state is that nothing
  changed. The fixture is per case rather than per request because the state a turn leaves behind is
  the turn's, not one request's: a case asking to cancel Friday and book Monday expects one
  post-state describing both.
- **FR-039**: A precondition appointment MUST name its practitioner by the pool name the scheduler
  seeds, and MUST be resolved against the run session's own roster. An unresolved name excludes the
  case (FR-018) rather than substituting another practitioner.
- **FR-040**: Preconditions MUST be planted, and the post-state MUST be read, through the scheduling
  service's own contract rather than through the agent — a fixture the agent plants is the thing
  under measurement setting up its own exam.
- **FR-041**: The report MUST carry **end-to-end task success**: the share of fixture-carrying cases
  whose post-turn scheduler state matches the expected post-state. A case is a success only if every
  part of its expected post-state holds — a turn that booked Monday and failed to cancel Friday did
  not do what was asked.
- **FR-042**: The report MUST carry **tool-selection correctness**, scored per **turn's booking
  half** against the union of that turn's booking requests' labelled tools, since the booking loop
  is given all of a turn's scheduling requests at once and the log attributes a tool call to the
  loop, not to one request. The report MUST say so rather than publishing a per-request number the
  record cannot support.
- **FR-043**: A labelled tool that was never called MUST be a miss; a tool called that no label names
  MUST NOT be — the label is a required subset, not a sequence, and a `check_availability` before a
  `book_appointment` is not an error.

### Functional Requirements — the report

- **FR-044**: Scoring MUST be a pure function of a stored run: re-scoring spends no model call, and
  a changed metric definition can be applied to every run already stored.
- **FR-045**: Every metric MUST be published with its numerator, its denominator, and the count
  excluded from it with the reasons. A metric with an empty denominator MUST be reported as *not
  measured*.
- **FR-046**: A report MUST declare its tier and MUST name every metric it did not compute, so a
  classifier-tier report cannot be read as a full one.
- **FR-047**: A report MUST record the conditions it was measured under: the corpus hash, the run
  clock, the case selection, the model identifiers used for classification, generation, embedding
  and reranking, and the pipeline's thresholds and caps in effect. A number without them cannot be
  compared to a later number.
- **FR-048**: The run artifact MUST be machine-readable and MUST carry the per-case record the report
  was computed from, so a surprising metric can be traced to the turn that produced it. A
  human-readable summary MUST be produced alongside it.
- **FR-049**: The harness MUST provide two tiers. The **full tier** drives every case as a real turn
  over the HTTP surface (FR-001) and computes every metric. The **classifier tier** invokes the same
  classification step the service uses, directly, with the case's history and message and nothing
  else — no session, no corpus, no scheduler, no generation — and computes only the metrics one
  classification call can decide. A posted turn always runs its whole pipeline, so there is no way
  to make a cheap tier out of one; a tier that costs a generation call per case is not the tier the
  clarification asked for.
- **FR-049a**: A classifier-tier run MUST be recorded as its own kind of run and MUST NOT be scored,
  compared or merged as though it were a full one — it exercises one step, not a turn.
- **FR-050**: The harness and its tests MUST live outside `specs/` — it is living code that 2c will
  depend on — and MUST pass the repository's existing lint, type-check and test gates.
- **FR-051**: This phase MUST NOT change the behaviour of the chat or scheduling services, beyond
  what is needed to make a turn's record readable for a case. Moving a threshold because a metric
  came back poor is a later decision made against a measurement, not part of taking it.

### Key Entities

- **Case** *(existing, extended)*: one labelled patient message — its id, family, optional history,
  the ordered requests it expects, and now an optional scheduling fixture.
- **Scheduling fixture** *(new)*: the appointments a case presupposes and the scheduler state it
  expects afterwards, including "unchanged".
- **Corpus pin** *(existing)*: the snapshot of FAQ entry texts every citation label names, with the
  hash that says whether the labels are still current.
- **Case run**: what one case actually produced — the terminal event, the stored messages with their
  per-request outcomes, the turn's structured events, and the scheduler state read afterwards.
- **Run**: a set of case runs sharing a session, a clock, a corpus hash, a tier and a case selection.
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
  model call and names the entries whose text moved.
- **SC-005**: The classifier tier completes all 135 cases making one classification call per case,
  no generation, embedding or rerank call, and no write to any database, and its report names every
  metric it did not compute.
- **SC-006**: A case whose turn failed, was silenced, was handed off, or was cancelled never appears
  in a metric's numerator or denominator, and appears in that metric's exclusion counts with its own
  reason.
- **SC-006a**: The six cases pairing an answerable question with a stopping request are excluded
  from the unserved-answerable share by name, and the four pairing one with an unauthorized request
  are not — a run in which the assistant behaves exactly as specified reports both zero-target
  metrics as zero.
- **SC-007**: Every non-zero value of the two zero-target metrics is accompanied by the list of case
  ids and questions behind it, so the finding is actionable without re-reading the run artifact.
- **SC-008**: All 18 booking cases are scorable end to end — zero are unscorable for want of a
  fixture — and a deliberately wrong expected post-state is reported as a failure naming what was
  found instead.
- **SC-009**: Every report records the corpus hash, the run clock, the model identifiers and the
  pipeline thresholds it was measured under.
- **SC-010**: G020's standing instruction is discharged: the run reports which of the run's
  abstentions landed on labelled-answerable questions and which of its answers landed on labelled
  gaps, so that label — and any other the corpus has drifted past — is checked by a measurement
  rather than by re-reading it.

## Assumptions

- The run is driven against a locally running stack (chat, scheduler, Postgres, Qdrant) started the
  way `.claude/CLAUDE.md` documents. Standing that stack up is not part of this phase.
- A full run spends live Claude, Voyage embedding and rerank calls, which `docs/testing-strategy.md`
  already sanctions for the tiers that are not on the per-push gate. How often that is affordable to
  run, and whether a recorded-response mode is wanted, is explicitly 2c's decision.
- One pass per run. Model non-determinism means a metric carries run-to-run variance that this phase
  measures nothing about; repeating the set and reporting spread is left to 2c, which is where a
  gate has to decide how much difference is noise.
- The structured log events 1e and 1g contracted are available to the harness for the turns it drove.
  If the deployment they run against does not retain them, retrieval-stage metrics are excluded with
  a reason rather than estimated from the survivors.
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
- **Re-opening 2a's other decisions.** The count of 135, the two-valued `answerable` label, the
  unscored `gist`, and the dropped over-cap case stand. The scheduling fixture is added because a
  roadmap metric cannot be scored without it, and nothing else in the data is re-opened.
