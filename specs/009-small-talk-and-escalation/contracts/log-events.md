# Contract: Log Events

No new event *kinds*. This phase widens the fields of events that already exist, so that Phase 2 can
compute its metric from the record alone. Every line below is correlated by the turn correlation id
spec 002 established.

## `intent.classified` (existing) — widened

| Field | Change |
|---|---|
| `intents` | May now contain the four added labels; `call_staff` and `unknown` carry their narrowed meanings |

This event says what the *classifier* returned, and nothing else. What the router then did with it
belongs to the router's own completion record, below.

## `node.completed` for `classify_intent` (existing) — widened

Already carries `intents`, `specialists` and `merge_required`. Gains:

| Field | Change |
|---|---|
| `specialists` | May now be `["small_talk"]`, or `["hand_off"]` for a stopping turn — as it already was for a turn that asked for a person |
| `stopping_cause` | **NEW**, nullable — the cause the routing table selected, before precedence resolves a mark |
| `notice_required` | **NEW**, bool — whether an unserved not-authorized request accompanies a servable one |

The two new fields live here, beside the routing fields that were already here, rather than on
`intent.classified`: they are decisions the *router* made, not things the classifier said, and one
node's decision should be readable from one line. A reader joining them to a turn uses the
correlation id, as they already do for every other event in the turn.

## `escalation.raised` / `escalation.unchanged` (existing) — unchanged shape

Already carry the reason, whether the conversation transitioned, and the existing reason when it did
not. They now carry the four new values in those fields; nothing about the events changes.

## `node.completed` for `small_talk` (existing shape, new node)

Reports `answer_chars` and `answer_text`, as the other specialists do, plus `truncated` — whether
the reply ran into the node's token cap. `faq_verdict`, `citations` and any retrieval fields are
absent, not null-filled: the node retrieved nothing.

## `small_talk.truncated` (**NEW**, warning)

Emitted only when a reply stopped because it ran out of room, carrying `max_tokens` and
`answer_chars`. Warning rather than error, and it calls no one: the tokens have already reached the
patient by the time it is known, so there is nothing left to fail cleanly, and paging a person over
a clipped pleasantry is the queue noise this phase exists to remove. A truncated reply is otherwise
shaped exactly like a short complete one — same absence of an error, same terminal event — so
without this line the record says the turn went fine.

## `node.completed` for `hand_off` (existing, widened)

Gains `cause` — which constant was written. Without it, four stopping causes produce four
indistinguishable node records.

## `turn.completed` (existing) — widened

`answer_source` may now be `small_talk`. For every hand-off it stays `hand_off`, and the cause is
read from the escalation events (FR-049).

A merged turn also carries **`notice_included`**. `merged` means the composing model wrote the
reply and nothing more — a turn pairing one servable intent with a not-authorized notice is composed
just as a two-specialist turn is — so without this field the two are one value, and a count of
mixed-intent merges is wrong by however many notices there were. It is on this line rather than
joined from the router's, because one node's decision should be readable from one record.

It is deliberately *not* an `answer_source` value: a notice can accompany one specialist or two, so
the two facts are orthogonal, and an enum trying to carry both would need a value per combination.

## The Phase 2 metric

*Escalations raised by turns that contained no request* is computed by joining, per correlation id:
`intent.classified.intents` (does it contain `small_talk` alone?) with the presence of an
`escalation.raised` line. `node.completed`'s `stopping_cause` gives the same turn's cause without a
second join, which is what the exception below is read from.

*Mixed-intent turns* are counted from `turn.completed` where `answer_source` is `merged` **and**
`notice_included` is false — the notice turns are composed too, and counting `merged` alone
includes them. The phase's own target is zero, with one deliberate exception —
`distress`, which is an escalation from a turn that may contain no request, and which the join must
therefore exclude by cause rather than by label.

## What is deliberately not logged

- No per-message safety score, confidence, or model rationale. The label is the decision, and a
  logged confidence that nothing reads is a number that invites being acted on.
- No new event for a small-talk turn that called nobody: the absence of an `escalation.raised` line
  beside an `intent.classified` line already says it, and an event per non-event is noise.
