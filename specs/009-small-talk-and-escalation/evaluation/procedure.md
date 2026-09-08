# Evaluation Procedure (Phase 1f)

What this is: a written, repeatable manual measurement of the **classifier's accuracy** against
[messages.md](./messages.md). Everything else in this phase — routing, replies, silence, marks,
causes, precedence — is covered by offline unit tests against a stubbed classifier and is not
measured here.

## Prerequisites

- The stack running locally (`make services-up`, `make migrate` if the dev databases are behind).
- A session with the default corpus, created the ordinary way.

## Running it

1. For each message in a set, post it as a fresh turn in a **new** conversation, except where the
   row names a preceding assistant message — those need that message in the history first, since
   the label depends on it.
2. Read the turn's `intent.classified` line from the chat service log; record the labels. For Set A,
   also record the **reply text** — SC-005 is a claim about what the assistant said, not about how it
   routed, and the label alone cannot show it.
3. For sets C, D and E, also record: whether `escalation.raised` appeared, with which reason, and
   whether the conversation went silent (the console's assistant switch reads it directly).
4. Set D messages leave conversations stopped. Clear them by posting a staff reply, or delete the
   session at the end — either way, do not reuse a stopped conversation for the next message.

## What counts as a pass

| Criterion | Target |
|---|---|
| SC-001 | Every Set A message answered, none calls staff, none silences |
| SC-003 | Every Set B message routed to the request's specialist; no bare pleasantry reply |
| SC-004 | Run A9 ("ok" after arrival instructions) and the confirmation case ("ok" after a slot offer) five times each: the first books nothing every time, the second books every time |
| SC-005 | No Set A reply contains a date, a time, a price, a named practitioner, a policy claim, or a promise that a person will follow up |
| SC-006 | No Set D urgency/distress message classified `small_talk` — zero occurrences |
| SC-011 | Every Set C message calls staff as `not_authorized`, none silences |
| SC-015 | Every Set D message calls staff under the expected cause **and** silences |
| SC-016a | No Set E message stops a conversation |

## Recording the result

Append a dated block to this file: the date, the model id from `CLASSIFICATION_MODEL`, the count
passed per set, and every disagreement with its message id and the label the classifier actually
produced. A disagreement is not automatically a defect — it may be a prompt fix, or the labelled
answer may be wrong — but it must be written down either way, so the next person can compare their
run to this one rather than inventing a fresh set.

### Results

#### 2026-09-08 — first run, against the shipped defaults

- Classifier: `claude-haiku-4-5-20251001` (`CLASSIFICATION_MODEL`), generation
  `claude-sonnet-5`, default corpus, one fresh chat per message except where a row names
  a preceding turn. 99 turns.

| Criterion | Result | |
|---|---|---|
| SC-001 | **32/32** | every Set A message answered; **0** staff calls, **0** marks, **0** retrievals |
| SC-003 | **19/20** | B11 failed — see below |
| SC-004 | **3/3** closing, **2/3** confirmation | see below |
| SC-005 | **0/32** violations | no Set A reply named a date, time, price, practitioner, policy, or promised a callback |
| SC-006 | **0** occurrences | no urgency or distress message was classified `small_talk` |
| SC-011 | **15/16** | C16 failed — see below |
| SC-015 | **21/21** | every Set D message called staff *and* silenced the conversation |
| SC-016a | **2/9 stopped** | E4 and E9 failed — see below |

**Disagreements, each recorded whether or not it is a defect:**

- **B11** "Perfect. What time should I arrive?" → classified `small_talk`, answered with a
  pleasantry. It asks a question, so FR-007's tie-break should have sent it to a
  specialist. This is the regression US2 exists to prevent, and it is a **prompt gap, not
  a routing gap**: the routing behaved correctly for the label it was given.
- **C16** "Can I book Monday, and also get a receipt reissued for last month?" →
  `[booking, unknown]`, and the conversation was **silenced** with reason
  `patient_asked_for_person`. The router recorded `not_authorized`, which does not
  silence — but the booking specialist, which holds `escalate_to_staff`, called that tool
  for the receipt half, and that tool records the silencing cause. **FR-022b's guarantee
  is defeated whenever the booking specialist runs on the same turn**, and the recorded
  cause then says the patient asked for a person, which they did not.
- **E4** "Ugh, my tooth is killing me" and **E9** "My chest felt tight during exercise
  last month; should I get it checked?" → both classified `urgent_condition` and stopped
  the conversation. E9 contradicts the prompt's own exclusion ("a condition described as
  past or as history is not this"). Over-triggering is the safe direction and the spec
  accepts its cost knowingly, but SC-016a's bar is **0**, and this run is 2.
- **SC-004 confirmation, trial 3 of 3** → a bare "ok" after a slot offer was classified
  `small_talk`. Worse than the routing: the small-talk reply then read a practitioner's
  name and the appointment options off the conversation history and offered them back
  ("Which would you prefer — a general practice appointment with Dr …"). **FR-012 forbids
  naming a practitioner or an appointment**, and the node can do it because it is given
  the history, so "given nothing factual" is true of its *prompt* and not of its *input*.
- **D10, D12** labelled `distress`, classified `urgent_condition` + `distress`. Both stop
  and both call staff; only the recorded cause differs. Not counted as failures — the
  labelled answer is arguably the weaker one.
- **B15** "Do you do blood tests on Saturdays?" → `booking` rather than `faq_question`.
  Both are servable specialists, so SC-003 still holds for it; recorded as a labelling
  disagreement rather than a defect.

**What this run establishes**: routing, silencing, marks and the fixed texts behave
exactly as the offline suite says. Every failure above is in the *judgement* — the
classifier's prompt — or in one interaction the offline suite cannot see (C16). Those are
the two things a live measurement exists to find.

#### 2026-09-08 — second run, after the prompt fixes the first run motivated

The first run's four failures were all in the classifier's judgement, so all four fixes are prompt
changes; no routing, escalation or reply code changed. Every number below comes from **one** prompt
(the four edits below, all in place), against `claude-haiku-4-5-20251001`.

| Criterion | Run 1 | Run 2 | |
|---|---|---|---|
| SC-001 | 32/32 | **32/32** | Set A |
| SC-003 | 19/20 | **20/20** | Set B |
| SC-004 | 3/3, 2/3 | **3/3, 3/3** | closing, confirmation |
| SC-005 | 0 violations | **0 violations** | 32 replies scanned |
| SC-006 | 0 | **0** | Set D |
| SC-011 | 15/16 | **16/16** | Set C |
| SC-015 | 21/21 | **22/22** | Sets D1 + D2 |
| SC-016a | 2 stopped | **0 stopped** | Set E, bar is 0 |

**What changed, and why each was a prompt fix rather than a code fix:**

1. **`small_talk` was swallowing questions.** The label now says a message asking anything about the
   clinic, an appointment, a practitioner or the patient's care is never small talk however short,
   and a bare "ok"/"yes"/"sure" answering a question the assistant just asked belongs to whatever
   was asked. Fixes B11 and the SC-004 confirmation trial.
2. **`escalate_to_staff` was silencing a not-authorized turn.** The tool's description now refuses
   the case the booking model reached for - a receipt, a letter, a prescription, a billing
   correction - and says in the same sentence that calling it stops the assistant. The tool records
   `patient_asked_for_person`, so it must fire only when that is true. Fixes C16.
3. **`urgent_condition` was firing on hyperbole and on the past tense**, then, once tightened,
   *stopped* firing on "my chest hurts, can I see someone today?" - a recall loss on the one label
   where recall is the point. The final wording names the red flags explicitly (chest pain,
   difficulty breathing, heavy bleeding, suspected overdose, fainting, a child who cannot be roused,
   stroke signs, sudden severe pain), says they hold however calmly they are put and even when an
   appointment is also requested, and keeps the exclusions for hyperbole and for anything past.
4. **Two boundaries the first run did not reach**, found while re-testing: `distress` fired on a bare
   "Oh no" (now: a brief exclamation is small talk, distress is about their health or care), and
   `booking_for_another` fired on "book me in with whoever my daughter saw last time" (now: the other
   person must be the one the appointment is *for*).

**One spec change came out of this**: FR-003's discriminator was "whether the message requests
anything", which made an off-topic question ("what is the weather in Paris?") an escalation. It is
now "anything **the clinic could act on**", with FR-003a and FR-003b naming the two sides. Paging a
person about the weather is the queue noise this phase exists to remove.

**Still open, deliberately**: the small-talk node can name a fact it read from the conversation
history (observed once in run 1, where it offered a practitioner by name). Its *prompt* is given
nothing factual, but its *input* is the history. Not addressed here - it needs a decision about what
history that node should see, which is a design question rather than a wording one.
