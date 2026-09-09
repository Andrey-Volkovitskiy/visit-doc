# Contract: Routing and specialist input

How the turn's segments become specialists, what each specialist is handed, and when the composer
merges. **Every rule of Phase 1c and Phase 1f survives unchanged** — each is now read against the
segments rather than against the message, which is the whole of the change (FR-015).

## 1. Selection

Selection runs over the derived intent set, so the existing table and the existing function are
untouched (FR-010):

| Segment intent | Routes to |
|---|---|
| `faq_question` | the FAQ specialist |
| `booking` | the booking specialist |
| `small_talk` | the small-talk specialist |
| `urgent_condition`, `distress`, `call_staff`, `booking_for_another` | the hand-off, which takes the whole turn |
| `unknown` | the hand-off alone; a **notice** when something servable accompanies it |
| `classification_failed` | the FAQ specialist (the fallback path) |

## 2. Overrides, in force per segment

| Rule | Effect | Requirement |
|---|---|---|
| An explicit request for a human on **any** segment | Takes the whole turn: nothing retrieved, nothing booked, fixed hand-off text, assistant silent | FR-011 |
| An urgent condition, distress, or a booking for another person on **any** segment | Takes the whole turn and stops the conversation; every other segment suppressed | FR-012 |
| A `small_talk` segment beside any real request | Dropped; routes nowhere | FR-013 |
| An `unknown` segment beside something servable | The servable segments run; the notice is the composer's to render | FR-014 |
| An `unknown` segment alone | Hand-off, no retrieval, no generation | FR-014 |

Precedence between causes, which cause silences the assistant, which mark a staff reply clears, and
the one-escalation-per-turn shape are all unchanged (FR-015, FR-045).

**Belt and braces on small talk.** The segmenter is told not to emit a pleasantry segment beside a
request (FR-005b) *and* the router drops one if it appears (FR-013). The two fail independently, so
both hold — the same reasoning that puts the not-authorized rule in the booking prompt as well as in
the input slice.

## 3. What each specialist reads

| Specialist | Receives | Mechanism |
|---|---|---|
| FAQ | its own segments, **one run each** | each run substitutes its own segment text into the trailing conversation entry via `history.replace_trailing_entry` |
| Booking | its own segments, **joined, one run** | the same substitution, with the booking segments in place of the raw patient message |
| Small talk | the bounded history, as today | unchanged — a small-talk turn is a one-segment turn |

**The isolation is structural, not instructed** (FR-020, research D5). The entry that carried the
other specialist's clause is *replaced*, so that clause is not in the prompt to be answered. The FAQ
specialist already builds its prompt this way; this phase changes what goes in the `Question:` line
and adds the same call to the booking path.

Both specialists keep the bounded conversation history they receive today: history is context, the
segment is the request (FR-021).

**The booking prompt states it as well** (FR-023): the booking specialist holds no clinic knowledge
and answers nothing outside its own segments. The input slice and the instruction fail independently.

**One booking run, not one per segment** (FR-022). Two scheduling requests are one piece of work
against one set of records, and the tool loop already sequences them; two concurrent loops against
one patient's appointments would be a write-ordering problem invented for no requirement.

## 4. Streaming vs collecting

Decided at routing time from the reply parts the turn *could* produce:
`len(faq_segments) + (1 if booking) + (1 if notice)`.

| Count | Specialists |
|---|---|
| 1 | stream their own tokens and emit their own terminal event — today's path, byte for byte (FR-051, FR-070). The state key is `specialists_collect`, false here |
| ≥ 2 | collect; the composer streams |

## 5. Merging

Decided **when the composer runs**, from the parts that actually exist
([data-model.md §5.2](../data-model.md)):

| Parts | Behaviour |
|---|---|
| 1 | that part is the reply; **no composing call** — streamed by its specialist if the turn was routed as one part (FR-051), emitted by the composer if several parts collapsed to one (FR-051a) |
| ≥ 2 | one composed reply, under the composer's existing constraints |

The two decisions differ in exactly one case, and that case is why they are two decisions: a turn
with two FAQ segments collects (routing count 2), and if either segment abstains the FAQ half
collapses to a single abstention part (actual count 1). That reply is a constant by design, and a
composing call to paraphrase it would put a model in front of the one reply the design keeps
model-free.

**The composer's contract is unchanged.** It preserves every claim exactly, never fills a gap from
its own knowledge, and never re-reports citations — they are carried through from what was retrieved
(FR-050). This phase adds no constraint to it; the constraint an abstention-beside-an-answer needs is
Phase 1h's, which is why FR-042 does not produce that combination.

**Every servable segment is addressed** (FR-052) and each answer stays with the request it answers
(FR-053).

## 6. What does not change

- `AnswerSource` gains no member. A turn whose several segments went to one specialist is composed and
  records `MERGED`.
- No new node, no new graph edge, no new conditional route.
- A message arriving while the assistant is silent is still never classified, and therefore never
  segmented.
