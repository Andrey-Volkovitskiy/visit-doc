---

description: "Task list for the frontend design pass (Phase 3a)"
---

# Tasks: The frontend design pass (Phase 3a)

**Input**: Design documents from `/specs/015-frontend-design-pass/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [test-impact.md](./test-impact.md)

**Tests**: Per the constitution's Principle VIII, test tasks are mandatory and MUST precede their
implementation: contract → test cases → tests (observed failing) → implementation → tests passing.
One exception is recorded and justified in plan.md's Complexity Tracking — **appearance** cannot be
test-driven, so FR-005, FR-006, FR-041 and the visual half of FR-013 are verified by the recorded
browser procedure in [quickstart.md](./quickstart.md) instead of by a test that could not fail for
the right reason.

**Baseline**: 12 test files, 234 tests, green. Every phase ends green.

**Organization**: grouped by user story. [test-impact.md](./test-impact.md) says which existing
tests each phase breaks; repairing them is part of that phase, never deferred.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable — different files, no dependency on an incomplete task
- **[Story]**: US1–US5, on user-story phases only
- **T000 is deliberate, not an off-by-one.** Numbering starts at T001; T000 sits before it because
  it settles an open risk (`research.md` Decision 7) that can invalidate the phasing itself, and it
  must run before the setup it would invalidate. A suffixed id cannot sort ahead of T001, so the
  zero is the honest way to say "before everything".
- Suffixed ids (T012a, T002b) are insertions made after the first numbering. Ids are never
  renumbered, because other artifacts and commit messages reference them.

## Path Conventions

All paths are under `services/frontend/`, a plain Node project and **not** a `uv` workspace member.
Commands run from that directory: `npm test`, `./node_modules/.bin/tsc -b --noEmit`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: the token system and the typeface — everything downstream reads from these.

- [X] T000 **Settle `research.md` Decision 7 before anything is built on the stack.** Install Tailwind v4 (`tailwindcss`, `@tailwindcss/vite`) and initialise shadcn in `services/frontend/`, vendor one dialog and one dropdown-menu, and render each in a throwaway vitest test to find which browser APIs jsdom lacks. Add whatever polyfills that demands to `services/frontend/tests/setup.ts`, delete the throwaway test, and record the finding in `research.md` Decision 7. **If these components cannot be rendered under jsdom at all, stop and report — the styling decision has a cost nobody priced and the phasing needs rethinking, not patching**
- [X] T001 Download IBM Plex Sans weights 400/500/600 as latin-subset `woff2` into `services/frontend/src/styles/fonts/`, and place the SIL OFL 1.1 license text beside them as `services/frontend/src/styles/fonts/OFL.txt`
- [X] T002 Create `services/frontend/src/styles/app.css` — the one global stylesheet — with `@import "tailwindcss"`, the `@font-face` rules, the `@theme` block carrying every token from [contracts/tokens.md](./contracts/tokens.md), element defaults, and the global `prefers-reduced-motion` block (FR-001 — one declaration of every token; FR-002 — a single light theme; FR-003 — this is the *only* global stylesheet the feature may add)
- [X] T002a Map shadcn's semantic variables onto the theme in `services/frontend/src/styles/app.css` — `--background`, `--foreground`, `--muted-foreground`, `--primary`, `--destructive`, `--border`, `--input`, `--ring` — per the mapping table in [contracts/tokens.md](./contracts/tokens.md). **A shadcn default left unmapped is a defect**: it introduces a colour outside the contract and is how a themed app reverts to looking like the library's demo
- [X] T002b Vendor the primitives the feature needs into `services/frontend/src/components/ui/` — tabs, dialog, dropdown-menu, switch, button, input, textarea — and strip the drop shadow each of dialog, dropdown-menu and popover ships with, which the chosen direction does not use (FR-004b, [contracts/tokens.md](./contracts/tokens.md))
- [X] T002c Configure the `@/*` path alias in `services/frontend/vite.config.ts` and `services/frontend/tsconfig.app.json`, and **re-check that the `/chat` dev-proxy prefix rule still routes `/chats` and `/chats/{id}/messages`** — `services/frontend/.claude/CLAUDE.md` flags that rule as load-bearing and non-obvious
- [X] T003 Import `./styles/app.css` in `services/frontend/src/main.tsx`
- [X] T004 Set the document title to "AI Clinic Receptionist" in `services/frontend/index.html`, and confirm **no** font `<link>` or other third-party URL is present anywhere in it (FR-004)

**Checkpoint**: page renders in IBM Plex Sans on the token background; nothing else has changed;
suite still 234 green.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the pure module and the documented conventions every later phase depends on.

**⚠️ No user story work begins until this phase is complete.**

- [X] T005 Write `services/frontend/tests/scroll.test.ts` covering `isPinnedToBottom` at its boundary: exactly at the bottom, one pixel short, within threshold, far above, and a zero-height container. Confirm failing (the module does not exist)
- [X] T006 Implement `isPinnedToBottom(scrollTop, scrollHeight, clientHeight, threshold)` in `services/frontend/src/lib/scroll.ts` as a pure function with no DOM access; confirm T005 passes
- [X] T007 [P] Record the styling convention in `services/frontend/.claude/CLAUDE.md`: one global sheet, utility classes on the element they style, theme tokens never restated as literals (`bg-[#0E7C7B]` is a defect), arbitrary values for one-off layout only, and vendored `ui/` components themed at source rather than overridden from call sites (FR-003)
- [X] T008 Correct the `data-testid` list in `services/frontend/.claude/CLAUDE.md` to include `staff-length-error`, which exists in `StaffThread.tsx` and is absent from the documented list

**Checkpoint**: `scroll.ts` unit-tested and green; conventions written down before anyone follows
them.

---

## Phase 3: User Story 1 - A patient holds a conversation (Priority: P1) 🎯 MVP

**Goal**: the patient messenger as a designed product — tab strip with overflow, aligned bubbles
with burst grouping, bottom-pinned thread, working indicator, live composer, empty-thread greeting.

**Independent Test**: open the app as a first arrival, send a message, read the reply. The staff
console may stay unstyled throughout.

### Tests for User Story 1 (write first, confirm failing) ⚠️

- [X] T009 [P] [US1] Write tests in `services/frontend/tests/ChatList.test.tsx` for FR-012/FR-012a: six chats render four tabs plus `chat-overflow`; the overflow holds the rest as `chat-overflow-item`; an open chat that would overflow takes a direct position and displaces the last; `chat-overflow` is absent when four or fewer chats exist
- [X] T010 [P] [US1] Write tests in `services/frontend/tests/ChatWindow.test.tsx` for FR-019a/FR-019b: `char-count` appears near the limit and not far from it; `length-error` appears past it; Send is disabled for empty, whitespace-only and over-long input and enabled otherwise; input is never truncated
- [X] T011 [US1] Write tests in `services/frontend/tests/ChatWindow.test.tsx` for FR-016/FR-017: `working-indicator` appears for an in-flight turn when `assistantMayReply` is true, one per concurrent turn, and is absent entirely when it is false — with nothing rendered in its place. Assert it is present in the **same render as the optimistic patient message**, which is what makes SC-005's half-second true by construction rather than by timing luck
- [X] T012 [US1] Write tests in `services/frontend/tests/ChatWindow.test.tsx` for FR-019c: `thread-greeting` shows for a loaded empty thread, is absent once a message exists, is absent while history is still loading, and is absent when the load failed
- [X] T012a [P] [US1] Write tests in `services/frontend/tests/MessageView.test.tsx` for FR-014: a sender indicator appears on the first message of each consecutive run from one sender and on none of the rest; a run of one still gets one; alternating senders each get one
- [X] T012b [US1] Write a test in `services/frontend/tests/ChatWindow.test.tsx` for FR-010b: no message renders a sent time, a received time or an elapsed time — and the one permitted exception, the creation time inside an unnamed chat's label (FR-011), still renders
- [X] T013 [US1] Write tests in `services/frontend/tests/ChatWindow.test.tsx` for FR-015/FR-015a, stubbing `scrollHeight`/`clientHeight` via `Object.defineProperty`: the thread lands at the bottom on open; new content follows when pinned; position holds when scrolled up. Assign `scrollTop` — never call `scrollIntoView`, which is `undefined` in jsdom
- [X] T014 [US1] Update the existing `ChatWindow` tests per [test-impact.md](./test-impact.md): drop the inline-colour assertion from the over-limit test and add an explicit disabled assertion; keep `length-error` and `char-count` as separate hooks so the shorten-back-under-the-limit test stays honest
- [X] T015 [US1] Confirm every test T009–T014, T012a and T012b fails, for the right reason

### Implementation for User Story 1

- [X] T016 [US1] Rework `services/frontend/src/components/ChatList.tsx` into a horizontal tab strip with `CHAT_TAB_BUDGET = 4` exported, per-tab delete, and create — the overflow control built on the vendored `ui/dropdown-menu` rather than hand-rolled (FR-004b)
- [X] T017 [US1] Restyle `services/frontend/src/components/MessageView.tsx` into aligned bubbles with burst-grouped sender icons. Keep `white-space: pre-wrap` as an inline style — it is behaviour, and a module class is uncomputable under jsdom (see [test-impact.md](./test-impact.md)) Covers FR-013 (the patient's own messages opposite and differently backed), FR-014 (the burst rule) and FR-019 (this pane renders no outcome, mark or citation — it passes none)
- [X] T018 [US1] Add the scroll container, bottom pinning and the FR-015a follow rule to `services/frontend/src/components/ChatWindow.tsx`, consuming `isPinnedToBottom` from `src/lib/scroll.ts`
- [X] T019 [US1] Add `assistantMayReply` to `ChatWindow`'s props and pass it from `services/frontend/src/App.tsx`, read from the poll row for the active chat with `?? true` — no new request (research.md Decision 3)
- [X] T020 [US1] Add the `working-indicator`, the `thread-greeting`, the `char-count`, and the disabled-Send rule to `services/frontend/src/components/ChatWindow.tsx` Also FR-018: assistant text renders progressively into one bubble per turn while a staff message renders whole. Existing tests already pin it — this task must not regress them
- [X] T021 [US1] Run the suite; confirm T009–T014, T012a and T012b pass and nothing else regressed

**Checkpoint**: MVP. The patient messenger is a finished screen; the console is untouched.

---

## Phase 4: The shell (supports US1–US5)

**Purpose**: header, panes, responsive stacking, the staff tab set and the attention total.

**Why this is not a user story phase, and why it sits here.** The shell serves every story and is
none of them, so it has no story label. It is placed *after* US1 deliberately: US1 is the MVP and
needs no shell to be complete — the patient messenger works inside the existing unstyled container —
while US2 through US4 all live inside this phase's tabs. Ordering it first would delay the MVP to
build something only the later stories need.

- [X] T022 Write tests in `services/frontend/tests/App.test.tsx` for FR-020/FR-021: the three tabs exist as a tab set with accessible names; `attention-total` sits outside the tabbed region and survives switching to Practitioners; only the open tab's panel renders. Confirm failing Satisfies SC-004 — the count is legible from whichever section is open
- [X] T023 Move the three attention-total tests out of `services/frontend/tests/StaffConsole.test.tsx` into `services/frontend/tests/App.test.tsx`, and drop the `attentionTotal` param from its `renderConsole` helper and its three positional call sites (FR-038 — the properties survive, their home changes)
- [X] T024 Update the four session-gate tests in `services/frontend/tests/App.test.tsx` to open the relevant tab first. **Strengthen "shows no empty roster when the session could not be provisioned at all"** — behind a shut tab it would pass with the session gate deleted, which is the regression it exists for (FR-025 — a session-scoped section must not render before a session exists; the tab makes that gate later as well as earlier, not weaker)
- [X] T025 Build the shell in `services/frontend/src/App.tsx`: header with mark and wordmark, two panes, the 1100px stacking rule, the page-level error banner, the staff tab set built on the vendored `ui/tabs` (FR-004b), and the attention total in the console header. **This rewrites a file T019 already edited — preserve the `assistantMayReply` pass-through it added, which is FR-017's only data path.** T011's tests catch its loss; do not treat them as unrelated if they fail here Covers FR-007 (one screen naming the product and holding both panes), FR-008 (side by side at 1100px and above, stacked below), FR-010 (a page-level failure reported once, visually distinct)
- [X] T026 Remove the attention total and its prop from `services/frontend/src/components/StaffConsole.tsx`, leaving a comment saying where it went and why (FR-021 — inside the tab it would vanish when staff open Practitioners)
- [X] T026a Write tests in `services/frontend/tests/App.test.tsx` for FR-010a and SC-011: the header, both panes and their headings render before any request resolves; a region awaiting content carries `region-loading` with its `data-region` name; and waiting, arrived-empty and failed are three distinguishable renderings. Confirm failing
- [X] T027 Add the FR-010a loading treatment in `services/frontend/src/App.tsx`: structure renders before any request returns, each waiting region carries `region-loading` with a `data-region` name, and waiting, empty and failed are three distinguishable renderings
- [X] T028 Run the suite; confirm T022 and T026a pass and nothing else regressed

**Checkpoint**: both panes framed, tabs working, total always visible.

---

## Phase 5: User Story 2 - A staff member works the console (Priority: P2)

**Goal**: conversation rail with non-colour attention marking, styled staff thread, and the
assistant switch with its permanently visible explanation.

**Independent Test**: drive a conversation into an escalation and handle it from the console alone.

### Tests for User Story 2 (write first, confirm failing) ⚠️

- [X] T029 [P] [US2] Write tests in `services/frontend/tests/StaffConsole.test.tsx` for FR-022: every conversation lists in server order, and one needing a person is marked by something that is not colour
- [X] T030 [P] [US2] Write tests in `services/frontend/tests/StaffThread.test.tsx` for FR-024a: `assistant-explanation` is present and visible without hover, focus or activation, whatever the switch's position
- [X] T031 [US2] Write tests in `services/frontend/tests/StaffThread.test.tsx` for FR-025a: `staff-empty-thread` shows for an open conversation holding no messages, and is distinct from `staff-no-thread`, which means none is selected
- [X] T031a [US2] Write a test in `services/frontend/tests/StaffConsole.test.tsx` and `services/frontend/tests/StaffThread.test.tsx` for FR-010b: no conversation row shows how long it has waited and no staff message shows a time — while the assistant's remaining pause time (FR-024), the other permitted exception, still renders
- [X] T032 [US2] Write tests in `services/frontend/tests/StaffThread.test.tsx` for FR-015b — the follow rule applies to the staff thread on the same terms, stubbing the scroll measurements as in T013
- [X] T033 [US2] Update the existing `StaffThread` composer tests per [test-impact.md](./test-impact.md): "sends nothing for whitespace alone" gains an explicit disabled assertion, and "switched mid-post can still send" types into the new box before asserting Send is enabled — it is otherwise disabled for a second reason
- [X] T034 [US2] Confirm T029–T033 and T031a fail, for the right reason

### Implementation for User Story 2

- [X] T035 [US2] Restyle `services/frontend/src/components/StaffConsole.tsx` into the conversation rail with non-colour attention marking
- [X] T036 [US2] Restyle `services/frontend/src/components/StaffThread.tsx` — staff messages opposite, their own background, the switch header built on the vendored `ui/switch`, with `assistant-explanation`, the empty-thread statement, the disabled-Send rule, the `char-count` Covers FR-023 (staff messages opposite the patient's and the assistant's, with their own background) and FR-024 (the switch, its remaining pause time, and its explanation)
- [X] T037 [US2] Apply the FR-015b follow rule in `services/frontend/src/components/StaffThread.tsx`, reusing `isPinnedToBottom` from `services/frontend/src/lib/scroll.ts`
- [X] T038 [US2] Run the suite; confirm T029–T033 and T031a pass and nothing else regressed

**Checkpoint**: US1 and US2 both work independently.

---

## Phase 6: User Story 3 - A staff member sees why the assistant answered as it did (Priority: P3)

**Goal**: the click-to-expand evidence marker, in both states, with the booking stub.

**Independent Test**: open a conversation holding one served request and one abstention; both read
correctly from the marker alone.

**⚠️ Read FR-026 before starting.** Outcomes live on the **assistant's reply**; the attention mark
lives on the **patient's message**. Each message carries a marker for its own data. Never pair a
message with its neighbour — a patient message may have no reply yet, be superseded, or be one of a
burst.

### Tests for User Story 3 (write first, confirm failing) ⚠️

- [X] T039 [P] [US3] Write `services/frontend/tests/OutcomeDisclosure.test.tsx` for FR-027/FR-028: the block is closed initially; activation expands it and reports `aria-expanded`; a second activation collapses it; two disclosures open at once are independent Contributes to SC-007 — one interaction reveals what the assistant answered and what it did not
- [X] T040 [US3] Write tests in `services/frontend/tests/OutcomeDisclosure.test.tsx` for FR-026/FR-026a: an assistant reply holding outcomes carries a marker with `data-outcome-state="served"`; a patient message holding an attention mark carries one reading `needs-person`; a message with a mixture of answered and unanswered requests reads `needs-person`; a message with neither carries no marker
- [X] T041 [US3] Write tests in `services/frontend/tests/OutcomeDisclosure.test.tsx` for FR-029/FR-030/FR-031: each request appears in recorded order with its question and citations; an abstention is marked unanswered with its reason and distinguished by more than colour; `booking-outcome-stub` is present in every expanded block Completes SC-007, and covers SC-008's first half — the booking gap reads as deliberately absent
- [X] T042 [US3] Update the five existing `StaffThread` outcome tests per [test-impact.md](./test-impact.md) to expand the marker first, and **strengthen** "draws no outcome block for a reply that ran no FAQ half" to assert no *marker* is rendered — the absence of a block is otherwise free
- [X] T043 [US3] Update the six `MessageView` outcome tests in `services/frontend/tests/MessageView.test.tsx` to expand the disclosure before asserting, now that the block is behind it
- [X] T044 [US3] Confirm T039–T043 fail, for the right reason

### Implementation for User Story 3

- [X] T045 [US3] Create `services/frontend/src/components/OutcomeDisclosure.tsx` owning its own `expanded` boolean, the marker, and the block
- [X] T046 [US3] Move the outcome rendering out of `services/frontend/src/components/MessageView.tsx` into the disclosure, preserving `request-outcome`, `outcome-question`, `outcome-unanswered`, `verdict-mark` and `citations` unrenamed (FR-036)
- [X] T047 [US3] Render the disclosure from `MessageView` keyed by message id, so the 2-second poll repaint reconciles to the same instance and an open block does not jump to another message (data-model.md)
- [X] T048 [US3] Run the suite; confirm green

**Checkpoint**: all three console-side stories work.

---

## Phase 7: User Story 4 - A staff member manages practitioners and the FAQ (Priority: P4)

**Goal**: both tabs as structured records, editing that replaces the tab's content, and the
appointments stub.

**Independent Test**: add a practitioner with two working days; add and edit an FAQ entry.

### Tests for User Story 4 (write first, confirm failing) ⚠️

- [X] T049 [P] [US4] Write tests in `services/frontend/tests/PractitionerAdmin.test.tsx` for FR-035a/FR-035b: choosing edit replaces the list with `practitioner-edit`; the back control returns; leaving a dirty form raises `discard-confirm`; leaving a clean one does not
- [X] T050 [P] [US4] Write tests in `services/frontend/tests/FaqAdmin.test.tsx` for the same rules against `faq-edit`
- [X] T051 [US4] Write a test in `services/frontend/tests/PractitionerAdmin.test.tsx` for FR-033: `appointments-stub` is present for a selected practitioner and states the data is not yet available — and no empty appointment list is rendered, which would assert there are none Completes SC-008 — neither gap renders as an empty result or an error
- [X] T051a [US4] Write tests in `services/frontend/tests/PractitionerAdmin.test.tsx` for FR-032 and FR-035: the roster renders each practitioner's name, specialty, appointment length and working hours as a readable record with create, edit and delete reachable; and `practitioner-error` names what failed
- [X] T051b [US4] Write tests in `services/frontend/tests/FaqAdmin.test.tsx` for FR-034 and FR-035: each entry's question is distinguishable from its answer, create, edit and delete are each reachable from the entry they act on, and `faq-error` names what failed
- [X] T052 [US4] Update the ~15 affected `PractitionerAdmin` tests per [test-impact.md](./test-impact.md) to enter the edit view before acting, reusing the existing `aria-label` selectors (`Full name`, `Specialty`, `Appointment minutes`, `Weekday`)
- [X] T053 [US4] Update the ~9 affected tests in `services/frontend/tests/FaqAdmin.test.tsx` the same way, entering the create/edit view before acting
- [X] T054 [US4] Confirm T049–T053, T051a and T051b fail, for the right reason

### Implementation for User Story 4

- [X] T055 [US4] Rework `services/frontend/src/components/PractitionerAdmin.tsx` into list / edit / create views with the dirty-guard confirmation built on the vendored `ui/dialog` (FR-004b), plus the appointments stub
- [X] T056 [US4] Rework `services/frontend/src/components/FaqAdmin.tsx` the same way, with question and answer visually distinguished
- [X] T057 [US4] Run the suite; confirm T049–T053, T051a and T051b pass and nothing else regressed

**Checkpoint**: every story independently functional.

---

## Phase 8: User Story 5 - The screen survives a narrow window and a keyboard (Priority: P5)

**Goal**: the accessibility and responsive floor, verified rather than assumed.

**Independent Test**: walk the page with Tab at 1440px, then repeat at 900px and 375px.

- [X] T058 [US5] Write a test in `services/frontend/tests/App.test.tsx` asserting the semantic landmarks and heading structure of the shell (FR-040) — the part of the floor jsdom can actually check
- [X] T059 [US5] Audit every interactive control for a real semantic element and an accessible name across all components (FR-039, FR-040); fix what is not
- [X] T060 [US5] Run the four-width pass from [quickstart.md](./quickstart.md) at 1440/1100/900/375 — no horizontal scroll, correct stacking, a 200-character unbroken string wrapping — and record the result Verifies FR-008, FR-009 and SC-001
- [X] T061 [US5] Run the keyboard pass from [quickstart.md](./quickstart.md) and record the result Verifies FR-039, FR-040 and SC-002
- [X] T062 [US5] Measure contrast for every text/background pair (FR-041), checking `--color-ink-muted` on `--color-surface-sunken` and `--color-attention` on `--color-attention-wash` specifically; record the ratios Verifies SC-003
- [X] T063 [US5] Verify reduced motion (FR-042): the indicator stops animating and still conveys that a reply is coming
- [X] T063a [US5] Verify in `services/frontend/src/` that every shadcn semantic variable resolves to a theme token, that no drop shadow survives on the dialog, dropdown or popover, and that no `dark:` variant or `prefers-color-scheme` block remains — the checks under "The library did not bring its own look" in [quickstart.md](./quickstart.md) (FR-002, [contracts/tokens.md](./contracts/tokens.md))
- [X] T064 [US5] Verify colour independence (FR-005, FR-006): grep `services/frontend/src/` for `--color-attention` and for `destructive` — shadcn maps its destructive variant onto that colour — and read every hit; view the page greyscaled and confirm sender and attention state remain readable Verifies SC-006 — sender and attention state survive without colour perception

**Checkpoint**: the floor holds, with evidence.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T065 [P] Add the self-hosted-typeface tradeoff to `README.md`, following the existing per-choice rationale pattern (Principle VI). The dependency ledger is T068's
- [X] T066 [P] Add every new hook from [contracts/testids.md](./contracts/testids.md) to the list in `services/frontend/.claude/CLAUDE.md`
- [X] T067 Confirm no third-party runtime request is made: load the app with the network panel open and check that every request is same-origin (FR-004, SC-010)
- [X] T068 Record every dependency this feature added in `README.md` as a "technology choices" section with its tradeoff, following the twelve existing ones — Tailwind v4, shadcn/ui and the Radix packages it pulled, and why an accessible primitive library was preferred to hand-rolling three controls (FR-004a, SC-010, Principle VI)
- [X] T068a Verify the two standing constraints by `git diff` against the merge base: `services/frontend/src/lib/chatStream.ts` and `services/frontend/src/lib/consoleApi.ts` are **unmodified** (FR-037 — the claim that this is a presentation-only feature rests entirely on it), and no `prefers-color-scheme` rule and no `dark:` variant exists anywhere under `services/frontend/src/` (FR-002 — light only, by decision rather than omission; shadcn ships a dark block by default and it must be removed, not left unreferenced)
- [X] T069 Run the full gate: `npm test`, `./node_modules/.bin/tsc -b --noEmit`, and `make precommit` from the repo root Verifies SC-009 — the suite passes and every behaviour it pinned before the feature is still pinned after it
- [X] T070 Update the known-defects list in `specs/015-frontend-design-pass/design/README.md` to record, for each defect, whether the shipped implementation resolved it — the clipped staff thread, the tabs reading as links, the missing favicon, and the mockup's use of a font service. The list stays; what changes is that each line says what happened to it

---

## Requirement coverage

Generated from the requirement ids cited in the task lines above, not maintained by hand — so it
cannot drift from them. Regenerate it after editing any task, and treat a `—` as a defect rather
than as a note.

Every one of the 57 functional requirements and 11 success criteria appears below with at least one
task. Where a requirement names several tasks, the first is usually its test and the rest its
implementation and verification.

| Requirement | Tasks |
|---|---|
| FR-001 | T002 |
| FR-002 | T002, T063a, T068a |
| FR-003 | T002, T007 |
| FR-004 | T004, T067 |
| FR-004a | T068 |
| FR-004b | T002b, T016, T025, T055 |
| FR-005 | T064 |
| FR-006 | T064 |
| FR-007 | T025 |
| FR-008 | T025, T060 |
| FR-009 | T060 |
| FR-010 | T025 |
| FR-010a | T026a, T027 |
| FR-010b | T012b, T031a |
| FR-011 | T012b |
| FR-012 | T009 |
| FR-012a | T009 |
| FR-013 | T017 |
| FR-014 | T012a, T017 |
| FR-015 | T013 |
| FR-015a | T013, T018 |
| FR-015b | T032, T037 |
| FR-016 | T011 |
| FR-017 | T011, T025 |
| FR-018 | T020 |
| FR-019 | T017 |
| FR-019a | T010 |
| FR-019b | T010 |
| FR-019c | T012 |
| FR-020 | T022 |
| FR-021 | T022, T026 |
| FR-022 | T029 |
| FR-023 | T036 |
| FR-024 | T031a, T036 |
| FR-024a | T030 |
| FR-025 | T024 |
| FR-025a | T031 |
| FR-026 | T040 |
| FR-026a | T040 |
| FR-027 | T039 |
| FR-028 | T039 |
| FR-029 | T041 |
| FR-030 | T041 |
| FR-031 | T041 |
| FR-032 | T051a |
| FR-033 | T051 |
| FR-034 | T051b |
| FR-035 | T051a, T051b |
| FR-035a | T049 |
| FR-035b | T049 |
| FR-036 | T046 |
| FR-037 | T068a |
| FR-038 | T023 |
| FR-039 | T059, T061 |
| FR-040 | T058, T059, T061 |
| FR-041 | T062 |
| FR-042 | T063 |
| SC-001 | T060 |
| SC-002 | T061 |
| SC-003 | T062 |
| SC-004 | T022 |
| SC-005 | T011 |
| SC-006 | T064 |
| SC-007 | T039, T041 |
| SC-008 | T041, T051 |
| SC-009 | T069 |
| SC-010 | T067, T068 |
| SC-011 | T026a |

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)** → blocks everything. No component can style without tokens.
- **Phase 2 (Foundational)** → blocks US1 and US2 (both consume `scroll.ts`).
- **Phase 3 (US1)** → depends on 1 and 2. **This is the MVP.**
- **Phase 4 (Shell)** → depends on 1. Blocks US2, US3, US4 — they live inside its tabs.
- **Phase 5 (US2)** → depends on 4.
- **Phase 6 (US3)** → depends on 5; the marker lives in the staff thread.
- **Phase 7 (US4)** → depends on 4 only. **Runnable in parallel with 5 and 6.**
- **Phase 8 (US5)** → depends on everything; it audits the finished surface.
- **Phase 9 (Polish)** → last.

### User Story Dependencies

US1 is genuinely independent — it needs no shell, and Phase 4 is sequenced after it for that reason.
US2, US3 and US4 all need the shell's tab set. US3 needs US2's thread. US4 is independent of US2 and
US3 once the shell exists.

### Within Each User Story

Tests → confirm failing → implementation → confirm passing. Not negotiable (Principle VIII).

### Parallel Opportunities

- `[P]` marks a task whose file no other task in the same block is writing. Two tasks appending
  different `describe` blocks to one file is **not** parallel work — it is a merge conflict with a
  polite name — so tasks sharing a test file are sequential and carry no marker.
- Within a story's test block, the genuinely parallel pairs are the ones on distinct files: T009
  (ChatList) with T010 (ChatWindow) with T012a (MessageView); T029 (StaffConsole) with T030
  (StaffThread); T049 (PractitionerAdmin) with T050 (FaqAdmin).
- **Phases 5/6 and Phase 7 are the real parallel win** — disjoint components, disjoint test files,
  both gated only on Phase 4.
- T065 and T066 — different files.

## Implementation Strategy

**MVP is Phase 3.** Stop there and the patient-facing product is finished while the console stays
as it is. That is a deliberate boundary, not a fallback.

Then Phase 4 to frame the console, then 5 → 6 sequentially while 7 proceeds alongside, then the
floor and the polish.

**Every phase ends with the suite green.** A phase that breaks a test repairs it inside that phase;
[test-impact.md](./test-impact.md) says in advance which tests each one breaks, so none of it should
be a surprise.

**Four tests must be strengthened, not merely repaired** — T024, T033, T042 and the whitespace test
in T033's file. Each would otherwise keep passing while testing nothing once its subject sits behind
a tab, a disabled control, or an absent marker. T024 is the one that matters most: left alone it
would pass with the session gate deleted outright.

---

## Phase 10: Convergence

Appended by `/speckit-converge` after Phases 1–9 closed. Each task names the requirement it
traces to and the kind of gap it closes.

- [X] T071 Guard leaving a **dirty** edit view by choosing another console tab, in `services/frontend/src/App.tsx` and the two admin sections — confirm before abandoning, or make the discard explicit. FR-035b names that exact route ("by the back control **or by choosing another tab**") and says doing neither is not permitted; today `onValueChange={setStaffTab}` switches unconditionally and Radix destroys the inactive `TabsContent`, so typed changes vanish with no prompt. The back control is already guarded, so only this route is open per FR-035b (contradicts)
- [X] T072 Record in `README.md` every dependency this feature added, each with its tradeoff — `clsx`, `tailwind-merge`, `class-variance-authority`, `lucide-react`, `tw-animate-css` and the dev dependency `@types/node` are named nowhere in the new "technology choices" section, which describes Tailwind and shadcn/Radix in prose only per FR-004a (partial)
- [X] T073 Draw the working indicator's sender icon as the assistant's own icon in `services/frontend/src/components/ChatWindow.tsx` — it is an empty accent circle today, while every assistant message renders a `Bot` glyph in the same slot, so one turn shows two different things in the position the reply will occupy per FR-014, FR-016 (partial)
- [X] T074 Perform and record the greyscale half of the colour-independence check against a running stack, into `specs/015-frontend-design-pass/evaluation/` — the `grep` half is done and recorded, but "with a greyscale filter on the page, you can still tell the patient's messages from the clinic's, and which conversations need a person" needs conversations and messages on screen per SC-006, T064 (partial)

---

## Phase 11: Convergence

Second convergence pass, after Phase 10 closed. No finding from Phase 10 re-appears; these three
are new, and all three are artifacts that no longer describe the code.

- [X] T075 Bring [data-model.md](./data-model.md) up to the display state that exists — it names six pieces and the code holds five more: `dirtyTab` and `pendingTab` in `App.tsx` (the tab-switch half of FR-035b, added by T071), and the three "has this region answered yet" latches `chatsLoaded`, `historyLoaded` and `threadLoaded` that make FR-010a's waiting / arrived-empty / failed three distinguishable states rather than two. Say for each why it is not inferred from an empty array, which is the decision each one encodes per Constitution VI, data-model.md (partial)
- [X] T076 Write a test in `services/frontend/tests/ChatWindow.test.tsx` for the untested half of FR-019: a message carrying an attention mark renders **no** `attention-mark` and **no** `outcome-marker` in the patient pane. The citations and outcome halves are covered; the mark half never was, and the disclosure added a second element that must be absent there — an outcome is the clinic's working note on how an answer was produced, and the console is the only surface that shows it per FR-019 (partial)
- [X] T077 Correct the Project Structure block in [plan.md](./plan.md) — it omits `services/frontend/tests/press.ts`, the helper every vendored control is driven through, and still annotates `tests/setup.ts` with "+ whatever jsdom polyfills Radix turns out to need", which T000 measured as **nothing added**. A structure block that names a file's purpose wrongly is worse than one that omits it per Constitution VI, plan: Project Structure (partial)

---

## Phase 12: Convergence

Third and final convergence pass. No code gap found; the one finding is a verification document
that no longer matches what was verified. Nothing from Phase 10 or 11 re-appears.

- [X] T078 Bring [quickstart.md](./quickstart.md)'s Tier 2 up to what was actually run — 23 boxes are unticked and the file never mentions `evaluation/`, where the four widths, the keyboard pass, the contrast table, the reduced-motion measurement, the request origins and the greyscale reading are recorded with their numbers. Tick what is verified and point it at its record; leave unticked, and say why, only what genuinely still needs a running stack (a live turn's indicator, streaming against a staff post, two markers across a poll tick, six real chats in the overflow) per Constitution VI, quickstart.md (partial)


---

## Phase 13: Follow-up

Asked for directly, after the convergence loop closed. Not gaps converge found — the first
reverses a decision this feature had recorded, which is the user's to make and theirs to reverse.

- [X] T079 Ship a favicon built from the mark beside "AI Clinic Receptionist" — `services/frontend/public/favicon.svg`, declared in `index.html` as `<link rel="icon" type="image/svg+xml">`, which is what stops a browser asking for the `/favicon.ico` this app does not have. **Reverses the decline recorded in [design/README.md](./design/README.md)**, and answers the objection it was declined over: `services/frontend/tests/favicon.test.ts` holds the file and the inline `Wordmark` to the same paths, canvas, rounded square and `app.css` tokens, so "a second copy to keep in step" is checked rather than hoped for. Verified by decoding it, not by fetching it: an `<img>` reports `naturalWidth: 26`, `/favicon.ico` is never requested, and no off-origin request is made (FR-004). **The first version was unparseable XML and invisible in every browser while passing all six of its text-matching tests** — `favicon.test.ts` now parses it with `DOMParser` as well, which is what catches that
- [X] T080 Run the six remaining Tier 2 boxes against the running stack and record them in [evaluation/live-stack-pass.md](./evaluation/live-stack-pass.md) — the scroll rule on both threads, the working indicator shown and withheld, streaming against a whole staff message, two markers across a poll tick, the six-chat overflow, and the edit views. Cost two live turns; everything else was free, a paused conversation being what makes FR-017 checkable for nothing. `quickstart.md` Tier 2 now has no unticked box
