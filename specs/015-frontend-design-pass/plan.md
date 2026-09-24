# Implementation Plan: The frontend design pass (Phase 3a)

**Branch**: `create-ui` | **Date**: 2026-09-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/015-frontend-design-pass/spec.md`

## Summary

Give the SPA its first stylesheet and the interaction behaviour a designed product implies, without
touching a single network contract. The approach is Tailwind CSS v4 with shadcn/ui — utility
styling over a CSS-first theme, with the three controls whose accessibility is genuinely hard
(tab set, modal, overflow menu) vendored in as accessible source rather than hand-rolled — plus a
self-hosted typeface, a three-tab staff console, and the handful of behaviours
the design makes conspicuous by their absence: bottom-pinned threads, a working indicator, disabled
send controls, an empty-thread greeting, and per-request evidence behind a click-to-expand marker.

Two capabilities the design draws are not built: they are rendered as visible stubs, because the
backend cannot serve them (`docs/ROADMAP.md`, Phase 3a).

## Technical Context

**Language/Version**: TypeScript 5.x (`typescript@~7.0.2` in this workspace), React 19.2, targeting
ES2022 via Vite 8

**Primary Dependencies**: React 19 + Vite 8, plus **Tailwind CSS v4** (`tailwindcss`,
`@tailwindcss/vite`) and **shadcn/ui**, which vendors component source into the repo and pulls
`@radix-ui/react-*` per component alongside `clsx`, `tailwind-merge`, `class-variance-authority`,
`tw-animate-css` and `lucide-react`. Each is recorded in `README.md` with its tradeoff (FR-004a).
The typeface remains committed asset files, not a package.

**Storage**: N/A — no client-side persistence. Session identity is an `HttpOnly` cookie the SPA
cannot read, which is why `session_exists` comes from the server.

**Testing**: vitest 4 + jsdom 30 + `@testing-library/react` 16, `npm test` / `make test-frontend`.
Assertions go through visible text and `data-testid`, never component internals.

**Target Platform**: Desktop and laptop browsers, 375px–1920px. Light theme only.

**Project Type**: Single-page web frontend inside a monorepo. `services/frontend` is a plain Node
project, not a `uv` workspace member, and is untouched by `ruff`/`mypy`/`uv sync`.

**Performance Goals**: The one stated target is FR-016/SC-005 — a working indicator within 500ms of
send, which is a render, not a request. No throughput or latency targets apply: every expensive
operation in this app belongs to the backend.

**Constraints**: No horizontal scroll 375px–1920px (FR-009); WCAG 2.1 AA contrast (FR-041); no
third-party request at runtime (FR-004); every existing `data-testid` preserved (FR-036); no change
to `src/lib/chatStream.ts` or `src/lib/consoleApi.ts` (FR-037); every added dependency documented
(FR-004a).

**Scale/Scope**: 8 existing components plus the app shell, all of whose markup is rewritten;
1 global stylesheet, 7 vendored shadcn components, 3 font files; 57 functional requirements;
12 existing test files holding 234 tests.

## Constitution Check

*GATE: evaluated before Phase 0 and again after Phase 1 design.*

| Principle | Verdict | Evidence |
|---|---|---|
| **I. Phase-gated scope** | **PASS** | Phase 2 closed with 014. Phase 3a is the next section of `docs/ROADMAP.md` and this feature implements exactly it, adding no service, no infrastructure and no platform layer. It explicitly declines to build the two capabilities it would need to widen scope for. |
| **II. AI core is the centrepiece** | **PASS, with a note** | This is not AI-core work, and the constitution makes platform work secondary. It is not a violation because the roadmap itself sequences 3a here, after the AI core and its eval harness are complete — the trade this principle guards against is spending AI-core time on infrastructure, which spending Phase 3 time on Phase 3 work is not. |
| **III. Service boundaries** | **N/A** | No boundary added, moved or crossed. |
| **IV. Structured outputs / tool interfaces** | **N/A** | No agent, classifier or tool is touched. |
| **V. Grounded retrieval, mandatory abstention** | **PASS** | Retrieval is unchanged. The feature *strengthens* the visibility of abstention: FR-029/FR-030 require each request's verdict and its citations to be readable, and FR-030 requires an abstention to be distinguishable by more than colour. FR-031 forbids implying a booking outcome the system does not record. |
| **VI. Documentation first-class** | **PASS, with obligations** | Three documents must change in the same commits as the code, and are tasks, not follow-up: `README.md` gains the styling and typeface tradeoffs; `services/frontend/.claude/CLAUDE.md` gains the CSS-module convention and every new `data-testid`; `docs/ROADMAP.md`'s Phase 3a is already updated. |
| **VII. Clean architecture, SOLID, patterns** | **PASS** | One source for the theme (FR-001); a utility class styles only the element carrying it, so FR-003's scoping still holds; the FR-015a rule extracted as a pure predicate rather than embedded in an effect; no component gains a network call (FR-037). Vendored shadcn components live under `src/components/ui/`, separate from this app's own components, so library code and product code stay distinguishable. |
| **VIII. Test-driven development (NON-NEGOTIABLE)** | **PASS for behaviour, deviation recorded for appearance** | See Complexity Tracking. Every requirement expressible as a test gets its test first, observed failing. Requirements about appearance cannot be; those get a recorded manual verification instead of a pretend test. |

**Post-Phase-1 re-check**: unchanged in verdict, changed in evidence. The styling decision was
reversed after Phase 1 — see `research.md` Decision 1 — so the feature now adds dependencies where
it previously added none. That does not move any verdict: **no constitution principle forbids a
dependency**, which is precisely what the reversal established, and Principle VI's requirement that
each be documented with its tradeoff is carried as a task rather than an intention. Principle II is
worth re-reading here: this is secondary work, and the stack was adopted knowing the migration cost
falls on the frontend rather than on the AI core.

## Project Structure

### Documentation (this feature)

```text
specs/015-frontend-design-pass/
├── spec.md              # 55 FRs, 11 SCs, 5 user stories
├── plan.md              # This file
├── research.md          # Phase 0: six decisions + two measured environment facts
├── test-impact.md       # Phase 0: which existing tests change, and why
├── data-model.md        # Phase 1: display-state model (no persisted data)
├── quickstart.md        # Phase 1: how to verify, including what only a browser can verify
├── contracts/
│   ├── testids.md       # The preserved + added data-testid surface (FR-036)
│   └── tokens.md        # The design token contract (FR-001)
├── checklists/
│   └── requirements.md  # Spec quality checklist, 16/16
├── design/
│   ├── a-front-desk.html  # The chosen mockup — authoritative for appearance only
│   └── README.md
└── tasks.md             # Phase 2 output — NOT created by /speckit-plan
```

### Source code

```text
services/frontend/
├── index.html                          # title, the icon link, no font <link> (FR-004)
├── public/
│   └── favicon.svg                     # NEW: the header's mark, at a fixed unhashed path
├── components.json                     # NEW: shadcn config (aliases, style, base colour)
├── vite.config.ts                      # + @tailwindcss/vite, + "@" path alias
├── tsconfig.app.json                   # + the matching "@/*" path mapping
├── src/
│   ├── main.tsx                        # imports the one global stylesheet
│   ├── App.tsx                         # shell: header, two panes, staff tab set, attention total
│   ├── styles/
│   │   ├── app.css                     # THE global sheet: @import tailwindcss, @theme, @font-face
│   │   └── fonts/                      # IBM Plex Sans 400/500/600 woff2 + OFL.txt
│   ├── components/
│   │   ├── ui/                         # NEW: vendored shadcn source — tabs, dialog,
│   │   │                               #   dropdown-menu, switch, button, input, textarea
│   │   ├── ChatList.tsx                # horizontal tab strip, 4 + overflow (ui/dropdown-menu)
│   │   ├── ChatWindow.tsx              # thread, indicator, composer, greeting
│   │   ├── MessageView.tsx             # bubble, burst grouping
│   │   ├── OutcomeDisclosure.tsx       # NEW: the (i) marker and its block
│   │   ├── StaffConsole.tsx            # conversation rail
│   │   ├── StaffThread.tsx             # staff thread, switch (ui/switch), explanation
│   │   ├── PractitionerAdmin.tsx       # roster + edit view + appointments stub (ui/dialog)
│   │   └── FaqAdmin.tsx                # entries + edit view
│   └── lib/
│       ├── chatStream.ts               # UNCHANGED (FR-037)
│       ├── consoleApi.ts               # UNCHANGED (FR-037)
│       ├── useConsolePoll.ts           # UNCHANGED
│       ├── utils.ts                    # NEW: shadcn's `cn()` (clsx + tailwind-merge)
│       └── scroll.ts                   # NEW: isPinnedToBottom, a pure predicate
└── tests/
    ├── setup.ts                        # UNCHANGED — T000 measured what Radix needs under
    │                                   #   jsdom and the answer was *no polyfill at all*
    ├── press.ts                        # NEW: the pointer sequence every vendored control
    │                                   #   is driven with; fireEvent.click does not open a
    │                                   #   Radix tab set or menu (research.md Decision 7)
    ├── favicon.test.ts                 # NEW: holds public/favicon.svg and the inline
    │                                   #   Wordmark to the same geometry and the same tokens
    └── …                               # one file per module; + scroll.test.ts, OutcomeDisclosure.test.tsx
```

**Structure Decision**: the existing layout is kept — `src/components/` for this app's components,
`src/lib/` for everything that is not a component, `tests/` mirroring both. Four additions.

`src/components/ui/` holds shadcn's vendored source, kept separate from the app's own components on
purpose: it is library code this repo happens to own, edited for theming rather than for product
behaviour, and mixing the two would make it unclear which files carry the product's logic.
`src/styles/` holds the global sheet and the fonts, which are neither component nor library module.
`OutcomeDisclosure.tsx` is a new component because FR-026–FR-031 describe a self-contained control
with its own open/closed state; leaving it in `MessageView` would give that component a second
responsibility and a state it otherwise does not have. `scroll.ts` joins `lib/` as a pure module for
the reason `research.md` Decision 4 gives: the rule must be testable without a DOM.

**No per-component stylesheets.** The earlier plan put a `*.module.css` beside each component; with
utility styling there is nothing for those files to hold, and keeping them would give every
component two places to look for its appearance.

## Implementation phasing

Ordered so each stage leaves the suite green and the app runnable, and so the P1 story is
demonstrable before anything below it starts.

**The shell follows the patient messenger rather than preceding it.** US1 needs no shell — the
messenger is complete inside the existing container — while US2 through US4 all live inside the
shell's tabs. Building it first would delay the MVP to produce something only the later stories
need. `tasks.md` phases these identically; if the two ever disagree, `tasks.md` is what an
implementer follows and this table is the stale one.

| Stage | Covers | Delivers |
|---|---|---|
| **0. Foundation** | FR-001–006, FR-004a–b | Install Tailwind + shadcn, settle the jsdom question (`research.md` Decision 7), declare the theme, self-host the fonts, vendor and theme the primitives. |
| **1. Patient messenger** (US1) | FR-011–019c | Tab strip with overflow, bubbles, burst grouping, scroll behaviour, indicator, composer, greeting. **MVP ends here.** |
| **2. Shell** | FR-007–010b, FR-020–021 | Header, two panes, responsive stacking, page-level error, staff tab set, attention total in the console header. |
| **3. Staff console** (US2) | FR-022–025a | Conversation rail, staff thread, switch with its permanent explanation. |
| **4. Evidence marker** (US3) | FR-026–031 | `OutcomeDisclosure`, both states, the booking stub. |
| **5. Practitioners & FAQ** (US4) | FR-032–035b | Both tabs, the replace-the-panel edit views, the 7-day appointments stub. |
| **6. Floor & docs** (US5) | FR-002, FR-039–042, VI | Keyboard/contrast/motion audit at four widths; the library-look audit (no unmapped shadcn default, no shipped shadow, no `dark:` variant); `README.md`, the dependency ledger, and `services/frontend/.claude/CLAUDE.md`. |

Test-impact work is not a stage: each broken test is repaired inside the stage that breaks it, per
FR-038, so the suite is green at every stage boundary.

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| **Principle VIII cannot be satisfied for appearance requirements** | A stylesheet has no observable contract a unit test can assert. `expect(el).toHaveStyle("color: #0F2E33")` only re-states the CSS in a second place: it fails when a token is renamed and passes when the colour is wrong for its purpose, so it pins the implementation while proving nothing about the requirement. Every *behavioural* requirement here — tab set, overflow, disabled control, expansion, scroll rule, greeting, stubs, semantics, focus order — is test-driven normally, tests first and observed failing. Appearance requirements (FR-005, FR-006, FR-013 colour half, FR-041 contrast) are instead verified by the recorded procedure in `quickstart.md`, run in a real browser at four widths, with the result written down. | Asserting computed styles was rejected as a test that cannot fail for the right reason. Snapshot tests of rendered HTML were rejected for the same reason plus churn: they fail on every markup change and are re-recorded without being read. Deferring appearance to Phase 3b was rejected because 3b is a browser *behaviour* suite and the roadmap explicitly does not write it here. |
| **Adopting Tailwind v4 + shadcn/ui, when a smaller compliant option existed** | Principle VII requires complexity beyond what a requirement needs to be justified, and this is that case. Three controls — the tab set (FR-020), the modal confirmation (FR-035b) and the overflow menu (FR-012) — carry focus management the application would otherwise reimplement: a roving tabindex with arrow keys, a focus trap with restore-on-close and background inerting, and a menu with typeahead and collision-aware positioning. FR-039, FR-040 and SC-002 state an accessibility floor three times; hand-rolling those three leaves it resting on a manual audit. FR-004a establishes that no rule here forbids a dependency — the repository's actual requirement is a documented tradeoff, which T068 supplies. | **Unstyled Radix primitives over hand-written scoped CSS was recommended and declined** — it buys the same accessibility for the same three controls at about four packages instead of a stack, and leaves the chosen design direction untouched. It was declined by the user in favour of the stack a reviewer of a portfolio project expects to see; that is a legitimate reason and it is recorded here rather than rationalised as a technical one. Hand-rolling all three was declined for the accessibility reason above. The accepted costs are a larger migration, a recognizable default aesthetic that must be themed away from (`contracts/tokens.md`, T002a/T002b), and an unmeasured jsdom risk (`research.md` Decision 7, T000). |
| **A new component (`OutcomeDisclosure`) and a new lib module (`scroll.ts`)** | FR-027 needs per-marker open state, and FR-015a needs a rule exercisable without a DOM — see `research.md` Decision 4 for the measurement that forces it. | Keeping both inside their parents was rejected: the disclosure state would make `MessageView` stateful for a concern that is not its own, and the scroll rule inside an effect would be untestable in the tier that runs on every push, passing vacuously. |
