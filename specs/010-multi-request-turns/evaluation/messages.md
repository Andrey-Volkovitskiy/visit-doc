# The labelled sets

Five sets, 98 messages, each with the segmentation a human assigned it. They are **evaluation data
for this phase, not a golden dataset** — the seed Phase 2's dataset can grow from, as spec 008's
calibration set and spec 009's labelled sets were. The measurement they support, and every run's
result, is in [`procedure.md`](./procedure.md); the JSON is in [`inputs/`](./inputs).

Every set is written against the **starter corpus** (`chat.rag.default_corpus`), so a question about
hours, location, parking, referrals, what to bring, insurance, prices, payment or arrival time is one
the clinic can actually answer. That matters for `compound.json`, whose whole claim is that each half
is individually answerable.

## `compound.json` — 12 messages (SC-001)

The headline defect. Each message asks two or three things the corpus answers *separately* today, and
which the corpus could not answer when embedded as one sentence.

> "What are your clinic hours, and what should I bring to my first appointment?"
> "Do you do telehealth, and how much is a dentist visit if I pay myself?"

Ten pairs and two triples. `expected` is the number of requests.

## `mixed.json` — 22 messages (SC-002)

A corpus question paired with a scheduling request — the pair that damages both halves today: the FAQ
node abstains on a clause no corpus could answer and pages a person for it, and the booking node
answers the policy half from nothing.

> "What are your clinic hours, and what dentist slots are free tomorrow?"
> "Where do I park, and what appointments do I have booked?"

Twenty pairs and two triples, each mixing `faq_question` with `booking`.

## `multi.json` — 26 messages (SC-003, ≥90%)

The hard cases, and the only set with a bar below 100%. It carries the shapes a segmenter gets wrong:

- **Ellipsis** — "Do you have parking, and is it free?" The second half must be restated as a
  question about parking, or it retrieves nothing.
- **A shared subject** — "Can I see Dr. Vesalius on Friday, and what does he specialize in?"
- **A follow-up clause** — "Do you take Medicare? What if you do not?" Dropping it loses a question.
- **A pleasantry beside a request** — "Hi! What should I bring, and can I book Monday?" The greeting
  is not a segment.
- **Above the cap** — C26 asks four things. Recorded as expecting three; what actually happens is in
  `procedure.md`.

## `single.json` — 22 messages (SC-004, ≥95%)

The set that bounds over-splitting, which is the failure this phase itself introduces and the one
that lands on the common case. Long messages, comma-heavy ones, a request asked twice in two ways, a
bare "ok" after arrival instructions, and two pleasantries — every one of them exactly one segment.

> "I have been meaning to ask for a while, and I hope this is the right place to ask, but what are
> your opening hours on Saturdays?"
> "What time do you open? When can I come in the morning?"

## `overriding.json` — 16 messages (SC-012)

A multi-request message that also carries one of the intents that takes the whole turn: an urgent
condition, distress, a booking for another person, a request for a human, or a request the assistant
is not authorized to serve. `expected_label` is the label that must appear among the segments; what
the router then does with it is unit-tested offline, not measured here.

> "What are your hours? Also my chest hurts and I feel faint."
> "Do you take Aetna? Also please write me a sick note for my employer."

## What these sets deliberately do not cover

- **Whether the turn then answers correctly.** That is retrieval's business, and Phase 1e's
  calibration set is where a floor is argued about.
- **Routing, marks, silence and replies** — all of it deterministic, all of it unit-tested against a
  stubbed classifier, none of it needing a live call.
- **Anything Phase 1h owns**: per-request verdicts and partial serving.
