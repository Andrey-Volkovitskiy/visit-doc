# Specification Quality Checklist: Rescheduling and Cancellation (Phase 1d, part 1)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-29
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

- Eight decisions were put to the user and answered; all are recorded in the spec's
  **Clarifications** section. Three came from `/speckit-specify`, four from `/speckit-clarify`, and
  one — the reach of the listings — was given directly.
  - **Practitioner change** — the original description asked for cancel-old-and-create-new. The
    user changed this: a practitioner change now *modifies* the existing appointment (FR-002),
    which removed the two-halves atomicity problem entirely.
  - **Cancellation** — the appointment is retained and marked cancelled (FR-009), releases its slot
    (FR-010), and is not eligible to be changed (FR-005).
  - **Listings** — two independent axes, time and status, with every combination answerable
    (FR-013). The unqualified question returns future standing appointments only (FR-014); widening
    one axis never widens the other (FR-015); anything reaching into the past is capped at the 20
    most recent (FR-016).
  - **Booking key lifetime** — cancelling releases it, so a freed slot rebooks as a new appointment
    (FR-011). This amends 005's FR-064, which tied key lifetime to the record's existence back when
    cancelling deleted the record.
  - **Change idempotency** — none. Reschedule and cancel are target-state assertions and cannot
    produce a duplicate (FR-019, FR-020); only booking, which creates, keeps a key.
  - **Stale confirmations** — guarded on the start time and practitioner the patient was actually
    shown, refused with a reason of its own (FR-021, FR-022).
- Two requirements go beyond the literal request, both deliberately: **FR-038** (the practitioner on
  each side of a logged change, without which a same-time swap logs as a no-op) and
  **FR-021/FR-022** (the stale-confirmation guard, which adds one field and one refusal reason).
- **FR-012 is the requirement most likely to be under-served in implementation.** Retaining
  cancelled appointments turns every existing appointment read — overlap checks, availability, the
  patient's list — into one that must name the statuses it means. An omitted filter fails silently
  and in the direction of a cancelled appointment blocking a slot or reappearing as booked, so it
  deserves explicit test coverage per read rather than one end-to-end check.
- **FR-006 no longer says the refusal set is closed as booking left it.** The stale-confirmation
  refusal is one new reason, slotted immediately after the not-found reasons in booking's existing
  precedence. That is a real change to 005's `BookingFailureReason` story, not a restatement.
- **Outstanding, judged low impact**: the spec does not say whether the appointment record stores
  *when* it was cancelled. Nothing in the requirements reads such a field — FR-037 already logs the
  cancellation — so it was left to the data-model step.
- Scope is the *first* part of Phase 1d only. Escalation is excluded by FR-042 and remains part 2.
