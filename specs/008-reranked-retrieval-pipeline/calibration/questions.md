# Calibration Set (SC-008 / SC-008a)

Twenty questions against the **shipped default corpus** (`chat/rag/default_corpus.py`, 9 entries).
Data, not a test: no runner, no CI, no assertions. It exists so the shipped thresholds have evidence
behind them, and so the next person to move a floor can compare their number to the one that shipped.

**Bar (SC-008)**: with the shipped defaults, ≥9 of the 10 answerable questions are answered citing
the entry named below, and ≥9 of the 10 unanswerable ones abstain.

**Procedure**: create a session (its corpus is seeded automatically), ask each question in the
patient pane, and read the turn's `faq.verdict` and `turn.completed` events for the verdict and the
citations. Scores come from the logs — the console shows none (FR-023g).

## Answerable (expect an answer citing the named entry)

| # | Question | Expected entry | Why it is here |
|---|---|---|---|
| A1 | What should I bring to my first appointment? | 1 (what to bring) | corpus wording — the easy case, and the regression canary |
| A2 | What are your clinic hours and locations? | 7 (hours/location) | corpus wording |
| A3 | Can I see a specialist if my GP hasn't sent anything over? | 0 (referral) | paraphrase, no term overlap |
| A4 | Is it possible to have my appointment over video call? | 2 (telehealth) | paraphrase, no term overlap |
| A5 | How much is a dentist visit if I'm self-paying? | 5 (out-of-pocket rates) | vocabulary shared with the insurance entries |
| A6 | Do you take Aetna? | 3 (accepted plans) | vocabulary shared with the payment entries |
| A7 | Can I pay with my HSA card? | 6 (payment methods) | vocabulary shared with the cost entry |
| A8 | What happens if you don't take my insurance? | 4 (out-of-network) | vocabulary shared with the plans entry |
| A9 | How long before my slot should I turn up as a returning patient? | 8 (arrival time) | multi-clause question |
| A10 | Where do I park, and how far is it from the clinic door? | 7 (hours/location) | multi-clause question |

## Unanswerable (expect an abstention)

| # | Question | Why it should abstain |
|---|---|---|
| U1 | Do you have wheelchair access at the entrance? | near miss — plausible clinic policy, absent from the corpus |
| U2 | What is your cancellation and no-show policy? | near miss |
| U3 | Do you offer interpreter services for non-English speakers? | near miss |
| U4 | Do you accept Blue Cross for dental implants specifically? | heavy overlap with entry 3, which does not answer it |
| U5 | What are your Sunday opening hours? | heavy overlap with entry 7, which does not answer it |
| U6 | How much does an MRI scan cost out-of-pocket? | heavy overlap with entry 5, which does not answer it |
| U7 | I have chest pain radiating to my arm, what should I do? | clinical — must not be answered from a policy corpus |
| U8 | Is 200mg of ibuprofen safe with my blood pressure medication? | clinical |
| U9 | What's the best route to drive from the airport? | off-topic |
| U10 | Who won the football last night? | off-topic |

U4–U6 are the point of the whole phase: they clear the similarity floor and must be rejected by the
**rerank** floor. If none of them reached reranking, the calibration would have proved something
about 0.3 rather than about the reranker.

## Recorded result

| | |
|---|---|
| Date run | 2026-09-06 |
| Corpus | `DEFAULT_FAQ_ENTRIES`, 9 entries, 9 chunks (each entry is one chunk) |
| Embeddings | `voyage-4-lite` (1024-dim) |
| `RERANK_MODEL` | `rerank-3`, `relevance_score` confirmed normalized to [0, 1] |
| `SIMILARITY_FLOOR` / `SIMILARITY_CAP` | 0.3 / 5 (fixed by decision, not swept) |
| **`RERANK_FLOOR`** | **0.58** (replaces the provisional 0.4) |
| Answerable answered citing the right entry | **10 / 10** |
| Unanswerable abstained | **9 / 10** |

### The sweep

Similarity floor held at 0.3 throughout; only the rerank floor moved. SC-008 asks for **≥9 and ≥9**,
so the "passes" column is that bar, not a rank.

| rerank floor | answerable correct | unanswerable abstained | SC-008 |
|---|---|---|---|
| 0.30 | 10/10 | 5/10 | fails |
| 0.35 | 10/10 | 5/10 | fails |
| **0.40** *(the provisional default)* | 10/10 | 6/10 | **fails** |
| 0.45 | 10/10 | 7/10 | fails |
| 0.50 | 10/10 | 8/10 | fails |
| 0.55 | 10/10 | 9/10 | passes |
| **0.58 — shipped** | **10/10** | **9/10** | **passes** |
| 0.60 | 10/10 | 9/10 | passes |
| 0.65 | 9/10 | 9/10 | passes |
| 0.68 | 9/10 | 9/10 | passes |

### What the numbers do not settle

Read this before moving the floor. Three facts constrain what any value here can claim.

**1. The two classes overlap, so no floor separates them.**

```
weakest CORRECT answer      0.6367   A8  "What happens if you don't take my insurance?"
worst UNANSWERABLE          0.6953   U4  "Do you accept Blue Cross for dental implants?"
                                         ^ higher than the weakest correct answer
```

There is no threshold that answers all ten and refuses all ten. Every value is a choice of which
mistake to make, not a solution to be found. U4 is admitted at **every** floor that keeps A8, and
rejecting it costs A8 and A4 both — which is why the abstained column never reaches 10/10 while the
answered column holds.

**2. A wide band scores identically, so the digit is not derived from the data.**

Every floor from **0.520 to 0.636** yields 10/10 and 9/10 — a band 0.116 wide, bounded below by U5
("Sunday opening hours", 0.5195) and above by A8 (0.6367). Nothing in this set can prefer one point
in it to another. **0.58 is that band's midpoint**, chosen because it is the only principled way to
pick inside a plateau: it maximizes the distance to the nearest mistake on *both* sides at once
(0.06 below to U5, 0.057 above to A8), so a corpus edit that nudges either score has the most room
before the result changes.

*A first pass shipped 0.55 and defended it by its margin to A8 alone. That argument was one-sided —
0.55 leaves only 0.030 to U5 — and applied to both sides it selects the midpoint. The same pass also
recorded 0.65 as if it failed; it does not, since 9/10 meets SC-008. Both are corrected above.*

**3. The evidence is weaker than a table of numbers looks.**

- **The questions and their labels were written by the same author who then scored against them.**
  This is not an independent test set. A paraphrase chosen because it felt fair may be one this
  particular reranker happens to handle well.
- **n = 20, one corpus, one embedding model.** The plateau is far wider than any difference this
  sample can resolve. "0.58 beats 0.55" is not a claim these numbers support; "0.58 beats 0.40" is.
- **The sweep ran through a standalone script, not the shipped pipeline.** It used the real chunker,
  the real embeddings and the real reranker over the real corpus, but recomputed cosine similarity
  in Python rather than querying Qdrant. Same metric, and with 9 chunks a top-25 fetch returns
  everything, so the ranking is equivalent — but the retrieval step it exercised was a
  reimplementation. A future run should drive `answer_faq` and read the scores out of the logs,
  which is what those logs exist for.

### What the numbers do say

At the 0.3 similarity floor, **6 of the 10 unanswerable questions reach reranking**, and the rerank
floor rejects **5 of those 6**. That is this phase's thesis in one line: the bi-encoder is too blunt
to be the only gate, and the cross-encoder is doing the precision work the design assigned it. The
two unanswerable questions the similarity floor alone rejected sit at 0.291 and 0.297, just under
0.3 — worth knowing before anyone lowers it.
