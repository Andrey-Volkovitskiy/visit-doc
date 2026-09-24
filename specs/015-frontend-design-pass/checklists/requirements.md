# Specification Quality Checklist: The frontend design pass (Phase 3a)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-23
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

Three items needed a judgement call rather than a plain yes, and the reasoning is recorded here so a
reviewer can overturn it rather than re-derive it.

**"No implementation details" — passed with a deliberate, bounded exception.** Two kinds of
concrete detail appear and are held to be requirements rather than implementation:

1. *Viewport pixel widths* (1100px, 375px, 1920px in FR-008, FR-009, SC-001). For a design feature
   the breakpoint is the requirement — "works on a small laptop" is not testable, and 1100px was
   decided with the user. Stating it is what makes the acceptance scenario checkable.
2. *Named files at the boundary of the feature* (`src/lib/chatStream.ts` and `src/lib/consoleApi.ts`
   in FR-037 and Dependencies; `services/frontend` in Assumptions). These appear only to say what
   the feature must **not** change. Naming the two modules is what makes "no contract change"
   verifiable; describing them abstractly would leave the constraint unenforceable. This matches the
   house style of the specs under `specs/` — 014 names `.run/chat.log` and structlog for the same
   reason.

Deliberately *absent*: no styling mechanism, no library choice, no component names, no CSS property
names, and no file layout for the new styling. FR-001 through FR-006 state the properties the
design system must have — one source for tokens, scoped component styling, a reserved semantic
colour, no new dependency — and leave how to achieve them to `/speckit-plan`.

> **Superseded on 2026-09-23** by the stack reversal recorded below. The spec now names Tailwind and
> shadcn/ui, and FR-004's dependency ban is gone. The paragraph above describes the spec as it stood
> at first review and is kept as that record, not as a current claim.

**"Written for non-technical stakeholders" — passed, with one qualification.** The user stories,
acceptance scenarios and success criteria read without technical knowledge. The Dependencies section
and parts of Preservation do not, because their audience is the developer who must not break a test
hook contract. Splitting those out rather than removing them keeps the stakeholder-facing bulk of
the document clean.

**"Requirements are testable" — FR-041 and SC-003 depend on a tool, not a reading.** WCAG AA
contrast is a computed ratio, so both are checkable mechanically; the spec says "verified, not
assumed" to stop this becoming a claim someone makes by eye.

## Clarification session, 2026-09-23

Five questions asked and answered; all five integrated. Effect on this checklist:

- **"Requirements are testable and unambiguous"** was the weakest pass before and is now a clean
  one. Three of the five answers removed an untestable requirement: FR-012 fixes the tab count at
  four instead of leaving "fit" undefined, FR-010a says what the first paint shows, and FR-010b
  settles a question the spec had not noticed it was silent on.
- **A contradiction was found and fixed while integrating.** FR-010b as first written forbade every
  rendered time, which collided with FR-011's existing `Unnamed · 14:32` fallback label and with
  the edge case preserving it. FR-010b now names that label and the FR-024 countdown as the two
  things that are not exceptions to it.
- **Two accepted costs are now recorded in Assumptions** rather than left to be discovered: a
  paused conversation is silent to the patient, and a staff queue carries no waiting time.

## Second clarification session, 2026-09-23

Five further questions, all integrated; the spec grew from 45 to 54 requirements. What changed:

- **Three requirements were silently unfinished and are now complete.** FR-015 pinned the thread to
  the bottom "when a chat is opened" and said nothing about the far commoner case of a message
  arriving while it is open (now FR-015a/FR-015b); FR-032/FR-034 said editing exists without saying
  where it happens (now FR-035a/FR-035b); FR-024 required an explanation without saying whether it
  had to be discoverable (now FR-024a).
- **Two things the spec never mentioned are now specified.** The composers' 2000-character limit,
  whose `length-error` hook FR-036 already required to survive (now FR-019a/FR-019b), and the empty
  thread a first arrival actually lands on (now FR-019c/FR-025a).
- **A second contradiction was caught during integration.** FR-019c as first written showed the
  greeting whenever a chat "holds no messages", which includes a thread still loading — directly
  against FR-010a's rule that waiting, empty and failed are never rendered alike. FR-019c now
  requires the history to have loaded.
- **A vacuous-pass risk is recorded rather than shipped.** FR-015a's "already at the bottom" test
  cannot be exercised under jsdom, which reports zero for every scroll measurement, so the
  hold-position branch would pass without ever being taken. Assumptions now requires the plan to
  say how it is verified.

## Re-validated after planning and the analyze loop, 2026-09-23

Still **16/16**; no checkbox changed state. Two spec changes landed after the last review and were
re-checked against it:

- **FR-026 was corrected during planning, and FR-026a added.** The original said a message carries
  an evidence marker when "its turn" recorded outcomes or carried an attention mark — but outcomes
  are written to the assistant's reply and the mark to the patient's message, so no single message
  satisfies both. It was implementable only by pairing a message with its neighbour, which breaks
  for a message with no reply yet, a superseded turn, or a burst. This strengthens "requirements
  are testable and unambiguous" rather than weakening it.
- **User Story 3's prose and first acceptance scenario** were brought in line with it.

The analyze loop itself changed only `plan.md` and `tasks.md`; no requirement text was touched by
it, and no `## Clarifications` bullet was edited at any point.

## Stack reversal, 2026-09-23 (post-plan)

Still **16/16**; no checkbox changed state. FR-004's ban on new dependencies was challenged by the
user and did not survive the check: **no minimal-dependency rule exists in this repository** — not
in `.claude/CLAUDE.md`, `README.md` or `docs/ROADMAP.md` — and the chat service alone declares
twenty runtime dependencies. The rule the repo actually keeps is the one its twelve "technology
choices" README sections show: record the tradeoff.

FR-004 now forbids only third-party *runtime requests* (which still rules out a font service);
FR-004a permits dependencies against a documented tradeoff; FR-004b requires the three controls with
hard focus management to be built on an accessible primitive library. The feature adopts Tailwind
v4 + shadcn/ui.

Effect on this checklist: **"No implementation details" is now a weaker pass than it was.** The spec
names a specific stack in FR-004b's rationale and in a clarification, where before it stated only
properties and left the mechanism to the plan. It is held to be a pass because the stack was a
user decision recorded in the clarification session, which is exactly what that section is for —
but a reviewer who disagrees should retarget FR-004b at "an accessible primitive library" alone and
leave the naming to `plan.md`.

One risk is recorded rather than resolved: `research.md` Decision 7 flags that Radix components may
not render under this jsdom without polyfills, and it is **unmeasured**. T000 settles it before
anything is built on the stack.

## What was outstanding for `/speckit-plan` — all answered

These were never gaps in the spec; they were decisions the plan owned, and it has now made each:

| Question | Answered in |
|---|---|
| How component-scoped styling is achieved, and where tokens live | [research.md](../research.md) Decision 1 · [contracts/tokens.md](../contracts/tokens.md) |
| Which font files to ship, and in what format | research.md Decision 2 — three self-hosted `woff2`, SIL OFL 1.1 |
| How FR-017 reaches the patient pane | research.md Decision 3 — off the poll row App already reads |
| How FR-015a is verified, given jsdom | research.md Decision 4 — a pure predicate plus two stubbed tests |
| Which treatment FR-035b takes | research.md Decision 5 — confirm on leaving a dirty form |
| Which existing tests change under FR-038 | [test-impact.md](../test-impact.md) — every one, with its repair |

Nothing on this list remains open.
