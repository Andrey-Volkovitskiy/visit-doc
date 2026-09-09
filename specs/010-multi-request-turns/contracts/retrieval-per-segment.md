# Contract: Retrieval, once per request

Phase 1e's pipeline is unchanged in every respect but one: how many times it runs. No stage moves, no
floor moves, no cap moves, and nothing is re-indexed.

## 1. One run per FAQ segment

Each FAQ segment gets its **own** (FR-030):

- search, against the same session-scoped live revisions
- observation pool (25 by default)
- similarity gate — floor 0.3, cap 5
- reranking call
- rerank gate — floor 0.58, cap 3
- surviving shortlist
- generation call, over that shortlist alone (FR-033a)

**Nothing is pooled across segments, at any point, before or after either gate** (FR-031). A shared
shortlist under the 3-chunk cap re-creates the defect this phase exists to remove: the stronger
question's chunks crowd the other's out, and the weaker question is answered from evidence that was
never about it.

## 2. Concurrency

The runs are concurrent (FR-032), fanned out **inside** the FAQ node rather than in the graph
(research D1) — one node, one state key, no channel reducer, and the graph's routing untouched.

A *k*-segment turn therefore issues exactly *k* searches, at most *k* reranking calls and at most *k*
generation calls, with *k* ≤ 3 (FR-036, FR-033b, SC-008, SC-008a), and costs roughly one round trip
of wall-clock per stage rather than *k* (SC-009).

## 3. Failure

**A failure in any segment fails the whole turn** (FR-037): the first failing run propagates, its
siblings are cancelled, and `TurnPipelineError` leaves the node exactly as it does today — one
failure escalation, no reply, nothing partial delivered.

| Failure | Effect |
|---|---|
| Embedding or vector search, any segment | The whole turn fails. Not an abstention: a broken dependency and an empty shortlist are different situations with different fixes. |
| Reranking, one segment | That segment only degrades: answered from the ≤5 chunks that cleared the similarity floor, recording `answered_unreranked`. No staff are called — a dependency outage is not a corpus gap (FR-035). |
| Generation, any segment | The whole turn fails, as today. |

The surviving segments' results are never served on their own; that is partial serving, and it is
Phase 1h's.

## 4. Citations

Derived per segment from that segment's survivors, then **deduplicated** for the turn on
`(entry_id, chunk_index)`, first appearance winning, in segment order (FR-034). Applied where the FAQ
half's result is assembled, so the streaming path and the merged path cannot report different
citation sets for the same evidence.

One chunk that answers two questions is cited once (SC-011). The citation set remains identical, by
construction, to the chunks actually placed in the generation prompts — that is what a citation means
in this system, and per-segment retrieval strengthens rather than weakens it.

## 5. The turn's verdict

The wire keeps one verdict and one citation list (FR-040). The collapse rules are in
[data-model.md §5.1](../data-model.md):

1. any abstention → the **first abstaining segment's** verdict in message order (FR-043);
2. else any `answered_unreranked` → `answered_unreranked` (FR-041);
3. else `answered`.

**The FAQ half is all-or-nothing this phase** (FR-042): if any segment abstained, no segment's answer
is delivered, the patient receives the existing abstention message, and one corpus-gap escalation is
raised — however many segments abstained (FR-045). Serving the answerable half is Phase 1h, which
brings the composer constraint that makes it safe.

Every segment's own verdict is logged, so the turn-level value is lossy only in that one field
(FR-044), and nothing per-segment is stored (FR-044a).

## 6. What is unchanged

- The pipeline's stages, thresholds, caps, verdict values and gate semantics.
- The empty-corpus case: a session publishing no live revisions issues no search and runs no gate, and
  records `turn.retrieval_skipped_empty_corpus` — now per segment, for a turn whose corpus is empty
  for every one of them.
- Chunking, indexing, the Qdrant schema, and every stored embedding.
