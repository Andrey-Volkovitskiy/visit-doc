# 018 — Retrieval under a realistic corpus: findings

What was measured from extending the starter corpus to the decision not to build hybrid search,
2026-10-01, on branch `018-hybrid-retrieval`. ROADMAP Phase 4b is the plan this follows; this file
is what the measurements said, so a later reader can tell a decision from a guess.

Every number below comes from a stored run under `.run/evals/` (the baseline is committed under
`evals/baselines/`) or from an offline replay or probe described where it is used. Every change
below was judged before a noise band existed, so a movement of one or two cases in a full run is a
sample, not evidence: the conclusions rest on movements that are structural (a chunk that never
reached a stage, and then did) or on replays of one prompt many times. The band measured
afterwards (section 8) confirms the reading - full-run answer counts move by ±1 on an unchanged
build.

## Summary: what changed, and why

Phase 4b set out to make retrieval hard enough to measure and then add hybrid search. Making it
hard worked; the weaknesses it exposed were not where hybrid search reaches. Every change below
shipped on its own evidence, and hybrid search was not built (section 7). Unserved answerable
requests went from 12/87 on the extended corpus to 1/87, with no answer on a labelled gap at any
step; the closing baseline is `01M3WD842TTD1Q9FX8TDRTMFAB`.

| change | why | evidence | commit |
|---|---|---|---|
| Ten clinic documents added to the starter corpus, five case families and six gaps to the golden set | 9 one-chunk entries put the whole corpus in every pool, so no retrieval number could move | sections 1-2 | `051e194`, `e28479a` |
| Similarity floor removed (`SIMILARITY_FLOOR` -1.0); the reranker alone decides | a 0.25 cosine floor dropped short questions whose right chunk dense search had ranked first | section 4: 12 to 8 unserved of 87, no gap answered | `b0aa7a1` |
| The reranker-down fallback keeps a floor of its own (`UNRERANKED_SIMILARITY_FLOOR` 0.25) | without one an outage would answer from a shortlist nothing has judged | section 4 | `b0aa7a1` |
| The answerer gives a no the information states | "not even a no" was read as "never a no", so a stated refusal was declined as a gap | section 5: 16/25 vs 7/25 answered on replay, every guard and stress gap still declined | `957f240` |
| Chunk by heading section, each chunk prefixed with its heading path | a fixed window ran across sections and set one section's sentence beside another's | section 6: 10/10 on the two replayed cases it broke | `a171078` |
| Noise band on that build | to tell a real movement from the answerer's run-to-run variation | section 8: only the answerer varies, ±1 | `fcc024b` |
| Classifier: what the clinic sent, billed or treated is `faq_question`; arriving late is a term, not a booking | four of the five remaining misses were routing | section 9: 142 to 145 of 146 exact on replay, and on the full run | `009d4ff` |

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

### The fix, and what it cost

The answer prompt's "Say nothing it does not say - not even a no" became "... not even a no - but a
no it does say is an answer, so give it." Four wordings were replayed offline against prompts
rebuilt from the no-floor run (5 samples each): the five failing requests, the five labelled gaps
that reach generation, nine near-miss gaps fed their three best-reranked chunks as if the rerank
floor had let them through, and twelve controls the prompt already answered.

| | current | stated no | + context limit | + partial answer |
|---|---|---|---|---|
| failing (5), answered | 7 / 25 | **16 / 25** | 16 / 25 | 11 / 25 |
| gaps reaching generation (5), declined | 25 / 25 | **25 / 25** | 25 / 25 | 25 / 25 |
| near-miss gaps forced through (9), declined | 45 / 45 | **45 / 45** | 45 / 45 | 45 / 45 |
| controls (12), answered | 55 / 60 | **60 / 60** | 60 / 60 | 56 / 60 |

The stated-no wording alone was kept; a second replay reproduced it. A fifth wording ("'you' in the
question means the clinic") was tried for `G-s-11` and made four cases worse. `G-s-11` stays
unreliable under every wording because of what the chunk holds, not what the prompt says: asked why
it declined, the model read "do you **take** Delta Dental PPO?" as a question about taking a
medicine, since the blood-thinner paragraph shares the chunk. That is a chunking fix.

A full run on the change (`01M3W2MA7Y1BBTG2Y9F5W1NCKN`, `957f240`, $0.94) against the no-floor
baseline, conditions identical:

| | baseline | stated no |
|---|---|---|
| unserved answerable | 10 / 87 | 6 / 86 |
| wrong abstentions | 6 / 29 | 2 / 25 |
| answers on labelled gaps | 0 | 0 |

`G-m-02`, `G-s-06`, `G-u-06` and `G-w-05` moved to answered. `G-u-04` was answered in the targeted
run before it and errored here on a Voyage blip (excluded as `run_error`). One case moved the other
way: `G-t-08` ("Can I put moisturiser on before the heart tracing?", against "do not put body
lotion ... on your chest") was declined. Replayed ten times, the old prompt declined it 0 times and
the new one 3 - a real cost, not noise: the stated no is about lotion, and the clause makes the
model surer that a no must be the information's own words. Kept, because four requests that were
answered one or two times in five now answer five in five and no gap moved; recorded, because
`G-t-08` is the case a later wording has to keep.

## 6. Finding: chunk by section, and say which section

The 1,000-character window ran across a document's sections: a chunk could end on a bare heading,
start mid-sentence, and put one section's sentence beside another's. `G-s-11` showed the cost -
asked why it declined "Do you take Delta Dental PPO?", the model said it read "take" as taking a
medicine, because the blood-thinner paragraph shared the chunk. An entry with headings is now
chunked by section, every chunk prefixed with its heading path (`# Dental care guide` /
`## Dental insurance`); a section longer than a chunk is split further under the same prefix, and
an entry with no heading - every Q&A entry - is chunked exactly as before. The starter corpus went
from 36 chunks to 62.

Replayed ten times each on the new chunks: `G-s-11` answered 10 of 10 (0-4 in 5 before), `G-t-08`
10 of 10 (7 in 10 under the stated-no prompt on the old chunks, so this also repairs that
regression), `G-w-05` 5 of 10 (about 1 in 5 before), and the gaps `G-k-07` and `G-k-14` declined 10
of 10. A full run (`01M3W4D3ZPHGWSV5DPQJB5RZC3`, `a171078`, $0.90) against the stated-no baseline:

| | stated no | + section chunks |
|---|---|---|
| unserved answerable | 6 / 86 | 5 / 87 |
| wrong abstentions | 2 / 25 | 1 / 24 |
| answers on labelled gaps | 0 | 0 |
| similarity hit@1 | 0.927 | 0.952 |

Four of the five unserved requests left are the classifier's. The cost that came with it: three
gaps (`G-k-07`, `G-k-14`, the MRI half of `G-n-10`) now clear the rerank floor and are declined by
the answerer rather than stopped before it, so the generation step carries more of the
abstention than it did. All declined; the margin is thinner, and those are the cases to watch when
the answer prompt next changes.

## 7. Decision: hybrid search is not built

The "dense, no floor" row the ablation was to carry became the default pipeline, so hybrid would
have had to beat it rather than the pipeline it was planned against. It had nothing to beat:

- **Every answerable request that reached retrieval had its cited chunk in the shortlist**
  (similarity hit@5 83/83), and the reranker put it first every time (rerank hit@1 83/83) - in the
  baseline and in all five band runs, which did not vary.
- **The cases written to favour lexical matching did not need it.** The offline probe (section 3)
  had dense search rank the cited entry first in 7 of family `w`'s 8 cases and BM25 in 4; BM25's
  one solo win (`G-w-05`) reached the reranker through dense search anyway, and its residual
  failure is the answerer weighing two chunks that disagree, which no retriever fixes.
- **Every remaining miss is downstream of retrieval** - the classifier routing elsewhere, or the
  answerer declining - so fusion could reorder the pool without changing an outcome.

Building it would have cost a sparse vector on every point, a second prefetch that must carry the
session and revision filter, a run-condition setting and its ablation runs, to measure a result
already predicted as null. The predicted null is recorded here in place of a measured one, with its
limit: the claim covers a 19-entry, 62-chunk corpus searched with a 25-wide pool. A corpus large
enough that the pool stops reaching the right chunk is where a lexical branch could matter, and
that - not BM25 on this corpus - is the question to reopen.

## 8. The noise band

Five full runs of the heading-aware-chunking build (`a171078`) - the baseline
`01M3W4D3ZPHGWSV5DPQJB5RZC3` and four untraced runs - measured as band
`01M3W93XYN42QWH1TCYXN3E5FC` (`evals/baselines/bands/`). Request count, intent, segmentation,
every similarity and rerank metric, gate survival, tool selection and end-to-end success were the
same in all five, case for case. Only the answerer moved:

| | low | high |
|---|---|---|
| unserved answerable | 4 / 87 | 6 / 87 |
| wrong abstentions | 0 | 2 |
| abstained at generation | 0.066 | 0.085 |

Two cases varied: `G-w-05` (declined in 3 runs of 5) and `G-r-01`'s first request (1 of 5). So the
full-run step from 6 to 5 unserved that section 6 reports is inside the band, and the evidence
for section chunks is the ten-sample replays, not the total. A classifier or retrieval change, on
the other hand, moves something this build never moved - and because a prompt change states no new
condition, the band is applied to its comparison automatically.

## 9. Finding: the classifier misread what the clinic itself sent or did

With retrieval and the answerer settled, four of the five unserved requests were routing: a text
from the clinic's short code (`G-s-10`), aftercare after a filling (`G-t-01`) and a bare billing
code (`G-w-02`) went to `small_talk`, and arriving late (`G-t-04`) to `booking`. The classifier
runs at temperature 0 and the band shows it never varied, so each wording was replayed once over
the whole set - every case, not only the four, since a routing clause can move anything - with the
input rebuilt by the service's own history renderer:

| prompt | exact over 146 |
|---|---|
| current | 142 |
| + "what the clinic sent, billed or treated is its business" | 143 (fixes two, breaks `G-a-02`) |
| + "arriving late is a term of an appointment" | 141 (breaks `G-a-02` and `G-w-03`) |
| **both** | **145**, in three replays out of three |

Either clause alone moved "Do you have a dentist?" (`G-a-02`) off `booking`; together they did
not, so they ship together (`009d4ff`). A targeted live run on the four, `G-a-02` and every
small-talk and out-of-topic case matched the replay: everything right but `G-w-02`, which the
model still reads as unintelligible.

A full run on `009d4ff` (`01M3WD842TTD1Q9FX8TDRTMFAB`, $0.94) against the chunking baseline and
the band confirmed it. Intent went from 164/168 to 167/168 and exact segmentation from 142/146 to
145/146 - both outside a band in which they never moved - and the three re-routed requests were
ranked first by both stages and answered. Unserved answerable fell from 5/87 to 1/87, with no
wrong abstention and no answer on a labelled gap; `G-w-05` was answered this time, which the band
already shows it doing. Nothing else moved. The run is the baseline that closes Phase 4b.

## 10. Left open

- **`G-w-02`** ("How much is 93000 if I'm paying myself?") - a bare billing code still routes to
  `small_talk`.
- **`G-w-05`** ("Cigna DHMO") is answered about half the time, and is left open deliberately.
  Retrieval is identical in every run - the dental guide's "we do not accept … Cigna Dental Care
  DHMO" ranked first - but the original Q&A entry's "we accept … Cigna" sits beside it, and the
  answerer sometimes reads the pair as a contradiction and declines. It never answers yes, so the
  failure is a needless hand-off, not a wrong answer. A "specific beats general" clause in the
  answer prompt was replayed against the closing baseline, over every request that reached
  generation and every gap the rerank floor stopped (2026-10-03):

  | | current | S1: "the one that names the exact thing outweighs one that speaks in general" | S2: "a statement that names exactly what was asked outweighs a general one" |
  |---|---|---|---|
  | `G-w-05` answered | 6/20 | 20/20 | 17/20 |
  | guards / stress gaps declined | 70/70, 160/160 | 70/70, 160/160 | 70/70, 160/160 |
  | controls answered | 253/255 | 249/255 | 249/255 |

  S1 fixed the target and broke its twin: `G-s-02` ("UnitedHealthcare Navigate HMO") has the same
  general-against-specific shape and the current prompt answers it every time, but under S1 it
  declined 3 in 3, and `G-v-03`, whose prompt holds one chunk and no conflict at all, 2 in 3.
  Naming disagreement taught the model to look for it rather than to resolve it. Neither wording
  shipped. Fixing the old entry instead would remove the conflict the case exists to test.
- **The answerer now carries more of the abstention** (section 6) - three gaps reach it that the
  rerank floor used to stop.
- **The corpus is still small** - 62 chunks against a 25-wide pool. Section 7's decision holds for
  this size; a larger corpus is what would reopen it.
