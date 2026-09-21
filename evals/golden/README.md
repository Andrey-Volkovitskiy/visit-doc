# The golden set (v2)

The labelled dataset Phase 2 measures against (`docs/ROADMAP.md`). 145 patient messages carrying
180 labelled requests, grouped into 16 families that between them cover what a patient actually
does with this assistant.

**It is data, and `cases.json` is a generated artifact.** The set is written in
`golden_harness.golden_set` and rendered here by `make eval-build-set`; a hand edit to the JSON puts
the two out of step and `evals/harness/tests/test_golden_set.py` fails byte-for-byte. The
declaration lives in the harness because this directory holds data and no code, and because the
invariants a JSON Schema cannot state — a `cites` id that exists in the corpus pin, a fixture start
inside the named practitioner's own working hours — are checked as the set is built, naming the case
at fault. Computing metrics over it is the harness's job, and comparing one build's run against
another's is `eval-compare`.

## v2, and what it does not descend from

v1 was a consolidation: 135 cases assembled from the labelled sets that specs 008, 009, 010 and 011
each shipped with their own manual procedure. That gave it good coverage of the *defects those
phases fixed*, and a shape that mirrored the specs' own sections rather than the assistant's use.

v2 is written from the use cases instead. It descends from no spec's set and quotes no case from one.
The consequences worth knowing:

- **Booking is tested on its own.** v1 reached all six scheduling tools and carried nine confirmed
  writes, but it had no family dedicated to booking: every booking request sat inside
  `pleasantry-plus-request`, `mixed-faq-booking` or `segmentation-edges`, so booking was always
  measured beside something else. v2's family `a` walks the node's range by itself — roster,
  specialty, slots, the patient's own appointments, and the three writes — which is also what lets a
  failure there be attributed to booking rather than to the segmentation around it.
- **No stored run re-scores.** Every case id changed, so `eval-score` and `eval-compare` refuse
  every run taken against v1, including the 2b record under
  `specs/012-golden-set-metrics/evaluation/`. That record stays frozen as evidence of what the build
  did on 2026-09-15; it is not a baseline, and a current baseline needs a fresh `make eval-run`.
- **v1 is not deleted, it is superseded.** Its cases and the provenance of each are in git history.

## Files

| File | What it is |
|---|---|
| `cases.json` | The set, grouped by family — generated; see above. `schema.json` is authoritative for the shape. |
| `schema.json` | JSON Schema for the file. All 145 cases validate against it. |
| `corpus.json` | The pinned FAQ corpus every `cites` label names, with a `sha256` over the entry texts. |
| `PROVENANCE.md` | Where v2 came from, and what has to be re-checked when the corpus moves. |

## The file shape

Families carry the description of what they test, so the grouping is data rather than a comment the
format cannot hold:

```json
{
  "families": [
    {
      "letter": "j",
      "name": "single-faq-answered",
      "tests": "One FAQ question the pinned corpus answers. The whole RAG path has to work...",
      "cases": [
        {
          "id": "G-j-01",
          "message": "What should I bring to my first appointment?",
          "requests": [
            {"intent": "faq_question", "gist": "what to bring",
             "answerable": true, "cites": ["what-to-bring"]}
          ]
        }
      ]
    }
  ]
}
```

A case id is `G-<family letter>-<nn>`, where the letter is its family's. `nn` is the case's
**identity** inside that family, not its index: the numbers ascend but need not be contiguous. A
removed case leaves its number unused for good, and a number is never reused, because renumbering
the cases after a removal would rename labels that had not changed and make every stored run that
selected them unscoreable — v1 left `G103` as a hole for the same reason. Family `a` has no
`G-a-03`. The loader checks the letter and the ascent, neither of which a JSON Schema pattern can:
an id filed under the wrong family, repeated, or out of order is refused by name.

There is no `family` key on a case and no `source` key at all. The family is the group the case sits
in — one copy, which cannot disagree with itself — and the loader carries the family's name onto each
case as it flattens the file. `source` is gone because v2 has one source, described above, rather
than a per-case citation of another spec's set.

## The families, in file order

| | Family | n | What it tests |
|---|---|---|---|
| a | `single-booking` | 13 | One booking request the booking node serves alone, across its whole range. Writes carry a scripted second turn, so a bare "OK" has to read as the booking it answers. |
| b | `urgent_condition` | 9 | Something an emergency department exists for. Takes the whole turn; ends it. Carries the hyperbole counter-case. |
| c | `distress` | 7 | Real fear or acute upset. Carries the brief-exclamation counter-case. |
| d | `booking_for_another` | 7 | The appointment is plainly for someone else. Nothing may be written. |
| e | `not_authorized` | 9 | A request the assistant may never serve, whatever the corpus grows to hold. |
| f | `call_staff` | 6 | An explicit request for a human — the one cause that owes the patient silence. |
| g | `small-talk` | 12 | Messages asking for nothing. None may page a person or search the corpus. |
| i | `out-of-topic` | 5 | Clearly outside the clinic's domain: nothing to retrieve and nobody to page. |
| j | `single-faq-answered` | 16 | One answerable FAQ question: both gates cleared, answer generated, right entry cited. |
| k | `single-faq-gap` | 14 | One question the corpus cannot answer, most overlapping an entry heavily. Must abstain. |
| l | `single-faq-that-looks-multi` | 7 | One request wearing the clothes of several. Must not be over-split. |
| m | `compound-faq-answered` | 8 | Two or three answerable questions, each retrieved on its own and cited on its own. |
| n | `compound-faq-mixed` | 9 | One answerable, one a gap. Answer the half you can; name the half you cannot. |
| o | `request-segmentation` | 7 | A request that cannot be searched as written and must be restated without adding. |
| p | `pleasantry-plus-request` | 8 | A greeting in front of a real request, drawn from every route. |
| q | `mixed-faq-booking` | 8 | An FAQ half and a booking half; each specialist sees only its own. |

There is no family `h`: the letters follow the order the set was specified in, and `h` was not used.

## What a request label carries

`intent` must be one of `chat.domain.schemas.IntentLabel`'s values (bar
`classification_failed`, which no label may claim — it is assigned by orchestration on a failed
call). Then, per intent:

- `faq_question` carries `answerable` and `cites`. `cites` is non-empty exactly when `answerable` is
  true, and every id in it must exist in `corpus.json`.
- `booking` carries `tools` — the tools whose *absence* means the request went unserved. It is a
  required subset, not a sequence: a `check_availability` before a `book_appointment` is not a miss.
  The set is **closed under prerequisites**, because the tools take ids and not names:
  `check_availability` and `book_appointment` need a `practitioner_id`, which only
  `list_practitioners` yields, and `reschedule_appointment` and `cancel_appointment` need an
  `appointment_id`, which only `list_my_appointments` yields. So a case about checking a named
  practitioner's slots still requires the roster read that turns the name into an id — naming the
  dependent tool alone would expect the loop to have invented one. `golden_set.bk()` adds each
  prerequisite, so a case states the tool it is *about*; two tests hold the rule, one of them
  checking its premise against the tools' own schemas.
- Every other intent carries neither.

`gist` is a human reference and is **never scored**. A segment has many valid restatements, and
exact-matching the classifier's wording would measure phrasing rather than segmentation.

## The intent a request contributes to escalation

Derived at read time rather than labelled, so the two cannot disagree:

| A request with this intent | contributes this `EscalationReason` |
|---|---|
| `urgent_condition` | `URGENT_CONDITION` |
| `distress` | `DISTRESS` |
| `call_staff` | `PATIENT_ASKED_FOR_PERSON` |
| `booking_for_another` | `BOOKING_FOR_ANOTHER_PERSON` |
| `not_authorized` | `NOT_AUTHORIZED` |
| `faq_question` with `answerable: false` | `CORPUS_COULD_NOT_ANSWER` |
| anything else | none |

A turn raises **every** cause its requests contribute; the *mark* is the first of them in
`escalation._PRECEDENCE` order, and whether the assistant falls silent is `_SILENCING`'s separate
question. `ASSISTANT_FAILED` is the one cause no case can carry: it is a property of a broken run,
not of a message.

## The scheduling fixture

Present on exactly the cases carrying a booking request — the loader refuses the file otherwise.

- `given` — planted standing before the turn, each entry naming a practitioner, a day and a time.
- `expect` — the **complete** set of the patient's appointments after the case's last turn, standing
  or cancelled, matched one to one. An appointment no entry accounts for is a failure, and a case
  expected to change nothing restates `given`.
- `reply` — the patient's scripted answer, posted as a second full turn once the first has replied.
  It is present on exactly the cases whose tools include a write, because a write is confirmed
  before it happens and a read has nothing to confirm.

Days are signed offsets from the run clock, which is a **Monday**: `+2d` is Wednesday, `+5d`
Saturday. The two seeded practitioners differ in both specialty and hours, and a fixture outside
them would never plant:

| Practitioner | Specialty | Days | Hours |
|---|---|---|---|
| `William Osler` | General Practice | Mon–Fri | 09:00–17:00 |
| `Andreas Vesalius` | Dentistry | Mon–Sat | 09:00–14:00 |

With 60-minute slots, the last legal start is 16:00 for the GP and 13:00 for the dentist. No booking
case may name the clock's own weekday, because "Monday" on a Monday clock reads as either today or a
week out and a label can only mean one of them.

## What the file deliberately does not hold

Anything derivable from `requests`. There is no turn-level expected outcome, no expected reply text,
no count of expected segments, no `expected_verdict`. Each would be a second copy of something the
request list already says, free to disagree with it.
