# Provenance

Where v2's labels come from, and what has to be re-checked when the things they depend on move.

## The rule

A label is a claim about what the assistant *should* do, and it is only worth something if a person
decided it deliberately. Nothing here was produced by running the system and recording what it did:
that would make the set agree with the build by construction and measure nothing. Where a label and
a run disagree, either could be wrong, and deciding which is an adjudication a person performs.

## Where v2 came from (2026-09-20)

v2 was written fresh, from the assistant's own use cases, against three fixed things:

1. **The pinned corpus** (`corpus.json`, 9 entries, `sha256` over the entry texts). Every
   `answerable: true` label names the entry that carries the answer, and every `answerable: false`
   label was checked against all nine entries — not against the nearest one.
2. **The seeded roster** — `William Osler` (General Practice, Mon–Fri 09:00–17:00) and
   `Andreas Vesalius` (Dentistry, Mon–Sat 09:00–14:00), from `SESSION_SEED` and the physician name
   pool. Every scheduling fixture was checked to fall inside the named practitioner's own working
   week and hours, with 60-minute slots.
3. **The intent set** — `chat.domain.schemas.IntentLabel`, less `classification_failed`, which no
   label may claim.

It descends from **no** spec's labelled set. v1's cases came from 008's calibration questions, 009's
sets A–E, 010's compound/mixed/multi/overriding sets and 011's sets A–B, each with its own manual
procedure; v2 quotes none of them. That is the point of the rework: those sets were built to
demonstrate the defect each phase fixed, so the resulting shape recorded the project's history rather
than what a patient does.

v1 and the per-case provenance of its 135 cases are in git history, at the commit before this one.

### What was deliberately kept from v1's thinking

Not its cases, but four decisions it arrived at the hard way, each of which cost a measurement to
learn:

- **A near miss is the useful gap.** A gap case sharing no vocabulary with any entry proves nothing:
  retrieval fails it at the similarity floor without the reranker having to decide anything. Most of
  family `k` overlaps an entry heavily and must still abstain.
- **Counter-cases sit in the family they contradict.** `urgent_condition` carries the hyperbole case
  ("my tooth is killing me"), `distress` carries the brief exclamation, `booking_for_another`
  carries the booking that merely mentions a third party. A label won by keyword rather than by
  meaning fails in the family that owns the keyword, where someone will read it.
- **A gist is never scored.** It is a human reference. A segment has many valid restatements, and
  comparing the classifier's wording to a hand-written one measures phrasing.
- **No case names the run clock's own weekday.** On a Monday clock, "Monday" reads as today or as a
  week out, and a label can only mean one of them.

## G-a-03 removed (2026-09-21, by the reviewer's decision)

The case read *"What does Andreas Vesalius specialise in?"* and was labelled `booking`, because a
practitioner's specialty is live records data — no corpus entry names a practitioner. The first two
runs of family `a` both classified it `faq_question`, so it searched the corpus, abstained, and
called a person under `corpus_could_not_answer`: a cause that says "write an FAQ entry" about
something no entry should ever assert.

Probing the classifier directly showed the cause was not the routing rule but the wording, and the
trigger was narrow — the bare full name as the sentence's subject:

| Input | Produced |
|---|---|
| *"What does Andreas Vesalius specialise in?"* (the case, ×5) | `faq_question`, 5/5 |
| *"What does Andreas Vesalius special**ize** in?"* | `small_talk` |
| *"What does **Dr.** Vesalius specialise in?"* | `booking` |
| *"What does **William Osler** specialise in?"* | `booking` |
| *"What does **Sarah Whitfield** specialise in?"* (invented name) | `booking` |
| *"I have an **appointment with** Andreas Vesalius — what does he specialise in?"* | `booking` |

The `small_talk` result is the tell, since that label means a question the clinic has nothing to do
with: the model was reading the 16th-century anatomist. Vesalius is bound to anatomy *as a subject*,
where Osler — equally famous, and the other seeded name — is bound to being a physician. The seed
pool is deliberately made of recognizable dead figures, and this is the one name where that
backfires.

So the case measured a name-recognition artifact of the demo seed data rather than the
`booking`/`faq_question` boundary it was written for. The reviewer removed it rather than re-word it.
The shape it was meant to cover survives in **G-q-06**, *"What should I bring, and what does Dr.
Vesalius specialise in?"*, in the wording that classifies correctly.

**Its number is not reused.** Family `a` runs 01, 02, 04 … 14 with no `G-a-03`, because renumbering
the eleven cases after it would rename labels that had not changed and make every stored run that
selected them unscoreable. v1 left `G103` as a hole for the same reason. The loader enforces the
family letter and that the numbers ascend, and deliberately not that they are contiguous.

## The corpus extended, and four retrieval families added (2026-10-01, by the reviewer's decision)

ROADMAP Phase 4b. The 9 entries were too few and too unlike each other to measure retrieval: the
v2 baseline read similarity hit@3 1.0 and MRR 0.988. Nine of the clinic's longer documents were
added after them, **extending** the corpus rather than replacing it, so that every label resting on
the original nine keeps its meaning: `insurance-plan-guide`, `dental-care-guide`,
`test-preparation`, `vaccinations`, `running-late`, `reminders-and-messages`,
`check-in-and-registration`, `privacy`, `gp-services`. 18 entries, 34 chunks where there were 9,
so the 25-wide pool no longer returns the whole corpus. The documents were written against three
rules: nothing in them contradicts one of the nine; none names a practitioner, a specialty on the
roster or anyone's hours, which are live records data; and none answers an existing gap.

The labels below were drafted by the assistant and adjudicated by the reviewer, who approved
every one of them as written on 2026-10-01.

### What moved in existing labels

Every existing label was re-checked against the nine new documents.

- **Six answerable labels gained a second citation**, `insurance-plan-guide`, because the plan guide
  now answers them too and a label citing only the old entry would score a correct retrieval as a
  miss: `G-j-08` (Aetna), `G-l-04` (Blue Cross), `G-m-02` and `G-q-03` (which plans), `G-m-06`
  (Cigna), `G-o-06` (Medicare). Their scored digests changed, and scoring refuses a whole run,
  not just the case, when any case it selected has a moved label - so `eval-score` and
  `eval-compare` refuse every earlier run that selected one of the six, the v2 baseline among
  them.
- **Two were left alone, and are the first to re-read**: "when is payment due" in `G-m-04` and
  `G-m-06`. The plan guide says a copay is collected at check-in, which is part of an answer and
  not the whole of one, so it was not added as a citation.
- **Every gap still holds.** None of the new documents answers one. Three sit deliberately close,
  and are the ones to re-read whenever their document changes: `G-k-04` (cancellation and no-show)
  against `running-late`, which says what happens to a late arrival and nothing about cancelling
  or not turning up; `G-k-07` (fasting before a blood test) against `test-preparation`, whose
  spirometry section says to avoid a large meal - and which mentions no blood test; and `G-n-06`
  (whether a scan is included) against `vaccinations`, which says a vaccine is charged in addition
  to the visit. `G-k-02`/`G-n-02` (wheelchair access) are also what the e2e tier's abstention
  journey asks, so no document may mention step-free access without moving that test too.

### What was added

- **Five gaps in `k`** (`G-k-17` to `G-k-21`), near misses against the new documents: a charge for
  arriving too late, the shingles vaccine, the length of a filling appointment, Humana, reminders by
  WhatsApp. Each is a subject a document is close to and does not cover, and the notes say which
  sentence each is close to.
- **Four families**, each aimed at one retrieval weakness, so the Phase 4b ablation can be read per
  family: `s` exact terms (12), `t` paraphrase (9), `u` an answer deep in a long document (7),
  `v` a near-miss distractor (6, after `G-v-06` below). `s` and `t` are each other's counterweight - lexical search should
  help the first and dense search the second, and a fusion is judged on both.
- **`G-v-03` reworded the same day, by the reviewer's decision**, after the first targeted run of
  family `v`: *"Will my Aetna plan pay for a dental cleaning?"* became *"Can I use my Aetna medical
  insurance for a dental cleaning?"*. Aetna also sells dental plans, so "my Aetna plan" did not say
  which kind the patient held, and the dental guide answers only the medical one - the answerer's
  abstention on the old wording was defensible. The id is kept: no baseline had been taken on it.
- **`G-v-06` removed the same day, by the reviewer's decision.** *"Will you email me my test
  results?"* was classified `not_authorized` and handed off, which is a defensible reading: it can
  be heard as a request for results, which the assistant may never serve, as much as a question
  about the clinic's email policy. A case whose right intent depends on that reading measures the
  wording, not the routing. Its number is not reused, so family `v` runs 01-05 and 07.
- **A tenth document, `procedure-prices`, and family `w`, by the reviewer's decision.** The first
  targeted run showed dense search ranking the cited entry first in every case of family `s`:
  each exact term there has its category to itself, so the category alone picks the entry and BM25
  has nothing to win. `w` puts the token where it is the only discriminator - bare billing codes
  from a new price list beside the entries about receipts and rates, and plan qualifiers (Cigna
  DHMO, an AARP HMO, Blue Essentials) beside the insurance entries that accept the brand - plus
  `G-k-22`, a code the list does not hold. The document prices procedures only, never a visit, a
  scan, an MRI or a follow-up, so every gap still holds and no existing label gains a citation;
  its last section was reworded before pinning, because "a code not in this list is part of the
  visit" would have answered `G-n-06`'s scan half and `G-k-22` both. An offline probe ranked the
  cited entry under dense search and a plain BM25 over the 36 chunks: dense first in seven of
  eight, BM25 in four, BM25 alone in one (`G-w-05`). Every case was kept regardless - selecting
  cases by which retriever fails them would decide the ablation before it ran.

### What it means for stored runs

The corpus pin changed and six labels moved, so the v2 baseline
(`evals/baselines/01M321DWRXSVSY7GW9RY3CR9YW`) no longer re-scores and is evidence of the 9-entry
build rather than a baseline for this one. A new baseline, and a noise band for it, are
taken next.

## What the set depends on, and what to re-check when each moves

### The corpus

`corpus.json` pins a snapshot. When `chat.rag.default_corpus.DEFAULT_FAQ_ENTRIES` changes:

1. Re-take the pin, and confirm its `sha256` is the digest of its own entry texts — a pin that does
   not check against itself is not a pin.
2. Re-check every label resting on a **changed** entry: each `cites` of it in families `j`, `m`, `n`,
   `o`, `p`, `q`, and — the direction that is easy to miss — every gap in `k` and `n` the changed
   entry might now answer. A widened entry turns gaps into answerable requests silently.
3. Record what moved here, with the date.

Two gaps are near misses against one specific sentence each, and should be re-read first whenever
that entry changes: `G-k-13` (a height limit in the garage the hours entry names) and `G-k-03`
(payment by cheque, against a list of accepted methods that does not mention one).

### The roster and its hours

Every fixture in families `a`, `d`, `o`, `p` and `q` is stated as a day offset and a time, and is
legal only against the seeded practitioners' schedules. If `SESSION_SEED` changes specialty, hours or
slot length, or the name pool's order changes which name each seed receives, every fixture has to be
re-checked: `validate_plantable` refuses one that would not plant, and
`test_every_committed_fixture_plants_against_the_default_clock` runs it over the whole set.

The dentist's shorter day (ending 14:00) and the GP's Saturday absence are both load-bearing:
`G-a-07` exists because only one of them works Saturday, and `G-o-07` because the clinic's published
closing time and the dentist's own last slot are different answers to the two halves of one message.

### The intent set

`test_the_schemas_intents_are_the_classifiers_labels` compares `schema.json`'s enum to `IntentLabel`
for equality, so adding an intent to the service without adding it here fails the suite rather than
producing an unexplained validation error later.

### The prompts

`test_no_prompt_quotes_a_golden_case` fails if any prompt quotes a case's own words: a case a prompt
shows the model measures whether the model followed its example. Drafting v2 tripped it four times,
and the fixes went in both directions — three cases kept the corpus's natural phrasing of "do I need
a referral?" and the classifier prompt's example was reworded instead; one case (`G-l-05`) was
rewritten, because the example it collided with was the prompt's own and predates the set.

## Scoring consequences of this rework

`intent`, `message`, `history`, `answerable`, `cites`, `tools` and `scheduling` are scored fields:
their digest is what a stored run records, and scoring refuses a run whose labels have moved since.
Because **every case id changed**, no run taken against v1 re-scores at all — the ids it selected no
longer exist. That includes the 2b record under `specs/012-golden-set-metrics/evaluation/`, which
stays frozen as evidence of what the build did on 2026-09-15 rather than as a baseline. A current
baseline needs a fresh `make eval-run`.

`family`, `note` and the family's own `name` and `tests` are **not** scored, so regrouping the set or
rewording a description leaves every digest where it was.
