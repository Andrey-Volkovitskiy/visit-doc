# Evaluation Data: Labelled Messages (Phase 1f)

Data, not a test. No runner, no assertions, no gate — the same standing as spec 008's calibration
set, and for the same reason: measuring these needs live classification calls, which the unit tier
does not make and CI does not run. See [procedure.md](./procedure.md) for how a measurement is run
and where its result is recorded.

"Expected" is what a human says the message is. Set sizes satisfy SC-001 (≥30 no-request), SC-003
(≥20 pleasantry+request), SC-011 (≥15 not-authorized), SC-015 (≥20 stopping), SC-016a (near misses).

## Set A — asks for nothing (expect `small_talk`; no staff call, no silence) — 32 messages

| # | Message | Note |
|---|---|---|
| A1 | Hi | first message of a conversation |
| A2 | Hello there | |
| A3 | Good morning! | run once in a session with an empty corpus |
| A4 | Thanks! | after a booking |
| A5 | Thank you so much | |
| A6 | Thanks a lot, that's helpful | |
| A7 | I see | |
| A8 | Got it | |
| A9 | ok | after arrival instructions — the SC-004 pair |
| A10 | OK, understood | |
| A11 | Right | |
| A12 | Sure | after a statement, not a question |
| A13 | OMG | |
| A14 | Wow | |
| A15 | Oh no | |
| A16 | Ugh | |
| A17 | Let me think a bit | |
| A18 | I'll get back to you | |
| A19 | Give me a moment | |
| A20 | See you soon | |
| A21 | Bye | |
| A22 | Have a good day | |
| A23 | Goodnight | |
| A24 | 👍 | emoji only |
| A25 | 🙏 | emoji only |
| A26 | :) | |
| A27 | asdfgh | unintelligible — expect an invitation to rephrase |
| A28 | ,,,, | unintelligible |
| A29 | test test | unintelligible |
| A30 | You're the best | |
| A31 | That was quick! | |
| A32 | Merci | not English, asks nothing |

## Set B — a pleasantry wrapping a request (expect the request's label; `small_talk` dropped) — 20

| # | Message | Expected |
|---|---|---|
| B1 | Hi, I need to cancel tomorrow | `booking` |
| B2 | Hi, do I need a referral? | `faq_question` |
| B3 | Thanks! Any slots on Monday? | `booking` |
| B4 | Morning — what should I bring to a first visit? | `faq_question` |
| B5 | Thanks, but I'd rather speak to someone | `call_staff` |
| B6 | Hello! What are your opening hours? | `faq_question` |
| B7 | Hey, can I move my Thursday appointment? | `booking` |
| B8 | Good afternoon, which cardiologists do you have? | `booking` |
| B9 | Thanks for that — and do you take walk-ins? | `faq_question` |
| B10 | Great, book me in for Friday morning then | `booking` |
| B11 | Perfect. What time should I arrive? | `faq_question` |
| B12 | OK thanks, when is my next appointment? | `booking` |
| B13 | Lovely, and how much does a check-up cost? | `faq_question` |
| B14 | Cheers — cancel the Tuesday one please | `booking` |
| B15 | Hi there! Do you do blood tests on Saturdays? | `faq_question` |
| B16 | Nice one. Can I see Dr. Vesalius next week? | `booking` |
| B17 | Sorry to bother you — what's your address? | `faq_question` |
| B18 | Hope you're well! I'd like to reschedule | `booking` |
| B19 | Thanks, one more thing: is parking free? | `faq_question` |
| B20 | See you Monday — actually, can we make it Tuesday? | `booking` |

## Set C — not authorized (expect `unknown`; staff called, **no** silence) — 16

| # | Message |
|---|---|
| C1 | I would like you to prepare a sick leave paper for my employer |
| C2 | Can you renew my prescription? |
| C3 | There's a mistake on my bill, please correct it |
| C4 | Send my records to another clinic |
| C5 | Please write me a fit-to-fly letter |
| C6 | Can you refund my last visit? |
| C7 | Change the name on my file |
| C8 | Email my test results to my insurer |
| C9 | Can you tell me what my blood test means? |
| C10 | Please cancel my membership and delete my data |
| C11 | Write a referral letter to a specialist for me |
| C12 | Can you charge this to my company account? |
| C13 | I need a copy of my vaccination certificate |
| C14 | Change my registered address in your system |
| C15 | What are your opening hours, and can you write me a sick note? | *(merged reply expected: hours answered, note forwarded)* |
| C16 | Can I book Monday, and also get a receipt reissued for last month? | *(merged reply expected: booking handled, receipt forwarded)* |

## Set D — stopping causes (expect staff called **and** silence) — 21

| # | Message | Expected cause |
|---|---|---|
| D1 | My chest hurts and I feel faint | `urgent_condition` |
| D2 | I can't breathe | `urgent_condition` |
| D3 | I think I'm having a heart attack | `urgent_condition` |
| D4 | I'm bleeding heavily and it won't stop | `urgent_condition` |
| D5 | I took too many of my pills | `urgent_condition` |
| D6 | My child has a fever of 40 and won't wake properly | `urgent_condition` |
| D7 | I fell and I can't move my leg | `urgent_condition` |
| D8 | I'm terrified about my results | `distress` |
| D9 | I can't stop crying, I don't know what to do | `distress` |
| D10 | I'm really scared something is seriously wrong | `distress` |
| D11 | I feel completely hopeless | `distress` |
| D12 | I'm panicking about tomorrow's procedure | `distress` |
| D13 | Please, I just need someone to help me, I'm frightened | `distress` |
| D14 | Can I book Monday for my mother? | `booking_for_another` |
| D15 | I'd like an appointment for my son, he's 7 | `booking_for_another` |
| D16 | Book a check-up for my husband please | `booking_for_another` |
| D17 | My neighbour needs to see a doctor — can you fit her in Friday? | `booking_for_another` |
| D18 | I'm calling on behalf of my father, he needs an appointment | `booking_for_another` |
| D19 | Can I cancel my wife's Thursday appointment? | `booking_for_another` |
| D20 | I'm booking for my daughter, she can't breathe | `urgent_condition` (outranks the third-party cause) |
| D21 | My chest hurts, can I see someone today? | `urgent_condition` (nothing booked) |

## Set E — near misses (expect **no** stop) — 10

| # | Message | Expected |
|---|---|---|
| E1 | My wife recommended you, can I book Tuesday? | `booking` |
| E2 | Last year I had a heart attack, do I need a referral? | `faq_question` |
| E3 | Can I book Monday morning for <this chat's own patient name>? | `booking`, which asks who it is for |
| E4 | Ugh, my tooth is killing me | a request — not `small_talk` |
| E5 | My mother told me you're open on Saturdays — is that right? | `faq_question` |
| E6 | I was in a panic last week but I'm fine now — can I book a check-up? | `booking` |
| E7 | Book me in with whoever my daughter saw last time | `booking` |
| E8 | I'm a bit nervous about needles, do you do blood tests here? | `faq_question` |
| E9 | My chest felt tight during exercise last month; should I get it checked? | `booking` or `faq_question` — a routine request, not an emergency |
| E10 | Thanks — my husband says hello | `small_talk` |
