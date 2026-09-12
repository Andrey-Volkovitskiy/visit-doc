# Specification Quality Checklist: Metrics Over the Golden Set (Phase 2b)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-12
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

### Validation record — 2026-09-12

Checked once, against the criteria above, and against the data the spec claims things about. What
the pass looked at, and what it changed:

- **Implementation detail.** A measurement harness cannot avoid naming *what it measures*, so the
  requirements name the published surface a case is driven through, the stored per-request record,
  and the classification and retrieval events whose field contract two earlier phases already froze
  (`specs/008-.../contracts/log-events.md`, `specs/010-.../contracts/log-events.md`). Those are the
  inputs, not the design: no requirement names a language, a library, a file layout, or a command.
  The one operational fact that survives — that a run needs the stack running — sits in Assumptions,
  where it is a dependency rather than a description.
- **Three claims were checked against `cases.json` and corrected**, rather than carried as written:
  1. The unserved-answerable metric originally counted every labelled-answerable request that went
     unserved. Six cases in `overriding-segment` pair an answerable question with an urgent
     condition, distress, a request for a person, or a booking for another — turns that spec 009
     *requires* to answer nothing. As first written, a perfectly-behaving system would have scored
     six defects on the metric the roadmap says should be zero. FR-031a now excludes them by name,
     and SC-006a is the criterion that catches the mistake coming back.
  2. `not-authorized` was grouped with them, and does not belong: an unauthorized request escalates
     *without* silencing the assistant, so the four cases pairing one with a real question are still
     expected to answer it. They stay in the denominator.
  3. `call_staff` was written as a family name. It is an intent; the families are the fourteen
     `README.md` lists.
- **Testability.** Every success criterion is a count, a share, or a conservation check over a named
  set — SC-002 is the strongest of them, since a harness that quietly drops a request cannot make
  the three states sum to 190.
- **Scope boundedness.** Out of Scope names the six things a reader would otherwise assume this
  phase touches, the first two being 2c's gate and 2d's tracing.

Three decisions are recorded rather than left open, and are where a reviewer should push back first
if they disagree:

- **There is one run shape, and it is a full turn** (FR-049, FR-049a). The clarification session
  first settled on two tiers, a cheap classifier-only one beside the full one, and the user reversed
  it for uniformity before any of it was planned. The reversal is the stronger position: a cheap
  tier could only have called the classification step directly — a posted turn always runs its whole
  pipeline — so its segmentation numbers would have described a different execution path from every
  other metric in the same report. What the cheap tier was buying is bought by FR-044 instead, since
  re-scoring a stored run costs nothing. Cost is now controlled only by running fewer cases, which
  makes 2c's cadence question the whole of that decision rather than half of it.
- **The set's labels are extended in this phase** (FR-037, FR-038). The user chose fixtures over
  scoring end-to-end task success from a denominator of four. That is data work on 2a's artifact
  inside 2b, and the last Out of Scope bullet is the fence around it: the fixture is added because a
  roadmap metric cannot be scored without it, and nothing else in the data is re-opened.
- **Retrieval ranking is read from the log, not the record** (FR-030). The roadmap says 2b computes
  from the per-request record; 1e says the same phase computes from its events. Both are true and
  they answer different questions — the record says what the turn decided and stood on, the log says
  what the ranking was, and hit@k is a question about a ranking. The spec states the split rather
  than letting a reader find the two sentences and assume one of them is stale.

One thing the spec decides that the user was not asked about: **the unserved-answerable share is the
one metric whose denominator comes from the label rather than from the aligned requests** (FR-031),
so a question lost to a segmentation mistake still counts as a question the patient did not get an
answer to. It is stated with its reasoning so it can be reversed on review.
