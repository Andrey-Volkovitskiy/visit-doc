# Contract: the chat service's HTTP surface — the 007 delta

**Feature**: `007-escalation-and-staff-console` | **Date**: 2026-09-01

Extends the surfaces 001 and 003 established. Everything not restated here is unchanged —
`POST /chats`, `GET /chats`, `DELETE /chats/{id}`, `GET /chats/{id}/messages`, and `POST /chat`'s
request body all keep their shapes. (`PATCH /chats/{id}/patient` was listed here too; it is
withdrawn along with patient renaming — see 005 FR-048 as amended.)

Three things change and two are added:

| | |
|---|---|
| **CHANGED** | `POST /chat` gains a fourth terminal event and a silent path |
| **CHANGED** | `GET /chats/{id}/messages` renders a third sender |
| **CHANGED** | `/faq` becomes session-scoped |
| **NEW** | `/console/*` — the staff side, and the practitioner proxy |
| **NEW** | `/admin/*` — maintenance, invisible to the schema |

**Session scoping is uniform and has no exception** (FR-032). Every route below except `/admin/*`
reads the `visitdoc_session_id` cookie and puts the session in the `WHERE` clause of its statement —
never in a check afterwards. A resource belonging to another session is reported exactly as one that
never existed: `404`, with the same body. No route accepts a session id as a parameter, and no
response ever contains one.

---

## `POST /chat` — CHANGED: the silent path

The request body is unchanged. Before anything else runs — before classification, retrieval, any
tool call, and any generation call (FR-009, FR-015) — the handler reads the conversation's state in
the same locked transaction that stores the patient's message.

**When the assistant may not speak**, the response is still `200` and still NDJSON. It carries the
patient's message into the thread and terminates with one line:

```json
{"type": "silent"}
```

Nothing else is emitted: no token, no `done`, no error. The client renders nothing for it (FR-019).
The stored message carries `attention_mark = "unanswered"`, and the conversation's `attention_since`
is set if it was not already.

### Terminal events — one added

| `type` | Meaning | Client |
|---|---|---|
| `done` | the turn produced a reply | render it |
| `cancelled` | this turn produced no reply: a newer message superseded it, or a person took the conversation over before its reply was written | discard the tokens received |
| `silent` | **NEW** — the assistant may not speak here; the message is kept | render nothing |

`silent` is a third value because the other two already mean something else: `cancelled` tells a
client to discard a message that is in fact being kept, and an empty `done` announces a reply that
does not exist (research #4).

### `done.answer_source` — CHANGED: a fourth value

005's `chat-api.yaml` publishes it as `"faq" | "booking" | "merged"`. It gains **`hand_off`**:

```json
{"type":"done","grounded":null,"citations":[],"message":null,"answer_source":"hand_off"}
```

A turn whose classified intents included `call_staff` produces no answer at all — no retrieval, no
generation, no booking — and its whole reply is the sentence telling the visitor a staff member has
the conversation and will reply in it (FR-005, and the first of the spec's Edge Cases). So it is a
fourth *source* rather than a variant of the other three, which each name something that answered.

`grounded` is `null` and `citations` is empty for the same reason they are on a booking reply: the
turn never retrieved anything, so it is neither grounded nor abstaining and has nothing to cite. A
client renders the streamed tokens, exactly as it does for the other three.

### Cancellation by a staff message

A staff message posted while a turn is generating cancels it, and the reply is discarded entirely
(FR-013a). Two mechanisms carry that, because the cancellation alone cannot reach every moment:

- **While the turn is registered** — a reply is persisted only if its task is still the registered
  one when it finishes, so a cancelled turn stores nothing. The staff-post handler and the turn
  handler serialize on the chat's existing advisory lock, and the turn's task is registered
  **inside** that lock, so no turn can start between a cancel and the pause that follows it.
- **After it has deregistered** — a turn deregisters itself *before* taking the lock its writes
  need, deliberately: a staff post takes that lock first and only then asks for a cancellation, so a
  turn still registered while queued on the lock would be a cancellation waiting on the very lock
  its canceller holds. In that window `cancel_for_chat` finds nothing, so the reply's own `INSERT`
  carries the guard instead — it writes only where no staff message has arrived since the patient
  message it answers and no pause is running. A refused write ends the turn with `cancelled`, on
  the same terms as a supersede.

The same guard covers the switch (FR-017c): turning the assistant off writes the pause, and a pause
is one of the two things the guard reads.

A **patient's** own next message is deliberately not covered by it. Nothing has contradicted the
answer they already asked for, and a turn that deregistered before the newer one registered won the
supersede race outright — so its reply stands, and the registry check remains the only thing that
decides that case.

---

## `GET /chats/{chat_id}/messages` — CHANGED: a third sender

`MessageOut` gains no field. `sender` widens to `"patient" | "assistant" | "staff"`, and two
existing fields gain a rule:

- `grounded` and `citations` are `null` on a staff message, as they already are on a patient
  message — a staff message was never retrieved against.
- `attention_mark` — **NEW**, `null` or one of `patient_asked_for_person`,
  `corpus_could_not_answer`, `assistant_failed`, `unanswered`. Only ever non-null on a patient
  message.
**No `staff_name` field is added, and none may be** (FR-021, FR-022). `sender` already carries
everything the patient is told about who wrote a message: a client renders **"Staff"** for
`sender: "staff"` and **"AI assistant"** for `sender: "assistant"`, and leaves the patient's own
messages unlabelled (FR-023). A name field would be a second source for a fact the sender already
states, and it would name a person this system does not have.

The route is unchanged otherwise, and it is what the console reads too: FR-025 requires the staff
view to show the entire thread, which is exactly what this already returns.

---

## `GET /console/conversations` — NEW

The one polled endpoint. It serves **both** panes (research #19): the staff list renders it, and the
patient pane refetches the active chat's messages when that chat's `last_message_at` advances past
what it holds.

```json
{
  "attention_total": 2,
  "conversations": [
    {
      "chat_id": "01J...",
      "patient_name": "Jane Austen",
      "last_message_at": "2026-09-01T12:03:11.418Z",
      "emphasized": true,
      "escalated": true,
      "escalation_reason": "patient_asked_for_person",
      "attention_since": "2026-09-01T12:02:58.002Z",
      "assistant_may_reply": false,
      "pause_seconds_remaining": null
    }
  ]
}
```

| Field | Rule |
|---|---|
| `conversations` | **Every** chat in the session, emphasized or not (FR-024, FR-027). Not a queue of escalated ones. |
| ordering | emphasized first; within each group, `attention_since` ascending — longest wait first (FR-027). Unemphasized chats keep the existing list order (newest activity first). |
| `emphasized` | derived: escalated, or `attention_since` set (FR-029). Every reason looks identical at this level (FR-029, and the spec's Assumptions). |
| `escalated` / `escalation_reason` | the silencing state and the reason that raised it. `escalation_reason` is `null` exactly when `escalated` is `false`. Only `patient_asked_for_person` silences (FR-003d), so it is the only value this field carries today — a corpus gap or a failure emphasizes the conversation without escalating it, and shows up in `emphasized` and in the message's `attention_mark` instead. |
| `assistant_may_reply` | derived from escalation **and** pause, never stored (FR-017a) — the switch's position. |
| `pause_seconds_remaining` | integer while a pause is running, `null` otherwise — including while escalated, where there is no deadline to show (FR-017b). Computed server-side, so two tabs count down together (FR-018). |
| `attention_total` | the count of emphasized conversations. A conversation counts **once**, however many marks sit inside it (Edge Cases). |

A session with no chats returns an empty list and `attention_total: 0` — not an error. A request
with no session cookie returns the same empty shape, exactly as `GET /chats` already does for a
first arrival.

---

## `POST /console/chats/{chat_id}/messages` — NEW

Post as staff, into the patient's own thread.

```json
{ "content": "Hi — I've looked at your bill and the second charge was an error." }
```

`content`: 1–2000 characters, rejected by the same meaningless-content validator patient messages
face. `201` returns the created `MessageOut`.

**One transaction performs all of it** (FR-009a, FR-013, FR-014, FR-027c, FR-029a), in this order:

1. cancel any generation running for this chat, discarding its partial reply (FR-013a);
2. insert the message with `sender = "staff"`;
3. clear `escalated_at` and `escalation_reason` — replying **is** taking the conversation, so there
   is no separate resolve action (FR-009a);
4. clear `attention_since` — a person spoke (FR-029a);
5. clear every **clearable** mark in the chat, however many accumulated, in one statement; permanent
   marks are untouched (FR-027c);
6. set `assistant_paused_until = now() + 2 minutes`, whether or not the conversation was escalated
   (FR-013), restarting it if a pause was already running (FR-014).

Allowed in **every** conversation of the session, escalated or not (FR-024). There is no conversation
a staff member must escalate first in order to speak in, and none they may not speak in twice.

`404` if the chat belongs to another session or does not exist — including when it stopped existing
partway through the six steps, where the cascade that removed the chat has already taken the
inserted message with it. The insert having committed does not make `201` the honest answer: by the
time the response is written the message provably is not in the thread, and a staff member told
their reply landed would find no trace of it on the next read.

---

## `POST /console/chats/{chat_id}/assistant` — NEW

The switch (FR-017).

```json
{ "enabled": true }
```

**`enabled: true`** clears `escalated_at`, `escalation_reason` and `assistant_paused_until`. Valid
in either silenced state and in neither: turning on an assistant that was already on changes nothing
and is not an error.

**`enabled: false`** sets `assistant_paused_until = now() + 2 minutes` — the **identical write** a
staff message performs — restarting it if a pause was already running (FR-014), and cancels any
generation in flight, discarding the partial reply on FR-013a's exact terms (FR-017c). It is valid
in any state; in an already-escalated conversation it changes nothing observable, since an
escalation has no deadline and still governs.

**Neither direction touches `attention_since` or any message mark** (FR-017b, FR-029a). A
conversation stays emphasized and its unanswered messages stay marked across both. Taking a
conversation is not answering it, and handing it back is not answering it either — which is what
makes the control safe to use freely.

**`false` writes no state of its own.** It is the same column, the same duration, the same expiry
and the same countdown as a staff message's pause, so nothing downstream distinguishes a pause a
person asked for from a pause a message caused — not the gate, not a reload, not a second tab. Only
the log's `paused_by` records which it was, and that exists to make a silence traceable, not to make
it behave differently (research #24).

**The two directions are asymmetric on purpose**: `true` ends an escalation as well as a pause;
`false` starts only a pause and can never create an escalation. An escalation records that the
assistant asked for a person and none has dealt with it — a fact about what happened, not a switch
position, and not something a staff member can manufacture.

Returns the conversation's row in the same shape `GET /console/conversations` uses, so the caller
does not have to wait for the next poll to see the result.

`404` if the chat belongs to another session or does not exist — including when it stopped existing
between being resolved and being written to, where the write matches no row and neither direction of
the switch moved: nothing was written, so there is no state to report. The staff message route
answers that window the same way, though not for the same reason — there the insert had already
landed, and the cascade took it away again.

---

## `/console/practitioners` — NEW (a proxy, not a second implementation)

Four routes — `GET`, `POST`, `PATCH /{id}`, `DELETE /{id}` — forwarding to the scheduler's existing
`/practitioners` REST API with `X-Session-Id` taken from the cookie session (FR-036, SC-012). A fifth,
`GET /console/specialties`, forwards the same way to the scheduler's `/specialties`, so the console's
specialty chooser is populated from the set that owns it rather than from a copy that can disagree
with it.

**Request and response bodies are the scheduler's own, relayed unchanged.** So are its status codes
and its refusal messages: `409` for a duplicate name, `422` for overlapping working ranges, `404`
for a practitioner belonging to another session. FR-035 requires every rule to come from the service
that owns it, and this proxy re-implements none of them — including the seeded-name default, which
means a `POST` with an empty body is a valid create.

**Transport failures**, which the scheduler cannot report because it never saw the request:

| Condition | Status | Body |
|---|---|---|
| unreachable | `503` | `"scheduling is unavailable; nothing was changed"` |
| timed out (5s) | `504` | `"scheduling did not answer; the change may not have been applied — try again"` |

**One attempt, no retry** (research #20). A console form must not silently create two practitioners
because the first response was slow; the unknown outcome is reported as unknown, which is 006's rule
for a write whose answer never arrived.

---

## `/faq` — CHANGED: session-scoped, and a new write path

The four routes keep their paths and their request bodies. What changes is who they belong to and
how they are written.

### Scope

Every route filters on the cookie session (FR-039). A request with no session cookie lists nothing
and can create nothing — `GET /faq` returns `[]`, and the write routes return `404`/`401` on the
same terms the rest of the surface uses. Another session's entry is a `404` (FR-032), and entries
There are no ownerless entries to consider: the deployment removed every entry that predated
ownership (FR-039e), and the schema cannot store another.

`GET /faq` on a new session returns `[]` — plainly empty, which is the ordinary starting state of
every session and not an error (FR-039b, FR-039d).

### Response shape

`FaqEntry` is unchanged: `id`, `content`, `created_at`, `updated_at`. **No retrievability field is
added, and none may be** (FR-040): every listed entry is answerable, so an indicator could never say
otherwise, and one that can never fire teaches a staff member to rely on a signal that would not
warn them.

### `POST /faq` — the create sequence

1. validate content; refuse `409` `"this session's corpus is full (200 entries) — delete one first"`
   if the session is at the cap, **before** touching any store (FR-039f);
2. reserve the entry id from the sequence, and mint a revision id;
3. chunk, then embed — **before any store is written** (FR-042a);
4. write the chunks under the new revision;
5. **one** local commit inserts the row with its content and its live revision — the single moment
   the entry becomes visible to the console and to retrieval (FR-042c);
6. sweep this entry's non-live chunks — best effort, silent (FR-042h).

### `PUT /faq/{id}` — the update sequence

The same, with two differences: the expected revision is read at step 1 from the entry's own row,
and step 5 is an `UPDATE` carrying it as a staleness guard —
`WHERE id = :id AND session_id = :s AND live_revision = :expected` (FR-042c). Zero rows updated is a
failed save, not a `404`.

### `DELETE /faq/{id}`

The row is removed **first**, which un-publishes every revision it named and makes the entry
unanswerable at that instant; the chunk removal follows as housekeeping and its failure is **not**
reported as a failed delete (FR-042f). This reverses 001's deindex-first ordering, and is safe only
because unretrievability now comes from the row rather than from the index being empty.

### Failure reporting — every write route

| What failed | Status | The entry afterwards |
|---|---|---|
| embedding (step 3) | `503`, naming the unavailable dependency | **unchanged**, still answering from its previous text (FR-042a) |
| the chunk write (step 4) | `503`, naming the retrieval store | **unchanged**, previous revision still live (FR-042e) |
| the publishing commit (step 5) | `503`/`409` — retryable | **unchanged**; the new revision sits unpublished and unreachable |
| the sweep (step 6) | — | **succeeded**; the leftover chunks are unreachable and the next save sweeps them |

**No rollback, revert, or compensating write is performed on any of these** (FR-042e). None is
needed: content is written once, in the commit that publishes it, so a failure before that commit
changed nothing observable and a failure *of* it is the change not happening. 001's
`_revert_faq_update` is deleted, not repaired — a best-effort repair that half-succeeds and swallows
its own failure is what left the two stores silently disagreeing.

Retrying is always safe, repeatable, and needs no manual repair of the index (FR-042g).

---

## `/admin/sessions` — NEW, and invisible

Two routes, on this same public surface, guarded by one secret. **Not a user role** (FR-049):
patients and staff still never log in, and nothing in the console links here.

| Route | Effect |
|---|---|
| `DELETE /admin/sessions/{session_id}` | delete one named session |
| `DELETE /admin/sessions` | delete all of them |

### The guard — four properties, each of which has a wrong default

1. The secret is carried in the **`X-Admin-Secret` header**, never a query string or path
   segment, which reach access logs and browser history where redaction does not follow (FR-048a).
2. The comparison is **constant-time** (`hmac.compare_digest`), so a refusal says nothing about how
   much of the secret was right.
3. Both routes are declared `include_in_schema=False`, so they appear in **no** published schema or
   documentation page (FR-048a, SC-019a). This must be on the route decorators — a router cannot
   retroactively hide its routes from `/openapi.json`.
4. A **configured secret that is unset or empty refuses every request**, checked before the
   comparison — an empty configured secret would otherwise `compare_digest`-match an empty header
   and admit everyone (FR-048a).

Every refusal is the same `403` with the same body: `{"detail": "refused"}`. It never says which
part was wrong, and the secret is never echoed, logged, or returned (FR-048, FR-050) — it is added
to the service's existing secret-fields tuple rather than getting a redaction path of its own.

### What a deletion removes, and what it reports

Per session, in order (research #23): the scheduler's patients, practitioners and their appointments
over gRPC; then this service's session row, taking its chats, messages, marks and FAQ entries by
cascade; then that session's chunks from the retrieval store.

```json
{
  "results": [
    {"session_id": "01J...", "status": "deleted",
     "patients_deleted": 2, "practitioners_deleted": 1, "appointments_deleted": 4},
    {"session_id": "01K...", "status": "incomplete", "detail": "scheduling did not answer"}
  ]
}
```

`status` is `deleted` or `incomplete`, per session, and a partial outcome is **never** reported as
success (FR-051). Re-running for the incomplete ones is safe and converges: deleting an absent
session succeeds with zero counts on both sides. Deleting **all** sessions offers exactly the
guarantees of deleting one, applied to each (FR-052).

A chunk removal that fails is **not** an incomplete deletion: the rows that vouched for those chunks
are already gone, so they are unreachable, and reporting a leak as an incomplete delete would send
an admin back to re-run something that already achieved every observable effect.
