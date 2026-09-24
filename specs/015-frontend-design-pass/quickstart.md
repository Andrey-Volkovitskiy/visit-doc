# Quickstart: verifying the design pass

Two tiers, because this feature has two kinds of requirement. Behaviour is verified by the automated
suite. Appearance cannot be — see the Complexity Tracking entry in [plan.md](./plan.md) — so it is
verified by the recorded procedure below, in a real browser, with the result written down.

## Tier 1 — automated

```bash
cd services/frontend
npm test                      # or: make test-frontend, from the repo root
./node_modules/.bin/tsc -b --noEmit
```

The `-b` is load-bearing. `tsconfig.json` is solution-style with `"files": []`, so a plain
`tsc --noEmit` checks **nothing** and exits 0 with a blatant type error sitting in `src/`. It does
not look like a weak check; it looks like a passing one.

**Baseline before any work**: 12 files, 234 tests, green. Every stage in the plan ends green — a
stage that breaks a test repairs it before it closes, per FR-038. [`test-impact.md`](./test-impact.md)
lists which tests each stage breaks and how each property is re-expressed.

Four of those tests would keep passing while testing nothing once their subject moves behind a tab
or a disabled control. They are strengthened, not left. The worst is App's *"shows no empty roster
when the session could not be provisioned at all"* — behind a shut tab it would pass with the
session gate **deleted**, and that gate is the regression it exists for.

## Tier 2 — the browser

Nothing below is covered by the automated suite. Most of it needs a real browser; the library-look
section is mostly `grep` and can be run without one.

**Most of it has been run, and the results are recorded in [`evaluation/`](./evaluation/) rather
than in a pull request comment** — a number in a PR thread is gone the moment the branch merges,
and these are the only evidence FR-041 and SC-006 will ever have. A ticked box below names the file
holding its numbers. Much of it turned out to be automatable against the production build with the
API stubbed at the network, which costs no Claude or Voyage call and can be re-run; what is left
unticked is what genuinely needs a stack answering for real, and each says so.

| Record | Covers |
|---|---|
| [`evaluation/browser-pass.md`](./evaluation/browser-pass.md) | the four widths, the keyboard pass, reduced motion, request origins |
| [`evaluation/contrast.md`](./evaluation/contrast.md) | every foreground/background pair in the palette |
| [`evaluation/greyscale.md`](./evaluation/greyscale.md) | colour independence, with `greyscale.png` and `colour.png` beside it |
| [`evaluation/live-stack-pass.md`](./evaluation/live-stack-pass.md) | the six that need a running stack, with `paused.png` beside it |

**Three of these checks found a real defect, and all three were fixed**: 1px of horizontal overflow
at 375px, a focusable tab panel with no focus ring, and two text/background pairs under AA.

Tier 2 is now complete — every box below is ticked and carries the file holding its evidence.

### Start the stack

```bash
make services-up            # chat + scheduler in the background
make run-frontend-dev       # holds a terminal, serves :5173
```

If `localhost:5173` will not open from the Windows-side browser, use the WSL IP from
`hostname -I` — it changes on every WSL restart, so re-read it rather than reusing one.

### The four widths (FR-008, FR-009, SC-001)

At **1440, 1100, 900 and 375**:

- [X] No horizontal scrollbar on the document. **0px at 375, 900, 1100, 1440 — and at 414, 768,
      1600 and 1920, so the whole of SC-001's range.** (`browser-pass.md`)
- [X] Panes side by side at 1100 and above; stacked below. (`browser-pass.md`)
- [X] Paste a 200-character unbroken string into the composer. It wraps inside its bubble and
      widens nothing — typed at each width before measuring. Sending it needs a stack and was not
      done; wrapping is a layout property and does not depend on the send landing.

A quick automated assist for the first item, using the Playwright setup described in
`CLAUDE.local.md` — the browser is cached, the driver goes in a scratch directory, and
`LD_LIBRARY_PATH` must point at `~/.local/lib/ms-playwright-sysdeps`:

```js
const overflow = await page.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth);
```

### Keyboard (FR-039, FR-040, SC-002)

- [X] Tab through the entire page. Every control reachable, **focus visible on every one**, and
      every focused element matches `:focus-visible`. (`browser-pass.md`)
- [X] The staff tab set works by keyboard and is announced as a tab set — ArrowRight moves focus
      and sets `aria-selected`. (`browser-pass.md`)
- [X] An evidence marker opens and closes on Enter/Space and reports its expanded state — it is a
      real `<button>` with `aria-expanded`, pinned by `OutcomeDisclosure.test.tsx`.
- [X] Tab order follows reading order; nothing reachable is invisible. (`browser-pass.md`)

### Colour and contrast (FR-005, FR-006, FR-041, SC-003, SC-006)

- [X] Every text/background pair passes WCAG AA — computed for **every pair the palette can form**,
      not only the ones on screen. The two named here both pass (4.91 and 4.83); the sweep found
      six others under AA, of which two were live and were fixed. (`contrast.md`)
- [X] `--color-attention` appears **only** where a person is needed. Every hit read; `ChatList`'s
      delete confirmation was using `variant="destructive"` and no longer does. (`greyscale.md`)
- [X] With a greyscale filter on the page, you can still tell the patient's messages from the
      clinic's, and which conversations need a person. (`greyscale.md`, with the screenshots)

### Motion (FR-042)

- [X] With reduced motion enabled, the working indicator stops animating (`animation-name: none`)
      and holds a visible resting opacity of 0.55 **and still says a reply is coming** — its
      meaning is a `role="status"` and an accessible name, which no media query can reach.
      (`browser-pass.md`)
- [X] The dialog and dropdown enter/exit animations `tw-animate-css` brings respect it too — the
      global block in `app.css` covers every animation, which is what makes this verified rather
      than assumed. (`browser-pass.md`)

### The library did not bring its own look (FR-002, contracts/tokens.md)

The step that decides whether this reads as your product or as a shadcn demo.

- [X] Every shadcn semantic variable resolves to a theme token. Checked by extracting every
      semantic utility the vendored components use and matching it against the `@theme` block:
      all resolve. shadcn's own `--accent` (a hover tint) is deliberately **not** declared, so the
      three `bg-accent`/`border-accent` uses can only mean this app's teal.
- [X] No drop shadow on the dialog, the dropdown or any popover — `grep` for `shadow-`,
      `box-shadow` and `drop-shadow` under `src/` returns nothing. No gradient either.
- [X] No `dark:` variant and no `prefers-color-scheme` block under `src/`. The only `dark` in the
      tree is the token name `--color-accent-dark`.

### The two stubs (FR-031, FR-033, SC-008)

- [X] The expanded evidence block states the booking outcome is not yet recorded and reads as
      deliberately absent — pinned by `OutcomeDisclosure.test.tsx`, which asserts the wording
      carries no "error/failed/unavailable" and renders no list, and seen in `greyscale.png`.
- [X] The practitioner's 7-day panel says the same and shows no empty list — pinned by
      `PractitionerAdmin.test.tsx`.

### Against a stack answering for real

Each of these needs content the stubbed build cannot produce — a turn the agent actually runs, or a
poll tick that actually changes something. **All six were run on 2026-09-24 and the numbers are in
[`evaluation/live-stack-pass.md`](./evaluation/live-stack-pass.md)**, with `paused.png` beside it.

It cost **two live turns** — one answered, one abstained — plus their Voyage and Langfuse usage.
Everything else was free, and the trick worth reusing is that **a paused conversation stores a
patient message and generates no reply at all**, which is what makes FR-017 checkable for nothing.
One session was provisioned and reused across all three scripts, so the corpus was seeded once.

- [X] **Scroll rule (FR-015a/b)**: both threads held at `scrollTop: 0` while content arrived from a
      second tab, and both followed to the bottom once the reader returned there.
- [X] **Working indicator (FR-016/FR-017)**: **155 ms** from Send to the indicator, against SC-005's
      500. Paused, it never appeared in 6 seconds of watching, and nothing stood in for it — no
      empty assistant bubble, no notice.
- [X] **Streaming (FR-018)**: the reply grew in more than one step inside one bubble; a staff
      message arrived whole (six identical length samples), with no indicator for it.
- [X] **Two markers open at once (FR-028)**: both still expanded after three or four poll ticks,
      and still attached to the same messages — the key-by-message-id property, actually exercised.
- [X] **Overflow (FR-012/FR-012a)**: six chats, four direct tabs; the one opened from the overflow
      took the **last** direct position and the tab it displaced went back into the overflow at its
      place in the server's order.
- [X] **Edit views (FR-035a/b)**: editing replaced the tab and is not an overlay; header, attention
      total and tab set stayed live; leaving clean asked nothing, and leaving dirty asked first —
      **by the back control and by choosing another tab**, which is the gap convergence found.

A paused conversation stores the message and generates no reply, so the marks, emphasis and
staff-side scenarios can be exercised **without spending Claude or Voyage calls**. Only the
indicator and streaming checks need a live turn.

### Stopping

```bash
make services-down
```

Never `pkill -f "chat.main"` — that pattern matches the command line of the shell running it and
kills the caller. (Worth saying twice: it happened in this feature's own session, with
`pkill -f "http.server"`.)

## What "done" looks like

- 234+ tests green (**398** as this feature closes), typecheck clean, and every checkbox above
  either ticked with its record named or left standing with the reason it needs a live stack.
- `research.md` Decision 7 is closed — it ships as an open risk otherwise. T000 records what jsdom
  actually demanded of the vendored components, and `tests/setup.ts` carries whatever that was.
- `README.md` carries a "technology choices" section for Tailwind, shadcn/ui and the Radix packages
  it pulled, each with its tradeoff, alongside the self-hosted-typeface rationale.
- `services/frontend/.claude/CLAUDE.md` carries the styling convention and every hook in
  [`contracts/testids.md`](./contracts/testids.md), including `staff-length-error`, which it is
  missing today.
