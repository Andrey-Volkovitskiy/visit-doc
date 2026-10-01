# 018 — Retrieval under a realistic corpus: findings

What was measured between extending the starter corpus and the first change to the retriever,
2026-10-01, on branch `018-hybrid-retrieval`. ROADMAP Phase 4b is the plan this follows; this file
is what the measurements said, so a later reader can tell a decision from a guess.

Every number below comes from a stored run under `.run/evals/` (the baseline is committed under
`evals/baselines/`) or from an offline replay or probe described where it is used. No run here was
measured against a noise band - none exists for this build yet - so a movement of one or two cases
is a sample, not evidence. The three conclusions this file draws rest on movements that are
structural (a chunk that never reached a stage, and then did), not on rates.

## 1. Why the corpus had to change first

The v2 baseline (`evals/baselines/01M321DWRXSVSY7GW9RY3CR9YW`, 9 entries of 136-364 characters,
one chunk each) read similarity hit@3 1.000, MRR 0.988, rerank hit@1 43/43, no answerable request
unserved and no wrong abstention. The 25-wide search pool returned the whole corpus on every query.
Nothing a retriever change could do would have moved a number, so the corpus was extended before
the retriever was touched.

## 2. What was added

- **Ten clinic documents beside the nine entries**, extending rather than replacing them so every
  existing label kept its meaning: an insurance plan guide, a dental care guide, test preparation,
  vaccinations, running late, reminders and messages, check-in and registration, privacy, GP
  services, and a self-pay price list by procedure code. 19 entries, 36 chunks, 1.0-2.9k
  characters a document. Written against three rules: no contradiction of the nine; no
  practitioner, roster specialty or working hours, which are live records data; no answer to an
  existing gap. Smaller than the roadmap's "tens of documents, several pages each".
- **Golden-set families aimed at retrieval** (`evals/golden/PROVENANCE.md` has the per-case
  record): `s` exact terms (12), `t` paraphrase (9), `u` an answer deep in a long document (7),
  `v` a near-miss distractor (6), `w` a lexical discriminator (8), and six new gaps in `k`. Six
  existing insurance labels gained the plan guide as a second citation. 146 cases, 168 requests.

## 3. Finding: dense ranking was not the weak link

Across every targeted run and the full baseline, dense search put the cited entry in the top 5 for
every routed request (similarity hit@5 83/83) and the reranker put it first every time it was
handed it (rerank hit@1 80/80). Family `s`, written to test exact terms, scored similarity hit@1
11/11: each term in it - a plan name, a form code, a vaccine brand - has its category to itself in
this corpus, so the category alone picks the entry.

Family `w` was written afterwards to put an exact token where it is the only discriminator. An
offline probe ranked each case's cited entry under dense search (`voyage-4-lite`, the build's own
embedding) and a plain Okapi BM25 over the same 36 chunks, before any case was run:

| | dense rank 1 | BM25 rank 1 | BM25 alone |
|---|---|---|---|
| family `s` (12) | 12 | 12 | 0 |
| family `w` (8) | 7 | 4 | 1 (`G-w-05`, "Cigna DHMO") |

Dense search placed bare billing codes ("94010", "93000", "D7140") on the price list unaided. BM25
lost four of them, because in a corpus this small a question's ordinary words ("receipt",
"paying") are rare enough to outweigh the code. Every case was kept whatever the probe said -
selecting cases by which retriever fails them would decide the ablation before it ran.

## 4. Finding: the similarity floor was dropping right answers

The baseline (`evals/baselines/01M3VYV0RYA8N8RF2S1RQE5PSF`, $0.93) left 12 of 87 answerable
requests unserved, from three causes:

| cause | cases |
|---|---|
| similarity floor dropped a chunk dense search ranked **first** | `G-s-05` "PR-4" (0.229), `G-u-04` "braces" (0.239), `G-w-08` "HC-9" (0.220) |
| the answerer declined with the right chunk in front of it | `G-s-06`, `G-s-11`, `G-u-06`, `G-w-05`, `G-m-02` |
| the classifier routed it elsewhere | `G-s-10`, `G-t-01`, `G-w-02` (small talk), `G-t-04` (booking) |

The floor's three are one shape: a short question turning on one token scores low against a long
section, whatever its rank. A run with the floor disabled (`SIMILARITY_FLOOR=-1`, run
`01M3VZWB0SA7P17DQD8S827T21`, $0.92) compared against the baseline:

| | baseline | no floor |
|---|---|---|
| unserved answerable | 12 / 87 | 8 / 87 |
| wrong abstentions | 8 / 30 | 4 / 27 |
| answers on labelled gaps | 0 | 0 |
| similarity gate survival | 0.964 | 1.000 |
| rerank hit@1 | 80 / 80 | 83 / 83 |
| booking, end to end | 24 / 24 | 24 / 24 |

All three floor misses were answered from a chunk the reranker ranked first. The one gap the floor
used to stop (`G-n-05`, "children under five") was stopped by the rerank floor instead, so nothing
the similarity floor caught got through without it. `G-u-06` also moved, from declined to
answered, but it declined 2 times in 5 on replay of an unchanged prompt - that movement is the
answerer's own variance, not the floor's. The cost was four more rerank calls in 146 cases.

**Decision, made on this evidence: the shortlist has no similarity floor by default**
(`SIMILARITY_FLOOR` defaults to -1.0, the lowest a cosine can be; the cap of 5 alone picks the
shortlist, and the rerank floor decides the abstention). A reranker outage no longer answers from
the shortlist as the cap left it, which without a floor would be the least bad of whatever the
search returned: it answers only from chunks at or above `UNRERANKED_SIMILARITY_FLOOR` (0.25, the
floor the shortlist used to have, so `answered_unreranked` keeps meaning a floor-checked answer)
and abstains at the similarity floor when none clears it. The new setting is stated in
`service.configured` and recorded as a run condition; a run that predates it is read as having
fallen back through its own similarity floor, which is what that build did.

## 5. Finding: the answerer declines answers it holds

Five of the twelve were the generation step returning `NO_ANSWER` with the chunk that answers in
its prompt. Replaying each stored prompt five times against the same model and system prompt:

| case | declined on replay | reading |
|---|---|---|
| `G-s-11` "Do you take Delta Dental PPO?" | 5 / 5 | stable; the chunk says "for dental appointments we accept ... Delta Dental PPO" |
| `G-m-02` "Which insurance plans do you accept?" | 4 / 5 | stable, and a regression the extension caused |
| `G-u-06` "Can I get a DOT physical with you?" | 2 / 5 | flaky |
| `G-t-08` "moisturiser before the heart tracing" | 0 / 5 | the live miss was one sample |

`G-m-02`'s stored prompt had been cut by the log's long-string truncation, so its replay was
rebuilt from the corpus with the answerer's own prompt format. It holds the original insurance
entry, which answers the question outright (rerank 0.92), and two plan-guide chunks, the second of
which stops mid-section ("## UnitedHealthcare / We..."). The answer prompt says listing what the
information does say is worse than `NO_ANSWER`, and against a list that looks incomplete the model
declines. Most of the five are a stated "we do not", which the prompt's "say nothing it does not
say - not even a no" is plausibly read against; that is a hypothesis, not yet tested.

None of these is retrieval, and hybrid search will not move them.

## 6. What this means for hybrid search

The "dense, no floor" row the ablation was to carry is now the default pipeline. Hybrid has to
beat it, not the pipeline it was planned against, and on this corpus dense ranking already puts
the right chunk in the reranker's shortlist every time. The probe's one BM25 win (`G-w-05`) was
recovered by the reranker anyway. Hybrid search is expected to show no measurable effect here,
which is a result worth recording rather than a reason to skip the measurement - it says the
retrieval problem this corpus poses is solved by a cross-encoder over a dense shortlist.

## 7. Left open

- **The answer prompt's strictness**, five of the remaining eight misses' worth. Its own measured
  change, since every one of its clauses was tuned against a named case.
- **Chunk boundaries.** The 1,000-character splitter ends chunks on a bare heading and starts the
  next mid-sentence; `G-m-02`'s truncated guide chunk is one consequence.
- **The classifier on short or odd questions** - three to `small_talk`, one to `booking`.
- **A noise band** for this build, before any of the above is read as a result.
- **The corpus is still small** - 36 chunks against a 25-wide pool. If hybrid shows nothing, the
  honest next question is whether a larger corpus would, not whether BM25 is useless.
