# Data Model: Small Talk and What Escalation Is For (Phase 1f)

No table, column, index, or constraint changes. This phase adds members to three closed sets and one
field to the graph's per-turn state. Both database columns that receive new values are already
`String(32)` and deliberately not database enums, so the values below need no migration.

## 1. `IntentLabel` (`chat/domain/schemas.py`) — what a message is

| Value | Meaning after this phase | Change |
|---|---|---|
| `faq_question` | A clinic policy/FAQ question | unchanged |
| `booking` | Anything only the live appointment records can answer | unchanged |
| `small_talk` | Asks for nothing that can be acted on — a pleasantry, an acknowledgement, a reaction, or an unintelligible fragment | **NEW** |
| `urgent_condition` | Describes a condition needing immediate attention | **NEW** |
| `distress` | Expresses fear, panic or acute upset, whether or not it asks anything | **NEW** |
| `booking_for_another` | Explicitly asks to book for someone other than this chat's patient | **NEW** |
| `call_staff` | **An explicit request for a human** | **NARROWED** (was "an urgent or staff-handled issue, e.g. a billing problem") |
| `unknown` | **A request the assistant is not authorized to serve** | **REDEFINED** (was "fits none of the above", routed to the FAQ path) |
| `classification_failed` | Assigned by orchestration only, never by the model | unchanged |

**On `booking_for_another` vs `booking_for_another_person`**: the intent label and the escalation
cause deliberately differ by a suffix. They are members of different closed sets — one says what the
message *is*, the other says why a person was called — and giving them one name would make a
`grep` for either return both. The suffix is the reminder that they are not the same value.

**Invariants**
- `classification_failed` stays structurally unreachable from the model's response schema, as today.
- The result is a list; a message may carry several labels. Which one wins is the routing table's
  business (contracts/classification.md), not the classifier's.
- No label may be inferred from another. `distress` is not "small talk with feeling", and
  `booking_for_another` is not "booking plus a name".

## 2. `EscalationReason` (`chat/domain/models.py`) — why a person was called

| Value | Silences? | Mark clears on a staff reply? | Change |
|---|---|---|---|
| `urgent_condition` | **yes** | yes | **NEW** |
| `distress` | **yes** | yes | **NEW** |
| `patient_asked_for_person` | yes | yes | unchanged |
| `booking_for_another_person` | **yes** | yes | **NEW** |
| `not_authorized` | no | yes | **NEW** |
| `corpus_could_not_answer` | no | **no** — permanent | unchanged |
| `assistant_failed` | no | **no** — permanent | unchanged |

Listed in `_PRECEDENCE` order (FR-047): the row order above *is* the precedence, strongest claim on
a person first. Whether a reason silences stays a separate membership (`_SILENCING`), not an
attribute of the value — the existing separation, extended from one member to four.

**Invariants**
- Every `EscalationReason` has an `AttentionMark` of the same name (below). The pair cannot disagree
  because the mark is derived from the reason, as it already is today.
- A conversation carries at most one escalation reason, set by a guarded `UPDATE` that will not
  overwrite an earlier one. Nothing in this phase changes that: a stronger cause arriving later does
  **not** upgrade an already-escalated conversation, because a message arriving while the assistant
  is silent is never classified (FR-025a).
- A turn may record several reasons; exactly one becomes the message's mark, chosen by precedence.
  The discarded ones stay in the log.

## 3. `AttentionMark` (`chat/domain/models.py`) — why one message needs a person

Gains `urgent_condition`, `distress`, `booking_for_another_person`, `not_authorized` — the same four
strings as the reasons. `unanswered` is unchanged and remains the only mark with no escalation
behind it.

`CLEARABLE_MARKS` becomes: `patient_asked_for_person`, `unanswered`, `urgent_condition`, `distress`,
`booking_for_another_person`, `not_authorized`. Permanent: `corpus_could_not_answer`,
`assistant_failed`.

**Storage check**: `messages.attention_mark` and `chats.escalation_reason` are both `String(32)`.
Longest new value: `booking_for_another_person` (26). No migration.

## 4. `AnswerSource` (`chat/domain/schemas.py`) — what produced the reply

| Value | Meaning after this phase | Change |
|---|---|---|
| `faq` / `booking` / `merged` | unchanged | — |
| `small_talk` | The small-talk node's reply | **NEW** |
| `hand_off` | **A fixed notice that a person now has this** — for any of the four stopping causes and for the solo not-authorized notice | **WIDENED** (was "the patient asked for a person") |

Widening rather than adding four values is FR-049: the reply kind is one fact, the cause is another,
and the cause already has a field. A reader wanting to know *why* a hand-off happened reads the
escalation reason, never the answer source.

## 5. Graph state (`_GraphState`, `chat/agent/graph.py`)

| Key | Change |
|---|---|
| `handed_off: bool` | **REPLACED** by `handoff_reason: EscalationReason \| None` — the node needs to know *which* constant to write, and a bool cannot say |
| `notice_required: bool` | **NEW** — set when `unknown` accompanies a servable intent, read by the merge step (FR-022c1) |
| `small_talk_result` | **NEW** — the small-talk node's own result, kept separate like the other specialists' |
| everything else | unchanged |

`small_talk_result` never coexists with another specialist's result: `small_talk` is dropped whenever
another label applies (FR-008), so the node runs alone, always streams, and is never merged.

## 6. What is not touched

- No new table, column, index, constraint, or Alembic revision.
- `messages` and `chats` gain no fields; `Message.faq_verdict` and `citations` stay `None` for every
  reply this phase introduces, which is what "no verdict, no citations" means concretely.
- `turn.py`'s silence gate, `chat_repository`'s writers, the FAQ pipeline, the tool registry, and
  every scheduling call are unchanged.
