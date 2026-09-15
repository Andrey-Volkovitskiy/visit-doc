# Phase 1 — Data Model

Everything this phase stores or extends. Three groups: the **label** (2a's data, extended), the
**record** (what a run leaves behind), and the **report** (what scoring computes from it). Only the
first is committed data with a schema of its own; the other two are the harness's own models.

---

## 1. The label — `evals/golden/`

### `Case` *(existing, one new optional field)*

| Field | Type | Notes |
|---|---|---|
| `id`, `family`, `message`, `requests`, `source`, `note`, `history` | — | unchanged from 2a |
| `scheduling` | object, optional | **New.** Present exactly on the 18 cases carrying a booking request, absent otherwise. |

### `SchedulingFixture` *(new)*

| Field | Type | Notes |
|---|---|---|
| `given` | array of `AppointmentRef` | The appointments to plant before the turn. May be empty. |
| `expect` | array of `AppointmentRef` | The **complete** set of the patient's appointments after the case's last turn. A case expected to change nothing restates `given`. |
| `reply` | string, optional | The patient's scripted answer, posted as a second full turn in the same chat (FR-037b). Present on a case whose write the booking loop has to confirm first; with it, the calendar after the first turn must still be `given`. |

### `AppointmentRef` *(new)*

| Field | Type | Required | Notes |
|---|---|---|---|
| `practitioner` | string | yes | A pool name the scheduler seeds — `William Osler` or `Andreas Vesalius` for a fresh session. Resolved against the run's own roster; an unresolvable name excludes the case (FR-039). |
| `day` | string, `^[+-][0-9]+d$` | yes | Days from the run clock's date. `+3d` on a Monday clock is Thursday. |
| `time` | string, `^([01][0-9]|2[0-3]):[0-5][0-9]$` | no | Omitted means *any time that day* — which is how "book one with William Osler on Friday" and "book Wednesday with William Osler instead" are labelled (FR-037a). |
| `status` | `standing` \| `cancelled` | in `expect` only | A precondition is always a standing booking, so `given` carries no status; an expectation must say which, since a cancelled appointment is still a row. |

**Why times are offsets and not instants**: the messages say "tomorrow", "Thursday", "Monday". An
absolute instant would pin the set to one calendar week and rot; an offset from the run clock says
the same thing and keeps saying it.

**Validation rules** (enforced when the set is loaded, before any model call):

1. `scheduling` is present if and only if the case carries at least one `booking` request.
2. Every `given` entry must be plantable against the run clock: inside its practitioner's weekly
   working range, on the 60-minute grid, strictly after the clock, and within the 90-day horizon.
   The scheduler enforces all four; the loader checks them first so a bad label fails as a label.
3. No two `given` entries may overlap, whichever practitioner each names — every one is planted for
   the case's one patient, so the scheduler's patient exclusion constraint would refuse the second
   (as its practitioner constraint would for one practitioner), and a fixture that cannot plant is
   a broken label.

### `corpus.json` *(existing, two changes)*

| Field | Change |
|---|---|
| `algorithm` | **New.** Names the construction: `sha256` over the entries' texts in index order, each UTF-8 encoded and terminated by `\n`, and nothing else in the digest. |
| `sha256` | **Re-taken** under that construction. The previous value could not be reproduced (research R3) and is recorded in `PROVENANCE.md` as what was written down, with a note that it is unverifiable. |

---

## 2. The record — what a run stores

### `Run`

| Field | Notes |
|---|---|
| `run_id` | ULID; also the artifact directory's name |
| `started_at`, `finished_at` | host wall clock, for the reader's orientation. Never used in a metric, and never reported as the run's duration — see `drive_seconds` |
| `drive_seconds` | the sum of the cases' own elapsed times (FR-047a). Resume-proof, where the span between the two timestamps above is not |
| `clock` | the pinned `local_now` sent as every turn's (FR-004). Default `2026-03-02T08:00:00`, a Monday at 08:00, so the whole working week lies ahead of it (research R7) |
| `session_id` | the session all cases share; not deleted when the run ends (FR-009) |
| `corpus` | the hash computed from the live corpus, the pinned hash, and whether they matched |
| `selection` | the case ids the run covers, and how they were chosen |
| `entry_ids` | each pinned corpus slug mapped to the run session's own FAQ entry id, taken when the pin was verified (FR-012) — what lets retrieval be scored with no stack |
| `conditions` | model identifiers for classification, generation, embedding and reranking; the similarity floor, the rerank floor, both caps, the pool size, `MAX_SEGMENTS` and context turns (FR-047) — the fields of the chat service's `service.configured` startup event, never the harness's own environment (FR-047c) |
| `labels` | a sha256 per selected case over its scored fields — `id`, `message`, `history`, each request's `intent`/`answerable`/`cites`/`tools`, and `scheduling`; never `gist`, `note`, `source` or `family` — which scoring checks before computing anything (FR-044a) |
| `cases` | ids of the cases recorded so far — what resume reads (FR-007) |

### `CaseRun` — one file per case

| Field | Notes |
|---|---|
| `case_id` | |
| `chat_id` | the chat this case alone ran in (FR-002) |
| `patient_id` | the scheduler patient that chat was provisioned with — what a resumed run's cleanup is addressed by (FR-041b) |
| `attempts` | how many times the case was driven; >1 only for a failure that prevented measurement (FR-007a) |
| `terminal` | `done` \| `silent` \| `cancelled` \| `error`, with the event's own payload. Absent when no response stream was read to its end: an `unresolvable_fixture` case (no turn was posted), any case whose stream broke the service's contract (`stream_broke_contract`), settled or `outcome_unknown`, and a `run_error` case whose recorded attempt received no stream — no connection, or a 429 or 5xx status line |
| `stream_broke_contract` | `true` exactly when the turn's response stream began and then broke the service's contract; `false` when it was read to its terminal event, broke off or timed out, or no stream was received. Never `true` beside a `terminal` (FR-041d) |
| `patient_message` | id, content, `attention_mark`. Absent when the service stored no patient message for the turn, and when the thread was not read back: an `unresolvable_fixture` case (no turn was posted), an `outcome_unknown` case whose thread could not be read back (a stream that broke the service's contract is read back like one that broke off), and a `run_error` case whose recorded attempt received no stream |
| `assistant_message` | id, content, `request_outcomes` — parsed through `RequestOutcome` itself, so the record's invariants are re-checked on the way in (research R5). Absent when no reply was stored, and wherever `patient_message` is absent because the thread was not read back. |
| `segments` | the produced segmentation, from `intent.classified` — `{position, intent, text}` per segment, plus `cap_bound` (FR-021a) |
| `events` | the turn's parsed log events, in order |
| `reply_turn` | for a case whose fixture carries a `reply`: that turn's `terminal`, `stream_broke_contract`, `patient_message`, `assistant_message` and `events`, on the same rules as the first turn's own fields; absent when the reply was not posted (FR-037b) |
| `scheduling_before_reply` | the patient's appointments read after the first turn of a reply case, before the reply was posted; never read for any other case (FR-037b) |
| `scheduling_after` | the patient's appointments read after the turn; also stored, when readable, for an `outcome_unknown` case, and never scored (FR-007d). Absent for a case with no fixture, an `unresolvable_fixture` case (no turn was posted), an `outcome_unknown` case whose appointments could not be read, and a `run_error` case none of whose attempts was sent. Every other written fixture case carries it — when a measured turn's appointments cannot be read, the run stops with the case unwritten |
| `cancelled_after` | the appointments the harness cancelled once the post-state was stored, possibly empty (FR-041b). Never scored; it is why the scheduler's own state after a run shows nothing standing, and a non-empty one on a case with no fixture is a turn that booked when it was not asked to |
| `elapsed_seconds` | how long this case took to drive, across all its attempts (FR-047b) |
| `excluded` | a turn-level `ExclusionReason` — one of the six case-scoped reasons or `handed_off_turn` — or absent. The request-level reasons are never stored: scoring derives them from the events |
| `unplantable` | on an `unresolvable_fixture` case only, why the fixture would not plant: `situation` (`not_on_roster` \| `roster_unreadable` \| `booking_refused` \| `booking_unanswered` \| `booking_unreadable` \| `no_patient`), `detail`, and — for `booking_refused` alone — `failure_reason` and the scheduler's `scheduler_message` (FR-039, FR-018). Absent on a case recorded before it was stored; the report lists every `unresolvable_fixture` case with it |

**State**: a `CaseRun` is written once, whole, after its turn has completed. There is no partial
record — a case that broke mid-drive is re-driven (up to the attempt bound) or written as excluded.

### `ExclusionReason` — a closed set (FR-018)

One per situation, never a single "not scored", each with its scope (FR-018a):

| Scope | Reasons |
|---|---|
| the case, from every metric | `run_error`, `silenced_turn`, `cancelled_turn`, `missing_log_slice`, `unresolvable_fixture`, `outcome_unknown` |
| the case, from retrieval and serving only | `handed_off_turn` — still scored for classification |
| one request, from the rerank stage | `reranker_unavailable`, `not_reached_reranker` (no cited chunk survived the similarity gate, FR-029a) |
| one request, from both retrieval stages | `no_search` (a turn whose corpus was empty), `not_routed_to_faq` (a labelled FAQ request produced under another intent) |

---

## 3. Scoring — derived, never stored on the record

### `Alignment` — per case

| Field | Notes |
|---|---|
| `state` | `aligned` (produced count equals labelled count) \| `unaligned` (counts differ) \| `excluded` |
| `pairs` | for an aligned case, labelled request ↔ produced request by position (FR-015) |

**Conservation rule** (FR-017, SC-002): summing each state's labelled requests over the whole run
equals the run's labelled request total — 190 for a full run. This is an assertion the scorer makes
about itself, not a number a reader has to check.

### `Metric`

| Field | Notes |
|---|---|
| `name` | |
| `numerator`, `denominator` | both published always (FR-045) |
| `excluded` | counts by `ExclusionReason` |
| `value` | `numerator / denominator`, or **`not_measured`** when the denominator is 0 — never 0.0, never 1.0 |
| `detail` | the case ids and questions behind a non-perfect value, for the metrics FR-034 and FR-041a require it of |

### `Report`

The run's `conditions` and `selection` verbatim, the alignment totals, every `Metric`, the verdict
distribution across all six `FaqVerdict` values (FR-036), the `cap_bound` count (FR-024), the
count of cases that needed more than one attempt (FR-007c), the cases whose turn was marked
`assistant_failed` and still replied (FR-007e), the cases whose stream broke the service's contract
and then settled, each with which turn (FR-041d), the drive and score durations (FR-047a)
— `score_seconds` lives here and never on `Run`, so scoring leaves the stored run untouched —
and the list of metrics **not** computed (FR-046).
