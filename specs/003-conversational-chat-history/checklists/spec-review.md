# Spec Review Checklist: Conversational Chat History

**Purpose**: Full-spec requirements-quality self-review before running `/speckit-tasks` — validates
that spec.md's requirements are complete, clear, consistent, measurable, and cover the scenarios
they claim to, not whether any implementation matches them.
**Created**: 2026-08-06
**Feature**: [spec.md](../spec.md)

**Note**: This checklist tests the *requirements*, not the system. Each item asks whether something
is written down clearly enough — not whether it works.

## Requirement Completeness

- [x] CHK001 Is a requirement defined for whether the anonymous per-browser identifier must be
      non-guessable/unenumerable, so one visitor's chat can't be reached by another guessing or
      iterating identifiers? [Gap, Spec §FR-001/§FR-009/§FR-010] — Resolved: new FR-017.
- [x] CHK002 Are Success Criteria defined for the bursty-messaging and cancel-and-restart
      capabilities (FR-013–FR-016), or do SC-001–SC-004 only cover the pre-burst behaviors
      (FR-001–FR-007)? [Gap, Spec §Success Criteria] — Resolved: new SC-005, SC-006.
- [ ] CHK003 Is there a requirement, or an explicit accepted-limitation note, covering what happens
      if a patient sends messages faster than the pipeline can ever complete a generation — is
      eventual delivery of at least one reply guaranteed, or could a fast-enough burst starve the
      chat of any answer? [Gap, Spec §FR-015]
- [ ] CHK004 Does the spec state whether accessibility (e.g., screen-reader announcement of new
      messages, keyboard operability of "Clear chat") is in scope or explicitly excluded for this
      feature? [Gap]

## Requirement Clarity

- [ ] CHK005 Is "immediately" in FR-005 ("MUST immediately and permanently delete") quantified or
      otherwise made testable, or is it acceptable as-is with an implicit
      synchronous-before-response meaning? [Clarity, Spec §FR-005]

## Requirement Consistency

- [x] CHK006 Do the "Clear chat" action and its confirmation button label ("Delete") use consistent
      terminology, or could a reader expect two different operations? [Consistency, Spec §FR-004] —
      Resolved: confirmation button relabeled "Clear".
- [ ] CHK007 Is the design-rationale clause in FR-013 ("so a third sender can be added later without
      restructuring") separable from its testable requirement ("MUST represent each message as an
      individual, independently ordered entry"), or does bundling a rationale inside a MUST
      statement risk it being read as a requirement in its own right? [Consistency, Spec §FR-013]

## Acceptance Criteria Quality

- [ ] CHK008 Can SC-004's "0% fabrication rate... in manual testing" be objectively verified without
      reference to a specific verification method, or does citing "manual testing" mix the outcome
      with how it's checked? [Measurability, Spec §SC-004]
- [x] CHK009 Is there a Success Criterion corresponding to User Story 1's Acceptance Scenario 4
      (burst messages both taken into account), or does that capability exist only at the
      acceptance-scenario level with no measurable outcome above it? [Traceability, Spec §SC,
      §US1] — Resolved: new SC-005.

## Edge Case Coverage

- [ ] CHK010 Does the spec define which browser tab's view FR-016 applies to when the superseding
      message was sent from a *different* tab than the one whose stream is being cancelled (Edge
      Cases already allows multiple tabs on one chat)? [Gap, Spec §Edge Cases, §FR-016]
- [ ] CHK011 Is the multi-tab edge case ("no additional locking/merging behavior is required")
      revalidated against cancel-and-restart (FR-015/FR-016), or does it predate those and only
      address simple concurrent reads/writes? Is it specified whether a tab that didn't send the
      superseding message reflects the new state live, or only on next reload? [Consistency, Gap,
      Spec §Edge Cases]

## Non-Functional Requirements

- [ ] CHK012 Is the out-of-scope status of rate limiting/abuse throttling (Assumptions)
      cross-referenced from Edge Cases or Requirements, or could a reader scanning only FRs/Edge
      Cases mistake its absence for an oversight rather than a deliberate decision? [Traceability,
      Spec §Assumptions]

## Dependencies & Assumptions

- [ ] CHK013 Is the assumption that context-window limits are "an implementation detail for
      planning, not a functional requirement" checked against any known model context limit, or
      could an unbounded chat eventually exceed it with no defined fallback stated anywhere in the
      spec? [Assumption, Spec §Assumptions]

## Ambiguities & Conflicts

- [ ] CHK014 Does FR-015's "while an earlier message's assistant reply is still being generated"
      clearly cover the retrieval/groundedness-gate phase, or could it be read as starting only once
      token streaming begins? [Ambiguity, Spec §FR-015]
- [ ] CHK015 Is "actually sent" in FR-014's ordering guarantee defined against an observable point
      (e.g., order of arrival), given that a patient's send-time and the server's receipt-time can
      differ under real-world network conditions? [Ambiguity, Spec §FR-014]

## Notes

- Focus: full spec review (all categories, not narrowed to one subsystem).
- Depth: standard — a focused pass, not an exhaustive formal gate.
- Audience/timing: author self-review before `/speckit-tasks`.
- Several items (CHK010, CHK011, CHK014) surfaced from the same underlying seam: the multi-tab edge
  case and FR-015/FR-016's cancellation behavior were specified in different sessions and haven't
  been explicitly cross-checked against each other — worth resolving together if addressed.
- 2026-08-06: CHK001, CHK002, CHK006, CHK009 resolved per user request — FR-017 (non-guessable
  identifier), SC-005/SC-006 (burst and cancellation outcomes), and FR-004's confirmation button
  relabeled "Clear" to match "Clear chat". FR-017 additionally required a research.md/data-model.md
  update: `Session.id` must use ULID's standard random-payload constructor, not the monotonic
  factory, to actually satisfy non-guessability.
