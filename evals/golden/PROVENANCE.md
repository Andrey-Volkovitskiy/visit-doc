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

## The corpus pin was re-taken (2026-09-14)

`corpus.json` had recorded
`sha256:9552b098bcc15fde51207409f514f2da246c730cea55281a0bc405027c55a267` (`9552b098…a267`),
described only as "a `sha256` over the entry texts". The construction behind it was written down
nowhere, and **none of the seventeen constructions tried in
`specs/012-golden-set-metrics/research.md` (R3) reproduces it** — joins, JSON renderings, reprs and
per-entry hash schemes of the texts, all listed there. That value is kept here as what was
recorded, and it is unverifiable: no check can be run against it.

**The texts had not drifted.** The nine `entries[].text` values were byte-identical to
`DEFAULT_FAQ_ENTRIES` when the pin was re-taken, so no label was re-checked and none changed.

The pin is now `ea83b6c4c0b55657c5fa73bb1b5d1226442b7fa71b6e8f8a8512a6d9a48b20a0`, under the
construction `corpus.json` states in its new `algorithm` field: `sha256` over `entries[].text` in
index order, each text UTF-8 encoded and terminated by `\n`, with nothing else in the digest. The
same digest over `DEFAULT_FAQ_ENTRIES` gives the same value, and the harness's tests recompute both.

## Scheduling fixtures (2026-09-14) — approved

The 18 cases carrying a booking request gained a `scheduling` fixture for spec 012's end-to-end
task success. **Approved by the project owner on 2026-09-14 (spec 012 FR-038a, task T050a)**, after
the review below reworded six messages and settled the replies; runs may now be taken against them
as ground truth. No `tools` label was touched.

Labelled against the default clock **Monday 2026-03-02 08:00** and a fresh session's roster:
William Osler (General Practice, Mon–Fri 09:00–17:00) and Andreas Vesalius (Dentistry, Mon–Sat
09:00–14:00), 60-minute slots. `+1d` is Tuesday, `+3d` Thursday, `+4d` Friday, `+7d` the next
Monday. Every fixture passes `validate_plantable` against that clock.

How they were decided:

- **A fixture is what the scripted exchange leaves behind.** Where the message leaves too much
  open for one scripted answer — which new time, which practitioner — the case carries no `reply` and the
  expectation restates `given`.
- **Read-only tools restate `given`.** Where the message presupposes an appointment ("my next
  appointment", "my Friday appointment"), one plausible appointment is planted then: Vesalius
  when the message implies a dentist, Osler otherwise, at a time inside hours on the grid.
- **The contract's four worked examples are used as written** (G042, G049, G088, G102), and the
  same reading is extended to the cases like them: a request that names the appointment, or the
  day to book, is expected to complete. A booking whose practitioner is not implied defaults to
  Osler, as G088's does.

| Case | Message | `given` | `expect` | Reason |
|---|---|---|---|---|
| G042 | Hi, I need to cancel tomorrow | Osler +1d 10:00 | same, cancelled | Contract example: the one appointment tomorrow is cancelled. |
| G044 | Thanks! Any slots with William Osler on Wednesday? | — | — | Availability is read-only and presupposes no appointment. |
| G048 | Hey, can I move my Thursday appointment? | Osler +3d 11:00 | same, standing | Contract principle: no new time is named, so one turn can only ask which. |
| G049 | Good afternoon, which cardiologists do you have? | — | — | Contract example: read-only; the session has no cardiologist. |
| G050 | OK thanks, when is my next appointment? | Osler +2d 14:00 | same, standing | Presupposes an appointment; listing it must leave it alone. |
| G051 | Cheers - cancel the Tuesday one please | Osler +1d 15:00 | same, cancelled | Names the day of the one appointment to cancel. |
| G053 | Nice one. Can I see a cardiologist next week? | — | — | Contract: read-only; there is no cardiologist to offer. |
| G062 | Can I book Wednesday with William Osler, and also get a receipt reissued for last month? | — | Osler +2d, standing, any time | Names practitioner and day but no time: the first turn offers times and asks which, and the reply takes the earliest. |
| G087 | What are your clinic hours, and what dentist slots are free tomorrow? | — | — | Availability is read-only and presupposes no appointment. |
| G088 | What should I bring, and can you book me Wednesday at 9 with William Osler? | — | Osler +2d 09:00, standing | Contract example: names practitioner, day and time. |
| G089 | Which insurers do you accept, and which practitioners do you have? | — | — | Listing practitioners is read-only. |
| G090 | What are your hours, and please cancel my Friday appointment. | Osler +4d 11:00 | same, cancelled | Names the day of the one appointment to cancel. |
| G091 | Where do I park, and what appointments do I have booked? | Vesalius +3d 12:00 | same, standing | A listing needs something to list; it must leave it alone. |
| G092 | What should I bring, and what does Dr. Vesalius specialize in? | — | — | Listing practitioners is read-only. |
| G093 | What is your address, and can I reschedule to the same time next week? | Osler +2d 10:00 | Osler +9d 10:00, standing | Presupposes one appointment; its target is fully named. A reschedule keeps the row, so nothing is cancelled. |
| G094 | Are you open Sundays, and can I book the earliest slot you have with William Osler? | — | — | Read-only by decision: the loop finds the earliest slot and asks to confirm it, and nothing is written in one turn. |
| G095 | Where are you, how much is a GP visit, and can I book one with William Osler on Friday? | — | Osler +4d, standing, any time | Names practitioner and day; no time is named. |
| G102 | Can you cancel Friday and book Wednesday with William Osler instead? | Osler +4d 10:00 | that one cancelled, plus Osler +2d standing, any time | Contract example, verbatim. |

**The confirmation rule is decided (2026-09-14, spec 012 FR-037b).** `handle_booking.py` tells
the loop to confirm practitioner and start before `book_appointment`, and never to call
`cancel_appointment` without a confirmation given in the *current* turn, so one turn asked to
cancel or book correctly writes nothing. The eight cases whose message asks for a write — G042,
G051, G062, G088, G090, G093, G095 and G102 — therefore carry a scripted `reply`, posted verbatim as a
second full turn in the same chat once the first turn has replied. The patient's appointments are
read after the first turn, where they must still be `given`, all standing — a write before the
confirmation fails the case — and after the reply, where they must match `expect`. Their `expect`
labels stand as drafted; tool selection counts both turns' calls, so their 2a `tools` labels are correct as they are.
Labelling them "unchanged" instead was rejected: no case would then write anything.
G062 was the eighth, added by the second round of decisions the same day: drafted with no reply and
an empty `expect`, its `book_appointment` label could never be met in one turn, so it was given a
reply and now expects one standing Osler booking on Wednesday at any time.

| Case | `reply` |
|---|---|
| G042, G051, G090, G093 | Yes, please go ahead. |
| G088 | Yes, please book it. |
| G062 | The earliest Wednesday time is fine, yes please book it. |
| G095 | The earliest time you have on Friday is fine, yes please book it. |
| G102 | Yes, please cancel Friday. The earliest Wednesday time is fine. |

Each reply is written to answer every choice the loop is told to leave to the patient, so that a
correct loop needs no third turn.

**Six messages reworded (2026-09-14, by the reviewer's decision).** Two defects in the drafted
messages would have measured the labels rather than the loop:

1. **A message whose tool needs a practitioner names one.** `check_availability` and
   `book_appointment` both take a `practitioner_id`, and the prompt says never to choose a
   practitioner for the patient, so "any slots on Monday?" or "the earliest slot you have" can
   only end in *which practitioner?* — a turn that is right to ask, spent on a question the case
   was not written to measure. G044, G062, G088, G094, G095 and G102 now name William Osler, and
   the replies no longer have to. G087 (*dentist slots*) and G053 (*a cardiologist*) keep their
   wording: a specialty resolves to one practitioner or to none, so neither leaves a choice.
2. **No booking message uses the run clock's own weekday.** The clock is Monday 08:00, so "Monday
   at 9" reads as an hour from now or as a week out, and a label can only mean one. G044, G062,
   G088 and G102 now say Wednesday (`+2d`); G088's expectation moved from `+7d` to `+2d`, and G102's
   new booking with it.

G094 stays read-only by decision: asked to book the earliest slot, one turn finds it and asks to
confirm, and it carries no reply. A test guards both rules for the committed set
(`evals/harness/tests/test_fixture_label.py`). The messages are scored fields, so every run taken
before this change is refused by scoring (spec 012 FR-044a); none had been committed.

