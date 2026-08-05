# Observability & Correlation Requirements Checklist: Structured Logging for App/AI Behavior

**Purpose**: Validate the completeness, clarity, and consistency of the log-structure and per-turn/
per-operation correlation requirements (FR-001–FR-009, FR-006, FR-013, FR-014, FR-020–FR-022, and
their supporting Success Criteria/User Stories) before this feature moves into `/speckit-tasks`.
**Created**: 2026-08-05
**Feature**: [spec.md](../spec.md)
**Depth**: Standard
**Focus**: Observability & correlation structure (security/redaction and error/severity-tiering
requirements are covered by separate, future checklists — out of scope here)

**Note**: This checklist tests whether the *requirements* are well-written — complete, unambiguous,
and internally consistent. It does not test whether any implementation satisfies them.

## Requirement Completeness

- [x] CHK001 Is it explicit whether FR-002's "what FAQ content was retrieved" covers every candidate
      considered — including on a turn that ends up abstaining — or only content that passed the
      groundedness check? [Completeness, Spec §FR-002, §FR-003] — Resolved 2026-08-05: retrieval is
      logged the same way regardless of grounded/abstained outcome (FR-002).
- [x] CHK002 Is there a requirement bounding how many retrieved candidates are logged per turn,
      independent of FR-013's per-field character truncation? [Gap, Spec §FR-002, §FR-013] —
      Resolved 2026-08-05: no cap; every retrieved candidate is logged with its score, highest first
      (FR-002).
- [x] CHK003 Does the spec require FAQ management operations to carry a correlating identifier
      analogous to FR-006's turn identifier, so concurrent FAQ operations can be distinguished from
      one another? [Gap, Spec §FR-007] — Resolved 2026-08-05: no; `entry_id` + timestamp is
      sufficient (Assumptions). **Superseded 2026-08-05** by the CHK012 resolution — reversed to
      yes, see FR-021.
- [x] CHK004 Is there a functional requirement guaranteeing a turn's log entries can be ordered by
      the sequence of pipeline steps (per User Story 3 Acceptance Scenario 2), or is ordering only
      implied by the presence of a timestamp field? [Gap, Spec §FR-006, User Story 3] — Resolved
      2026-08-05: timestamp alone is sufficient; no separate ordering field required (Assumptions).
- [x] CHK005 Does the spec define whether a log entry lost to a logging-mechanism failure (FR-008,
      Edge Cases) is itself surfaced anywhere, or is silent data loss acceptable as written? [Gap,
      Spec Edge Cases, §FR-008] — Resolved 2026-08-05: silent loss is acceptable; not reported or
      retried — servicing the request takes priority (FR-008, Edge Cases).

## Requirement Clarity

- [x] CHK006 Is "scores" in FR-004 ("citations and scores") defined anywhere in the spec — which
      scores, and how they relate to (or differ from) FR-002's retrieval outcome? [Ambiguity, Spec
      §FR-004] — Resolved 2026-08-05: it's the RAG similarity score, the same relevance score
      FR-002 captures per retrieved candidate (FR-004).
- [ ] CHK007 Is "candidate grounding" (FR-002) defined precisely enough to be distinguishable from
      the groundedness verdict itself (FR-003)? [Clarity, Spec §FR-002, §FR-003]
- [ ] CHK008 Can "single, centralized place" (FR-014) be verified without a source-code-structure
      judgment call, or is it inherently a design-review criterion rather than an externally
      observable one? [Measurability, Spec §FR-014]
- [ ] CHK009 Is "step-specific detail" (FR-009) grounded in a defined, closed set of steps, or is the
      set of possible "step/event types" intentionally left open-ended? [Clarity, Spec §FR-009]

## Requirement Consistency

- [x] CHK010 Do FR-001 ("log the visitor's incoming message text in full") and FR-013 (which names
      the visitor's message as an explicit truncation example) read as contradictory without
      cross-referencing the Edge Cases note that messages are already capped at 2,000 characters
      elsewhere in the system? [Consistency, Spec §FR-001, §FR-013] — Resolved 2026-08-05: "in full"
      dropped from FR-001; FR-013 alone governs.
- [ ] CHK011 Does FR-006's "every log entry produced during a single chat turn" clearly include or
      exclude the non-turn-scoped critical-event entry FR-018 describes as occurring "mid-turn"?
      [Ambiguity, Spec §FR-006, §FR-018]
- [x] CHK012 Are "step" (FR-009), "pipeline step" (FR-005), and "retrieval step" (FR-002) used
      consistently for the same concept, or could a reader reasonably interpret them as different
      granularities? [Consistency, Spec §FR-002, §FR-005, §FR-009] — Resolved 2026-08-05: expanded
      rather than merely clarified — embedding and (for FAQ ops) chunking/embedding are now their
      own logged sub-steps (FR-020, FR-022), and FR-005's step list was extended to include
      "embedding" for consistency.
- [ ] CHK013 Is the Edge Cases claim that visitor messages "never actually need truncation in
      practice" reconciled with FR-013's own text, which lists the message as a truncation example
      without that caveat? [Consistency, Spec Edge Cases, §FR-013]

## Acceptance Criteria & Measurability

- [ ] CHK014 Can SC-002's "100% of log entries can be correctly grouped back to the single turn" be
      evaluated as written, given that FAQ-operation and non-correlated critical-event entries have
      no turn identifier by design (FR-007, FR-015)? [Ambiguity, Spec §SC-002]
- [ ] CHK015 Is SC-006 ("read and understand a chat turn's full trace... alone") anchored to an
      objective standard (e.g., the specific fields User Story 1 already enumerates), or does it
      depend on a given reader's subjective familiarity with the system? [Measurability, Spec §SC-006]
- [ ] CHK016 Is SC-008's "modifying only one part of the system" independently verifiable by a
      reviewer without reading the implementation? [Measurability, Spec §SC-008, §FR-014]
- [ ] CHK017 Is SC-007 ("no single log entry ever displays more than 2,000 characters of any one text
      field") testable against every field FR-013 covers, or only the specific examples FR-013 names?
      [Measurability, Spec §SC-007, §FR-013]

## Scenario Coverage

- [ ] CHK018 Are requirements defined for what a turn's log trace looks like while the turn is still
      in progress — are entries emitted incrementally per step, or only once the turn finishes? [Gap,
      Spec §FR-006, User Story 2]
- [ ] CHK019 Are requirements defined for a message exactly at the 2,000-character validation
      boundary, given FR-013's truncation rule applies at ">2,000", not "≥2,000"? [Edge Case, Spec
      §FR-013]
- [ ] CHK020 Does the spec address whether a single chat turn could ever produce more than one
      turn-scoped error entry (e.g., a retried step failing twice), or is exactly one error entry per
      failing turn assumed? [Gap, Spec §FR-005]

## Dependencies & Assumptions

- [ ] CHK021 Is the dependency between FR-006 (turn correlation) and the not-yet-built
      conversation/session concept (Assumptions) bounded clearly enough that a future reader won't
      assume today's turn identifier already spans multiple turns of a conversation? [Clarity, Spec
      Assumptions]

## Ambiguities & Conflicts

- [ ] CHK022 Is it unambiguous which requirement governs a FAQ entry's retrieval-time content logging
      (FR-002) versus its authoring-time change logging (FR-007), for an entry that is retrieved
      around the same time it's being edited? [Ambiguity, Spec §FR-002, §FR-007]

## Notes

- Focus: Observability & correlation structure (FR-001–FR-009, FR-006, FR-013, FR-014 and directly
  supporting Success Criteria/User Stories), selected over security/redaction and
  error/severity-tiering, which are better suited to their own dedicated checklists.
- Depth: Standard.
- Audience/timing: pre-`/speckit-tasks` author self-review (no `tasks.md` exists yet for this
  feature).
- Items marked `[Gap]`/`[Ambiguity]`/`[Consistency]` indicate a possible spec update, not necessarily
  a defect — some may resolve to "intentionally left flexible" on review.
