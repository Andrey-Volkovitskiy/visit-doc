# CLAUDE.md — services/frontend

Guidance for the React + Vite SPA. Unlike `services/chat` and `services/scheduler`, this member has
no `@`-imported style guide: there is exactly one frontend, so its rules live here rather than in a
shared `docs/` file with a single reader.

`services/frontend` is a plain Node project, **not** a `uv` workspace member — it has its own
`package.json`/`node_modules` and is untouched by `uv sync`, `ruff`, and `mypy`.

## Commands

```bash
npm run dev      # vite dev server on :5173, proxying the API to :8000
npm test         # vitest run (also `make test-frontend` from the repo root)
npm run build    # tsc -b && vite build
./node_modules/.bin/tsc -b --noEmit # typecheck only
```

`tsc`/`vitest` are not installed globally and should not be — use `npm test` or the local
`./node_modules/.bin/` binaries, never a bare `tsc`/`vitest`.

**The `-b` in that typecheck is load-bearing, not a stylistic flag.** `tsconfig.json` here is a
solution-style config: `"files": []` plus references to `tsconfig.app.json` and `tsconfig.node.json`.
So `tsc --noEmit` resolves that root config, finds no files listed in it, checks **nothing**, and
exits 0 — including with a blatant type error sitting in `src/`. It is not a weak check, it is no
check at all, and it looks exactly like a passing one. `tsc -b --noEmit` walks the references and
checks both projects (`tsc -p tsconfig.app.json --noEmit` checks just the app half). Verified by
putting `export const x: number = "nope"` in `src/`: the first form exits 0, the second reports
`TS2322`.

## Layout

```
src/
├── App.tsx              # owns the shell, the two panes, the staff tab set, the error banner
├── components/          # ChatList, ChatWindow, MessageView, OutcomeDisclosure, StaffConsole,
│                        #   StaffThread, PractitionerAdmin, PractitionerWeek, FaqAdmin — this
│                        #   app's own components
├── components/ui/       # vendored shadcn source: tabs, dialog, dropdown-menu, switch,
│                        #   button, input, textarea — library code this repo owns
├── lib/chatStream.ts    # the patient side's network layer: every fetch and the NDJSON parser
├── lib/consoleApi.ts    # the staff side's network layer, same rules
├── lib/useConsolePoll.ts# the 2s poll of one endpoint, feeding both panes
├── lib/scroll.ts        # isPinnedToBottom, a pure predicate with no DOM access
├── lib/localTime.ts     # how the console writes a naive local time (day label, HH:MM), shared
│                        #   by the practitioner's week and the booking acts on a thread
├── lib/utils.ts         # shadcn's cn() — clsx + tailwind-merge
├── styles/app.css       # THE global stylesheet: tailwind, @font-face, @theme, the shadcn mapping
├── styles/fonts/        # IBM Plex Sans 400/500/600 woff2, self-hosted, with OFL.txt beside them
└── main.tsx             # StrictMode root
tests/                   # vitest + @testing-library/react, one file per module
```

**`src/components/ui/` is library code, `src/components/` is product code**, and the split is not
cosmetic. Files under `ui/` were vendored from shadcn and are edited for *theming*, never for
product behaviour; files beside them carry this app's logic. Mixing the two makes it unclear which
files a behaviour change belongs in.

## Styling

One global stylesheet, `src/styles/app.css`, and no others. It holds the Tailwind import, the
`@font-face` rules, the `@theme` block that declares every token, the mapping of shadcn's semantic
variables onto those tokens, the element defaults and the global reduced-motion block. A second
global sheet would give every value two homes.

- **Utility classes go on the element they style.** That is what keeps one component's appearance
  from reaching another's, now that there are no scoped module classes to do it.
- **A theme token is never restated as a literal.** `bg-[#0E7C7B]` is a defect — the token exists
  so the value has one home, and a literal is a copy that cannot be renamed. Same for `text-[15px]`
  and a hard-coded radius.
- **An arbitrary value is for a genuine one-off of *layout*** (`w-[37ch]`, `grid-cols-[208px_1fr]`),
  never for a value the theme names.
- **Vendored `ui/` components are themed at source**, not overridden from call sites with
  `!important` or a long class list. If a primitive looks wrong everywhere, fix the primitive.
- **No shadows, no gradients, no colour outside the theme.** The direction builds structure from
  hairline rules and background tint. Several shadcn components ship a drop shadow; each had it
  removed when it was vendored, and it should not come back.
- **`--color-attention` is reserved.** It means "a person is needed" and nothing else may use it —
  not a decorative accent, not a required-field asterisk, not a delete button at rest. shadcn maps
  `--destructive` onto it, so `variant="destructive"` is making that claim; use it only where it is
  true. And colour is never the only carrier of a state: text, weight, shape or position carries it
  too.
- **Light theme only.** No `dark:` variant and no `prefers-color-scheme` block belongs under
  `src/`; shadcn ships a dark theme by default and it was removed rather than left unreferenced.

The token contract is `specs/015-frontend-design-pass/contracts/tokens.md`, and the appearance it
describes comes from `specs/015-frontend-design-pass/design/a-front-desk.html`.

**Every network call goes through a `src/lib/` module — `chatStream.ts` for the patient side,
`consoleApi.ts` for the console.** No component calls `fetch` directly. That is what keeps the wire
contract — endpoint paths, request shapes, error handling — in one reviewable place per surface
instead of spread across components. Two modules rather than one because they are two surfaces with
two audiences; a third surface is a reason for a third module, not for a component reaching for
`fetch`.

## Talking to the backend

- **Always check `response.ok` before parsing a body.** `fetch` does not reject on a 4xx/5xx, and
  an error body is valid JSON, so an unchecked `await response.json()` casts `{detail: "..."}` to
  the success type and hands the caller an object with none of the fields it declares. The
  downstream failures are silent and far from the cause: an `undefined` id slips past a
  `!== null` guard, an `undefined` array throws inside `.map` during render (white-screening the
  SPA, since there is no error boundary), and a 404 body fed to `parseNdjsonStream` is yielded as
  one event with no `type` — which the terminal-event branch treats as a completed turn, showing
  an empty assistant bubble and no error at all. `ensureOk()` exists for this; use it in every
  wrapper.
- **Never let a promise float without a `.catch`.** `void somethingAsync()` in an effect or an
  event handler swallows the rejection: no error banner, no retry, and a first paint that sits
  empty forever with nothing on screen explaining why. Either `.catch` into `setError`, or
  `.catch(() => undefined)` with a comment saying why the failure is genuinely not worth surfacing.
- **Local wall-clock time only.** `localNow()` builds the offset-free `YYYY-MM-DDTHH:MM:SS` string
  the backend expects. `toISOString()` is deliberately never used: it converts to UTC, which moves
  the wall-clock time the assistant reasons about. There is no timezone anywhere in this system.
- The Vite dev proxy (`vite.config.ts`) matches by **prefix**, so the single `/chat` entry also
  routes `/chats` and `/chats/{id}/messages`. That is load-bearing but non-obvious — if you ever
  narrow that rule or add a more specific one, re-check that the `/chats` routes still proxy.

## State

- **Per-turn state, not per-component.** Several turns can be genuinely in flight at once (a burst
  of quick patient messages), and the server alone decides whether an earlier one is superseded.
  Anything belonging to *a turn* must be keyed by turn — `ChatWindow`'s `streaming` is a
  `Record<turnKey, string>` for exactly this reason. A single shared slot means whichever turn
  finishes first clears another's in-progress bubble, and their tokens interleave in one bubble
  until it does.
- **A still-completing request is never aborted just because a newer one started.** Its reply is
  already being persisted server-side, so dropping it client-side only makes the answer vanish
  until a reload. Aborting is for switching chats, where the reply belongs to a thread no longer
  on screen.
- **StrictMode double-invokes effects in development.** An effect that performs a *creating* side
  effect (`POST /chats` on a first arrival) needs an in-flight guard, or two cookie-less requests
  mint two sessions and only the last `Set-Cookie` survives — stranding the first session's chat,
  patient, and practitioner with no way to reach them.

## Tests

`tests/` mirrors `src/`, one file per module, run by vitest with jsdom and
`@testing-library/react`. Assert through what a user sees — visible text, the accessible role and
name where an element has one, and otherwise the `data-testid` hooks below — rather than component
internals.

**Where a role and name already identify an element, use them.** The staff tab set is
`getByRole("tab", { name: "Practitioners" })`, a composer is `getByLabelText("question")`, an
evidence marker is `getByRole("button", { expanded })`. A testid for something already addressable
is a second name for one thing.

**A hook names what a thing is, never what it looks like** — `outcome-marker`, not `blue-icon`. A
hook named after an appearance breaks when the appearance changes, which in a design pass is the
one thing guaranteed to happen. **Existing hooks may not be removed or renamed**; new ones may be
added.

Two hooks were retired despite that rule, deliberately: `booking-outcome-stub` and
`appointments-stub` each named an *absence* — 015's placeholder for a booking record and an
appointment list that did not exist yet — and spec 016 (FR-022) built both, so keeping the hooks
would have kept names pointing at nothing. Their successors are `booking-act` and
`bookings-toggle`/`practitioner-week`. The rule still stands for any hook naming a thing that
exists.

| Component | Hooks |
|---|---|
| `App` | `patient-pane`, `staff-pane`, `chat-list-error`, `attention-total`, `region-loading` |
| `ChatList` | `chat-list`, `chat-list-item`, `chat-overflow`, `chat-overflow-item`, `delete-confirm` |
| `ChatWindow` | `messages`, `no-chat`, `error`, `length-error`, `char-count`, `working-indicator`, `thread-greeting` |
| `MessageView` | `message`, `role-label`, `sender-icon`, `attention-mark` |
| `OutcomeDisclosure` | `outcome-marker`, `request-outcome`, `outcome-question`, `outcome-unanswered`, `verdict-mark`, `citations`, `booking-act` |
| `StaffConsole` | `staff-console`, `staff-conversations`, `staff-conversation`, `staff-no-conversations`, `region-loading` |
| `StaffThread` | `staff-thread`, `staff-no-thread`, `staff-empty-thread`, `staff-error`, `staff-length-error`, `char-count`, `assistant-switch`, `assistant-explanation`, `pause-countdown` |
| `PractitionerAdmin` | `practitioner-admin`, `practitioner`, `working-range`, `no-practitioners`, `practitioner-error`, `practitioner-edit`, `bookings-toggle`, `discard-confirm` |
| `PractitionerWeek` | `practitioner-week`, `week-day`, `week-appointment`, `week-empty`, `week-error`, `region-loading` |
| `FaqAdmin` | `faq-admin`, `faq-entry`, `no-faq-entries`, `faq-error`, `faq-edit`, `discard-confirm` |

Data attributes carry state a test would otherwise have to read off a colour: `data-sender` and
`data-mine` on a message, `data-burst-start` on the first of a sender's run, `data-chat-id` on a
chat tab, `data-emphasized` on a conversation, `data-position` and `data-verdict` on an outcome,
`data-mark` on an attention mark, `data-outcome-state` (`served` / `needs-person`) on an evidence
marker, `data-region` on a `region-loading` (`practitioner-week` for the week's loading line),
`data-operation` (`book` / `reschedule` / `cancel`) and `data-outcome` (`done` / `unchanged` /
`refused` / `not_sent` / `unknown` — an act with no recorded outcome is attributed `unknown`, never
as a sixth value) on a `booking-act`, and `data-failure` (`not_found` / `unreadable`) on a
`week-error`. `bookings-toggle` is also addressable as
`getByRole("button", { name: /show bookings|hide bookings/i, expanded })`; the testid exists because
a roster holds one per practitioner, so a test scopes it `within` its `practitioner` block.

### Driving the vendored controls

**`fireEvent.click` does not open a Radix tab set or dropdown menu.** They open on
`pointerdown`/`mousedown`, which a synthetic `click` never dispatches — and it fails in the shape
of a *missing element*, not a missing event, so it reads as a component that did not render. Use
`press()` from `tests/press.ts`, which fires the whole pointer sequence and drives the tab set, the
dropdown, the dialog and the switch, while still activating a plain `<button>` exactly once.
Measured, not assumed: see `specs/015-frontend-design-pass/research.md` Decision 7. Existing
`fireEvent.click` call sites work and were left alone.

**Radix renders a dialog and a dropdown menu into a portal on `document.body`**, outside the
subtree the component returned. So `discard-confirm`, `delete-confirm` and `chat-overflow-item`
are found with `screen.*` and never with `within(container)`. And a `data-testid` reaches a
vendored component only if it forwards its props — verify the forwarding rather than assume it,
since a swallowed testid looks exactly like a missing element.

**A scroll container reports nothing under jsdom.** `scrollHeight` and `clientHeight` are always
`0`, so the "follow only when the reader is at the bottom" rule is always-true unless a test stubs
them with `Object.defineProperty`. `scrollIntoView` is `undefined` and throws — scroll by assigning
`scrollTop`.

- Network is faked at the `chatStream`/`consoleApi` seam: `vi.spyOn(chatStream, "askChat")` and
  friends, so a test exercises the real component against a controlled wire, never a real server.
  A test rendering `App` has to stub both, since the two panes read from both.
- The repo-wide mocking discipline in [`docs/testing-strategy.md`](../../docs/testing-strategy.md)
  applies here too: do not assert that rendered text equals a string you handed the mock — that
  only proves the mock returned it. Assert on what the component *did* with it (which bubble it
  landed in, that it survived a sibling turn's cancellation, that an error surfaced).
- Interleaving matters: when a test needs two turns in flight, drive them with real awaited
  promises rather than synchronous generators, or React batches the updates into one render and
  the intermediate state you are testing never paints.
