# Contract: The Reranking Port

`chat/rag/reranking.py`. The seam between the pipeline and a cross-encoder. Shaped to match
`rag/embeddings.py`'s `embed_texts()` — a plain async function taking domain values and a client,
never a provider wire type crossing into orchestration (`.claude/CLAUDE.md`, Dependency Inversion).

```
async def rerank_chunks(
    client: AsyncClient,
    query: str,
    chunks: list[ScoredChunk],
    *,
    model: str,
    top_k: int,
    timeout_seconds: float,
) -> list[ScoredChunk] | None
```

## Returns

- `list[ScoredChunk]` — the same chunks, each with `rerank_score` filled in, in the provider's
  relevance order. **This is not the gate**: the floor is applied by `apply_rerank_gate`, so an
  all-below-floor response still returns a populated list here and abstains one step later. Keeping
  scoring and thresholding apart is what makes row 3 and row 4 of the decision table distinguishable.
- `None` — **and only** for "no scores were obtained". This is the whole of FR-010's failure
  taxonomy: transport error, auth refusal, rate limit, malformed or short response, or the deadline.

`None` and `[]` must never both be reachable from this function. `[]` is reserved for the gate's
"scored and rejected"; conflating them collapses the fallback into an abstention.

## Behavior

| Requirement | Contract |
|---|---|
| FR-010a | The whole call is wrapped in `asyncio.wait_for(..., timeout_seconds)`. The bound covers any provider-internal retries, not one attempt. |
| FR-010 | Every failure path returns `None`. This function raises nothing to its caller — a reranker failure is never a `TurnPipelineError`, unlike embedding and search. |
| FR-031 | Every `None` return is preceded by an error-level log event naming the stage and the underlying failure. |
| FR-010b | No module-level counter, latch, cooldown, or client-state mutation. Two calls in a row are independent; the second cannot observe that the first failed. |
| Assumptions | The client is constructed with retries disabled, so the deadline is not silently multiplied. |
| FR-002b | Called at most once per turn, and only with the similarity gate's survivors. |

## Mapping the provider response

The provider returns results referencing input positions, already sorted and truncated to `top_k`. A
result whose index does not correspond to an input chunk, or a response short enough that a survivor
went unscored, is an **unusable response** → `None`. Partial scoring is not silently accepted: a
chunk with no score would otherwise have to be treated as either zero or as passing, and both are
inventions.

## What the tests must pin

1. Happy path — scores land on the right chunks (verified by identity, not by list position).
2. Timeout — a slow client yields `None` inside the deadline, plus an error event.
3. Each failure class of FR-010 → `None`, never an exception escaping.
4. A response missing a chunk → `None` (not a partial list).
5. No retry — a failing client is called exactly once.
6. No cross-turn state — a failing call followed by a succeeding one succeeds.
7. `conftest.py`'s paid-API guard blocks `voyageai.client_async.AsyncClient.rerank`, proven the way
   `test_paid_api_guard.py` already proves it for `embed` and `messages.stream`.
