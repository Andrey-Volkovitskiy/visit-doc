# Data Model: Conversational Chat History

## Session

An anonymous visitor's identity, scoped to one browser (no login). Stored in PostgreSQL (`chat`
service's existing database, table `sessions`). Kept as its own entity, distinct from
`Chat`, specifically because spec.md's Future Direction section anticipates a later
**Patient** entity sitting between a `Session` and its chat(s) — see research.md #1.

| Field | Type | Rules |
|---|---|---|
| `id` | `str` (ULID, primary key) | Generated server-side on a visitor's first-ever message using the standard random-payload ULID constructor — **not** the monotonic factory — so it's non-guessable/non-enumerable (FR-017, research.md #1); this value is issued to the browser as the `visitdoc_session_id` cookie and never changes for that browser (research.md #1, #2) |
| `created_at` | `datetime` (UTC) | Set on create, immutable |

No further fields — this phase adds no profile data, no auth, nothing beyond "an anonymous identity
exists." Named `Session`, not `Visitor`/`User`, deliberately: this phase has no authentication, and
a `User`-flavored name would misleadingly suggest otherwise (research.md #1).

**Lifecycle**:
- **Create**: implicitly, the first time a message arrives with no valid `visitdoc_session_id`
  cookie (missing, or naming a session that no longer exists — FR-010).
- **No delete path in this feature**: nothing in this spec ever removes a `Session` — `DELETE
  /chat` (FR-004/FR-005) removes only that session's current `Chat`, not the
  `Session` itself (research.md #7). A `chats.session_id` cascade exists for referential
  integrity regardless (see `Chat` below), but nothing exercises it yet.
- **No automatic expiration**: same "no automatic expiration" posture as `Chat` (FR-011) —
  a `Session` only ever goes away if something explicitly deletes it, which this feature never does.

No state machine — a `Session` simply exists from creation onward.

## Chat

One continuous chat thread between a `Session` and the assistant. Stored in PostgreSQL (`chat`
service's existing database, table `chats`).

| Field | Type | Rules |
|---|---|---|
| `id` | `str` (ULID, primary key) | Generated server-side when a `Session`'s first message in a new chat arrives |
| `session_id` | `str` (foreign key → `sessions.id`, `ON DELETE CASCADE`) | Required. The `Session` this chat belongs to (research.md #1) |
| `created_at` | `datetime` (UTC) | Set on create, immutable |

Exactly one active `Chat` per `Session` is enforced by application logic
(`chat_repository.get_or_create_chat_for_session`), per FR-009 — **not** by a
uniqueness constraint on `session_id`. This is deliberate: a uniqueness constraint would itself need
to be dropped in a later migration once a `Session` can own more than one `Chat` (via
Patients); leaving it as an application-level rule now means that future change only adds code, it
doesn't also have to remove a constraint (research.md #1).

**Lifecycle**:
- **Create**: implicitly, the first time a message arrives for a `Session` that has no current
  `Chat` (a brand-new `Session`, or one whose prior `Chat` was just cleared).
  `GET /chat` never creates one; only `POST /chat` does.
- **Clear** (FR-004/FR-005): `DELETE /chat` deletes the row — the `Session` and its cookie
  are untouched (research.md #7). `messages` for it are removed by the FK cascade (`Message` below)
  — hard delete, no soft-delete/tombstone, mirroring `FaqEntry`'s delete semantics (spec 001
  Assumptions).
- **No automatic expiration** (FR-011): a `Chat` is never removed by anything other than an
  explicit `DELETE /chat`.

No state machine — a `Chat` simply exists from creation until it's cleared.

## Message

A single message belonging to a `Chat`, authored by one sender, ordered within the
chat by when it was sent. Stored in PostgreSQL, table `messages`.

| Field | Type | Rules |
|---|---|---|
| `id` | `str` (ULID, primary key) | For a **patient** message: the same `turn_id` already minted per chat request for structured-logging correlation (`core/correlation.py`'s `bind_turn_id()`) — reused, not a second identifier. For an **assistant** message: a fresh ULID minted at insert time (research.md #4) |
| `chat_id` | `str` (foreign key → `chats.id`, `ON DELETE CASCADE`) | Required |
| `sender` | `str` enum: `"patient"` \| `"assistant"` | Open set, not a hardcoded pair (FR-013) — a third value, `"staff"`, is anticipated by ROADMAP Phase 1d but not introduced by this feature (spec.md Future Direction) |
| `content` | `str` | For `sender="patient"`: the visitor's message as submitted, same 1–2,000 character constraint as spec 001's `ChatRequest.message` (FR-008 of this spec, FR-001a of spec 001). For `sender="assistant"`: the full answer text (grounded case) or the abstention message text (ungrounded case) |
| `grounded` | `bool \| None` | Only meaningful for `sender="assistant"`; always `NULL` for a patient message. Mirrors `ChatDoneEvent.grounded` |
| `citations` | `list[{entry_id, chunk_index, chunk_text}] \| None` (JSONB) | Only meaningful for `sender="assistant"`; always `NULL` for a patient message. Same shape as spec 001's `Citation`; empty list when abstaining, populated when grounded |
| `reply_to_message_ids` | `list[str] \| None` (JSONB, patient message ULIDs) | Only ever set on an **assistant** message: every patient message id it answers, in order. A single scalar FK isn't enough — a burst of several unanswered patient messages merged into one Claude turn (FR-014, `history.py`'s `build_history_messages`) is answered by exactly one assistant message, so this ties a reply to *all* the turns it actually addresses, not just the one that triggered the request, without history-building having to infer that pairing from row order (which a stray or delayed write could otherwise violate). Plain JSONB like `citations` above, not a FK — no per-element referential integrity, but this is diagnostic-only data, never joined on in SQL, and a chat's messages are always deleted together anyway (`chat_id`'s own cascade) |
| `created_at` | `datetime` (UTC) | Set on insert, immutable |

**Ordering**: messages for a `Chat` are listed by `created_at` ascending, via a dedicated
`ix_messages_chat_id_created_at` index — **not** by `id` ascending. Although ULIDs are
lexicographically sortable by creation time within a single writer, that ordering is not reliably
equivalent to `created_at` order across concurrent writers (e.g. two in-flight requests for the
same chat), so `chat_repository.list_messages` sorts by `created_at` explicitly rather than relying
on `id`'s incidental sortability.

**Lifecycle** (research.md #3 — a direct simplification versus this feature's earlier
`ConversationTurn` design, which needed a two-phase insert-then-update write):
1. **Patient message**: inserted once, synchronously, as soon as the message passes validation
   (FR-008) — before generation starts. Never updated afterward.
2. **Assistant message**: inserted once, and only once, when the RAG pipeline completes
   successfully — `content`/`grounded`/`citations` are all set at insert time, together. Never
   updated afterward.
3. **No row for a failed or cancelled attempt**: if the pipeline raises (FR-012) or its task is
   cancelled because a newer message superseded it (FR-015, research.md #9), no assistant row is
   ever inserted for that attempt. There is no "pending" or partially-written state to represent or
   clean up — a `Message` row is either fully written at insert time, or it doesn't exist.

No further transitions — a `Message` is append-only; there is no "edit a past message" feature in
this spec.

**Ordering does not imply strict alternation** (FR-002, FR-014): a chat's `Message` sequence
may contain several consecutive `patient` rows in a row (a burst, or a patient message that never got
a reply, followed by another patient message) before the next `assistant` row.

## Relationships

```
Session (1) ──────< (currently: exactly 1, enforced in code, not schema) Chat
   id                     session_id (FK, ON DELETE CASCADE)
                                │
                                └──────< (many) Message
                                         chat_id (FK, ON DELETE CASCADE)
                                         sender: patient | assistant (open set, FR-013)
```

Deleting a `Chat` atomically removes all of its `Message` rows via the FK cascade
(research.md #7) — there is never a `Message` without a live parent `Chat`. Nothing in this
feature deletes a `Session`, but `chats.session_id`'s cascade exists so a `Chat` can
never outlive its `Session` either, should a delete path be added later. The `Session ──
Chat` relationship is schematically one-to-many already (an ordinary FK, no uniqueness
constraint) even though this feature only ever creates one `Chat` per `Session` at a time —
see `Chat`'s Lifecycle above and research.md #1 for why.

## Derived data: chat context for generation

Not a stored entity — computed per request by `agent/history.py`'s `build_history_messages`:

| Element | Source |
|---|---|
| History | All of the chat's existing `Message` rows, queried once *before* the current patient message is inserted — each becomes a `user` or `assistant` entry per its `sender`, using `content` verbatim (not the retrieval-augmented prompt built for an assistant message at the time, research.md #5) |
| Current message | The just-validated patient message, appended in-memory as the final `user` entry — not re-read from the database |

Consecutive entries of the same role (arising from a burst of patient messages, FR-014, or a patient
message that got no reply, research.md §3/§5) are merged into one message before being sent to
Claude, to satisfy the Messages API's strict alternation requirement — one general rule, not a
per-cause special case (research.md #5). Under cancel-and-restart (FR-015, research.md #9), a
superseded patient message is already a persisted, reply-less row by the time the next message's
pipeline run queries History — so it's already present as a trailing `user` entry the merge pass
folds the new message into, with no separate handling needed.

**Retrieval reuses that same merged trailing entry** (research.md #6) — `search_faq` is called with
the merged current-turn `user` text, not the raw current patient message in isolation, so a burst
like "When can I see" + "Dr. Josh?" retrieves on the combined text rather than on "Dr. Josh?" alone.
For the non-burst case (no prior reply-less message) the merged entry is just the current message
itself, so this is behaviorally identical to always-single-message retrieval — no regression, purely
additive for bursts.

## Runtime state: in-flight generation registry (not persisted)

`agent/generation_registry.py` holds a process-local `dict[chat_id, tuple[turn_id, asyncio.Task]]`
tracking the currently-running reply-generation task per chat, used to implement cancel-and-restart
(FR-015, research.md #9) — the "still current" check itself compares task identity, not `turn_id`;
the `turn_id` half is carried purely so a cancellation can be logged (`turn.cancelled`) against the
specific patient turn that got superseded, rather than only against an opaque task. This is
ephemeral in-memory state, not a database table: it never survives a process restart, and it
doesn't need to — a restart simply means no generation is currently in-flight for any chat, which
is also the correct starting state.

## Superseded entities

This feature's `Message` entity supersedes two prior designs:
- Spec 001's `ChatExchange` (`specs/001-grounded-faq-chat/data-model.md`), which was explicitly
  "transient, not persisted... no conversation persistence required this phase."
- This same feature's own earlier `ConversationTurn` design (one row per patient-message-and-reply
  pair, with a nullable `assistant_reply` and an insert-then-update lifecycle), superseded when
  spec.md was revised to require a flat, sender-tagged, non-alternating message log (FR-013/FR-014)
  and cancel-and-restart semantics (FR-015) — see research.md #3 for why the flat model also
  simplifies persistence, not just the entity shape.
