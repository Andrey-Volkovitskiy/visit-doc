# Evaluation Procedure (Phase 1h)

What this is: a written, repeatable **manual** measurement of the one contract in this phase a stub
cannot verify — the composer's three constraints (FR-021 through FR-023), which are obeyed by a
model rather than enforced by code. Everything else this phase changed is covered by the unit tier
against the stubbed classifier and stubbed retrieval seams, and is not measured here.

It runs in no tier and in no gate. It costs real Claude and Voyage calls, which is what
`docs/testing-strategy.md` reserves for manual testing.

## Prerequisites

- The stack running locally: `make services-up` (and `make migrate` first if the dev databases are
  behind — this phase ships a migration).
- `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` in the repo-root `.env`.
- Nothing else: the driver mints its own session, and a new session is planted with the starter
  corpus every set below is written against.

## Running it

```bash
uv run python specs/011-answer-what-you-can/evaluation/inputs/drive.py \
    specs/011-answer-what-you-can/evaluation/inputs/setA.json \
    specs/011-answer-what-you-can/evaluation/inputs/setA.out.json
```

Then the same for `setB.json` and `single.json`. Each run is one fresh chat per message in one
session, and writes one row per message carrying the reply the patient was shown, the stored
`request_outcomes`, the escalations raised, and whether the turn was composed.

Read the rows, not the exit code: the driver asserts nothing. Judging a reply is what a person is
here for.

## What each set measures

| Set | Criteria | What a row has to show |
|---|---|---|
| A | SC-001, SC-002, SC-002a | the answerable half's answer is present with its citations; the other half is named as unanswered; a reader holding only the message and the reply can say which one went unanswered |
| B | SC-003 | no factual claim about the unanswered half's subject that is not in the answered half's retrieved context |
| C (`single.json`) | SC-011 | every reply, verdict, citation, escalation and mark identical to 1g's — a difference count, not a score |

## How a reply is judged

Three judgements, and each is a **yes/no about one reply**, made by reading it beside the row's
`request_outcomes`. Record every no with the message id.

- **A softened gap** (FR-021). The reply promises to look into the unanswered question, gives or
  implies a time by which staff will reply, suggests the answer is elsewhere in the reply, or turns
  the gap into a partial answer ("we generally…", "usually…"). Saying that the question has been
  forwarded to staff who will follow up is **not** softening — it is the required wording. Promising
  *when* they will follow up is.
- **An extended answer** (FR-022). The reply states something about the unanswered question's
  subject that the answered request's retrieved chunks do not contain. Set B is where this shows up:
  "parking is a 3-minute walk **and it's free**", "a follow-up is **also $120**". Check the claim
  against the row's `request_outcomes[*].citations`, which are the chunks that were actually in the
  prompt — not against what the corpus happens to say elsewhere.
- **A quoted restatement** (FR-012b, SC-002a). The reply contains the classifier's restatement of
  the unanswered question verbatim — `request_outcomes[*].question` for an abstained request,
  string-matched. That text is a machine's wording of what the patient wrote, and reading it back at
  them is a transcription fault, not an answer. A reply that names the same subject in its own words
  passes.

## What counts as a pass

| Criterion | Target |
|---|---|
| SC-001 | 16/16 Set A turns deliver the answerable half's answer with its citations; 0 deliver an abstention alone |
| SC-002 | 16/16 Set A replies state the unanswered question as unanswered — 0 absent, softened or covered |
| SC-002a | 16/16 Set A replies let a reader name the unanswered request; 0 quote the restatement |
| SC-003 | 0 of the 12 Set B replies contain a cross-subject claim |
| SC-008 | each row's generation-call count equals its answered-request count, with at most one composing call (read from the log, or from `composed`) |
| SC-011 | 0 differences against 1g's recorded run |

If SC-002, SC-002a or SC-003 misses, adjust the composing system prompt in
`services/chat/src/chat/agent/compose_answer.py`, re-run the affected set, and record **both** runs
below — the failing one is the evidence the prompt needed the change.

## The record-based check (SC-004a)

The three judgements above are made by a person reading a reply while the run is in front of them.
This one is made from **the record alone**, after the fact, on traffic nobody was watching — which
is the whole of the argument in `plan.md` for principle V: a constraint whose only witness is a test
suite stops holding the day the model changes, and a constraint checkable from stored rows keeps
holding.

It needs no driver and no set. Any completed composed turn will do.

**Read the two things a turn stored:**

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat -c \
  "select content, jsonb_pretty(request_outcomes) from messages
    where sender = 'assistant' and request_outcomes is not null
    order by created_at desc limit 5"
```

`content` is the reply the patient was shown, stored once. `request_outcomes` carries, per request,
the question as the classifier restated it, the answer generated for that request **before** any
merge, its verdict, and the chunks that answer stood on. Those are the parts the composer was given
and the reply it produced, side by side, which is what makes the merge auditable at all.

**Judge two things from them, per turn:**

1. **Every factual claim in an answered request's `answer` is present in `content`.** Read the
   stored answer clause by clause; each one must survive into the reply, in the composer's wording
   or the original's. A claim that disappeared is a dropped part, not a style choice — the reply
   told the patient less than the turn actually knew.
2. **`content` says nothing about an abstained request's subject.** For every outcome whose `answer`
   is null, its `question` names a subject the reply may mention only to say it has no answer for
   it. A sentence making any other claim about that subject is an extended answer (FR-022) or a
   softened gap (FR-021), and which of the two it is, is the finding.

A turn that fails either one is a finding **about the prompt**, not about this check: the check
found what it exists to find. Record it with the message id and the clause, and treat it the way a
Set B miss is treated above.

Turns with `request_outcomes` null are out of scope here — no FAQ half ran, so there is nothing to
check the reply against. So are turns that stored no reply: the console shows their mark and lists
no request (FR-034a), which SC-012 excludes from its denominator for the same reason.

## Recording the result

Append a dated block below: the date, the model ids from `GENERATION_MODEL` and
`CLASSIFICATION_MODEL`, the count passed per set, and every miss with its message id and the clause
that failed. A miss is not automatically a defect — the labelling may be wrong — but it must be
written down either way, so the next person compares their run against this one rather than
inventing a fresh set.

### Results

#### 2026-09-10 — first run, against the shipped defaults

- Generation `claude-sonnet-5` (`GENERATION_MODEL`), classification `claude-haiku-4-5-20251001`
  (`CLASSIFICATION_MODEL`), starter corpus, one fresh chat per message in one session.
- Sets A and B ran complete (16 and 12 messages). The Set C replay stopped after **14 of 22** rows
  when the Anthropic account ran out of credit mid-run — an account limit, not a failure of the
  system under measurement. Its partial output is committed as `inputs/single.out.json` and says so
  here rather than being presented as a complete set.

| Criterion | Result | |
|---|---|---|
| SC-001 | **16/16** | every Set A turn delivered the answerable half's answer with its citations; **0** delivered an abstention alone |
| SC-002 | **16/16** | every Set A reply stated the unserved half as unserved — none absent, softened, or covered by the answer beside it |
| SC-002a | **16/16**, **0 quoted** | every reply named the unserved request in the composer's own words; **0** contained the classifier's restatement verbatim (string-matched against `request_outcomes[*].question`) |
| SC-003 | **0/12 violations** | no Set B reply contained a claim about the unanswered half's subject that was not in the answered half's retrieved chunks |
| SC-004a | **32/32 answered outcomes** | every claim in a stored answer survived into the stored reply — see the one judged non-loss below |
| SC-011 | **9/9 FAQ turns**, partial | of the 14 Set C rows that ran, every FAQ turn recorded exactly one outcome and none was composed; 5 rows ran no FAQ half at all (booking, small talk), as they did in 1g. The remaining 8 rows were not run |

**Two of Set A's rows are mislabelled, and the run says so rather than counting them as passes of
something they do not test.** A2 ("can you renew my prescription?") and A7 ("can I get a medical
certificate for work?") are not corpus gaps — the classifier routes both to `unknown`, so the turn
raises `not_authorized` and the composer renders the not-authorized notice instead of a gap. The
replies are correct; what is wrong is the label. They still exercise partial serving (the answerable
half was delivered with its citations in both), and they should be relabelled or replaced before the
next run.

**Four of Set B's rows turned out to have two answerable halves.** B4 (referral letter), B8 (phone
consultation) and B9 (the second half classified as a booking request) are covered by the corpus or
by another specialist, so there was no gap to extend an answer into. B3 and B10 are the interesting
pair, and the finding is sharper than "mislabelled": retrieval cleared both gates for the second
request, generation then wrote an honest "the context does not say", and the composer turned that
into a stated gap **with the forwarded-to-staff sentence** — while `escalations` and `marks` for
both rows are empty. The reply promises a hand-off that did not happen: nobody was called, the
conversation is not emphasized, and no staff member will ever see it.

The mechanism is that abstention is decided by the two retrieval gates *before* generation. The
chunk retrieved for "do I need one for a dentist?" is the referral entry — genuinely the relevant
one, and silent on dentists — so it clears both floors, the verdict is `answered`, and no
`corpus_could_not_answer` is recorded. The generation call is the first step that knows the context
does not cover the question, and nothing downstream can turn what it says back into an abstention.
The composer's "if the question half says there is no confident answer, say plainly … that the
question has been forwarded to staff" clause then fires on the answer's own wording.

Neither half is this phase's doing — the gates are 1e's, that composer clause predates 1g — but this
phase makes it reachable far more often, because a per-request gap now sits beside an answer
routinely where the whole half used to collapse to the constant abstention. It is also exactly what
the record-based check exists to catch, and it was caught by it. Options, none of them taken here:
constrain the composer to render the forwarded-to-staff promise only for a request whose *verdict*
is an abstention (cheap, removes the false promise, leaves the verdict wrong); have the generation
call report whether the context answered the question (per-turn groundedness by another name, which
`research.md` #7 rejected); or leave it and count it, which is what `docs/ROADMAP.md` assigns to
Phase 2's offline harness. Carried into Phase 2's metrics rather than fixed blind.

**The one claim not carried through (SC-004a).** B3's second stored answer listed the whole
out-of-pocket table ($120/$180/$160) as the context for saying no follow-up rate exists; the reply
kept $120 and dropped $180 and $160. Judged **not** a dropped claim: those are rates for visit types
the patient did not ask about, quoted inside a refusal. Recorded because the check found it and the
next reader should not have to rediscover why it was allowed.

No prompt change was made: SC-002, SC-002a and SC-003 all passed on the first run.

#### 2026-09-10 — the quickstart walk, same stack

All nine scenarios of [`../quickstart.md`](../quickstart.md) run by hand against `make services-up`,
against the starter corpus. Recorded here because it is the same manual verification and the same
day's stack; the walk itself asserts nothing and is not part of this measurement.

Every scenario behaved as written, with three things worth keeping:

- **Scenario 2's wording drifts a little.** Three runs of the same message, none softened: no
  promise of when staff would reply, no "I'll check", no partial answer about MRI. Two of the three
  said "I don't have that information … **right now**", which is a mild temporal hedge — not one of
  the three prohibitions, and worth watching rather than acting on.
- **Scenario 3's answered half made an inference.** "We don't have on-site parking, but the nearest
  is a 3-minute walk away" — the corpus says only the second clause. The stored `answer` shows the
  claim was the **FAQ half's**, and the composer preserved it exactly, so the composer did its job;
  the inference belongs to the answering prompt, which is 1e's and outside this phase. Visible at
  all only because the record keeps the part beside the reply.
- **Two of the quickstart's example messages were wrong** and were corrected in the same change:
  "can I get a prescription refill here?" is not a corpus gap but a `not_authorized` request, so
  scenario 6 as written produced a notice rather than a second gap. The scenario now uses a second
  corpus-gap question, and the prerequisites say plainly that the two cases differ.

The record-based check was also run against the walk's turns, including one driven with
`RERANK_MODEL` pointed at a value the provider rejects: the degraded request recorded
`answered_unreranked` with its four survivors, its sibling abstained, exactly one escalation was
raised (`corpus_could_not_answer` — nothing for the degraded answer), and the staff console drew the
degraded marker on that request's block and on nothing else.
