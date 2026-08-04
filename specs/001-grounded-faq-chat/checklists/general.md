# General Requirements Quality Checklist: Grounded FAQ Chat (Phase 0 Walking Skeleton)

**Purpose**: Broad requirements-quality pass across `spec.md` — completeness, clarity,
consistency, measurability, and coverage — before generating `tasks.md`. Author self-review
timing, standard depth.
**Created**: 2026-07-30
**Feature**: [spec.md](../spec.md)

**Note**: This checklist validates the requirements as written, not the implementation. Items
question whether `spec.md` says enough, says it clearly, and says it consistently — not whether
the eventual code works.

## Requirement Completeness

- [x] CHK001 Is there a measurable Success Criterion corresponding to FR-016 (delete an FAQ entry)? [Gap, Spec §Success Criteria] — Resolved: added SC-006.
- [ ] CHK002 Are requirements defined for how `GET /faq` behaves as the number of entries grows (pagination, sort order)? [Gap, Spec §FR-008]
- [ ] CHK003 Is a minimum meaningful content length specified for FAQ entries, beyond "not missing"? [Gap, Spec §FR-009]
- [ ] CHK004 Is there a defined maximum (or expected range) for how many FAQ entries/passages a single grounded answer can cite? [Gap, Spec §Key Entities]
- [x] CHK019 Is content consisting only of the FR-014 label scaffolding (e.g. "Question: ...\nAnswer:" with nothing after "Answer:") covered by FR-009/FR-017's meaninglessness check, or only literal whitespace/dashes? [Gap, Spec §FR-009, §FR-014, §FR-017] — Resolved: FR-009/FR-017 extended to cover bare `Question:`/`Answer:` labels with no text after them.
- [x] CHK020 If chunk-level filtering (FR-017) ever removes every chunk from an otherwise-accepted entry, is any requirement defined for surfacing that, rather than leaving a silently unretrievable entry? [Gap, Spec §FR-017] — Resolved: structurally impossible, not just handled — FR-017 now states the invariant (FR-009 guarantees meaningful content exists somewhere; chunking discards nothing; so at least one chunk always survives filtering).

## Requirement Clarity

- [ ] CHK005 Is "sufficiently relevant" (FR-005) given any spec-level criteria or example so SC-002 is testable by someone unfamiliar with the implementation? [Clarity, Spec §FR-005]
- [ ] CHK006 Is "fabricated or unsupported answer" (FR-005) defined precisely enough for different testers to judge SC-002's "0% fabrication rate" consistently? [Clarity, Spec §FR-005, §SC-002]
- [x] CHK007 Is empty/whitespace-only FAQ entry content explicitly classified as "missing required content" for FR-009 purposes? [Ambiguity, Spec §FR-009] — Resolved: FR-009 now explicitly rejects whitespace/dash-only content; new FR-017 covers the same rule at the retrieval/chunk level as a backstop.

## Requirement Consistency

- [x] CHK008 Do User Story 1's "source document(s)" language, FR-003, and the Key Entities' "retrieved passage" description consistently describe what a citation identifies — a document, or a verbatim passage? [Consistency, Spec §US1, §FR-003, §Key Entities] — Resolved: US1, Acceptance Scenario 1, and FR-003 reworded to "passage(s)"/entry content, matching Key Entities.
- [ ] CHK017 Does FR-017's parenthetical reference to "chunking" leak an implementation detail into a business-level requirement, contrary to the spec's own "no implementation details" quality bar (Content Quality checklist)? [Consistency, Spec §FR-017]
- [x] CHK018 Do User Story 2's title ("Add and update FAQ content via API") and its Independent Test description reflect that delete is now in scope (FR-016, Acceptance Scenarios 6-7)? [Consistency, Spec §User Story 2] — Resolved: title is now "Add, update, and delete...", narrative and Independent Test both cover delete.

## Acceptance Criteria Quality

- [ ] CHK009 Can SC-001's "correctly cited" be objectively verified without a human judgment call, now that citations are raw retrieved text rather than a named source? [Measurability, Spec §SC-001]

## Scenario Coverage

- [ ] CHK010 The Edge Case for ambiguous/multi-topic questions poses a question ("does the system still ground itself only in what it retrieves rather than guessing?") without stating the resolved behavior — is that behavior actually specified anywhere? [Gap, Spec §Edge Cases]
- [ ] CHK011 Are requirements defined for concurrent modification of the same FAQ entry (e.g., a delete racing an update)? [Gap, Spec §Edge Cases]

## Edge Case Coverage

- [ ] CHK012 Is behavior specified for a visitor question that plausibly matches multiple, unrelated FAQ entries at similar retrieval confidence? [Gap, Spec §Edge Cases]
- [ ] CHK021 Is there a requirement for how a citation's `entry_id` should be handled if the cited FAQ entry is later deleted (e.g. a stale reference surfacing in prior chat history)? [Gap, Spec §Key Entities, §FR-016]

## Non-Functional Requirements

- [ ] CHK013 Are requirements defined, or explicitly deferred, for concurrent visitor chat load? [Gap, NFR]
- [ ] CHK014 Does the spec state whether visitor questions/answers are logged or retained anywhere, given the Chat Exchange entity itself is not persisted? [Ambiguity, Spec §Key Entities, §Assumptions]

## Dependencies & Assumptions

- [ ] CHK015 Is the 20,000-character FAQ entry limit validated against any real clinic policy document, or is it purely an untested assumption? [Assumption, Spec §Assumptions]
- [ ] CHK016 Is the assumption that retrieval "naturally finds nothing relevant" for language-mismatched questions validated, or could it behave differently at larger/more multilingual corpus scale? [Assumption, Spec §Clarifications]

## Notes

- Check items off as resolved (either by editing `spec.md` or by explicitly deciding the gap is
  out of scope for this phase, and noting that decision).
- This is a "Standard" depth, author-facing pass — not a formal release gate.
