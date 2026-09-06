# Feature Specification: Reranked Retrieval Pipeline (Phase 1e)

**Feature Branch**: `008-reranked-retrieval-pipeline`

**Created**: 2026-09-05

**Status**: Draft

**Input**: User description: "Create a spec for @docs/ROADMAP.md phase 1e" — "RAG done properly":
upgrade Phase 0's naive embed-and-top-k retrieval into a pipeline with a defensible stage for each
job, add a cross-encoder reranking stage between retrieval and generation, gate both stages on an
explicit score floor and cap, log every stage's candidates and decisions, build citations only from
what survived, and retire the turn's `grounded` boolean.

## Clarifications

### Session 2026-09-05

- Q: The first 1e bullet is titled "Defensible chunking, replacing Phase 0's naive split", but its
  body describes only the retrieval stage. Does 1e change how documents are split at index time? →
  A: **No — retrieval only; chunking is unchanged.** The existing splitter (~1,000 characters, 150
  of overlap, nudged back to the nearest paragraph or sentence boundary, degenerate chunks dropped)
  stays exactly as it is, and nothing is re-indexed. The bullet's title is a leftover from when the
  phase was planned; its body is the actual scope. This keeps 1e a *query-time* change end to end:
  every entry already in a session's corpus is answered from the same points it was answered from
  before, and only the selection of those points changes.

- Q: 1e caps the cosine stage at 5 chunks, but the reranking bullet argues "retrieve wide for recall
  → rerank → keep the best few". A cap of 5 before a rerank to 3 is not wide. Which was meant? → A:
  **Literally 5 then 3, with a cosine floor of 0.3.** The retrieval stage keeps every chunk scoring
  at or above 0.3, up to the 5 highest; the reranking stage keeps every survivor at or above the
  rerank floor, up to the 3 highest. The reranker is therefore doing *precision* work on this
  corpus, not recall work: its job is to throw out the chunk that scored 0.34 because it shares
  clinic vocabulary with the question while answering something else. Casting a genuinely wide net
  (20–30 candidates) is deferred rather than rejected — it costs a larger reranker call per turn,
  and the corpus is small enough that top-5 recall is not yet the binding constraint. Phase 2's
  calibration is where that gets measured instead of assumed.

- Q: The vector search could be asked for exactly the five the cap allows, in which case the cap
  never drops anything and nobody can ever tell whether a wider net would have helped. How wide does
  retrieval actually fetch? → A: **Fetch a wider observation pool — 25 by default — and gate it at
  floor 0.3 with a cap of 5.** Behavior is untouched: the same chunks reach the prompt and the same
  chunks are cited. What changes is only what the turn *records* — candidates 6 through 25 are
  logged with their scores and marked observed-but-not-considered, so the cap becomes a threshold
  Phase 2 can calibrate against evidence rather than a number nobody can second-guess. A cap that
  discards only chunks it never fetched cannot be raised or lowered on any grounds. The cost is one
  larger vector search per turn and no additional model call of any kind, since the reranker still
  only ever sees the survivors.

- Q: SC-008 pins the rerank floor's shipped default against 20 hand-checked questions, but never
  says where that set lives afterwards. Artifact or one-off? → A: **A committed data file plus a
  documented manual procedure — no test, no CI.** Each of the 20 carries the question, whether the
  corpus answers it, and for the answerable ones the entry that should be cited; the floor chosen
  and the result it measured are recorded beside it. Without it the next person to move a floor
  invents 20 new questions and cannot compare their number to the one that shipped. It is
  deliberately *not* an automated test: a live-model test is Phase 2's eval harness, and building
  one here would start the next phase inside this one. What it is instead is the seed Phase 2's
  50–100-question golden dataset grows from.

- Q: SC-008 requires "correct citations", which is not testable as written — must every citation come
  from the answering entry? → A: **At least one citation must be a chunk from the entry a human says
  contains the answer.** Requiring all of them would fail a correct answer for the ordinary reason
  that a second entry also had something relevant to say, and the cap already keeps the citation list
  to three.

- Q: FR-010 makes a reranker *failure* non-fatal, but a reranker that is merely slow is not a failure
  and had no deadline — and it sits between retrieval and the first generated token, so every second
  it hangs is dead air. What bounds it? → A: **An explicit 5-second deadline, configurable, and
  exceeding it is a failure under FR-010** — error logged, fall back to the retrieval survivors, the
  patient still gets an answer. The tolerant end of the range was chosen deliberately: falling back
  costs the turn its precision stage, so a call that would have returned at 3 seconds is worth
  waiting for. A deadline here carries none of the usual "a timeout never proves the server did
  nothing" hazard, because reranking is a pure read with no side effect that could be left
  half-finished.

- Q: FR-023 puts the verdict in the message history both panes read, but does a human ever see it? →
  A: **Only in the staff console, and only for one verdict: answered without reranking.** That
  message carries a small marker saying the answer was produced without the reranking stage, with a
  hover explaining it; the other four verdicts get no visible treatment, because an answer with
  citations and an abstention message already say what they are. The patient pane gains no verdict
  marker of any kind — how an answer was produced is not the patient's to reason about. *(What the
  patient pane shows about citations is a separate question, settled below.)* This is the converse of spec 007's
  rule about the FAQ screen's indexing indicator: that signal was removed because it could never
  fire, and this one is shown because it can, and because it is the only verdict a staff member can
  act on — by reading the conversation and correcting the patient.

- Q: "Every step is logged ... the same logs will be used in Phase 2 for metrics and threshold
  adjustments." Where does that per-stage record live? → A: **Structured log events only.** No trace
  table, no new storage, no new read API. Each stage emits an event carrying its candidates, their
  scores, and what it kept and dropped, correlated by the turn correlation id that spec 002 already
  puts on every line. Phase 2 owns the question of how those become queryable metrics — that is what
  Langfuse and the golden-dataset harness are *for*, and standing up a bespoke trace store here
  would build the thing Phase 2 then replaces.

- Q: 1e retires the turn's `grounded` flag, and Phase 2's roadmap entry says 1e "replaces `grounded`
  with a typed verdict". What replaces it? → A: **A typed verdict naming which gate the turn stopped
  at.** Each value is one situation: the turn was answered from a reranked shortlist; it was answered
  from the retrieval survivors because the reranker was unavailable; or it abstained, at one of the
  gates. A boolean cannot say the abstentions apart, and which gate rejected a question is exactly
  the signal that says *which floor is mistuned* — the reason 1d built the FAQ screen by hand in the
  first place. *(The abstention half was split twice more below, giving six values in all.)*

- Q: A retrieval-gate abstention covered two different situations — the session's corpus is empty and
  no search was issued at all, or the corpus had content and nothing cleared the floor. Should they
  be one value? → A: **No — they split, giving five verdicts.** The two call for different fixes (add
  entries, versus rewrite the entry or lower the floor), and a value whose meaning needs the word
  "or" is exactly what `.claude/CLAUDE.md`'s "one value, one meaning" rule forbids. Nothing about
  behavior changes: both abstain identically, escalate identically, and are identical to the patient
  — this splits the *record*, not the path, so spec 007's refusal to special-case an empty corpus
  (which was about behavior, and which still stands) is untouched. Since 007 seeds every new session
  with the default corpus, an empty corpus no longer means "new session" — it means the seed failed
  or someone deleted everything, which is worth naming rather than folding into a retrieval miss.

- Q: Both panes render citations today from one shared component. Who should see them after this
  phase? → A: **The staff console only; the patient pane stops showing them.** The citation list is
  evidence about how an answer was produced, and this phase makes it precise enough to audit — which
  is a staff activity, not a patient one. A patient reading a clinic's answer wants the answer;
  chunk text pasted underneath is the clinic's internal source material, and showing it invites them
  to read around the answer rather than trust it. The citations stay in the data on both paths
  (FR-016a) so the two panes read one message record and the staff view is complete; only the
  rendering differs. This is a patient-visible removal, and the only one this phase makes.

### Session 2026-09-06

- Q: FR-023d hides citations from the patient because chunk text is internal source material, while
  FR-016a still ships that text to the patient's browser — so devtools shows what the rule says is
  not theirs. Which gives way? → A: **Neither: one payload for both panes, and FR-023d's rationale is
  corrected to claim presentation rather than access.** This app has no confidentiality boundary to
  enforce — one anonymous session owns both panes, and the same person can read and edit every entry
  on the FAQ screen — so an omitted field would buy nothing while splitting one message record into
  two shapes that can drift. What the rule is actually for is that a patient reading a clinic's
  answer should get the answer, not the clinic's working notes underneath it. Stated that way it is
  a presentation rule, which is what it always was, and the spec no longer implies a protection it
  does not provide (FR-023f).

- Q: SC-008's calibration is a person asking questions and reading results, but the console shows
  citations with no numbers — should it annotate each with its similarity and rerank score? → A:
  **No. Citations only; every score lives in the logs and nowhere else.** One number in one place: a
  score rendered in the console and logged as well is a second copy that can disagree with the first
  after any change to how either is built, and the console would then be a plausible-looking source
  of truth that nobody verifies. The calibration procedure reads the logs, which carry strictly more
  than the console could show — the candidates each gate *rejected*, which is what a floor is
  actually tuned against and which no citation list contains (FR-023g). Phase 2 keeps the option of
  putting retrieval effects on the FAQ screen, as the roadmap always said it might.

- Q: During a sustained outage every turn pays the full 5-second deadline. Should a circuit breaker
  short-circuit the call after N consecutive failures? → A: **No breaker. Every turn tries, waits,
  and falls back.** A breaker is shared mutable state — a failure counter, a latch, a clock to reopen
  on — and each piece needs an invariant written down and a test holding it, which is real risk taken
  on to save five seconds a turn in a single-user demo. Without one, turn 1 and turn 100 of an outage
  behave identically, recovery is immediate the moment the dependency returns, and there is no state
  that can be left open or reset wrongly (FR-010b). If a breaker is ever wanted, an outage's cost is
  already measurable from the run of error events the fallback logs.

- Q: FR-027 logs every candidate in the 25-strong pool, and today's equivalent event logs each
  chunk's full text — which would put ~25,000 characters on every FAQ turn, most of it for candidates
  the cap discarded. What does the tail carry? → A: **The considered candidates carry their full
  text; the discarded tail carries its identity, its score, and its text truncated to the first 200
  characters.** Identity and score alone would answer "how close was it" but not "was it the chunk I
  was hoping for", which is the question someone lowering a cap is actually asking — and 200
  characters is enough to recognize a chunk without reprinting it. Per-turn log volume lands near
  today's rather than five times it (FR-027a).

- Q: The per-turn completion line carries each citation with the score it was retrieved at, but there
  are two scores now — and FR-023g just made "one number in one place" a rule. What does it carry? →
  A: **Both scores per surviving citation**, the rerank one absent on an unreranked answer, keeping
  the line the self-contained per-turn summary it already is (FR-025a). The drift risk behind
  FR-023g does not apply: the console would be a second copy built by different code in another
  language, while this line is written by the same function from the same values it just used. The
  payoff is that Phase 2 can compute a per-turn metric from one line instead of joining stage events
  by correlation id.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An answer stands only on chunks that actually answer the question (Priority: P1)

A patient asks a policy question. Retrieval finds several chunks that are near the question in
embedding space — some because they answer it, some only because they share the clinic's vocabulary.
A reranking stage scores each surviving chunk *against the question* and keeps only the best few that
clear a relevance bar. The answer is generated from those, and the citations recorded for the turn —
which staff can read in the console — are those same chunks, so a citation means "this is what the
answer stands on" rather than "this was nearby".

**Why this priority**: This is the phase's reason to exist. Today every chunk the vector search
returns is concatenated into the prompt regardless of its own score, so a single strong match drags
four weak ones into the context and into the citation list. Everything else in this spec — the gates,
the verdict, the logging — is machinery in service of this outcome.

**Independent Test**: Ask a question the corpus answers in one specific entry, with other entries
present that share its vocabulary. Confirm — from the staff console, where citations are now shown —
that the answer's citations are only the chunks the reranker kept, that there are at most three of
them, and that a chunk which the retrieval stage returned but the reranker rejected appears in
neither the prompt context nor the citations.

**Acceptance Scenarios**:

1. **Given** a corpus where five chunks clear the retrieval floor for a question but only two are
   actually about it, **When** the patient asks it, **Then** the answer is generated from those two
   chunks alone, exactly those two are cited, and the citation list is visible in the staff console
   and absent from the patient pane.
2. **Given** a question whose reranked shortlist would exceed three chunks, **When** the turn runs,
   **Then** only the three highest-scoring survivors reach the prompt and the citations.
3. **Given** an answered turn, **When** its citations are compared against the text placed in the
   generation context, **Then** every citation corresponds to a chunk that was in that context and
   every chunk in that context is cited — the two sets are identical.
4. **Given** a mixed-intent message that routes to both the FAQ and booking specialists, **When** the
   merged reply is composed, **Then** its citations are the FAQ half's reranked survivors, unchanged
   by the merge, exactly as a single-specialist turn's would be.

---

### User Story 2 - A question the corpus cannot answer is refused before a word is generated (Priority: P1)

A patient asks something the clinic's corpus has no answer for. Either nothing retrieved clears the
similarity floor, or things did but none of them survived reranking. In both cases the assistant says
plainly that it does not have the information and that staff have been asked to follow up, calls
staff, and never spends a generation call inventing an answer around weak context.

**Why this priority**: Abstention is a correctness requirement, not a nicety (constitution principle
V), and this phase gives it a second gate. It ships with US1 because the two are one decision made
twice: the same score that qualifies a chunk to be cited is the score that, absent, means there is
nothing to answer from.

**Independent Test**: Ask a question with no answer in the corpus and confirm the fixed abstention
message is what the patient receives, that no generation-model call was made for it, that the
conversation carries a call to staff with the corpus-gap reason, and that the turn record names the
gate that stopped it. Repeat with a corpus tuned so the question clears retrieval but fails
reranking, and confirm the same patient-visible outcome with a different recorded gate.

**Acceptance Scenarios**:

1. **Given** a question whose best retrieved chunk scores below the similarity floor, **When** the
   turn runs, **Then** the patient receives the abstention message, no reranking call and no
   generation call are made, and the turn is recorded as having abstained because nothing cleared
   the similarity floor.
2. **Given** a question whose retrieved chunks clear the similarity floor but none clear the rerank
   floor, **When** the turn runs, **Then** the patient receives the same abstention message, no
   generation call is made, and the turn is recorded as having abstained at the reranking gate.
3. **Given** either abstention, **When** the turn completes, **Then** staff are called with the
   existing corpus-gap reason and the patient's message carries the existing corpus-gap attention
   mark — the mark, the reason, and the assistant's continued willingness to talk are all unchanged
   from spec 007.
4. **Given** a session whose corpus is empty, **When** the patient asks anything the FAQ path
   handles, **Then** the turn abstains without embedding the question or searching, exactly as it
   does today, and is recorded with the empty-corpus verdict rather than the similarity-floor one.

---

### User Story 3 - A reranker outage degrades the answer instead of breaking the turn (Priority: P2)

The reranking service is unreachable or errors. Rather than failing the turn or abstaining, the
assistant logs the failure as an error and answers from the retrieval survivors alone — the behavior
the system had before this phase. The turn's record says the answer was produced without reranking,
so nobody later mistakes it for one the reranker approved.

**Why this priority**: Reranking is a new external dependency on the FAQ path, which is the path that
works today. A new dependency that can take down a working path is a regression dressed as an
improvement. It is P2 rather than P1 only because it cannot be built before the stage it degrades.

**Independent Test**: Make the reranking dependency fail, ask a question the corpus answers, and
confirm the patient still gets a grounded, cited answer built from the retrieval survivors; that an
error-level log line names the failure; and that the turn's verdict distinguishes this answer from a
reranked one.

**Acceptance Scenarios**:

1. **Given** the reranking dependency is unavailable, **When** a question clears the retrieval gate,
   **Then** the answer is generated from the retrieval survivors (up to five chunks), those chunks
   are the citations, and the turn is recorded as answered without reranking.
2. **Given** the reranking dependency is unavailable, **When** a question does **not** clear the
   retrieval gate, **Then** the turn abstains exactly as it would have with a healthy reranker —
   the fallback never rescues a question the first gate already rejected.
3. **Given** any reranking failure, **When** it is handled, **Then** an error-level event names the
   stage and the underlying failure, and the turn's other stages log exactly as they otherwise would.
4. **Given** a reranking failure, **When** the turn completes, **Then** no call to staff is made on
   account of it — a degraded answer is an answer, not a corpus gap.

---

### User Story 4 - Every stage's decision is reconstructable from the logs (Priority: P2)

Someone tuning a threshold by hand — the workflow 1d's FAQ screen exists to support — asks a
question, watches the answer, and then reads the log for that turn to see exactly what each stage
saw: which chunks came back and at what similarity, which the floor and cap kept and which they
dropped, what the reranker scored each survivor, which the rerank floor and cap kept, and what
verdict the turn ended on. Nothing about the decision requires re-running the turn or reading code.

**Why this priority**: The roadmap makes this the input to Phase 2's metrics and threshold
calibration, and a floor cannot be tuned against outcomes it does not record. It is separable from
US1–US3 — the pipeline works without it — but the phase's value does not survive without it.

**Independent Test**: Run one answered turn and one abstained turn, then reconstruct from their log
lines alone the full candidate list with scores at each stage, every keep/drop decision with the
threshold that produced it, and the final verdict — without consulting the source or the database.

**Acceptance Scenarios**:

1. **Given** any FAQ turn, **When** its log lines are collected by the turn's correlation id, **Then**
   they contain the retrieval candidates with their similarity scores, the similarity gate's floor
   and cap with what it kept and dropped, and the turn's verdict.
2. **Given** a turn that reached reranking, **When** its logs are read, **Then** they additionally
   contain each candidate's rerank score and the rerank gate's floor and cap with what it kept and
   dropped.
3. **Given** a turn that abstained, **When** its logs are read, **Then** they name which gate rejected
   it and the scores that fell short, so the reader can tell "nothing was close" from "something was
   close and the floor was too high".
4. **Given** a chunk that a gate dropped, **When** the logs are read, **Then** the chunk is
   identifiable and its score is present — a gate that logs only its survivors cannot be tuned, since
   lowering a floor is a decision about what it rejected.

---

### User Story 5 - A turn's outcome says which gate it stopped at (Priority: P3)

Wherever the system currently records or reports whether a turn was "grounded" — the stored message,
the terminal event the chat client reads, the message history the patient pane and the staff console
render — it now reports a typed verdict instead: answered, answered without reranking, abstained
because the corpus is empty, abstained because nothing cleared the similarity floor, or abstained
because nothing cleared the rerank floor. A turn with no FAQ half carries no verdict at all, as it
carries no groundedness flag today.

**Why this priority**: The boolean's two values no longer partition the outcomes: an answered turn is
always grounded, so `true` has stopped carrying information, while `false` now covers three
situations that call for three different fixes. It is P3 because it is a contract change over surfaces US1–US4 already
make correct — valuable, but nothing else waits on it.

**Independent Test**: Run one turn of each of the five kinds and confirm the stored message, the
terminal stream event, and the rendered history all report the matching verdict; then run a
booking-only turn and confirm all three report no verdict.

**Acceptance Scenarios**:

1. **Given** a turn answered from a reranked shortlist, **When** it completes, **Then** its verdict is
   "answered" in the stored message, the terminal event, and the message history.
2. **Given** a turn answered without reranking, **When** it completes, **Then** its verdict says so
   and is distinguishable from a reranked answer everywhere the verdict appears.
3. **Given** a turn that abstained, **When** it completes, **Then** its verdict names why — an empty
   corpus, the similarity floor, or the rerank floor — and the patient-visible message is the
   existing abstention text, identical across all three.
4. **Given** a turn with no FAQ specialist (a booking-only reply) or a staff or patient message,
   **When** it is read back, **Then** it carries no verdict — the field is absent, not defaulted to
   an answered or abstained value.
5. **Given** the migration has run, **When** the message table is inspected, **Then** it holds no
   message carrying a verdict this phase's gates did not produce — no value was translated from the
   old flag, because there was nothing stored to translate (FR-024).

---

### Edge Cases

- **Exactly one chunk clears the similarity floor.** It is still sent to reranking. A shortlist of
  one is precisely the case where a bi-encoder is least trustworthy — one nearby point and nothing to
  compare it against — so skipping the cross-encoder there would drop the check on the turn most
  likely to need it.
- **A chunk sits exactly on a floor.** Both floors are inclusive: a score equal to the floor passes.
  Stated so that a threshold moved to a value seen in a log has the effect the reader expects.
- **More candidates clear a floor than its cap allows.** The cap is applied after the floor, keeping
  the highest scores; the dropped-by-cap chunks are logged separately from the dropped-by-floor ones,
  because one says "the bar is too high" and the other says "the bar is too low".
- **The observation pool is larger than the whole corpus.** The search returns whatever exists and
  the pool is simply short — not an error, and not distinguishable in effect from a full pool whose
  tail fell below the floor. The logged pool size is what it actually returned.
- **The observation pool's tail is all noise.** Expected, and the point: a pool whose candidates 6
  through 25 are visibly irrelevant is the evidence that a cap of 5 is not costing anything — and the
  200-character previews (FR-027a) are what let a reader see that at a glance instead of inferring it
  from scores.
- **A discarded candidate is shorter than 200 characters.** It is logged whole and MUST NOT be
  presented as truncated (FR-027b).
- **The reranker returns a shortlist in a different order than the similarity search did.** The
  reranked order wins — for the prompt context and for the citation order — since re-ordering is the
  stage's entire purpose.
- **The reranker succeeds but scores every candidate below the floor.** That is an abstention at the
  reranking gate, not a fallback: the reranker answered, and its answer was "none of these".
- **The reranker fails after the retrieval gate has already abstained.** It is never called, so there
  is nothing to fail; the turn is an ordinary retrieval-gate abstention.
- **The reranker answers after the deadline has passed.** The answer is discarded and the turn stays
  on the path it already took. A late score cannot be allowed to re-open a decision the turn has
  moved past, and reordering a shortlist mid-generation would change what the citations mean.
- **The reranker is slow on every turn rather than failing outright.** Each turn pays the deadline
  and falls back — accepted, not mitigated (FR-010b). It is visible as a run of error-level events
  and a run of turns answered without reranking, the two signals a reader needs to tell a degraded
  dependency from a corpus problem, and it is also the measurement that would justify a breaker if
  one were ever wanted.
- **Embedding or vector search fails.** Unchanged from today: the turn fails as a pipeline error, the
  patient's message carries the assistant-failed mark, and staff are called. The reranker's
  non-fatal treatment is deliberately *not* extended to the stages the answer cannot be produced
  without.
- **A turn is cancelled or the assistant is silenced mid-pipeline.** Unchanged from spec 007: the
  turn produces no reply and stores nothing, whatever stage it had reached.
- **A conversation mixes reranked and unreranked answers.** Expected during an outage, and each
  message carries its own verdict — the marker is per message, never per conversation, so a
  conversation is never labelled degraded as a whole.
- **A message stored before this change is read in the console.** There are none (FR-024), so this
  case does not arise. It is listed because its absence is a decision — the alternative, a
  backfilled verdict, would have put a value on a turn no gate in this phase ever judged.
- **An entry is edited between retrieval and reranking.** Unchanged: retrieval already reads the
  revisions the row published at the moment it searched, and reranking scores the text retrieval
  returned.

## Requirements *(mandatory)*

### Functional Requirements

**The retrieval stage**

- **FR-001**: The FAQ path MUST retrieve candidate chunks by vector similarity against the session's
  live corpus, unchanged from today in what it searches and how it is scoped.
- **FR-002**: Retrieval MUST keep only candidates whose similarity score is at or above a **minimum
  similarity floor**, and of those, at most the **five** highest-scoring. Both bounds MUST be
  configurable without a code change.
- **FR-002a**: The vector search MUST fetch an **observation pool** larger than that cap —
  **25** candidates by default, configurable — so that the cap has something to discard and the
  discarded candidates are on the record. The pool size MUST NOT affect the answer: only the
  candidates surviving FR-002 may reach reranking, the generation context, or the citations, and a
  candidate beyond the cap MUST be recorded (FR-027a) and otherwise ignored.
- **FR-002b**: The observation pool MUST NOT cause any additional model call. It widens the vector
  search alone; the reranker sees only FR-002's survivors.
- **FR-003**: The minimum similarity floor MUST default to **0.3**.
- **FR-004**: The similarity floor MUST be applied **per chunk**, not to the best chunk on behalf of
  the rest. A chunk below the floor MUST NOT reach reranking, the generation context, or the
  citations, regardless of how well another chunk scored. This replaces the existing gate, which
  admitted every retrieved chunk whenever the single best one cleared its threshold.
- **FR-005**: The turn MUST abstain immediately — no reranking call, no generation call — in either
  of two distinct conditions, which FR-021 records as two distinct verdicts: **(a)** the session
  publishes no live revisions, so no search is issued at all; **(b)** a search was issued and no
  candidate cleared the similarity floor. The behavior is identical and the record is not: folding
  them into one condition is what the verdict split exists to undo.

**The reranking stage**

- **FR-006**: Every candidate surviving FR-002 MUST be scored by a **cross-encoder reranker** that
  sees the question and the chunk together, and the survivors MUST be ordered by that score.
- **FR-007**: Reranking MUST keep only candidates whose rerank score is at or above a **minimum
  rerank floor**, and of those, at most the **three** highest-scoring. Both bounds MUST be
  configurable without a code change.
- **FR-008**: When no candidate clears the rerank floor, the turn MUST abstain: no generation call.
- **FR-009**: Both floors MUST be inclusive, and each cap MUST be applied after its floor.

**Fallback**

- **FR-010**: A failure to obtain reranking scores MUST NOT fail the turn and MUST NOT cause an
  abstention. The turn MUST log the failure at error level and generate its answer from the retrieval
  survivors (FR-002) instead. "Failure" covers every way the scores fail to arrive — an error, a
  refusal, a rate limit, an unusable response, or the deadline below.
- **FR-010a**: The reranking call MUST carry an explicit deadline, **5 seconds** by default and
  configurable, after which the turn MUST stop waiting and proceed under FR-010. The turn MUST NOT
  be able to stall on this call for longer than that deadline.
- **FR-010b**: Reranking MUST be attempted on every turn that reaches it, independently of whether
  earlier turns failed. No circuit breaker, failure counter, or cooldown may skip the call, so a turn
  during an outage behaves exactly as the first one did and recovery needs nothing reset.
- **FR-011**: A turn answered under FR-010 MUST be recorded as answered **without reranking**, so it
  is never counted or displayed as a reranked answer.
- **FR-012**: FR-010 MUST NOT apply retroactively to the retrieval gate: a question that failed
  FR-005 abstains whether or not the reranker is healthy.
- **FR-013**: A reranker failure MUST NOT call staff. It is a dependency outage, not a corpus gap,
  and the patient received an answer.

**Generation and citations**

- **FR-014**: The generation context MUST contain exactly the surviving chunks — the reranked
  shortlist, or the retrieval survivors under FR-010 — in the order the surviving stage produced,
  and nothing else retrieved for this turn.
- **FR-015**: Citations MUST be derived structurally from those same surviving chunks and MUST NOT be
  self-reported by the generation model.
- **FR-016**: For any answered turn, the set of cited chunks and the set of chunks in the generation
  context MUST be identical.
- **FR-016a**: Citations MUST remain in the data on every path that carries them today — the terminal
  event of the chat stream and the stored message history both panes read. FR-023d is a rendering
  rule, not a change to what is recorded or returned: the staff console and the patient pane read one
  message record, and stripping the patient's copy would make them two shapes free to drift apart.
- **FR-017**: On a mixed-intent turn, the merged reply MUST carry the FAQ half's citations unchanged;
  the composing step MUST NOT add, drop, or re-derive them.

**Abstention**

- **FR-018**: Any abstention MUST produce the existing patient-facing abstention message, with no
  citations, and MUST NOT invoke the generation model.
- **FR-019**: Any abstention MUST call staff with the existing corpus-gap reason and MUST leave the
  existing corpus-gap attention mark on the patient's message, unchanged from spec 007 — including
  that the assistant is not silenced by it.
- **FR-020**: The four abstentions MUST be distinguishable in the turn's record and logs. What makes
  them indistinguishable to the patient is FR-018 and FR-019, which apply to *any* abstention; this
  requirement adds only the record.

**The verdict**

- **FR-021**: Each turn with an FAQ half MUST carry a **typed verdict** with exactly these six
  values: *answered*, *answered without reranking*, *abstained: empty corpus*, *abstained: the search
  matched nothing*, *abstained: nothing cleared the similarity floor*, *abstained: nothing cleared
  the rerank floor*.
- **FR-021b**: *Abstained: the search matched nothing* MUST be distinct from *abstained: nothing
  cleared the similarity floor*. A session that publishes live revisions whose chunks the search does
  not return has an index behind its rows; no floor rejected anything, and lowering the floor cannot
  fix it.
- **FR-021a**: Assigning one of the four abstention verdicts MUST NOT branch behavior. The verdict
  is a label on the outcome FR-018 and FR-019 already define; no code may read it to decide what the
  patient sees, whether staff are called, or whether generation runs.
- **FR-022**: A turn with no FAQ half MUST carry **no verdict** — absent, not defaulted.
- **FR-023**: The verdict MUST replace the `grounded` boolean everywhere it is currently recorded or
  reported: the stored assistant message, the terminal event of the chat stream, and the message
  history read by the patient pane and the staff console. No surface may keep the boolean.
- **FR-023a**: The staff console MUST mark an assistant message whose verdict is *answered without
  reranking*, with a hover explaining that the answer was produced without the reranking stage. It
  MUST NOT mark the other five verdicts: an answer with citations and an abstention message already
  say what they are, and a marker on every message marks nothing.
- **FR-023b**: The patient pane MUST show nothing about the verdict. How an answer was produced is
  not the patient's to reason about, and the abstention message already says the one thing that
  concerns them.
- **FR-023c**: FR-023a's marker MUST be distinguishable from spec 007's attention marks, which sit on
  *patient* messages and mean a person is needed. This one sits on an *assistant* message and means
  the answer above it is second-best.
- **FR-023d**: **Citations MUST be rendered in the staff console only.** The patient pane MUST NOT
  render a citation list for any message, on any verdict — replacing today's behavior, where both
  panes show the same citation list to whoever is reading. A citation is evidence about how an answer
  was produced: useful to a staff member auditing it, and noise to a patient who asked a question and
  wants the answer rather than the clinic's working notes stacked beneath it.
- **FR-023e**: The staff console MUST render citations for every assistant message that has them,
  unchanged in content and ordering from the surviving chunks (FR-014), so the console is where an
  answer can be audited against what it stood on.
- **FR-023f**: FR-023d MUST NOT be described, in the spec or in the code, as withholding anything
  from the patient. The citation text reaches the patient's browser and is simply not drawn. There is
  no confidentiality boundary here to enforce — the session that reads the patient pane owns the
  corpus and can read and edit every entry of it on the FAQ screen — so any wording implying
  protection would claim a control that does not exist.
- **FR-023g**: The console MUST NOT display similarity scores, rerank scores, gate thresholds, or
  rejected candidates. Scores live in the log events (FR-027 to FR-030) and nowhere else, so there is
  one copy of each number and no second one to fall out of step with it. A citation list also cannot
  show what a gate *rejected*, which is the half a floor is tuned against — so the console is where
  an answer is read, and the log is where a threshold is calibrated.
- **FR-024**: The migration replacing `grounded` with the verdict MUST NOT translate old values into
  new ones. There are no stored messages to translate — the message table is empty — and a mapping
  written for rows that do not exist would be untested code asserting a history nobody can check. The
  migration therefore drops the old column and adds the new one, and MUST document that it discards
  `grounded` rather than converting it.
- **FR-024a**: Because the migration assigns no verdict, it is correct **only** against an empty
  message table: a surviving row would silently acquire a NULL verdict, which means *no FAQ
  specialist ran* — a false statement about a turn that had one. The migration MUST make that
  condition explicit rather than leave it as an assumption a later reader has to reconstruct.
- **FR-025**: The verdict MUST be present in the turn's completion log record alongside the
  citations, so a log reader sees the outcome and the evidence in one place.
- **FR-025a**: Each citation in that record MUST carry **both** its similarity score and its rerank
  score. The rerank score MUST be absent — not zero, not the similarity score — on a turn answered
  without reranking, where no rerank score was ever obtained. The completion record MUST therefore
  stand alone as a per-turn summary, without needing the stage events joined to it.

**Logging**

- **FR-026**: Each stage MUST emit a structured log event, correlated with the rest of the turn by
  the existing correlation id. No new datastore, table, or read API is introduced for this.
- **FR-027**: The retrieval event MUST carry every candidate in the observation pool — identifying
  the entry and chunk — with its similarity score, in score order. A candidate the similarity gate
  **considered** MUST carry its full chunk text.
- **FR-027a**: A candidate the cap excluded MUST be logged, MUST be marked as observed but not
  considered so no reader can mistake it for something the turn weighed, and MUST carry its chunk
  text **truncated to the first 200 characters** — enough to recognize the chunk, not enough to
  multiply the turn's log volume by the size of the pool.
- **FR-027b**: Truncation MUST be visible as truncation, so a reader never mistakes a shortened chunk
  for a short one.
- **FR-028**: The similarity gate event MUST carry its floor, its cap, the observation pool size, the
  candidates it kept, and the candidates it dropped with their scores, distinguishing those dropped
  **by the floor** from those dropped **by the cap** — the first says the bar is too high, the second
  says it is too low, and a log that conflates them cannot tune either.
- **FR-029**: The reranking event MUST carry each candidate's rerank score.
- **FR-030**: The rerank gate event MUST carry its floor and cap, what it kept, and what it dropped,
  with the same floor/cap distinction as FR-028.
- **FR-031**: A reranking failure MUST be logged at error level, naming the stage and the underlying
  failure.
- **FR-032**: The verdict MUST be logged for every FAQ turn, naming the gate for an abstention.
- **FR-033**: Log events MUST carry chunk text subject to the existing logging conventions, and MUST
  NOT introduce any new exemption from the redaction rules spec 002 established.

**Scope boundaries**

- **FR-034**: Chunking MUST be unchanged — same splitter, same size, same overlap, same boundary
  handling, same degenerate-chunk filtering — and no existing corpus content may be re-chunked or
  re-indexed by this feature.
- **FR-035**: The retrieval query MUST remain the turn's trailing patient message as it is composed
  today. Extracting a per-intent sub-query is Phase 1f and is out of scope.

### Key Entities

- **Observation pool**: every chunk the vector search returned for this turn, wider than the cap on
  purpose. It is the turn's evidence about what the cap and floor rejected; it is never the answer's
  evidence.
- **Retrieval candidate**: a chunk in the observation pool, carrying the entry and chunk it came
  from, its text, and its similarity score. Only those surviving the similarity gate are considered.
- **Reranked candidate**: a retrieval survivor after the cross-encoder has scored it against the
  question — the same chunk, now carrying a second, differently-derived score.
- **Surviving chunk**: a candidate that cleared the last gate the turn ran. It is the unit that
  reaches the generation context and becomes a citation; the two are the same set by FR-016.
- **Gate**: a floor plus a cap applied to a scored candidate list, producing survivors and two kinds
  of rejection — below the floor, and beyond the cap.
- **FAQ verdict**: the typed outcome of a turn's FAQ half (FR-021), naming what happened and, for
  an abstention, why — an empty corpus, a search that matched nothing, the similarity floor, or the
  rerank floor. Six values, each one situation, none of them needing the word "or" to describe.
- **Calibration set**: 20 committed question records — the question, whether the default corpus
  answers it, and for the answerable ones the entry that should be cited. It is evidence for the
  shipped thresholds, run by a person, never by CI.
- **Turn record**: the existing stored message plus the existing per-turn completion log line, both
  of which now carry the verdict in place of the groundedness flag.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On 100% of answered turns, every chunk the assistant cites is a chunk that cleared the
  turn's final gate, and no chunk that a gate rejected appears in the answer's context or citations.
- **SC-002**: On 100% of turns where no chunk clears a gate, zero generation-model calls are made.
- **SC-003**: With the reranking dependency failing, 100% of questions that clear the retrieval floor
  still receive an answer with citations — a reranker outage causes no failed turns and no
  abstentions.
- **SC-004**: For every FAQ turn, a reader with only that turn's log lines can state the full
  candidate list and score at each stage, every keep/drop decision and the threshold that caused it,
  and the final verdict — without re-running the turn or reading source code. Every candidate is
  identifiable and recognizable from its logged text, in full for those the gate considered and as a
  200-character preview for those the cap discarded.
- **SC-004a**: A FAQ turn's per-turn log volume stays within roughly today's, rather than scaling
  with the observation pool size.
- **SC-004b**: A turn's verdict, its surviving chunks, and both scores for each of them are readable
  from the single completion record, with no other log line required.
- **SC-005**: Every answered turn carries at most 3 citations; every turn answered without reranking
  carries at most 5.
- **SC-005a**: No citation text appears anywhere in the patient pane, on any message or any verdict,
  while every citation of every assistant message is readable in the staff console for the same
  conversation.
- **SC-006**: All five tunables (two floors, two caps, the observation pool size) can be changed and
  take effect without editing or rebuilding application code.
- **SC-006a**: Changing the observation pool size alone changes no answer and no citation on any
  turn — only what that turn logged. Two runs of the same question at different pool sizes produce
  the same context, the same citations, and the same verdict.
- **SC-007**: Given any turn's stored record, a reader can tell which of the six outcomes it had,
  and for an abstention, whether the corpus was empty, the search matched nothing, the similarity
  floor rejected it, or the rerank floor did.
- **SC-007a**: A staff member reading a conversation in the console can tell, without opening a log,
  which assistant answers in it were produced without reranking — and sees no marker at all on a
  conversation where every answer was reranked.
- **SC-008**: Against the shipped default corpus and the thresholds shipped as defaults, the
  committed calibration set of 20 questions — 10 the corpus answers and 10 it does not — yields at
  least 9 answers that cite the answering entry, and at least 9 abstentions, respectively. A question
  counts as correctly cited when **at least one** of its citations is a chunk of the entry the set
  names as containing the answer.
- **SC-008a**: The calibration set, the thresholds it produced, and the result it measured are
  committed together, so the same measurement can be repeated by a person following a written
  procedure and its outcome compared against the recorded one. The procedure reads the turn's log
  events for its scores — the console shows the answer and its citations, never a number (FR-023g).
- **SC-009**: A single-intent FAQ turn's end-to-end latency grows by no more than the reranking call
  plus the wider vector search; no stage is run twice, and no additional embedding, reranking, or
  generation call is introduced by the observation pool.
- **SC-009a**: No FAQ turn waits on reranking for longer than the configured deadline — with the
  dependency hung, time-to-first-token exceeds the pre-phase baseline by at most that deadline, and
  the turn still produces a cited answer.

## Assumptions

- **The reranker is a hosted cross-encoder service**, called once per turn with the question and the
  retrieval survivors. Which provider and model is a planning decision; the project's existing
  embedding provider offering a reranking model makes it the obvious first candidate, and no new
  vendor relationship is assumed by this spec.
- **The minimum rerank floor's default value is calibrated during implementation**, against the
  default corpus and using SC-008's question set, because a cross-encoder's score scale is a property
  of the chosen model and cannot be named before it is chosen. The floor is a configurable setting
  from the start (FR-007) and its shipped default is recorded with the reasoning that produced it.
  The similarity floor is *not* subject to this: it is fixed at 0.3 by decision (FR-003).
- **Lowering the effective similarity bar from today's 0.5 is deliberate and safe because the
  reranker is behind it.** Today a single chunk at 0.5 admits four weaker ones with it; after this
  change a chunk at 0.3 is admitted only as a *candidate*, and the cross-encoder decides whether it
  survives. The gate gets more permissive and the pipeline gets stricter.
- **A reranker outage is absorbed per turn, not tracked across turns.** No component remembers that
  a previous turn's reranking failed. This is the reason the phase adds no state outside a single
  turn's own pipeline.
- **No retry is assumed for the reranking call.** The fallback is the failure handling (FR-010), and
  a retry in front of it would spend the deadline twice to reach the same answer it already had. If
  the client library retries internally, FR-010a's deadline still bounds the whole call including
  those retries — the deadline is on obtaining the scores, not on one attempt at them.
- **Reranking runs on every FAQ turn that clears the retrieval gate**, including collect-mode turns
  serving a mixed-intent message. There is no fast path that skips it.
- **Sessions are ephemeral demonstration sessions, and the message table is empty**, verified at
  planning time in both the development and test databases. That is what lets FR-024 drop a column
  instead of translating it, and it is stated as a checked fact rather than an assumption because the
  migration's correctness rests on it (FR-024a).
- **Phase 2 owns metrics and calibration.** This phase produces the record they will be computed
  from and ships defaults good enough to demonstrate; it does not ship a metric suite, a tracing
  backend, or an automated eval run. The committed calibration set is the evidence for one shipped
  number, not a harness: it has no runner, no assertions, and no place in any gate, and Phase 2 is
  free to reshape it entirely when it grows the golden dataset around it.
- **Existing escalation behavior is reused wholesale** — the corpus-gap reason, the attention mark,
  the assistant remaining free to keep talking. This phase changes what triggers an abstention, not
  what an abstention does.

## Out of Scope

- Changing the chunking strategy or re-indexing existing corpus content (FR-034).
- Sub-query extraction per intent — Phase 1f (FR-035).
- A persisted, queryable retrieval-trace store; Langfuse tracing; the golden dataset; an automated
  eval runner; CI-gated eval metrics — all Phase 2. The calibration set committed here is data
  without a runner and is not any of those.
- Widening the retrieval stage beyond five candidates; revisited in Phase 2 with measurements.
- Any change to how FAQ entries are created, saved, published, or deleted, and any change to the
  staff console's FAQ screen.
- Any patient-visible change beyond the removal of the citation list (FR-023d) and the existing
  abstention message (FR-023b), and any staff-console change beyond FR-023a's single marker and
  FR-023e's citation rendering.
