# Feature Specification: Comparing One Version Against Another (Phase 2c)

**Feature Branch**: `013-compare-eval-runs`

**Created**: 2026-09-16

**Status**: Draft

**Input**: User description: "Create a spec for phase 2c" — the refactored Phase 2c in
`docs/ROADMAP.md`: a command a developer runs deliberately to compare two stored runs and report
what moved, plus the run-to-run noise band that says which movements mean anything. Explicitly not
a per-commit CI gate, and not tracing (2d).

## Why this exists

Phase 2b shipped the harness and took the first full run: 135 cases, 190 labelled requests, every
metric with its numerator and denominator, committed under
`specs/012-golden-set-metrics/evaluation/` as evidence rather than a threshold. That run immediately
produced the work this phase exists to serve. It found four wrong abstentions, all stopped by the
rerank floor at 0.559, 0.535, 0.520 and 0.377 against a floor of 0.58; it found G020 answered at
0.754 where its label predicted the floor would stop it; it found three tool labels that expect a
call the design makes unnecessary. Every one of those findings ends in the same sentence: *change
something and see what happens.*

Nothing today can answer that. Three specific things stand in the way, and none of them is fixed by
looking harder at two reports.

1. **An aggregate hides the cases that moved.** The unserved-answerable share is 6/87. A change that
   takes it to 7/87 might have degraded one case, or degraded two and fixed one — and only the
   second is worth a morning. The number is the same either way, and so is a reader's impression of
   it. Worse, a metric that does not move at all can have completely different cases behind it.
2. **A difference in conditions is indistinguishable from a difference in behaviour.** A run records
   the corpus hash, the model ids, the floors and the caps exactly as the service stated them. Two
   reports read side by side do not enforce that: nothing stops a reader attributing to a prompt
   change a movement that a corpus edit or a model version caused. And when the change under test
   *is* a floor move, the same fields are supposed to differ — so "the conditions differ" cannot be
   a blanket refusal either.
3. **A run's numbers move on their own.** The pipeline is non-deterministic. Nobody has ever run the
   golden set twice against one unchanged build, so the project has no idea whether a one-case
   movement is a signal or a coin flip. 2b said this in as many words and deferred it here: every
   statement of the form "this is better" currently rests on nothing.

This phase builds the one tool that closes all three — a comparison over two stored runs, reporting
per-metric movement, the cases behind it and the condition delta above it — and measures the noise
band that tells a reader which movements are worth acting on.

**Three things it deliberately is not.** It does not gate a build: the per-commit CI gate the
roadmap used to call for is dropped, for the reasons `docs/ROADMAP.md` Phase 2c now records, and
nothing in this phase may fail a build. It does not tune anything: like 2b, this phase measures, and
a movement it reports is a finding rather than a licence to move a floor inside this phase. And it
does not re-label: a disagreement between a label and a run remains a person's to adjudicate.

## Clarifications

### Session 2026-09-16

- Q: How much live spend should the noise-band measurement cost? → A: **Five full runs** over all
  135 cases against one unchanged build — roughly an hour of driving and five times a full run's
  model cost. Five points give a per-metric observed range wide enough that a movement outside it is
  genuinely surprising, and enough per-case evidence to tell a case that flips occasionally from one
  that flipped once. They are still an observed range rather than a confidence interval, and the
  spec says so wherever the band is reported, so no reader mistakes five observations for a
  statistical claim. The cost is deliberate and is this phase's one live-call expense.
- Q: Should the comparison command ever exit non-zero, so something could fail on it later? → A:
  **It always exits zero.** It is a reporting tool, and an exit code is the one thing a CI step
  would build on by accident. A later phase that wants a gate adds that deliberately, with the band
  in hand and with the scheduled run that produces the new artifact — which is the half that cannot
  be free.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See what moved between two runs (Priority: P1)

A developer changes something in the assistant — a floor, a prompt, a routing rule — takes a fresh
run of the golden set, and asks what that change did. They name the run they started from and the
run they just took, and get back: whether the conditions differ and how, which metrics moved and by
how much with both numerators and denominators, and, for every metric that moved, which cases moved
underneath it and in which direction. Cases that moved under a metric whose value did not change are
reported too.

**Why this priority**: This is the phase. Without it, 2b's findings cannot be acted on — a change
can be made but its effect cannot be read. It needs nothing that does not already exist: two stored
runs are enough, and the first one is already committed.

**Independent Test**: Compare the committed 2b run against a second full run taken from the same
build. Every metric appears with both values, the condition delta is empty, and each reported case
movement can be confirmed by opening that case's record in both runs.

**Acceptance Scenarios**:

1. **Given** two stored runs over the same 135 cases, **When** they are compared, **Then** every
   metric in both reports appears with its before and after value, numerator and denominator, and
   the direction of any movement.
2. **Given** a metric whose value is identical in both runs but whose underlying cases differ,
   **When** they are compared, **Then** the metric is reported as unchanged **and** the cases that
   moved beneath it are listed, rather than the metric being omitted as uninteresting.
3. **Given** two runs whose recorded conditions differ in the rerank floor, **When** they are
   compared, **Then** the condition delta names the floor and its two values, is presented before
   any metric, and the comparison still reports every metric.
4. **Given** two runs whose label digests differ for one or more cases, **When** they are compared,
   **Then** the comparison stops, names the cases whose labels moved, and reports no metric
   movement — the two runs answer different questions.
5. **Given** a request whose verdict was an abstention in one run and an answer in the other,
   **When** the runs are compared, **Then** that request is listed by case id and position with the
   direction of the change and the gate that stopped it where one did.
6. **Given** a case excluded from the metrics in one run and not the other, **When** the runs are
   compared, **Then** the case is named with both exclusion states, because an exclusion silently
   changes the denominator of every metric it touches.
7. **Given** the same run compared against itself, **When** it is compared, **Then** every metric is
   unchanged, no case movement is reported, and the condition delta is empty.

---

### User Story 2 - Know which movements mean anything (Priority: P2)

Before trusting any movement, a developer needs to know how much the numbers move when nothing
changes. The phase measures that once: five full runs against one unchanged build, recorded as a
noise band — the observed range of each metric, and the cases observed to move between runs of an
identical build. A comparison then marks each movement as inside or outside that range, and says
plainly that the band is five observations rather than a statistical interval.

**Why this priority**: It is what makes the word "regression" mean anything, and the roadmap calls
it the phase's first task. It is second here only because the comparison is usable without it — a
movement reported without a band is still a movement, provided nothing calls it a regression.

**Independent Test**: Measure the band from five runs of one build, then compare two of those same
five runs. Every movement between them must fall inside the band by construction, and none may be
marked outside it.

**Acceptance Scenarios**:

1. **Given** five runs of one unchanged build under identical conditions, **When** the band is
   measured, **Then** it records, per metric, the lowest and highest observed value, and, per case,
   every request or turn observed to differ between any two of the five runs, with how many of the
   five runs each varying case was in the minority in.
2. **Given** a band and two runs to compare, **When** a metric's movement lies within the band's
   observed range, **Then** it is marked as inside the observed range and the report states that
   the range comes from five runs and is not a confidence interval.
3. **Given** a band measured under one set of conditions and two runs recorded under different
   conditions, **When** they are compared with that band, **Then** the report says the band was
   measured under other conditions and does not mark any movement against it.
4. **Given** five runs of which one has different recorded conditions, **When** a band is measured
   from them, **Then** it is refused, naming the condition that differs — a band built across two
   builds measures the change, not the noise.
5. **Given** a case that differs between two runs of an unchanged build, **When** a later comparison
   reports that same case as moved, **Then** the report marks it as a case already known to vary on
   its own.

---

### User Story 3 - Compare a narrowed run during an investigation (Priority: P3)

While investigating one finding, a developer drives a handful of cases rather than all 135, because
a full run costs ten minutes and real money. The comparison serves that loop: a narrowed run
compares against the same cases of a wider baseline, with the restriction stated in the report, so
nobody reads a 12-case result as a statement about the set.

**Why this priority**: It is the loop 2b's `CASES=` and `FAMILY=` selection exists for, and the one
a developer actually runs several times an hour. It is last because the P1 comparison is what makes
it possible, and a full-to-full comparison already delivers value without it.

**Independent Test**: Take a four-case run, compare it against the committed 135-case run, and
confirm the report covers exactly those four cases, states the restriction, and recomputes both
sides' metrics over the same four cases rather than quoting the baseline's published denominators.

**Acceptance Scenarios**:

1. **Given** a run covering 12 cases and a baseline covering 135, **When** they are compared,
   **Then** the report compares only the 12 cases present in both, and names that restriction with
   the count before any metric.
2. **Given** that same pair, **When** a metric is reported, **Then** both sides' numerators and
   denominators are computed over the 12 common cases, never quoted from the baseline's own report
   over 135.
3. **Given** two runs with no cases in common, **When** they are compared, **Then** the comparison
   stops and says so, rather than reporting every metric as unchanged over an empty set.
4. **Given** a case recorded in one run and absent from the other, **When** they are compared,
   **Then** it is named as present on one side only and excluded from every metric.

---

### Edge Cases

- **A run that never finished.** A run artifact whose driving stopped partway carries fewer case
  records than its selection names. It is comparable — it is simply a narrower case set — but the
  report must say the run is incomplete, so a metric computed over 40 of 135 cases is never read as
  the set's.
- **A metric computed in one run and not the other.** 2b reports a metric as not computed when its
  selection contained nothing to compute it over. "Not computed" and "zero" are different facts and
  must not subtract into a delta; the pair is reported as such.
- **A denominator that moved.** A movement from 6/87 to 6/80 is a change even though the numerator
  held. Both parts are always shown, and a denominator change is called out, because it usually
  means exclusions moved rather than behaviour.
- **A case that changed in a direction that is neither better nor worse.** An abstention that moved
  from the similarity floor to the rerank floor is still an abstention. It is reported as changed
  without a direction, rather than being forced into an improvement or a degradation.
- **A band with nothing to say about a metric.** A metric absent from the five band runs — because
  their selection never exercised it — has no observed range, and any movement in it is reported
  unmarked rather than assumed stable.
- **The baseline lives under `specs/`.** 2b committed exactly one run, and it is the natural first
  baseline. It must be usable as one where it sits, without being copied back into the local
  artifacts directory first.
- **Two runs of the same build in which a case broke differently.** A case excluded as a failed turn
  in one run and scored in the other is an exclusion movement, not a behaviour movement, and is
  reported in that group.

## Requirements *(mandatory)*

### Functional Requirements

#### The command and what it reads

- **FR-001**: A single command MUST compare two stored runs and report what moved, taking a baseline
  run and a new run as its two inputs.
- **FR-002**: Each input MUST be accepted either as a run identifier resolved against the local
  artifacts directory or as a path to a run directory, so the run committed under
  `specs/012-golden-set-metrics/evaluation/` is usable as a baseline where it sits (FR-048b of 2b
  keeps it the only committed run, and this must not become a reason to copy it around).
- **FR-003**: The comparison MUST read only stored run artifacts — the run record, the per-case
  records and the reports — and the golden set's own labels. It MUST NOT drive a turn, call a model,
  reach the chat or scheduling services, or read a database.
- **FR-004**: The comparison MUST be a pure function of its two inputs, in the sense 2b already
  enforces for scoring: its modules may not import the HTTP client, the gRPC stack, the database
  layer or the driver, and this MUST be enforced by a test, not by convention.
- **FR-005**: Running the comparison with the whole stack down MUST produce the same output as
  running it with the stack up.
- **FR-006**: The command MUST always exit zero, whatever it finds. It MUST NOT offer a mode that
  exits non-zero on a regression, and no part of this phase may cause a build to fail on a metric.
- **FR-007**: A comparison MUST be reproducible: re-running it over the same two stored runs MUST
  produce the same report apart from its own timing fields — when it ran, and how long it took.

#### Conditions, labels and what invalidates a comparison

- **FR-008**: The report MUST state the delta between the two runs' recorded conditions — corpus
  hash, model identifiers, floors, caps, pool size, segment cap and context turns as each run
  recorded them — and MUST present it before any metric.
- **FR-009**: A difference in conditions MUST NOT stop the comparison. A deliberate threshold change
  is the most common reason to run this tool, and refusing it would remove the tool's main use.
- **FR-010**: A difference in the corpus hash MUST be reported as prominently as a threshold
  difference, because it changes what every retrieval metric was measured against.
- **FR-011**: A difference in the scored-field digest of any label MUST stop the comparison, naming
  the cases whose labels moved. Two runs scored against different labels are answers to different
  questions, and 2b already refuses to re-score under this condition — this phase MUST NOT be a way
  around that refusal.
- **FR-012**: An incomplete run — one holding fewer case records than its selection names — MUST be
  comparable, and the report MUST state that it is incomplete and over how many cases.

#### What the report contains

- **FR-013**: Every metric present in either run MUST appear in the report with both runs' value,
  numerator and denominator, and the direction of any movement.
- **FR-014**: A metric that is unchanged MUST still appear. Reporting only what moved hides the
  difference between "this held steady" and "this was never computed".
- **FR-015**: A metric computed in one run and not computed in the other MUST be reported as that
  pair, and MUST NOT be turned into a numeric delta.
- **FR-016**: A movement in a metric's denominator MUST be reported distinctly from a movement in
  its numerator.
- **FR-017**: For every metric, the report MUST list the cases whose contribution to it changed,
  identified by case id and, where the metric is per request, by request position.
- **FR-018**: Case movements MUST be reported even when the metric they sit under did not move.
- **FR-019**: A case movement MUST carry a direction — improved, degraded, or changed without a
  direction — and the report MUST NOT force a change that is neither better nor worse into one of
  the first two.
- **FR-020**: The report MUST group movements by what changed: a request's verdict, a request's
  retrieval rank, a turn's segmentation or intents, a booking's tool selection, a booking's final
  database state, and a case's exclusion state. Each answers a different question and calls for a
  different fix — a verdict that moved while its rank held is a gate's doing, and a rank that moved
  without the verdict is retrieval churn that has not cost anything yet.
- **FR-021**: A case whose exclusion state differs between the runs MUST be named with both states,
  since an exclusion changes denominators without any behaviour changing.
- **FR-022**: A case present in only one of the two runs MUST be named as such and excluded from
  every metric.
- **FR-023**: When the two runs cover different case sets, every metric MUST be computed over the
  cases both runs recorded, and MUST NOT be quoted from either run's own published report. The
  report MUST state the restriction and the number of common cases before any metric.
- **FR-024**: A comparison whose two runs have no case in common MUST stop and say so.
- **FR-025**: The report MUST be produced in both a human-readable form printed to the terminal and
  a machine-readable form, and the machine-readable form MUST be sufficient to re-render the
  human-readable one without re-reading the runs.
- **FR-026**: A comparison's stored output MUST record which two runs it compared, so a report found
  later can be traced to its inputs.

#### The noise band

- **FR-027**: The phase MUST measure a noise band from **five** full runs over all 135 cases
  against one unchanged build, and store it as its own artifact.
- **FR-028**: The band MUST record, per metric, the lowest and highest value observed across the
  five runs, and, per case, every request or turn that differed between any two of them, with how
  often each varying case took each outcome across the five.
- **FR-029**: The band MUST record the conditions it was measured under, as a run does.
- **FR-030**: Building a band from runs whose conditions or label digests differ MUST be refused,
  naming what differs. A band built across two builds measures the change rather than the noise.
- **FR-031**: A comparison MAY be given a band. When it is, each metric movement MUST be marked as
  inside or outside the band's observed range for that metric.
- **FR-032**: Wherever the band is reported, the report MUST state that the range is the spread of
  five observations and not a confidence interval or a statistical significance claim.
- **FR-033**: A comparison given a band whose recorded conditions differ from either run's MUST say
  so and MUST NOT mark any movement against it.
- **FR-034**: A metric the band has no observation for MUST have its movement reported unmarked,
  never assumed stable.
- **FR-035**: A case the band observed to vary on an unchanged build MUST be marked as such wherever
  a later comparison reports it as moved.
- **FR-036**: Without a band, the report MUST describe movements in neutral terms and MUST NOT call
  any movement a regression or an improvement of the system.

#### The phase's record

- **FR-037**: The five band runs' reports and the band artifact MUST be committed under this
  feature's `evaluation/` directory as the phase's record, together with one worked comparison
  between two of them.
- **FR-038**: That record MUST state that it is evidence of how much the numbers move on their own,
  not a threshold any later run is required to beat.
- **FR-039**: The committed 2b run MUST remain the **documented** baseline a comparison starts from —
  a convention the documentation states and the quickstart demonstrates, not a value the command
  fills in. Both runs MUST be named explicitly on every invocation: a baseline silently defaulted to
  a path under `specs/` is a command reaching somewhere its caller did not look. Promoting a
  different run to baseline MUST stay a deliberate act with a reason recorded — nothing in this
  phase may promote a baseline automatically.
- **FR-040**: The documentation the constitution requires MUST be updated in the same change: what
  the comparison is and is not, how a band is measured and what it does not claim, and the commands.

### Key Entities

- **Run reference**: one of the two stored runs under comparison — an identifier or a path, plus the
  conditions, label digests, case set and completeness read from it.
- **Condition delta**: the field-by-field difference between the two runs' recorded conditions, with
  each field's two values; empty when the runs were measured under identical settings.
- **Metric movement**: one metric's pair of values with both numerators and denominators, its
  direction, whether its denominator moved, and, when a band is supplied, whether it falls inside
  the observed range.
- **Case movement**: one case's change between the runs — its group (verdict, segmentation, tool
  selection, database state, exclusion), the request position where the metric is per request, both
  states, a direction where one exists, and whether the band observed this case to vary on its own.
- **Noise band**: the per-metric observed range and per-case variation gathered from five runs of
  one unchanged build, with the conditions it was measured under.
- **Comparison record**: the stored machine-readable result — its two run references, the condition
  delta, every metric movement, every case movement, the common-case restriction, and the band it
  was marked against if any.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Given two stored runs, a developer learns what changed without opening a single case
  file: every metric appears with both values and their numerators and denominators, and every case
  behind a changed metric is named.
- **SC-002**: A comparison makes zero model calls and completes with every service stopped.
- **SC-003**: Re-running a comparison over the same two stored runs produces an identical report
  apart from its own timing fields — when it ran, and how long it took.
- **SC-004**: Every case named as moved can be confirmed by opening that case's record in both runs;
  no movement is reported that the two records do not show.
- **SC-005**: A metric whose value is unchanged while its underlying cases differ is reported, with
  those cases named — the case a bare delta cannot express.
- **SC-006**: Two runs scored against different labels produce no metric comparison at all, and the
  cases whose labels moved are named.
- **SC-007**: A narrowed run compared against the committed 135-case baseline reports only the
  common cases, states the restriction and the count, and recomputes both sides over those cases.
- **SC-008**: The noise band is measured from five full runs of one unchanged build and published
  with the conditions it was measured under, and every report that cites it states that it is five
  observations rather than a statistical interval.
- **SC-009**: Comparing any two of the five band runs marks every movement as inside the observed
  range, and marks none outside it.
- **SC-010**: No invocation of the comparison exits non-zero, and no build in the repository can
  fail because of a metric value.
- **SC-011**: The phase ships with its band and one worked comparison committed, and a reader can
  trace any number in them to the **reports** of the five runs that produced them without re-running
  anything. The five runs' per-case records are deliberately not committed: five full runs are some
  12 MB of JSON, against 2b's one committed run at 2.5 MB, and the band's value is the shape of the
  spread rather than the turn behind each observation. The consequence is stated rather than
  discovered — a committed band's per-case variation cannot be re-derived later, and a question that
  needs the turn behind it needs the five runs taken again.

## Assumptions

- The harness Phase 2b shipped is the foundation: run artifacts keep the layout and the fields its
  run-record contract defines, `make eval-run` remains the only way a new run is produced, and
  scoring stays a pure function of a stored run. This phase adds a reader, not a second driver.
- Taking the five band runs is a manual act against a locally running stack, as 2b's runs are.
  Standing that stack up is not part of this phase.
- The five band runs are taken against one unchanged build in one sitting of roughly an hour.
  Restarting the services
  between them is acceptable, since the conditions check (FR-030) is what actually enforces
  sameness, and a restart under identical settings records identical conditions.
- The golden set stays at 135 cases with its labels unchanged through this phase. Re-labelling a
  case is a separate, deliberate act that invalidates comparisons across it, which FR-011 enforces
  rather than prevents.
- Cost is controlled the way 2b established: by narrowing a run, never by running a case
  differently. The band's five full runs are the phase's one deliberate live-call expense.
- A developer reading a comparison knows the build they changed. The tool reports what moved and
  under which conditions; attributing a movement to a specific edit stays the reader's judgement.

## Out of Scope

- **A CI gate.** No workflow runs the golden set, and no build fails on a metric. The roadmap
  records why, and a later phase may revisit it with the band in hand and with a scheduled run that
  produces the artifact — the half that cannot be free.
- **Tracing (2d).** Per-step latency and token cost stay with Langfuse in the next subphase.
- **Tuning anything.** No floor, cap, prompt or routing rule moves in this phase. A movement this
  tool reports is a finding, and acting on it is the next change, not this one.
- **Re-labelling from a run.** A disagreement between a label and a run remains a person's to
  adjudicate, as 2b's record already states for its own findings.
- **Statistical inference.** Five observations give an observed range. Confidence intervals,
  significance tests and a per-metric variance model need more runs than this phase spends, and
  claiming any of them from five points would be the kind of unfalsifiable number the eval work
  exists to avoid.
- **Automatic baseline promotion.** Nothing decides on its own that a newer run is the one to
  compare against next.
