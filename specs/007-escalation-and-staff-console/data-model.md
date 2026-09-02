# Phase 1 Data Model: Escalation and the Staff Console (Phase 1d, part 2)

**Feature**: `007-escalation-and-staff-console` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

Two tables in `visitdoc_chat` change, one Qdrant payload widens, and one enum gains a member. No new
table is created in either service, and none is created for the staff member — it is not modelled at
all (FR-022, FR-023).

**Every store is emptied first** (FR-039e). That is a precondition of the schema below rather than a
deployment convenience: two of the new columns are `NOT NULL`, which no pre-existing row could
satisfy.

Everything here is downstream of three facts the feature introduces:

1. a conversation can be **silent** and can **need a person**, and those are not the same fact
   (research #1);
2. a message can carry a **mark** whose lifetime is decided by its kind alone (FR-027c);
3. an FAQ entry has an **owner** and names one **live revision**, which is what makes "listed" and
   "searchable" the same fact (FR-040).

There is deliberately no fourth: **the staff member is not modelled at all** — not a table, not a
column, not a derived value. It is the `staff` value of a message's `sender`, and the label a client
renders from it (FR-022, FR-023, research #10).

---

## Chat datastore (`visitdoc_chat`)

### `chats` — MODIFIED, four columns

| Column | Type | Change | Notes |
|---|---|---|---|
| `escalated_at` | `TIMESTAMPTZ NULL` | **NEW** | Non-NULL means **silenced**: the assistant generates nothing here (FR-009). No deadline — nothing about time passing clears it. |
| `escalation_reason` | `VARCHAR(32) NULL` | **NEW** | `patient_asked_for_person` \| `corpus_could_not_answer`. Set once with `escalated_at`, never overwritten by a later request (FR-007). |
| `assistant_paused_until` | `TIMESTAMPTZ NULL` | **NEW** | The pause **deadline** (FR-018). Written as `now() + interval '2 minutes'`, evaluated as `> now()`, both in SQL — one clock, the database's (research #2). |
| `attention_since` | `TIMESTAMPTZ NULL` | **NEW** | Non-NULL means **a person is needed here**: emphasis (FR-029), the attention total (FR-028), and the list's ordering (FR-027). |

```sql
CHECK ((escalated_at IS NULL) = (escalation_reason IS NULL))
```

An escalation without a reason is unrepresentable (FR-007a: "no escalation is raised without one").

Indexes: `ix_chats_session_attention (session_id, attention_since)` — the console listing filters by
session and orders by attention, and it is the one query polled every two seconds (research #19).

#### Why four columns and not two

`escalated_at` answers *may the assistant speak*; `attention_since` answers *has a person acted*.
They are cleared by different things and they disagree in two of the four cases the spec enumerates:

| Situation | `escalated_at` | `attention_since` | Effect |
|---|---|---|---|
| patient asked for a person | set | set | silenced + emphasized |
| corpus could not answer | set | set | silenced + emphasized |
| assistant failed (FR-003d) | **not set** | set | **not** silenced, emphasized |
| patient message while silent | not set | set (if unset) | emphasized |
| staff posts a message | cleared | cleared | speaks again after the pause, no longer emphasized |
| switch turned on (FR-017b) | cleared | **not cleared** | speaks again, **still** emphasized |

The last two rows are why one column cannot carry both, and they are the conversation-level form of
FR-027d's rule about marks. Research #1 records the five requirements that fix this shape.

#### Derived, never stored

- **`assistant_may_reply`** = `escalated_at IS NULL AND (assistant_paused_until IS NULL OR
  assistant_paused_until <= now())` — FR-017a requires it computed from the two states that actually
  decide, so it cannot disagree with them.
- **`emphasized`** = `escalated_at IS NOT NULL OR attention_since IS NOT NULL` (FR-029).
- **`pause_seconds_remaining`** = `GREATEST(0, assistant_paused_until - now())`, NULL when no pause
  is running — the countdown of FR-017b, computed server-side so two tabs agree (FR-018).

### `messages` — MODIFIED, one column, one new sender value

| Column | Type | Change | Notes |
|---|---|---|---|
| `attention_mark` | `VARCHAR(32) NULL` | **NEW** | One of four kinds, or NULL for "no mark / cleared". Only ever set on a **patient** message. |

`sender` is unchanged — a plain `VARCHAR(16)`, chosen in 003 so a value could be added with no
migration. `MessageSender` gains `STAFF = "staff"` at the Python level only (research #8).

Index: `ix_messages_chat_attention_mark (chat_id, attention_mark) WHERE attention_mark IS NOT NULL`
— partial, because the clearing statement and the "does this chat hold a mark" read both address
only marked rows, and marked rows are a small minority of a chat's messages.

#### The four kinds, and the two properties each one decides

| `attention_mark` | Set when | Silences? | Lifetime |
|---|---|---|---|
| `patient_asked_for_person` | the model called `escalate_to_staff` | **yes** | cleared by a staff message |
| `corpus_could_not_answer` | the FAQ path abstained | **yes** | **permanent** |
| `assistant_failed` | a tool errored, a dependency was unreachable, or a write's outcome is unknown | no (FR-003d) | **permanent** |
| `unanswered` | the message arrived while the assistant was silent | no — it is a *consequence* of silence | cleared by a staff message |

This is FR-027c's grid, and it is the whole of the mark: nothing else is stored beside the kind, and
there is no `cleared_at` for a lifetime the kind already decides (research #7). Clearing is one
statement:

```sql
UPDATE messages SET attention_mark = NULL
 WHERE chat_id = :chat_id AND attention_mark IN ('patient_asked_for_person', 'unanswered')
```

The clearable set is a Python constant rendered into that `IN` list, so "which kinds clear" is one
fact in one place.

**One mark per message.** When one turn raises two calls for the same message, the mark stored is
the highest of `patient_asked_for_person` > `corpus_could_not_answer` > `assistant_failed`; every
call is still recorded in the log (research #6, FR-033).

### `faq_entries` — MODIFIED, two columns and one CHECK

| Column | Type | Change | Notes |
|---|---|---|---|
| `session_id` | `VARCHAR(26) NOT NULL REFERENCES sessions(id) ON DELETE CASCADE` | **NEW** | The owning session (FR-039). |
| `live_revision` | `VARCHAR(26) NOT NULL` | **NEW** | The ULID of the one revision retrieval may search for this entry (FR-042b). |

Index: `ix_faq_entries_session (session_id)` — every console listing, the cap count, and the
live-revision read filter on it.

**Both are `NOT NULL`, and that is only possible because the table is emptied first** (FR-039e,
research #11). The deployment removes every pre-existing session and every pre-existing entry, so
there is no ownerless row for the columns to accommodate. No CHECK constraint is needed: an earlier
draft made both nullable and used a two-armed CHECK to stop them disagreeing, and `NOT NULL` says
the same thing with nothing to disagree about.

**The two `NOT NULL`s are FR-040 as a datastore rule.** Every entry has an owner and names a live
revision, and a published revision always holds at least one chunk (below), so *listed* and
*searchable* cannot come apart — there is no state for a retrievability indicator to report, which
is why FR-040 forbids one. An entry belonging to nobody is not filtered out of retrieval; it cannot
be stored.

`content` and the two timestamps are unchanged. **No `status`, no `pending`/`ready` flag** — the
spec's earlier readiness flag is withdrawn, and the live revision is not a replacement for it: the
index holds several revisions of an entry and cannot say which is current, so the row says.

**No revision history table.** FR-042b keeps only the live revision; the sweep's predicate is "not
the live one", which needs no record of what came before (research #17).

### `sessions` — UNCHANGED, and gains no derived reading either

Not one column, and nothing computed from its `id`. An earlier draft derived a staff display name
from it; there is no display name (research #10). A staff message is identified by its sender, and
its chat's session already says which session it belongs to — so "this session's staff member" needs
no representation anywhere (FR-022). SC-011c's "zero sessions require any migration or backfill" is
true because there is nothing that could need one.

---

## Retrieval store (Qdrant, collection `faq_chunks`)

### `ChunkPayload` — MODIFIED, two fields

| Field | Change | Notes |
|---|---|---|
| `session_id` | **NEW** | The owning session. Used by the **session-wide** delete, which runs after the rows are gone and has no other handle on them (FR-039c). |
| `revision` | **NEW** | The revision this chunk belongs to (FR-042b). |
| `faq_entry_id` | unchanged | Now load-bearing for the sweep, which addresses an entry's revisions without reading the index's contents. |
| `chunk_index`, `chunk_text` | unchanged | |

The collection is **emptied by the same reset** (FR-039e), so every point in it carries a
`session_id` and a `revision` — there is no class of point that a filter has to be relied on to
exclude, and none that would fail `ChunkPayload` validation if a filter were ever wrong.

Points are **immutable**: a save writes new points under a new revision and deletes, overwrites and
modifies nothing (FR-042b). Point ids stay random UUIDs, which is now a property rather than an
accident — nothing addresses a point by id, so nothing can collide with a revision still being read.

Three filters, and no other query reaches the collection:

| Purpose | Filter |
|---|---|
| **Retrieval** (FR-039a, FR-042d) | `must=[MatchAny(revision, <this session's live revisions>)]` |
| **Per-entry sweep** (FR-042h) | `must=[MatchValue(faq_entry_id, id)]`, `must_not=[MatchValue(revision, live)]` |
| **Session delete** (FR-039c) | `must=[MatchValue(session_id, id)]` |

Retrieval's filter carries the session predicate and the live-revision predicate as **one term**:
a revision id is minted by one session's save and never shared, so filtering to that session's live
revisions scopes both at once (research #13). This is the requirement's "a filter on the search
itself, not a check applied to the results afterwards".

Payload indexes are created on `revision`, `faq_entry_id` and `session_id` alongside the collection
(`ensure_collection`), so all three filters are index-backed rather than full scans.

---

## Scheduler datastore (`visitdoc_scheduler`) — UNCHANGED

No column, constraint, or index changes. `DeleteSession` (FR-047) is a new *capability* over the
existing schema: it deletes the session's practitioners and patients, and their appointments follow
by the FK cascades 005 created and 006 deliberately left status-blind, so cancelled appointments go
too.

---

## Enforcement: which rule lives where

| Rule | Enforced by | Not by |
|---|---|---|
| An escalation always has a reason (FR-007a) | `CHECK ((escalated_at IS NULL) = (escalation_reason IS NULL))` | a service-layer assertion |
| Every listed FAQ entry is retrievable (FR-040) | `NOT NULL` on `session_id` and `live_revision` — on a table the deployment emptied first — plus a published revision always holding ≥1 chunk | a per-entry indicator the console renders, or a filter excluding ownerless rows |
| A published revision holds ≥1 chunk | `is_meaningless` rejects empty content at the API boundary, and the chunk filter applies the **same** check to slices of it — so a character that made the whole content meaningful survives in whichever chunk holds it | a count assertion after indexing |
| A session's FAQ entries die with it (FR-039c) | `ON DELETE CASCADE` on `faq_entries.session_id` | an application loop over entries |
| No cross-session read or write (FR-032) | `session_id` in the `WHERE` of every statement, and in the Qdrant filter | a check on the result |
| One publish wins a race (FR-042c) | `AND live_revision = :expected` in the publishing `UPDATE`'s `WHERE` | a read-then-write |
| A mark's lifetime (FR-027c) | the kind's membership of the clearing statement's `IN` list | a stored `cleared_at` |
| A pause expires exactly once, everywhere (FR-018) | `assistant_paused_until > now()` evaluated in SQL | a per-tab timer or a Python clock |

---

## State transitions

### A conversation

```
                    ┌──────────── staff message ─────────────┐
                    │                                        │
  OPEN ──escalate(asked│corpus)──> ESCALATED ──switch on──> OPEN
   │                                    │                     ▲
   │                                    └── (no deadline) ─────┘
   │
   ├──staff message ──┐
   │                  ├──> PAUSED (2 min) ──expiry or switch on──> OPEN
   └──switch off ─────┘        │
                               └── another staff message, or another switch off:
                                   deadline reset to now()+2min

Both arrows into PAUSED write the **same column with the same value**. There is no "manually
paused" state distinct from "paused by a reply" — only the log's `paused_by` records which gesture
it was (FR-017b, research #24). The switch cannot produce an ESCALATED state at all: an escalation
records that the assistant asked for a person, which is a fact about what happened.
```

`escalate(assistant_failed)` is deliberately absent from this diagram: it sets `attention_since` and
marks the message, and moves the conversation between none of these states (FR-003d).

A staff message performs all of it in one transaction: cancel any running generation, insert the
message, clear `escalated_at`/`escalation_reason`, clear `attention_since`, clear every clearable
mark, and set `assistant_paused_until = now() + 2 minutes`.

### An FAQ entry

```
  (nothing) ──create──> LIVE(rev1) ──update──> LIVE(rev2) ──delete──> (nothing)
                            │                      │
                    failed save: still LIVE(rev1)  └─ rev1 becomes superseded, swept per-entry
```

There is no in-flight state, and that is the design's whole point: a revision is published by the
commit or it is not (FR-042c). A failure before the commit changed nothing anyone can observe, so
there is nothing to roll back and no compensating write to half-succeed (FR-042e).

---

## What each API surface reads

| Surface | Reads | Writes |
|---|---|---|
| `POST /chat` (gate) | four `chats` columns, under the chat's advisory lock | the patient message, its `unanswered` mark, `attention_since` |
| `POST /chat` (turn) | the session's live revisions, in the same transaction (research #14) | the assistant message; the end-of-turn escalation |
| `GET /console/conversations` | `chats` + newest message time + whether a mark is outstanding | — |
| `POST /console/chats/{id}/messages` | — | everything in "A conversation" above |
| `POST /console/chats/{id}/assistant` | — | on: clears `escalated_at`, `escalation_reason`, `assistant_paused_until`. off: sets `assistant_paused_until` only. Neither touches `attention_since` or a mark |
| `/faq` (all four) | `faq_entries` scoped to the cookie session | content + `live_revision`, and the chunks under a new revision |
| `/admin/sessions…` | — | the scheduler's session rows, then `sessions` (cascading), then the session's chunks |
