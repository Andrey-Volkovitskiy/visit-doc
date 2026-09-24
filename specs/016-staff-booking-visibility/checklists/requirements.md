# Specification Quality Checklist: What staff can see of the schedule (Phase 3a leftovers)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-24
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

- **Named existing contracts, deliberately.** The spec names `request_outcomes`, `FaqVerdict`, the
  evidence marker's FR-026/FR-026a and two test hooks. These are existing product contracts the
  feature must preserve or retire, not implementation choices. The roadmap text it implements is
  framed in the same terms, and specs 011 and 015 set the precedent. No storage layout, RPC shape,
  endpoint path, framework or module is prescribed. The two open "how" questions (a new scheduler
  call or an extended one, and how the panel learns of a change) are left to planning in
  Assumptions.
- **Three decisions were taken without a clarification round**, and are recorded under
  Clarifications with their reasons:
  - the booking record is anchored on the patient message (binding roadmap text, plus the
    missing-reply cases);
  - writes are recorded and reads are not;
  - the window is seven local calendar days from the viewer's clock.

  Each can be revisited with `/speckit-clarify`.
- **Revised 2026-09-24 on user direction:** the practitioner's week moved from the edit view to the
  roster, behind a **Show bookings** / **Hide bookings** toggle at the bottom of each practitioner's
  block (Story 2, FR-007–FR-009, SC-003/SC-004). The checklist was re-run after the change, and every
  item still passes.
