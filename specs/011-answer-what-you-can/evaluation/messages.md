# Evaluation Data: Labelled Messages (Phase 1h)

Data, not a test. No runner in any tier, no assertions, no gate — the same standing spec 009's
labelled set and spec 010's segmentation set have, and for the same reason: what these sets measure
is a **model-obeyed** contract (FR-021 through FR-023), which cannot be asserted offline against a
stub. See [procedure.md](./procedure.md) for how a measurement is run and where its result is
recorded.

Everything else this phase changed — the record's shape, the part count, the escalation, the
collapse, the call counts — is covered by the unit tier against the stubbed classifier and stubbed
retrieval seams, and is deliberately not measured here.

Every set is written against the **starter corpus** (`chat.rag.default_corpus.DEFAULT_FAQ_ENTRIES`,
nine entries), which is what a new session begins with. "Answerable" and "unanswerable" below are
claims about *that* corpus: a message's unanswerable half is one no entry covers, so retrieval's
gates stop it and the request abstains.

Set sizes satisfy SC-001/SC-002/SC-002a (≥15 two-question messages, exactly one half answerable),
SC-003 (≥10 answer+gap pairs whose subjects are adjacent), and SC-011 (1g's single-request set,
replayed).

---

## Set A — one answerable half, one gap — 16 messages

Exactly one question of each message is answerable from the starter corpus. What is being measured
is that the answerable half is delivered *with its citations* and the other half is named as
unanswered — where today the whole turn abstains.

Committed as [`inputs/setA.json`](./inputs/setA.json).

| # | Message | Answerable half | The gap |
|---|---|---|---|
| A1 | What are your clinic hours, and do you validate parking? | clinic hours | parking validation |
| A2 | Do I need a referral to see a specialist, and can you renew my prescription? | referral not needed | prescription renewal |
| A3 | Is there an interpreter available, and what should I bring to my first appointment? | what to bring | interpreter availability |
| A4 | Do you offer telehealth appointments, and is the clinic wheelchair accessible? | telehealth | wheelchair access |
| A5 | Which insurance plans do you accept, and how long does a blood test result take? | insurance plans | lab result turnaround |
| A6 | How much is a GP visit out of pocket, and do you charge a no-show fee? | out-of-pocket rates | no-show fee |
| A7 | When is payment due, and can I get a medical certificate for work? | payment due | medical certificate |
| A8 | How early should I arrive, and is there Wi-Fi in the waiting room? | arrival time | waiting-room Wi-Fi |
| A9 | Do you see children under five, and what are your opening hours? | clinic hours | paediatric care |
| A10 | Where are you located, and do you have an MRI scanner on site? | clinic location | MRI availability |
| A11 | What happens if my insurer is out-of-network, and do you offer flu vaccinations? | out-of-network | vaccinations |
| A12 | Can I pay with an HSA card, and can I bring my guide dog? | payment methods | assistance animals |
| A13 | Do you do telehealth, and how long is the usual wait for a same-day slot? | telehealth | same-day waiting time |
| A14 | What should I bring to a first visit, and do I need to fast before a blood test? | what to bring | fasting before a blood test |
| A15 | Is parking near the clinic, and do you offer sedation for dental work? | parking location | dental sedation |
| A16 | How much does a dentist appointment cost, and can I claim it through Medicare dental? | out-of-pocket rates | Medicare dental claims |

Two orders are deliberately mixed in: A3, A9 and A11 put the unanswerable half **first**, so a reply
that names the gap only when it comes second fails visibly rather than passing by position.

---

## Set B — the answer and the gap are about the same subject — 12 messages

Both halves are about one subject, and only one of them is covered. This is the set SC-003 is
measured on: adjacency is what makes "extend the answer to cover the gap" tempting, and a reply that
says the parking garage is free, or that a follow-up costs $120 because a first visit does, has
invented a claim the corpus never made.

Committed as [`inputs/setB.json`](./inputs/setB.json).

| # | Message | Subject | Answerable | The gap |
|---|---|---|---|---|
| B1 | What are your clinic hours, and are you open on public holidays? | opening hours | Mon–Sat 9–6 | public holidays |
| B2 | Where is the nearest parking, and how much does it cost per hour? | parking | Mega Mall garage, 3-minute walk | parking price |
| B3 | How much is a GP visit out of pocket, and how much is a follow-up visit? | visit prices | $120 GP | follow-up price |
| B4 | What should I bring to my first appointment, and do I need to bring a referral letter? | what to bring | ID, insurance card, prior results | a referral letter as a document |
| B5 | Which insurance plans do you accept, and does that include my dental cover? | insurance | BCBS, Aetna, Cigna, UHC, Medicare | dental cover specifics |
| B6 | How early should I arrive for a first visit, and how long does the paperwork take? | arrival and registration | 15 minutes early | paperwork duration |
| B7 | When is payment due, and can I set up a payment plan? | payment | due at time of service | payment plans |
| B8 | Do you offer telehealth, and can I get a phone call instead of coming in? | remote consultations | in-person only | phone consultations |
| B9 | What are your hours on Saturday, and what time does the last appointment start? | opening hours | 9–6 Mon–Sat | last appointment slot |
| B10 | Do I need a referral for a specialist, and do I need one for a dentist? | referrals | no referral for a specialist | dentist referral |
| B11 | What can I pay with, and do you take American Express contactless on a phone? | payment methods | cash, Visa, Mastercard, Amex, FSA/HSA | contactless on a phone |
| B12 | What is the out-of-pocket rate for a specialist, and is a scan included in that? | specialist pricing | $160 specialist | whether a scan is included |

---

## Set C — 1g's single-request set, replayed — 22 messages

The same messages spec 010 measured its segmentation against, replayed unchanged. SC-011 is a
**difference count, not a score**: every reply, verdict, citation, escalation and mark must match
what 1g produced. Any difference is a regression, whatever it looks like.

Committed as [`inputs/single.json`](./inputs/single.json), derived from
[`specs/010-multi-request-turns/evaluation/inputs/single.json`](../../010-multi-request-turns/evaluation/inputs/single.json)
rather than retyped, so the two sets cannot drift apart.

Each row expects exactly **one** request outcome — which is also what "a single-request turn is
unchanged" means once the record moved onto the request.
