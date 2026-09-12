# Provenance

Where each case came from, what the corpus changes forced, and what did not survive being
re-expressed as a request-level label. `docs/ROADMAP.md` Phase 2a calls consolidation "the first
real test of the per-request label shape"; this is what the test returned.

## The rule

**A label was re-checked only where the corpus text it depends on actually changed.** Every seed set
contains borderline calls — a question the corpus arguably answers and arguably does not — and
re-adjudicating all of them would have been a rewrite, not a consolidation, with no record of whose
judgement won. So the corpus diff drove the re-labelling, and borderlines against *unchanged*
entries were left exactly as the phase that shipped them called them. Where a borderline is close
enough to matter, it is flagged in the case's own `note` instead.

## Where the cases came from

| Source | Cases | What it contributed |
|---|---|---|
| `008/calibration/questions.md` | 20 | the only set with source-document labels; answerable/abstaining singles |
| `009/evaluation/inputs/` | 51 | intent labels: small talk, pleasantries wrapping requests, unauthorized requests, urgency, distress, booking for another |
| `010/evaluation/inputs/` | 45 | segmentation: compound, mixed, edge cases, single-requests-that-look-multi, overriding segments |
| `011/evaluation/inputs/` | 17 | partial serving: one answerable request beside one that is not |
| new | 3 | cases the corpus changes created; see below |

The seed's 18 JSON files hold 276 entries, but 009 re-ran varied copies of its sets as the prompt
changed (`setA`/`setA2`, `setB`/`setB2`, `retest`, `distress`), and `010/single` and `011/single`
are the same 22 messages labelled for two different questions. Deduplicated, the distinct seed is
what the table above accounts for.

## What the corpus changes moved

`DEFAULT_FAQ_ENTRIES` changed three times between 1h shipping (`ba0a6e1`) and this set being
written. Two of the three moved labels.

### 1. The referral entry widened

> `You can book a specialist appointment without a referral.`
> → `You can book a visit with any of our practitioners without a referral.`

The old answer spoke only of specialists, so "do I need a referral for a *dentist*?" fell outside
it. The new one covers any practitioner, which answers it.

| Case | Was | Is | Consequence |
|---|---|---|---|
| **G134** (`011/setB#B10`) | answerable + gap | answerable + answerable | **no longer a partial-serving case.** It was labelled as one; the corpus now answers both halves, so it cannot test what it was written to test. |
| **G097** (`010/multi#C8`) | ellipsis, second half a gap | both answerable | segmentation label unchanged — the second half must still be restated to stand alone |

G134's loss is the one that costs something: `partial-serving` is down a case, and the family is
where 1h's whole point is measured. It was not replaced with a manufactured substitute, because a
partial case has to be a question a patient would really ask.

### 2. The plans entry began offering a handoff

> `Please contact our front desk to verify your specific coverage.`
> → `If you're unsure whether your specific plan is covered, you can ask me to connect you with a
>    member of our friendly front desk team.`

The entry now invites the patient to ask for a person, which is a `call_staff` request the corpus
itself produces. Two new cases cover it — **G135**, the offer taken up in one turn, and **G136**,
taken up across turns with the entry's own words in the history.

It also puts **G020** (`Do you accept Blue Cross for dental implants specifically?`) at risk. That
question is one of the three 1e relied on to prove the *rerank* floor does work: it clears the
similarity floor and has to be stopped afterwards. The entry it overlaps with is longer now and
talks about clarifying coverage, which is closer to what the question asks. The label is still
`gap`, and it is the likeliest label in the set to be wrong. **Re-measure it.**

### 3. The hours entry priced the parking

> `The nearest parking is a 3-minute walk away`
> → `The nearest fee parking is a 3-minute walk away`

Two words, and "is parking free?" stopped being a gap: fee parking is not free.

| Case | Was | Is |
|---|---|---|
| **G016** (`009/setB2#B19`, `Is parking free?`) | intent-only label | answerable, cites `hours-location` |
| **G096** (`010/multi#C1`, `Do you have parking, and is it free?`) | second half a gap | both answerable |

The entry still does not say what parking *costs*, so **G023** is new: `How much does parking at the
Mega Mall garage cost per hour?` — the gap that survives the change, kept so the family does not
lose the shape G096 gave up.

## What did not survive re-expression

Four findings, all of the same kind: a seed set answered its own phase's question with a value that
does not mean one thing at the request level.

1. **`011` filed unauthorized requests as "unanswerable".** `setA` pairs an answerable question with
   an "unanswerable" one, but two of those — *renew my prescription* (**G132**), *a medical
   certificate for work* (**G133**) — are not corpus gaps at all. No entry will ever make the
   assistant able to write a sick note, which is `NOT_AUTHORIZED`, not `CORPUS_COULD_NOT_ANSWER`.
   One is fixed by writing an FAQ entry and the other by nothing, which is precisely why 009 split
   the cause in two. Re-expressed with `unknown` as the second request's intent.

2. **`009` labelled a whole message with one intent, and two of its messages carry two requests.**
   `C15` (*opening hours, and a sick note*) and `C16` (*book Monday, and reissue a receipt*) were
   both filed as `unknown` — correct as a turn-level routing answer, wrong as a description of the
   message. Each is now two requests (**G061**, **G062**), which is also what 009's own `retest`
   recorded for C16 when it wrote `unknown+booking` into the expected field.

3. **`010` labelled segment counts, not segment intents.** Every `compound`/`mixed`/`multi` case
   carries `"expected": 2` and nothing about what the two requests *are*. The per-request shape
   needs an intent each, and those were labelled by hand here. They were deliberately **not** taken
   from the committed `*.out.json` files: those hold what the shipped classifier produced, and
   promoting model output to a label would make segmentation accuracy unfalsifiable.

4. **`008` scored one question against one entry where there were two requests.** `A10` (*Where do I
   park, and how far is it from the clinic door?*) is two requests that happen to share an entry
   (**G086**). The label is now two citations of the same chunk — which is what `RequestOutcome`
   already does in code: a chunk under two outcomes is two provenances, not a duplicate.

A fifth, smaller one: 009's `prior` field carries a single prior message with **no role**, and its
contents are sometimes the patient's own earlier question (`setA#A4`, `#A9`) and sometimes the
assistant's answer (`setA2#A12`). Roles were assigned by reading each one. `history` here is an
ordered list of `{role, text}`, because a conversation is not one message deep.

## What was left behind

- **009's re-runs** (`setA2` beyond its new cases, `setB2` duplicates, `retest`, `distress`) —
  the same messages measured again as the prompt changed. Their value was the comparison, which
  belongs to 009's procedure, not to a labelled set.
- **`011/single` and `010/single`** are the same 22 messages; they are here once, labelled for both.
- **008's sweep table** (the rerank floor from 0.30 to 0.68) — a measurement, not a case.
- **`*.out.json`** everywhere — model output, for the reason in finding 3.
- **`010/multi#C26`** — *"What are your hours, where are you, do you take Aetna, and can I book
  Friday?"*, the only message in the seed with more requests than the cap of 3 allows. It was
  carried as G103 and then **removed by decision**: it is a rare shape, its label could not be the
  message's true segmentation, and 010 already records it as a standing miss with the fallback it
  produces. The id is not reused. If the cap of 3 is ever revisited, this message is the datum to
  revisit it against, and it is in `010/evaluation/inputs/multi.json` where 010 left it.
- **The `no-stop` / `not_small_talk` labels** (`009/setE#E4`, and `E2`/`E4`/`E9` in `009/retest`) —
  a label that says what the answer is *not*. It settled the question 009 was asking; nothing at the
  request level can be derived from it.
