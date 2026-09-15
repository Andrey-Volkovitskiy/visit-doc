# Contract: What a run stores

The artifact FR-006 requires, FR-007 resumes from, FR-044 re-scores, and FR-048a commits. Its shape
is a contract because scoring is defined as a pure function of it: a field left out here is a full
re-run later, at full cost.

## Layout

```text
<artifacts>/<run_id>/
├── run.json
├── report.json      # written by scoring; re-written by every re-score
├── report.md        # the human-readable summary FR-048 requires
└── cases/
    ├── G001.json
    └── …
```

One file per case, rather than one growing aggregate. Resume is then a directory listing (FR-007),
and a case that broke mid-write leaves one unreadable file instead of a truncated run.

## `run.json`

Written when the run starts and updated as cases complete. Carries the conditions FR-047 requires —
the corpus hash, the label digests (FR-044a), the clock, the selection, and the model identifiers
and the pipeline's floors, caps, pool size, `MAX_SEGMENTS` and context turns exactly as the chat
service's `service.configured` event stated them (FR-047c). A number
without them cannot be compared to a later number, which is the whole of why they are here.

It also carries `entry_ids`, each pinned corpus slug mapped to the session's own entry id (FR-012), so a
scorer can compare citations and ranked chunks without the stack. And it carries the `session_id` the run created. The session is not deleted when the run ends
(FR-009): the thread of a case that scored badly is the first thing a person opens.

## `cases/<id>.json`

Written once, whole, after the case's turn has completed. There is no partial record.

| Field | Why it is stored |
|---|---|
| `case_id`, `chat_id`, `patient_id` | the chat is the case's own (FR-002); the patient is what a resumed run's cleanup is addressed by (FR-041b) |
| `attempts` | >1 only where a failure prevented the measurement (FR-007a); the report counts these (FR-007c) |
| `elapsed_seconds` | this case's own driving time, across all attempts — the run total is their sum (FR-047a, FR-047b) |
| `terminal` | `done` / `silent` / `cancelled` / `error` and the event's payload — four different things that must never collapse into "no reply". Absent when no response stream was read to its end: on an `unresolvable_fixture` case, where no turn was posted; on any case whose stream broke the service's contract (`stream_broke_contract`), settled or `outcome_unknown`; and on a `run_error` case whose recorded attempt received no stream (no connection, or a 429 or 5xx status line) |
| `stream_broke_contract` | `true` exactly when the turn's response stream began and then broke the service's contract (a `TurnProtocolError` while reading it); `false` when the stream was read to its terminal event, broke off or timed out, or none was received. It is what tells the two situations `terminal`'s absence covers apart — a stream that broke the contract, and no stream — and never sits beside a `terminal`. The report lists every case with a turn that has it and then settled (FR-041d) |
| `patient_message` | id, content, `attention_mark` — the mark is how an `assistant_failed` turn is recognised. Absent when the service stored no patient message for the turn, and wherever the thread was not read back: on an `unresolvable_fixture` case, where no turn was posted; on an `outcome_unknown` case whose stream broke the service's contract or whose thread could not be read back; and on a `run_error` case whose recorded attempt received no stream |
| `assistant_message` | id, content, `request_outcomes`; absent when no reply was stored, and wherever `patient_message` is absent because the thread was not read back |
| `segments` | the produced segmentation from `intent.classified`, plus `cap_bound` |
| `events` | the turn's parsed log events in order — the ranked candidates live here |
| `reply_turn` | for a case whose fixture carries a `reply`: that turn's `terminal`, `stream_broke_contract`, stored messages and `events`; absent when the reply was not posted (FR-037b) |
| `scheduling_before_reply` | a reply case's appointments read after its first turn, before the reply was posted (FR-037b) |
| `scheduling_after` | the patient's appointments read after the turn. Also stored, when the read succeeds, for an `outcome_unknown` case — for a reader, never scored (FR-007d). Absent for a case with no fixture; on an `unresolvable_fixture` case, where no turn was posted; on an `outcome_unknown` case whose appointments could not be read; and on a `run_error` case none of whose attempts was sent, so there was nothing to read. Every other written fixture case carries it: when a measured turn's appointments cannot be read, the run stops with the case unwritten |
| `cancelled_after` | what the harness cancelled after storing the post-state (FR-041b); never scored |
| `excluded` | a turn-level `ExclusionReason` — one of the six case-scoped reasons or `handed_off_turn` — or absent; request-level reasons are derived at scoring and never stored |
| `unplantable` | why the fixture would not plant, on an `unresolvable_fixture` case only: the situation — `not_on_roster`, `roster_unreadable`, `booking_refused`, `booking_unanswered`, `booking_unreadable` or `no_patient` — a detail for a reader, and, for a refused booking, the scheduler's failure reason and its message. One exclusion reason covers a label problem and a scheduler that was down (FR-018, FR-039); this is what tells the two apart, and the report lists it. Absent on a case recorded before it was stored |

## When a case is not written

A case is written once, whole, or not at all. Besides the stops `contracts/log-access.md` names,
one more leaves the case in flight unwritten (decided 2026-09-14): a **booking case whose turn
completed but whose appointments could not then be read**. The harness releases its patient and
stops the run. A record without that read could not be scored for the booking metrics and would sit
among the others as though it had been; and a scheduler that cannot answer one read cannot answer
the next case's either. Resume drives the case again from the start, which is safe for the same
reason as a restart: its turn has ended, and the resume sweep cancels whatever it wrote (spec
FR-041b).

## Two rules about reading it back

**`request_outcomes` is parsed through `RequestOutcome` itself**, not through a local copy of the
shape. The model enforces that an answered request carries text and citations and an abstained one
carries neither, so a record that violates its own invariant fails at the scorer's front door rather
than propagating into a metric. The same applies to `FaqVerdict` and `IntentLabel`: a value the
enum does not know is an error, not an `unknown` bucket.

**Nothing in the record is a score.** It holds what happened, never what it was worth. Every
judgement — aligned or not, hit or miss, served or unserved — is computed at scoring time, which is
what lets a metric definition change and be re-applied to every run already on disk, including the
one committed under FR-048a.
