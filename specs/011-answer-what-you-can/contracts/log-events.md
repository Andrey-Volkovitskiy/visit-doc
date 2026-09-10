# Contract: Log events, after the summary verdict is gone

Updates the published field contract of `specs/010-multi-request-turns/contracts/log-events.md` in
the same change that changes the fields (FR-053). Covers FR-050–FR-052.

---

## 1. Unchanged

Phase 1e's six per-request events — `faq.retrieval_completed`, `faq.similarity_gate`,
`faq.reranking_completed`, `faq.reranking_unavailable`, `faq.rerank_gate`, `faq.verdict` — are
**unchanged**, including the `segment` field 1g added and the rule that the request's text is
carried once, on the classification event (FR-051). This phase changes what is done with a request's
outcome, not how it is produced or logged.

`intent.classified`, the escalation events, `faq.truncated`, `compose.truncated` and
`booking.truncated` are unchanged.

---

## 1a. `node.completed` — two fields whose meaning follows the requests

The `answer_faq` node's span keeps both field names and changes what each counts, because the half
no longer has one outcome to report:

| Field | Change |
|---|---|
| `abstained` | **meaning changed**: true when **any** request abstained, not when the half abstained as a whole. A partially-served turn is `abstained=true` *and* carries answers; read `segment_answers`/`turn.completed`'s `request_outcomes` for which requests those were. |
| `citation_count` | **meaning changed**: the sum over requests of the chunks each answer stood on. A chunk that supported two requests counts twice — two provenances, not a duplicate — so this is no longer the number of distinct chunks in the turn. |

`compose_answer`'s span carries `citation_count` with the same new meaning.

---

## 2. `turn.completed` — three changes

| Field | Change |
|---|---|
| `faq_verdict` | **removed.** There is no turn-level verdict (FR-002). |
| `citations` | **removed** as a turn-level list; the scored chunks move under each request's outcome. |
| `answer_source` | **removed** from this event only (research #9). Once `outcome` names the shape, the two carry one fact. The wire keeps it. |
| `outcome` | **meaning changed**: names the turn's shape — `faq`, `booking`, `small_talk`, `handed_off`, `merged` — and is never a verdict value, including on a single-request turn (FR-050a). |
| `request_outcomes` | **new.** One entry per FAQ request: `{position, verdict, citations}`, where `citations` carry both scores as `turn.completed`'s list did. Absent when no FAQ half ran. |
| `segment_count`, `segment_verdicts` | `segment_verdicts` is **superseded** by `request_outcomes`, which carries it plus the evidence. `segment_count` stays. |
| `abstention_message` | **narrowed**: set only when **every** FAQ request abstained (FR-052) — the case where the reply really is the abstention. Setting it on a partially-served turn would file an answered reply as an abstention. |
| `answer_text`, `booking_outcome`, `notice_included`, `message_ids_unified`, `duration_ms` | unchanged |

The request's **text** is still not repeated here: it is on `intent.classified`, joined by
`position` (010 FR-060/FR-061). The request's **answer** is not in the log either — it is on the
stored message (FR-040), which is where SC-004a reads it from.

---

## 3. Queries that must re-point in the same change

FR-050b is a requirement, not advice: the record and the questions asked of it must not disagree for
a release.

| Question | Was | Becomes |
|---|---|---|
| How many turns abstained? | `outcome` starts with `abstained_` | any `request_outcomes[*].verdict` is an abstention |
| How many answers were degraded? | `faq_verdict == answered_unreranked` | any outcome's verdict is `answered_unreranked` |
| Which gate stopped this turn? | `faq_verdict` | each outcome's own verdict — a turn may now have two different ones |
| Which specialist replied? | `answer_source` | `outcome` |
| How many requests did a turn serve? | not answerable | count of answered outcomes vs `segment_count` |

The last row is the phase's new question, and the one Phase 2's "share of answerable requests left
unserved" is computed from.

---

## 4. Test obligations

| # | Assertion |
|---|---|
| L1 | `turn.completed` carries no `faq_verdict`, no turn-level `citations`, and no `answer_source`. |
| L2 | `outcome` is `faq` for a single-request FAQ turn that abstained — never the verdict string. |
| L3 | A partially-served turn carries `request_outcomes` with two different verdicts and **no** `abstention_message`. |
| L4 | An all-abstained turn carries `abstention_message` equal to the constant the patient was shown. |
| L5 | A booking-only turn carries no `request_outcomes` key at all. |
| L6 | Each entry's `citations` carry both scores, with `rerank_score` absent (not zero) on a degraded request. |
