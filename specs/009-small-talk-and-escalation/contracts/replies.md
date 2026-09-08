# Contract: What the Patient Is Told

## Fixed replies (constants, no model call)

One constant per cause, held beside the cause table. Wording is a copy decision; the numbered
obligations are the contract, and a reply that misses one is a defect.

| Cause | Must convey | Must never |
|---|---|---|
| `patient_asked_for_person` | (existing text, unchanged) | — |
| `not_authorized` | 1. the assistant is not authorized to handle this; 2. it has been forwarded to staff, who will follow up; 3. they may ask for anything else meanwhile | promise a time; imply the assistant will handle it after all |
| `urgent_condition` | 1. if this is an emergency, contact emergency services now; 2. staff have been notified | assess severity, diagnose, reassure about the condition, or advise what to do medically |
| `distress` | 1. the message has been passed to a member of staff; 2. a person will follow up | diagnose, minimize, or promise a time |
| `booking_for_another_person` | 1. appointments here are booked for the person in this chat; 2. a staff member will arrange this one; 3. staff have been notified | claim anything was booked, held, or reserved |

**Emergency wording is not a hedge.** FR-044 requires pointing at emergency services precisely
because the assistant cannot judge whether it is one — the sentence is safe under every reading of
the message, which is what makes a constant the right instrument.

## The small-talk reply (generated, bounded)

- One call, `CLASSIFICATION_MODEL`, short cap, bounded history (`CONTEXT_TURNS`), streamed like any
  other single-specialist reply.
- **May**: acknowledge, thank, greet, close, and offer help in general terms.
- **Must not**: state a policy, a price, a date or time, a practitioner, or an appointment; promise
  a callback; say staff have been notified; give clinical content of any kind.
- **Given nothing factual, so it can invent nothing factual**: the node receives conversation text
  only — no corpus, no tools, no patient record beyond what the history already shows.
- An unintelligible message is answered by inviting a rephrase; nothing branches on that, it is a
  prompt case.
- The turn's completion carries `answer_source=small_talk`, `faq_verdict=None`, `citations=[]`, and
  no attention mark.

## The merged not-authorized notice

When `unknown` accompanies a servable intent, no fixed text is emitted. The merge step receives the
servable result(s) plus a flag saying a notice is owed, and its output must convey the three
`not_authorized` obligations above alongside the served answer. It must not claim the unserved
request was handled, and must not promise when staff will respond.

**Test shape**: the solo reply is asserted byte-for-byte; the merged one is asserted on obligations
with a stubbed composer, which is what makes both testable offline (SC-012a).

## Ordering guarantee

A turn that stops the conversation still delivers its reply *before* silence begins. The escalation
is applied after the graph completes; the constant is streamed during it. The patient always sees
why the assistant went quiet.
