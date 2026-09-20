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
