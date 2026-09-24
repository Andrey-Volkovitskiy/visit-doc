# The six checks that needed a stack answering for real

The remainder of `quickstart.md`'s Tier 2 — everything that could not be answered against the
built SPA with the API stubbed, because each needs content the agent actually produces or a poll
tick that actually changes something. Run **2026-09-24** against the running stack (chat `:8000`,
scheduler `:8001`, frontend dev `:5173`, Postgres and Qdrant in Docker), driven by Playwright so
the numbers are measured rather than judged.

**What it cost.** Two live turns — one answered, one abstained — so two Claude generations, their
Voyage embeddings and retrieval, and the Langfuse units for both (keys were set, so both turns were
traced). Everything else was free: chat creation, staff posts, the assistant switch and the edit
views spend nothing, and a **paused conversation stores a patient message and generates no reply at
all**, which is what made FR-017 checkable for nothing. One session was provisioned and reused
across all three scripts via a saved cookie jar, so the starter corpus was seeded once.

`greyscale.png` and `shot-paused.png` beside this file are from these runs.

---

## Overflow — FR-012, FR-012a

Six chats in one session.

| | |
|---|---|
| Shown directly | `Emily Dickinson`, `Mark Twain`, `Fyodor Dostoevsky`, `Leo Tolstoy` — **four**, the budget |
| Behind the overflow | `Charles Dickens`, `Jane Austen` |
| After opening `Jane Austen` from the overflow | `Emily Dickinson`, `Mark Twain`, `Fyodor Dostoevsky`, **`Jane Austen`** |
| Behind the overflow after | `Leo Tolstoy`, `Charles Dickens` |

The open chat took the **last** direct position, the tab it displaced (`Leo Tolstoy`) moved into
the overflow, and it went in at the place the server's order gives it rather than at the front —
which is the half of FR-012a that a looser reading would get wrong. Still exactly four direct tabs
throughout.

## Edit views — FR-035a, FR-035b

- Choosing edit **replaced the tab's content**: the edit view is visible, the roster is gone, and
  the view is not inside a `role="dialog"` — it is not an overlay and does not expand in place.
- The page header, the attention total and all three tabs stayed on screen and operable.
- The appointments stub reads *"The next seven days — Not yet available. This panel will list every
  appointment standing with William Osler over the next seven days…"*, and renders no list.
- **Leaving clean asked nothing** and returned to the roster.
- **Leaving dirty by the back control asked first**, and stayed put when refused.
- **Leaving dirty by choosing another tab asked first** — the gap convergence round 1 found and
  closed. The prompt appeared, Practitioners stayed open, the FAQ tab did *not* open, and the typed
  text was still `Dr. Grace Hopper`. Discarding completed the switch, and the roster still read
  `William Osler`, so nothing had been saved.

## Working indicator — FR-016, FR-017

**Shown, for a turn that gets a reply.** Measured from the click on Send to the indicator being in
the DOM: **155 ms**. SC-005 allows 500 ms. The patient's own message was on screen in the same
breath, which is what makes the number a property of the render rather than of the network.
Removed once the turn produced its reply.

**Absent, while paused, with nothing in its place.** A staff post pauses the assistant, so the
conversation was already paused; the switch was toggled to be sure and read `unchecked`, with
*"Quiet for another 1:58"* beside it and the explanation visible without hovering anything. A
message was then sent and the DOM watched for **6 seconds** — twelve times longer than the window
the indicator would have appeared in:

- indicator ever appeared: **no**
- the patient's message sits in the thread as sent, last in the thread, sender `patient`
- empty assistant bubbles standing in for the absent reply: **0**
- any "a person will reply / queued / waiting for" wording anywhere in the thread: **none**

That last pair is the whole of FR-017's second sentence, which forbids substituting anything.

## Streaming — FR-018

The assistant's reply **typed in**: sampling the last message's length every 250 ms showed it
growing in more than one step before settling at 128 characters, inside one bubble.

A staff message **arrived whole**: six samples of its length, all 61. No growth, and no indicator
was shown for it — a staff reply is a person writing, not a turn being awaited.

It is stored **once** and rendered in both threads — one copy in the patient pane, one in the
console. Worth writing down because the naive count says two, and "two" is what a duplicate-post
bug would also say.

## Two markers open at once — FR-028

The conversation held three evidence markers after the two turns (`served`, `needs-person`,
`needs-person` — the second question abstained, and the patient's message carries the mark).

Two were opened. After **~7 seconds, three or four ticks of the 2-second console poll**:

- both still report `aria-expanded="true"`
- both are still attached to **the same messages** they were opened on

That second line is the one that matters. The poll replaces the message array wholesale, and the
thread is keyed by message id precisely so React reconciles to the same component instances rather
than shifting every open block onto a different message. Keyed by index it would not, and this is
the check that would have caught it.

## Scroll rule, both threads — FR-015a, FR-015b

Arrivals were produced from a **second tab in the same session**, so the thread under test
*received* them rather than sending them — a send is the reader's own act and legitimately follows.

| Thread | Scrolled to the top, content arrives | Returned to the bottom, content arrives |
|---|---|---|
| Staff (`staff-thread`) | held at `scrollTop: 0` while `scrollHeight` grew | followed to `441` of `441` |
| Patient (`messages`) | held at `scrollTop: 0` while `scrollHeight` grew | followed to `379` of `379` |

Both threads hold position when the reader has scrolled up and resume following once they return —
FR-015b putting the console on the messenger's exact terms, which is what one shared predicate in
`src/lib/scroll.ts` buys.

---

## What this run did not cover

The greyscale reading is recorded separately in [`greyscale.md`](./greyscale.md) and was done
against stubbed data; nothing here supersedes it. Long-thread virtualisation, multi-user
concurrency and anything about the agent's *answers* are out of this feature's scope — it changed
no prompt and no network contract.
