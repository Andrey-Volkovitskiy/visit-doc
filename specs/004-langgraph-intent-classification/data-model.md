# Data Model: Adopt LangGraph + Intent Classification (Phase 1b)

No new database table, column, or migration (research.md #7) — everything below is either an
in-process type (Pydantic/`StrEnum`), a derived/ephemeral view over existing `Message` rows (spec
003's `data-model.md`), or a logged event (full wire contract in
[contracts/log-events.md](./contracts/log-events.md)).

## IntentLabel (enum, not persisted)

A `StrEnum` in `domain/schemas.py`, following this codebase's existing convention for any field
that only ever legally takes a small, fixed set of values (`docs/python-style-guide.md`, mirroring
`domain/models.py`'s `MessageSender`).

| Member | Value | Who can produce it |
|---|---|---|
| `FAQ_QUESTION` | `"faq_question"` | The classifier (a confident, successful classification) |
| `BOOKING` | `"booking"` | The classifier |
| `CALL_STAFF` | `"call_staff"` | The classifier (this feature's name for the escalation category, FR-003) |
| `UNKNOWN` | `"unknown"` | The classifier (FR-003's catch-all — the message genuinely fits none of the above; itself a normal, successful classification, not a failure) |
| `CLASSIFICATION_FAILED` | `"classification_failed"` | Orchestration code only (`graph.py`'s `classify_intent_node`, on any error/timeout/invalid-output from the classification call) — never the classifier itself (research.md #3, FR-007) |

The classifier's own JSON Outputs schema (`output_config.format`, research.md #3) restricts its
output to the first four members only — `CLASSIFICATION_FAILED` is structurally unreachable from a
model response, not just convention.

## IntentClassificationResult (structured-output shape, not persisted)

The parsed, validated result of one `classify_intent()` call — a small Pydantic model in
`domain/schemas.py`.

| Field | Type | Rules |
|---|---|---|
| `intents` | `list[IntentLabel]` | Non-empty; every element drawn from the classifier's 4 real categories (never `CLASSIFICATION_FAILED`) — multi-label, capturing every intent present in the message (FR-001) |

On a failed/invalid classification call, `classify_intent()` raises rather than returning a result
with `CLASSIFICATION_FAILED` in it — the caller (`classify_intent_node`) is what records
`[IntentLabel.CLASSIFICATION_FAILED]` as the logged outcome in that case (research.md #3). A
recorded outcome is therefore always exactly one of: a non-empty list of real categories, or the
single-element `[CLASSIFICATION_FAILED]` — never mixed (spec.md Key Entities: "never both, and
never silently recorded as one of the real categories"). A **third**, unrecorded outcome also
exists: if the message's turn is superseded/cancelled before `classify_intent_node` finishes
(research.md #2), no `IntentClassificationResult` and no log event are ever produced for it at all
— not a `CLASSIFICATION_FAILED` result, an absence, exactly like a cancelled turn produces no
assistant `Message` row (spec 003).

## Derived data: classification context window

Not a stored entity — computed per patient message by `history.py::bound_to_last_n_turns` (new,
research.md #5/#9), whose output then feeds `history.py::to_claude_messages` (also new — the same
formatting function `answer_faq_node` calls, unbounded, for FAQ generation):

| Element | Source |
|---|---|
| Turn-bounded bursts | `history.py::split_into_bursts`'s output over the chat's existing `Message` rows (same `history_rows` query `_event_stream` already performs for FAQ generation — no second DB read, with the current patient message already folded in by `_event_stream` before splitting), truncated by `bound_to_last_n_turns` to the last 5 complete patient-burst-then-response-burst pairs, always keeping the trailing (current, in-progress) burst regardless (research.md #5) |
| Claude-format messages | `history.py::to_claude_messages`'s output over those bounded bursts — one alternating `user`/`assistant` `MessageParam` per burst, the shape `classify_intent()` actually receives |

This is deliberately a *narrower* view than the one `answer_faq_node`'s generation call uses (which
runs `to_claude_messages` over the *unbounded* `bursts` — spec 003 Assumptions, unchanged by this
feature): classification is cost-sensitive (research.md #4), generation quality is not bounded the
same way. Both nodes format via the same `to_claude_messages` function — only how much of `bursts`
each one bounds first differs.

## Runtime state: none new

This feature introduces no new runtime state. Classification's `asyncio.Task` lifecycle is entirely
governed by the existing `agent/generation_registry.py` (spec 003 data-model.md "Runtime state:
in-flight generation registry") — the same per-chat `dict[chat_id, tuple[turn_id, asyncio.Task]]`
that already tracks and cancels the FAQ-answering task now also, by construction, governs
classification, since both run as nodes of the same graph invocation inside that one tracked task
(research.md #1/#2). No second registry, no separate task-lifetime-management module.

## Relationship to existing entities

`IntentClassificationResult` conceptually attaches to one `Message` row (spec 003's entity, sender
`"patient"`) via the conversation turn id — but only in the logged `intent.classified` event
(contracts/log-events.md), never as a column on `Message` itself (research.md #7), and only for a
`Message` whose turn actually completed — a `Message` whose turn was cancelled has neither an
assistant reply nor an `IntentClassificationResult` (research.md #2). `turn_id` already equals a
patient `Message.id` (spec 003 research.md #4), so no new identifier is introduced to make that link
— the existing correlation mechanism (`core/correlation.py::bind_turn_id`) already provides it.

```
Message (sender="patient", spec 003)
   id  ────────────────────────────────  turn_id (existing correlation id, reused, not duplicated)
                                                │
                                                └── intent.classified log event
                                                    intents: list[IntentLabel]  (contracts/log-events.md)
```
