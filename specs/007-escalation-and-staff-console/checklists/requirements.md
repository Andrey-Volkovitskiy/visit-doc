# Specification Quality Checklist: Escalation and the Staff Console (Phase 1d, part 2)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-31
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

Re-validated after a fifth clarification session on 2026-09-01 (4 questions, on scope and posture
outside the FAQ path). 16/16 items passing throughout. Cumulative state of the feature, and what a
reviewer should look at:

1. **Calling staff and silencing the assistant are separate consequences.** Three triggers call
   staff; only two silence (patient asked for a person, corpus could not answer). An assistant
   *failure* marks and emphasizes but never silences, so a transient outage cannot cost a
   conversation its assistant (FR-003, FR-003d). This resolved a live contradiction between SC-001a
   and SC-009e.

2. **Two attention axes, deliberately non-aligned.** Which mark silences and which mark is permanent
   are different questions whose answers disagree on two of the four kinds. FR-027c states the grid
   explicitly rather than leaving it to be inferred.

3. **The staff member is not an entity, and has no name either.** Nothing is stored for it and
   nothing is derived for it: a message labelled *Staff* (beside an assistant's *AI assistant*) is
   the whole of the concept (FR-021, FR-022, FR-023). This removed a one-to-one table, a uniqueness
   constraint for an invariant construction already guarantees, a migration — and then, in a later
   pass, the name pool and its derivation as well. FR-022a and SC-011c are withdrawn rather than
   edited, and are kept in the spec as withdrawals so a reader is not left wondering where they
   went. Trade named in Key Entities: giving staff any real attribute later means introducing the
   record then.

4. **The FAQ corpus is session-scoped and starts empty.** No template, no seeding, no corpus step in
   provisioning (FR-039, FR-039b). Combined with FR-003c (no empty-corpus exemption), a new
   session's **first FAQ question escalates and silences that conversation**. This is specified as
   expected behavior, but it is an emergent consequence of two separate answers and is the most
   surprising thing in the feature.

5. **Admin session deletion added** (FR-046→FR-052), guarded by one secret in environment
   configuration. It gives FR-039c a trigger. It is explicitly not a user role — FR-031 stands — and
   it needs a session-level delete the scheduling service does not have.

6. **The FAQ write path is changed, not merely called** (FR-042a→FR-042i). Indexed chunks become
   **immutable revisions**: a save embeds first, writes its chunks under a new revision without
   touching the old one, then makes the change visible in a single local commit that names that
   revision live; retrieval searches live revisions only; the compensating revert is removed; the
   delete ordering is reversed (row first, chunks swept after); and a failure leaves the entry
   exactly as it was, still retrievable on its previous text, reported as retryable. This removes
   the destructive step rather than reporting its consequences. The accepted cost is **leaked
   storage** — superseded chunks, unreachable throughout, swept idempotently by the entry's next
   save or its session's deletion (FR-042h, FR-042i).

6c. **The sweep is scoped to one entry, and deliberately not to a session** (FR-042h). Its predicate
   is "this entry's chunks whose revision is not the live one", which covers a superseded revision
   and a revision written by a save that never published with the same clause — so a retry clears
   its own failed attempt. A session-wide predicate was considered for its broader healing and
   rejected: it would delete a concurrent save's chunks in the window between their write and the
   commit that publishes them, producing exactly the row-vouching-for-missing-chunks state this
   design exists to prevent. Making it safe would need a grace period on write time, to reclaim
   storage nobody can reach.

6d. **The per-entry retrievability indicator is dropped** (FR-040, FR-041). Under revisions, a row
   names a live revision only if a save published one and a published revision always has chunks —
   the content validator and the chunk filter apply the same meaningfulness check, so no save can
   publish nothing. Every listed entry is therefore retrievable, and a signal that can never fire is
   worse than none. User Story 5 is re-premised on the invariant rather than on watching for
   divergence. Index loss occurring *outside* the write path is named as out of scope.

6e. **Two guards are distinguished, and only one is in scope** (FR-042c). The staleness guard uses
   the revision read when the operation began, protecting the **index** across the write-then-commit
   window. Preventing a **lost update** between two views one person has open would need the client
   to carry the revision it loaded, and is out of scope: FR-031 gives a session exactly one staff
   member. The upgrade path is left open since the revision already exists.

6f. **Empty and unreachable are separated on the retrieval path** (FR-042j). Retrieval now reads the
   session's live revisions before searching, and an empty result and a failed read both yield no
   revisions — so an unreachable store could silently become "I don't have a confident answer",
   which is the "one value, one meaning" collapse the project's principles forbid. They are
   required to produce different outcomes.

6g. **A per-session corpus cap of 200 entries** (FR-039f), enforced at create — and scoped
   deliberately to the corpus alone. It exists to bound a *mechanism*: retrieval carries the
   session's live revisions as a filter term on every FAQ turn (FR-042d), so corpus size sits on the
   hot path. It is expressly not an anti-abuse rule, and FR-039g declines to extend it —
   practitioners, chats, and messages stay unbounded on a login-less surface, with admin deletion
   as their only reclaim. A reviewer should read that asymmetry as chosen: the argument for capping
   them is identical, and it is declined because no design here is sized against them.

6h. **A failing sweep is silent by decision** (FR-042h). No event of any kind is raised — expressly
   including the critical dependency event the rest of the FAQ path raises when the retrieval store
   is unreachable, since that event means an operation could not be completed and a sweep is not
   one. The leak is bounded by the corpus cap, the entry's next save, and the session's deletion, so
   the reviewer should read the silence as chosen rather than missed.

6i. **The deployment resets every store** (FR-039e). An earlier answer left ownerless entries in
   place as inert leftovers that no query reached; this one deletes every pre-existing session and
   every pre-existing entry instead, in all three stores. That is what makes `session_id` and
   `live_revision` `NOT NULL` — an entry belonging to nobody stops being a state a filter excludes
   and becomes one that cannot be written. It is destructive and irreversible, and rests on the
   same synthetic-data precondition FR-045a already states for the absence of a retention policy;
   a reviewer should check that precondition rather than the deletion.

6a. **An earlier readiness-flag design was withdrawn.** A *pending/ready* state on the entry was the
   previous answer; immutable revisions remove the in-flight state it recorded, along with the state
   machine, the retrieval exclusion rule, the fallible content rollback, and the human retry a
   working entry's availability depended on. The draft's original objection — that a stored flag
   would duplicate a fact the stores already determine — is reinstated as a result.

6b. **A transactional outbox was considered and rejected**, with the reasoning recorded in
   Assumptions: `docs/ROADMAP.md` places it in Phase 3+ and constitution Principle I forbids pulling
   a platform layer forward; it needs a background worker this phase does not have; and there is no
   correctness hole left for it to close, since publishing a revision is a single-store commit. The
   only thing it would automate is the superseded-chunk sweep, which already converges.

7. **Four pieces of working behavior are deliberately changed**, all named in Dependencies: FAQ
   corpus scoping, the merge-consecutive-unanswered-messages rule, staff messages cancelling an
   in-flight generation, and the FAQ write ordering above — the last of which also supersedes the
   ordering rule recorded in `.claude/CLAUDE.md`.

8. **"No new backend" remains inaccurate** for this half of Phase 1d — practitioner administration
   (HttpOnly cookie vs. the scheduler's `X-Session-Id` requirement) and now session-level deletion
   both need capabilities that do not exist.

9. **Three decisions made beyond what was asked**, all flagged: the assistant switch works in the
   escalated state (or an unanswered escalation silences a conversation permanently); the switch was
   later made to work in **both** directions, so staff can take a conversation before writing
   anything, with "off" reusing the existing 2-minute pause rather than adding state (FR-017b,
   FR-017c); and the attention total from the first session was retained.

10. **Four boundaries are now declared rather than inferable**, all added in the fifth session:
   nothing but the corpus is bounded (FR-039g); the admin capabilities are public HTTP routes
   with four properties that follow from that — header-carried secret, constant-time comparison,
   exclusion from any published schema, and fail-closed when unconfigured (FR-048a); data retention
   is out of scope on a stated synthetic-data precondition (FR-045a); and accessibility and
   localization are out of scope with the app English-only (FR-045b). Each carries its cost in the
   text, including the sharp one — emphasis is visual weight alone, so the staff side's primary
   signal has no non-visual form.

No items require spec updates before `/speckit-plan`.
