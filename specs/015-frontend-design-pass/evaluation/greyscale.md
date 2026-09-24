# T064, T074 — colour independence, checked with the colour taken away

FR-005, FR-006 and SC-006. Two halves: a `grep` over the source for the reserved colour, and the
thing the grep cannot answer — whether a viewer who cannot perceive hue can still read the screen.
Run 2026-09-23. `colour.png` and `greyscale.png` beside this file are the same page, the second
with `html { filter: grayscale(1) }` applied.

**How it was populated without a backend.** Playwright stubbed the four API routes at the network,
so the page rendered the shape the real product produces — a patient message carrying an
`urgent_condition` mark, an assistant reply holding one answered and one abstained request, a staff
reply, and two conversations of which one needs a person — with **no stack running and no Claude or
Voyage call spent**. (Two traps worth recording: Playwright matches routes in *reverse*
registration order, so a catch-all registered last swallows every specific stub; and
`fetchChatHistory` reads `data.messages`, so a bare array is not a valid stub of that endpoint. The
second produced a white screen, and it was the stub that was wrong, not the app.)

## The grep half (FR-005)

Every use of `--color-attention` and of shadcn's `destructive` variant was read. All of them are
error banners or "a person is needed" markers — the two things the token means. The three delete
confirmations (`ChatList`, `PractitionerAdmin`, `FaqAdmin`) all **decline** `variant="destructive"`
and carry their weight in the sentence naming what is lost; `ChatList` used it at first and was
brought into line, because two components claiming the reserved colour for a delete and two
declining it is worse than either rule applied consistently.

No `dark:` variant, no `prefers-color-scheme` block, no drop shadow, no gradient and no hard-coded
colour (`#hex`, `rgb(`) survives anywhere under `services/frontend/src/`.

## The greyscale half (FR-006, SC-006)

### Which messages are the patient's

Read from the DOM with every stylesheet ignored, and confirmed in the screenshot:

| Pane | Sender | On the reader's side | Icon | Word label |
|---|---|---|---|---|
| Patient messenger | patient | **yes** | yes | — (their own) |
| Patient messenger | assistant | no | yes | "AI assistant" |
| Patient messenger | staff | no | yes | "Staff" |
| Staff console | patient | no | yes | — |
| Staff console | assistant | no | yes | "AI assistant" |
| Staff console | staff | **yes** | yes | "Staff" |

Three independent carriers — **position, glyph and word** — and each pane answers "whose side" for
its own reader, which is FR-013 and FR-023 as one mechanism. Any one of the three would survive
alone; the screenshot shows all three doing so.

### Which conversations need a person

| Row | `data-emphasized` | Glyph | Words |
|---|---|---|---|
| Jane Austen | `true` | ⚠ present | "Urgent condition" |
| Charles Dickens | `false` | absent | name only |

The marked row carries a warning glyph **and** names its reason underneath; the quiet row carries
neither. The console header reads "Needs a person: 1" regardless of the open section. In the
greyscale image the distinction is immediate and does not depend on the row tint at all.

### The evidence markers and their state

Both markers render as a bordered circle carrying a glyph, with `data-outcome-state` and an
accessible name in words — "Why this message needs a person" for a needs-person marker, "What this
answer drew on" for a served one. The two states use **different glyphs** (`TriangleAlert` against
`Info`), not one glyph in two colours.

Inside an expanded block, an unanswered request is named in a sentence of its own — "Not answered:
nothing in the clinic's documents came back for this question — forwarded to staff." — beside a
question that simply states what was asked. The difference is a whole element, not a tint.

## Verdict

**Passes.** With hue removed entirely, a viewer can still say which messages are the patient's,
which conversation needs a person and why, and which requests went unanswered. Colour is doing
emphasis work throughout and carrying no meaning on its own.

One cosmetic imperfection noticed in the image and deliberately not fixed: in the staff thread a
sender icon sits level with the *bottom* of its message group, so when a message carries an
expanded evidence block the icon ends up beside the block rather than beside the bubble. It is a
consequence of `items-end` aligning a tall column, it misleads nobody, and no requirement speaks
to it.
