# Data Model: One Message, Several Requests (Phase 1g)

Nothing here is persisted. Every type below lives for the duration of one turn, and the phase adds no
column, no table, no migration and no wire field (FR-044a). What follows is therefore the *in-memory*
model: one new value type, two changed ones, and the two collapse rules that turn a list of
per-request outcomes back into the single turn-level values the API already publishes.

## 1. `RequestSegment` — new

One thing the patient asked for.

| Field | Type | Rule |
|---|---|---|
| `intent` | `IntentLabel` | Any member of the existing closed set. Unchanged — this phase adds no label. |
| `text` | `str` | Non-empty after stripping. A **standalone restatement** of this request: understandable and retrievable without the rest of the message (FR-004). |

**Position is not a field.** A segment's position is its index in `IntentClassificationResult.
segments`, which is also the join key every log event uses (FR-060). A stored index and a list index
are two answers to one question, free to disagree; a derived one cannot.

**Invariants**
- `text` carries no claim, constraint, specialty, date, practitioner or symptom absent from the
  message and its history (FR-004b). Not machine-checkable — it is prompt work, measured by SC-005
  against the committed labelled sets.
- A segment is never a non-request part of the message: the segments *are* the requests (FR-005b).
- Two segments never carry the same request (FR-005a).

## 2. `IntentClassificationResult` — changed

| Field | Before | After |
|---|---|---|
| `intents` | `list[IntentLabel]`, `min_length=1`, returned by the model | **Derived property**: `[s.intent for s in segments]` |
| `segments` | — | `list[RequestSegment]`, `min_length=1`, `max_length=3`, returned by the model |

`intents` surviving as a property is what lets `_handoff_reasons`, `_select_specialists`,
`_HANDOFF_REASON_BY_INTENT` and the `intent.classified` event keep reading the value they read today
(FR-001). The labels cannot drift from the segments because they *are* the segments.

**Invariants**
- Never contains `CLASSIFICATION_FAILED` from the model: excluded from the request schema's enum and
  re-checked on the parsed result, exactly as today.
- At most three segments, enforced by `maxItems: 3` in the request schema *and* `max_length=3` on the
  field. A longer list is an invalid result, not a list to trim (FR-006a, FR-008).
- A turn always has at least one segment, including the failure path, which synthesizes
  `[{intent: CLASSIFICATION_FAILED, text: <trailing message>}]` so that no consumer downstream needs
  a second shape (research D4).

## 3. `FaqSegmentAnswer` — new

What one FAQ segment produced. One per FAQ segment, in segment order.

| Field | Type | Notes |
|---|---|---|
| `position` | `int` | The segment's index in the turn — the value its six log events carry. |
| `question` | `str` | The segment text this run retrieved for and answered. Carried so the record can show which question produced which evidence without a second lookup. |
| `answer_text` | `str` | Empty for an abstention: nothing was generated. |
| `verdict` | `FaqVerdict` | This segment's own outcome. The value set is unchanged — no seventh member. |
| `citations` | `list[Citation]` | This segment's survivors, before the turn-level dedupe. |
| `scored_chunks` | `list[ScoredChunk]` | The same chunks with both scores, for the record. |

**Invariant**: `citations` are drawn only from this segment's own surviving chunks (FR-033). Since
each segment's generation call receives only its own shortlist (FR-033a), this holds by construction
rather than by check.

## 4. `FaqResult` — changed

The FAQ half of the turn, as the composer and the completion record see it.

| Field | Before | After |
|---|---|---|
| `answer_text` | `str` | `str \| None` — the half's single text when it has exactly one reply part, `None` when it has several |
| `citations` | `list[Citation]` | Unchanged in type; now the **deduplicated** union across segments |
| `verdict` | `FaqVerdict` | Unchanged in type; now the **summary** of the segments' verdicts |
| `scored_chunks` | `list[ScoredChunk]` | Unchanged in type; deduplicated union, same order as `citations` |
| `segment_answers` | — | `list[FaqSegmentAnswer]`, in segment order |

**Reply parts.** The FAQ half contributes to the turn's reply:

- **one** part when it abstained — the existing constant abstention message, per FR-042's
  all-or-nothing rule;
- **one** part per answered segment otherwise.

**Invariant**: `answer_text is not None` ⟺ the half has exactly one reply part. A joined string
across several answers would be a reply no path wrote, and would then be the thing the completion
record reports as the turn's answer.

**Dedupe rule (FR-034)**: key `(entry_id, chunk_index)`, first appearance wins, order preserved
across segments in segment order. Applied where `FaqResult` is assembled, so the streaming path and
the merged path cannot report different citation sets for the same evidence (research D9).

## 5. Collapse rules

Two pure functions turn the per-segment record into the single values the wire types already carry.
Both are exhaustively testable with no client, no graph and no model.

### 5.1 `summarize_verdict(verdicts) -> FaqVerdict`

Applied in order:

| # | Condition | Result | Requirement |
|---|---|---|---|
| 1 | Any segment abstained | The **first abstaining segment's** verdict, in message order | FR-043 |
| 2 | Else, any segment is `answered_unreranked` | `answered_unreranked` | FR-041 |
| 3 | Else | `answered` | FR-041 |

Rule 1 before rule 2 is not arbitrary: an abstention is a claim about the turn's *outcome*, and a
degraded rerank is a claim about its *evidence*. A turn that abstained has no evidence to describe.

Rule 2 exists because the weaker claim governs — a turn resting partly on chunks no cross-encoder
approved must not be recorded as one resting on chunks it did (the same reason
`ANSWERED_UNRERANKED` is its own value rather than a flag).

**This value is knowingly lossy** when several segments abstained at different gates, and the loss is
confined to this one field: every segment's own verdict is in the log (FR-044). Phase 1h deletes this
function by moving the verdict onto the request.

### 5.2 Reply-part count → whether the composer merges

| Parts | What happens | Requirement |
|---|---|---|
| 1 | The single part is the reply. No composing call — streamed by its specialist when the turn was routed as one part, emitted here when several parts collapsed to one. | FR-051, FR-051a |
| ≥ 2 | The composer merges them into one reply. | FR-050 |

Parts = (FAQ half's parts, per §4) + (1 if the booking specialist ran) + (1 if a not-authorized
notice is owed).

**This is decided when the composer runs, not when the router routes** — the routing-time count can
be 2 (two FAQ segments) while the actual count is 1 (both abstained, collapsing to one abstention).
The routing-time flag keeps only its other job: telling the specialists whether to stream their own
tokens or collect for a composing step (research D7).

## 6. Graph state

| Key | Change |
|---|---|
| `segments` | **New**: `list[RequestSegment]` for the turn, written by the classifier node. |
| `merge_required` | **Renamed to `specialists_collect`**, which is what it still decides: whether the specialists collect instead of streaming their own tokens. It no longer answers "does the composer merge?" (§5.2). |
| `faq_result`, `booking_result`, `small_talk_result`, `handoff_reason`, `notice_required`, `specialists`, `escalation` | Unchanged. |

The disjoint-key rule is preserved: the per-segment fan-out happens *inside* the FAQ node, so one
node still writes one key and no channel reducer is introduced (research D1).

## 7. What is deliberately not modelled

- **No per-segment stored state.** `messages.faq_verdict` keeps one value and one meaning-for-now;
  `ChatDoneEvent` and `MessageOut` keep their shape (FR-044a). Phase 1h is where storage follows the
  verdict onto the request.
- **No new `FaqVerdict` member.** A "mixed" value would add a seventh member to a closed set Phase 1h
  is about to restructure, and would tell a reader nothing about what to fix.
- **No new `AnswerSource` member.** A turn whose several segments went to one specialist is composed,
  so it records `MERGED` — which continues to mean "the composing model wrote this reply", not "two
  different specialists ran".
- **No new escalation cause or attention mark.** The turn still raises at most one escalation, under
  the causes that already exist (FR-045).
