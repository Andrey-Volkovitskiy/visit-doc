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
./node_modules/.bin/tsc --noEmit    # typecheck only
```

`tsc`/`vitest` are not installed globally and should not be — use `npm test` or the local
`./node_modules/.bin/` binaries, never a bare `tsc`/`vitest`.

## Layout

```
src/
├── App.tsx              # owns the two panes, the chat list, the active chat, the error banner
├── components/          # ChatList, ChatWindow, MessageView, StaffConsole, StaffThread,
│                        #   PractitionerAdmin, FaqAdmin — presentational + local state
├── lib/chatStream.ts    # the patient side's network layer: every fetch and the NDJSON parser
├── lib/consoleApi.ts    # the staff side's network layer, same rules
├── lib/useConsolePoll.ts# the 2s poll of one endpoint, feeding both panes
└── main.tsx             # StrictMode root
tests/                   # vitest + @testing-library/react, one file per module
```

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
`@testing-library/react`. Assert through what a user sees — visible text, and the `data-testid`
hooks components already expose (`chat-list`, `chat-list-item`, `chat-list-error`, `messages`,
`message`, `role-label`, `attention-mark`, `citations`, `error`, `length-error`, `no-chat`,
`patient-pane`, `staff-pane`, `staff-console`, `staff-conversations`, `staff-conversation`,
`staff-thread`, `staff-no-thread`, `staff-no-conversations`, `staff-error`, `attention-total`,
`assistant-switch`, `pause-countdown`, `practitioner-admin`, `practitioner`, `working-range`,
`no-practitioners`, `practitioner-error`, `faq-admin`, `faq-entry`, `no-faq-entries`,
`faq-error`) — rather than component internals.

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
