# Phase 0 research: the frontend design pass

Six questions the spec deliberately left to the plan, plus the two environment facts that decide
three of them, plus a seventh added later — an open risk the styling reversal introduced, carrying
no evidence yet and marked as such. Every claim marked **measured** was run in this repo, not
recalled.

## Measured facts

Two probes were written, run, and deleted; the suite was 234/234 before and after.

**The vitest setup processes CSS imports.** A `*.module.css` imported by a component under test
yields a real hashed class name — `_hello_63d854` — rather than `undefined` or a key-echoing proxy.
Measured before the styling decision changed; it now matters only as evidence that a stylesheet
import does not disturb a render under test, which holds for Tailwind's single entry import too.
Utility class names are plain strings in the DOM and need no processing at all, so the test tier is
indifferent to them.

**jsdom's scroll surface is half-present**, which decides FR-015a's whole approach:

| Property | jsdom behaviour |
|---|---|
| `scrollTop` | Settable and readable — writing 99 reads back 99 |
| `scrollHeight` | Always `0`, not settable without `Object.defineProperty` |
| `clientHeight` | Always `0`, same |
| `scrollIntoView` | **`undefined`** — calling it throws `TypeError` |

The last row is the trap. `scrollIntoView` is the obvious way to satisfy FR-015, and it would throw
in every test that rendered a thread. It must not be used.

## Decision 1 — Styling mechanism *(revised 2026-09-23)*

**Decision**: **Tailwind CSS v4 with shadcn/ui.** One global stylesheet imports Tailwind and
declares the theme; components are styled with utility classes; the interactive primitives come from
shadcn/ui, which vendors Radix components into the repository as source.

**This reverses the original decision**, which was CSS Modules with zero new dependencies. The
reversal is recorded rather than overwritten because the reasoning that produced the first answer
was faulty, and that is worth more to a later reader than a clean-looking document.

**Why the first answer was wrong**: it rested on FR-004's blanket dependency ban, which in turn
rested on a "minimal dependencies" convention **this repository does not have**. Checked and not
found in `.claude/CLAUDE.md`, `README.md` or `docs/ROADMAP.md`; the chat service alone declares
twenty runtime dependencies. The repository's actual rule is the one its twelve "technology choices"
README sections demonstrate: record the tradeoff. A constraint invented to satisfy a convention that
does not exist should not have outranked an accessibility floor the spec states three times
(FR-039, FR-040, SC-002).

**Rationale for the new answer**:

- Three controls in this feature — the tab set, the modal confirmation and the overflow menu — need
  focus management that is genuinely hard to get right: a roving tabindex with arrow-key handling, a
  focus trap with restore-on-close and background inerting, and a menu with typeahead and
  collision-aware positioning. shadcn/ui supplies all three, already accessible (FR-004b).
- shadcn vendors its components **into the repository as source** rather than importing them from a
  package, so they can be themed and edited like any other file here and there is no wrapper library
  to fight.
- Tailwind v4 is CSS-first: the theme is declared in the stylesheet as custom properties under
  `@theme`, which means the token contract survives the change nearly intact — see
  `contracts/tokens.md`.
- FR-003's scoping requirement is met differently but genuinely: a utility class describes only the
  element carrying it, so one component's styling still cannot reach another's.

**The tradeoff, stated plainly**: markup in all eight components is rewritten, the task list is
reworked, and shadcn's default look is recognizable enough that theming away from it is real work —
against a design direction chosen partly for not looking templated. Accepted.

**Alternatives considered**:

- *Unstyled Radix primitives over CSS Modules* — the smaller change: it buys the same accessibility
  for the three hard controls, keeps the chosen design direction untouched, and costs about four
  packages instead of a stack. Recommended and **declined**, in favour of the stack a reviewer of a
  portfolio project expects to see.
- *CSS Modules with everything hand-rolled* — the original decision. Declined: it leaves the
  accessibility floor resting on a manual audit of three controls that are easy to get subtly wrong.
- *CSS-in-JS* — a runtime dependency that puts style recomputation on the render path of a component
  streaming tokens.

## Decision 2 — Serving the typeface

**Decision**: ship IBM Plex Sans as three `woff2` files (weights 400, 500, 600, latin subset) under
`src/styles/fonts/`, referenced by relative URL from `tokens.css` so Vite fingerprints them, with
`font-display: swap` and an explicit fallback chain.

**Rationale**: FR-004 requires self-hosting and a declared fallback. `woff2` alone is sufficient —
every browser that runs this app's JavaScript supports it, and adding `woff` doubles the bytes
committed for no reachable user. Importing from `src/` rather than dropping into `public/` means
the files get content-hashed names and immutable caching; `public/` would serve them unhashed.
Three weights cover the whole type scale: body, medium for controls and emphasis, semibold for
headings.

IBM Plex Sans is licensed **SIL OFL 1.1**, which permits redistribution; the license text ships
beside the fonts. The `.woff2` files are committed as assets rather than pulled from `@ibm/plex`:
not because a dependency is forbidden — Decision 1's revision removed that constraint — but because
that package ships every weight, style and script of the whole Plex family to deliver the three
latin files actually used, and a build step to extract them is more machinery than committing three
files.

**Alternatives considered**: a font service (settled against in the spec's clarifications, FR-004);
variable-font single file (one request instead of three, but a larger download than three static
weights when only three are used, and it complicates the fallback declaration); system stack only
(rejected in the design direction).

## Decision 3 — How the pause state reaches the patient pane

**Decision**: `App` passes `assistantMayReply` to `ChatWindow`, read from the console poll it
already holds — the same lookup that already produces `activeLastMessageAt`. Default `true` when
the active chat is not yet in the poll's answer.

**Rationale**: `useConsolePoll` already returns every conversation with `assistant_may_reply`, and
`App` already does `poll.conversations.find(c => c.chat_id === activeChatId)` for the last-message
timestamp. FR-017 needs one more field off an object already in hand: no endpoint, no request, no
new state. Defaulting to `true` matches what `App` already does for the staff side
(`staffConversation?.assistant_may_reply ?? true`) and is the safe direction — a brand-new chat has
no poll row yet, and showing the indicator for a turn that turns out to be silent is a smaller
error than withholding it for every turn in a chat's first two seconds.

**Alternatives considered**: `ChatWindow` polling for itself (a second request for a value already
on screen, and two sources able to disagree); inferring from the `silent` stream event (arrives only
*after* the turn is over, so the indicator would have already been shown for the whole turn —
exactly what FR-017 forbids).

## Decision 4 — Making FR-015a testable

**Decision**: extract the rule as a pure predicate in `src/lib/`, unit-test it directly across its
boundary, and cover the component wiring with two tests that stub `scrollHeight`/`clientHeight` via
`Object.defineProperty`. Scrolling is performed by assigning `scrollTop`, never by
`scrollIntoView`.

```
isPinnedToBottom(scrollTop, scrollHeight, clientHeight, threshold) -> boolean
```

**Rationale**: the measurements above show the condition reads `0 - 0 <= threshold` → always true
under jsdom, so a component test alone would take the follow branch every time and the
hold-position branch would never execute while its test passed — the vacuous pass the spec's
Assumptions section requires the plan to prevent. A pure predicate is exercisable with no DOM at
all, which is where the rule's edges belong; the two stubbed component tests then prove the
predicate is actually consulted. And `scrollIntoView` being `undefined` makes the assignment form
mandatory rather than stylistic.

**Alternatives considered**: stubbing in `tests/setup.ts` globally (hides the fact that these
numbers are fake from the tests that depend on it); deferring the whole rule to Phase 3b (leaves the
requirement unverified for the entire life of this feature, and 3b is explicitly not written here).

## Decision 5 — Abandoning an unsaved edit

**Decision**: the edit view tracks whether it is dirty; leaving it while dirty — by the back control
or by switching tabs — raises a confirmation. Leaving a clean form does nothing special.

**Rationale**: FR-035b permits confirm-or-explicit-discard and forbids doing neither. A confirmation
gated on dirtiness is the one that does not punish the common case: opening a practitioner to look
at them and closing again is frequent, and a prompt there would be noise. The repo already has this
exact pattern in `ChatList`'s delete confirmation, so it is a reuse rather than a new mechanism.

**Alternatives considered**: an explicit "Discard" button (still loses work on a tab switch, which
FR-035a makes possible); autosaving on leave (silently writes a half-edited practitioner to the
scheduler — the worst of the three); blocking the tab switch until resolved (FR-035a requires the
tab set to stay operable).

## Decision 6 — Which existing tests change

Delegated to a full read of all twelve test files rather than estimated; the findings are in
`test-impact.md` beside this file and feed the task ordering directly. The governing rule is
FR-038: where a test fails because behaviour deliberately changed, the property it protected is
re-expressed against the new behaviour rather than deleted.

## Not researched, deliberately

- **Virtualising long threads.** No requirement asks for it, and the demo's threads are tens of
  messages. Adding it would be a dependency and unrequested complexity.
- **Animation library.** FR-042 requires motion to be suppressible; the only motion in the feature
  is a three-dot indicator, which is a CSS keyframe.
- **Dark theme.** FR-002 excludes it. The token structure is what a later theme would redefine.

## Decision 7 — Radix under jsdom *(settled 2026-09-23, T000)*

**Status: measured.** A throwaway test rendered a vendored dialog, dropdown menu, tab set and
switch under this repository's jsdom, in three rounds, and was then deleted. What follows is the
finding; the risk as it was originally recorded is kept below it, because the thing it feared and
the thing that was actually there are not the same and a later reader is owed both.

**The feared answer did not happen: no polyfill is required.** Not one of the four primitives threw
for a missing browser API. `tests/setup.ts` is unchanged — it carries the single
`@testing-library/jest-dom` import it always did, and nothing was added to it. `ResizeObserver`,
`matchMedia`, `DOMRect` and the pointer-capture surface were all either unused by these four
components or absent without consequence. `App.test.tsx` rendering the whole tree is therefore safe,
which was the specific fear.

**A different answer took its place, and it is the one that costs something.** These components do
not open on a synthetic `click`. Radix opens a menu and switches a tab on `pointerdown`/`mousedown`;
`fireEvent.click` dispatches neither, so the control simply does not respond. Measured, per
primitive:

| Driven by | Tab set | Dropdown menu | Dialog trigger | Switch |
|---|---|---|---|---|
| `fireEvent.click` | **no** | **no** | yes | yes |
| `fireEvent.mouseDown` | yes | — | — | — |
| `fireEvent.pointerDown` (button 0) | no | yes | — | — |
| `fireEvent.focus` | yes | — | — | — |
| `fireEvent.keyDown` Enter | — | yes | — | — |

This matters more than a polyfill would have, because **it fails silently in the shape of a missing
element**. All twelve existing test files drive the DOM with `fireEvent.click`; a test that clicks a
tab and then cannot find what is behind it reports "unable to find an element with the text", which
reads as a component that failed to render rather than as an event that was never delivered.

**The answer is one helper, not a per-primitive rule.** `tests/press.ts` exports `press(el)`, which
fires the full pointer sequence — `pointerdown`, `mousedown`, `pointerup`, `mouseup`, `click`.
Measured: it drives all four primitives **and** activates a plain `<button>` exactly once, so a test
never has to know which kind of control it is holding, and a control that is later swapped for a
vendored one does not silently stop being pressed. Existing `fireEvent.click` call sites are left
alone — they work, and rewriting 234 passing tests to use a helper they do not need would be churn
with a migration's risk and none of its benefit.

### The risk as it was recorded before measuring

*(Kept for the reason Decision 1's reversal is kept: the reasoning that produced the fear is worth
more to a later reader than a document that looks as though it always knew.)*

shadcn/ui's interactive components are Radix underneath, and Radix commonly needs browser APIs jsdom
does not implement — `ResizeObserver`, `matchMedia`, `DOMRect`, and the pointer-event surface used
for outside-press detection. If they are absent, a component that merely *renders* can throw, which
would reach far past the three controls it was adopted for: `App.test.tsx` renders the whole tree,
so a missing polyfill fails tests that have nothing to do with a dialog.

The precedent is Decision 4: `scrollIntoView` being `undefined` in this jsdom was discovered by
probing, and would otherwise have thrown in every thread test written against the obvious approach.
The same class of surprise is likely here.

**This must be settled by the first task that installs the stack**, before any component is built on
it — render one shadcn dialog and one dropdown menu in a throwaway test and see what jsdom demands.
The likely answer is a short polyfill block in `tests/setup.ts`; the answer that matters is whether
there is one at all, because if these components cannot be rendered under jsdom then Decision 1 has
a cost nobody priced and the task list needs reordering, not patching.

The guess in that last paragraph was wrong in both directions: there is no polyfill block, and the
real cost landed on how tests *drive* the components rather than on whether they render.
