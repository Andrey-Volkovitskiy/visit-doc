# Specification Quality Checklist: One Message, Several Requests (Phase 1g)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-09
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

- **Validation run**: 2026-09-09, first iteration, all items pass. Re-validated the same day after
  `/speckit-clarify` integrated five answers; no item changed state. 56 functional requirements,
  15 success criteria, 5 prioritized user stories, 16 edge cases.
- **Vocabulary carried from earlier phases, not implementation detail.** "Classifier", "segment",
  "similarity floor", "rerank floor", "verdict", "escalation cause" and "attention mark" are this
  product's own established terms, defined in `specs/008-reranked-retrieval-pipeline/` and
  `specs/009-small-talk-and-escalation/`. No language, framework, datastore, model provider or
  wire-format name appears in the spec (verified by search).
- **No [NEEDS CLARIFICATION] markers were needed.** Three decisions that could have been questions
  were resolved from `docs/ROADMAP.md`, which is binding scope guidance, and each is recorded in
  Assumptions with its reasoning (five further decisions were settled interactively later the same
  day and are recorded in the spec's own Clarifications section):
  1. *What a turn does when one FAQ request is answerable and another is not* — it abstains on the
     FAQ half as a whole (FR-042). Partial serving is Phase 1h's named deliverable, and doing it here
     would need 1h's composer constraint. Not a regression: such a message abstains entirely today.
  2. *What happens above the three-segment cap* — the least separable requests are combined, never
     dropped (FR-006a), and the fact is recorded (FR-007). Under-splitting is today's behaviour;
     losing a request is a new failure.
  3. *Whether the booking specialist runs per segment* — it runs once over its segments (FR-022).
     Retrieval is per-request because its gates are per-query; booking's invariants are per-patient.
- **SC-009 is deliberately stated in user-perceived terms** ("about as long as a patient asking
  one") rather than a millisecond target: the wall-clock number would be measuring the model and
  search providers, while what this phase controls is that the two retrievals overlap. The call-count
  criteria (SC-007, SC-008) are what bound the work itself.
- **The one deliberate lossy value is named rather than hidden.** FR-043 collapses several differing
  abstentions into one turn-level verdict by a fixed rule, and FR-044 requires every request's own
  outcome in the record so nothing is lost outside the summary field. That collapse is exactly what
  Phase 1h removes, and it is called out in the spec rather than left for a reader to discover.
