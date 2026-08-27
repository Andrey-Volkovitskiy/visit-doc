# Specification Quality Checklist: Scheduling Service and End-to-End Booking (Phase 1c)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-11
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

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.

### Validation notes (2026-08-11)

- **Sixteen open questions were resolved with the user**, not guessed — recorded in the spec's
  Clarifications section. Two of them (multi-chat sessions, and the single combined
  chat+patient+appointments delete) changed the spec's shape after a first draft and were rewritten
  in, rather than layered on.
- **Re-validated after `/speckit-clarify`** (same day, five further questions plus a user-supplied
  correction). All 16 items still pass; two were materially strengthened rather than merely
  preserved: "requirements are testable and unambiguous" (FR-047's "within a few seconds" became a
  2-second timeout, two attempts, 5-second ceiling) and "edge cases are identified" (lost booking
  confirmations, ambiguous practitioner requests, timezone drift, and unnamed chats during an outage
  are now covered).
- **One earlier clarification was superseded, not duplicated**: the timezone answer originally said
  the stored zone changes when the user changes it deliberately; the session timezone is now fixed
  for the session's life, and the original bullet carries a pointer to the refinement.

### Validation notes (2026-08-12)

- **Second clarify pass, five further questions.** All 16 items still pass; the spec is now 57
  functional requirements and 13 success criteria.
- **One item was failing in substance before this pass and is now genuinely met**: "all functional
  requirements have clear acceptance criteria". FR-042 seeded a practitioner on first visit without
  saying what hours they worked, so SC-001 ("book within two minutes of arriving") was unreachable
  on a literal reading — a practitioner with no schedule offers no times. FR-057 now defines the
  full default (General Practice, Mon–Fri 09:00–17:00, 60 minutes, next pool name) and the seeding
  path is defined to use it.
- **One requirement was deliberately closed rather than expanded**: "his local time should be
  displayed in a conversation" is met by FR-032/FR-033 alone. Per-message timestamps were considered
  and rejected, and that is recorded in Assumptions so it does not read as an oversight later.

### Timezone simplification (2026-08-12, post-clarify)

The stored-per-session-timezone design was removed at the user's direction as over-engineered. Since
every patient, practitioner, and staff member reachable from one session shares a single local time,
no time is ever converted between zones, and a stored zone identifier bought nothing. The spec now
treats all times as plain local date-times (FR-033, FR-043) with the client supplying the current
local date and time (FR-032). Removed with it: absolute-instant storage, the daylight-saving rule,
the travelling-user case, the UTC fallback, and the "which surface changes the timezone" question.
Both superseded clarification bullets are marked in place rather than deleted, so the reasoning
behind the reversal stays legible. All 16 checklist items still pass.

### Validation notes (2026-08-12, third clarify pass)

Two questions, not five — the remaining unknowns are implementation choices, and padding the round
would have produced questions whose answers changed nothing. Both were real:

- **The refactor created one hole it did not close.** With no stored timezone, FR-020's "in the
  past" had two candidate clocks that disagree for any user outside the server's own zone. FR-058
  now names the client-supplied local date-time as authoritative for every past/upcoming/horizon
  judgement, which is the only clock comparable to a stored local wall-clock time.
- **Name assignment was under-specified from the start** and had survived two passes: FR-015 said
  "next unused" for practitioners while FR-011 said only "not already used" for patients, and
  FR-013 never said which name got a number. All three are now one deterministic pool-order rule,
  which is what lets SC-007 assert an exact name instead of a property.
- **"Enforced in the datastore itself" (FR-016, FR-017)** is the one phrase that edges toward
  implementation. It is kept deliberately: Constitution principle III makes datastore-level
  enforcement of booking conflicts a binding requirement, and stating it only as "the system must
  prevent" would lose the part that is actually being specified. No technology is named.
- **FR-021's 90-day horizon and FR-007's 60-minute default** are stated as concrete numbers so they
  are testable; both are recorded in Assumptions as chosen defaults rather than user-supplied values.
- **Scope boundary with Phase 1d** is stated in three places (title, Assumptions, ROADMAP) because
  the phase deliberately ships booking without cancellation — a reviewer who misses that will read
  the missing undo path as an omission rather than a decision.
