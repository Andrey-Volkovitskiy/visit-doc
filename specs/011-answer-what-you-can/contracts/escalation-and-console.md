# Contract: The escalation, and what staff see

Covers FR-030–FR-034a, FR-042, FR-043.

---

## 1. The escalation itself — unchanged, with one trigger reworded

| Property | Status |
|---|---|
| Cause (`corpus_could_not_answer`) | unchanged |
| Precedence, mark, `CLEARABLE_MARKS` | unchanged |
| Does **not** silence the conversation | unchanged |
| One escalation per turn, however many requests failed | unchanged (FR-030) |
| Stored payload | **none added** (FR-031a) |
| Trigger | was "the half's summary verdict is an abstention"; becomes "**any** request abstained" (research #8) |

The trigger changes only because the value it read is gone. `EscalationRequests` de-duplicates by
precedence, so recording once per abstained request would produce the same conversation state and a
noisier log — FR-030 settles it at once.

**Nothing is escalated for an answered request**, including one answered without reranking (FR-032).
A degraded answer is an answer; a dependency outage is not a corpus gap.

---

## 2. The unserved requests are derived, never stored twice

```
unserved(assistant_message) =
    [o.question for o in assistant_message.request_outcomes or []
                if not o.verdict.answered]      # in position order
```

That is the whole of FR-031's "carries every unanswered request, verbatim". Writing the same text
beside the escalation as well would be two rows holding one fact, updated by two writes — and the
first time they disagree nothing says which is right.

**Verbatim means the classifier's restatement**, which is what was actually retrieved for. It is
what staff act on, and it is deliberately *not* what the patient-facing reply quotes (FR-012b,
`composition.md` §4).

---

## 3. What the staff console renders

For an assistant message carrying outcomes, one block per outcome, in position order:

| Outcome | Rendered |
|---|---|
| answered | the question, its citations |
| answered without reranking | the question, its citations, **the degraded marker on that block** |
| abstained | the question, and a line saying it was not answered and has been forwarded to staff |

The marked patient message keeps its attention mark exactly as today; the reply that carries the
outcomes is the next message in the thread, which is where a staff member reads what failed.

**Removed**: the message-level `data-faq-verdict` attribute and the message-level degraded marker.
Both were message-level because the verdict was; a marker on the message would now be a claim about
requests it does not describe. Tests reading them re-point at the per-outcome blocks in the same
change (FR-050b's frontend half).

**Unchanged**: the patient pane draws none of this (FR-042). The conversation listing, the
attention counter, the assistant switch, the pause countdown and the practitioner and FAQ screens
are untouched — no new screen, no new control.

---

## 4. An escalation with no reply behind it

A turn superseded by a newer message, or one that failed outright, can call staff without storing an
assistant message. There are then no outcomes to derive from, and the console shows the mark with
**no unanswered request listed** (FR-034a) — not an empty one, and not a guess.

This is exactly today's situation for every escalation, so it is a case this phase does not improve
rather than one it breaks. SC-012 excludes such turns from its denominator for that reason.

---

## 5. Test obligations

| # | Assertion |
|---|---|
| E1 | A turn with one answered and one abstained request raises exactly one escalation, cause `corpus_could_not_answer`, and does not silence the conversation. |
| E2 | A turn with three abstained requests raises exactly one escalation. |
| E3 | A turn where every request was answered raises none — including when one was `answered_unreranked`. |
| E4 | The derived unserved list is the abstained outcomes' questions, in position order, and is empty for a fully answered turn. |
| E5 | The console renders one block per outcome, with the degraded marker only on the degraded block. |
| E6 | A turn that stored no reply shows its mark and no unanswered request. |
| E7 | The patient pane renders no citation, no verdict and no question label, for the same payload. |
