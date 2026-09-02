# Phase 0 Research: Escalation and the Staff Console (Phase 1d, part 2)

**Feature**: `007-escalation-and-staff-console` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

Twenty-four decisions. They fall into four groups, and the groups are almost independent of each
other: **who may speak in a conversation** (#1–#10), **what the corpus is and how it is written**
(#11–#18), **how the two panes stay in step and reach the other service** (#19–#21), and **the
maintenance surface** (#22–#24).

The spec's twenty-six recorded clarifications are settled input and are not re-litigated here. Two
places where the spec's own requirements pull against each other are resolved rather than papered
over — #1 and #6 — and each says which requirements it is reconciling.

---

## #1 — Silencing and emphasis are two stored facts on the conversation, not one

**Decision**: `chats` gains **four** columns, in two independent pairs:

| Column | Answers | Ended by |
|---|---|---|
| `escalated_at` + `escalation_reason` | *may the assistant speak here* (with the pause) | a staff message, **or** the switch (FR-009a) |
| `attention_since` | *does a person need to act here* (emphasis, and the list's order) | a staff message **only** (FR-029a) |

`assistant_paused_until` is the fourth, and is #2.

**Rationale**: The spec states the grid for *message marks* (FR-027c) and forbids collapsing its two
axes (FR-027d). The same two axes exist at conversation level and the spec does not state them as
plainly, so the requirements read inconsistently if emphasis is derived from the escalated state:

- FR-029 defines emphasis as "escalated, or holds an unanswered message".
- FR-003d says a call raised because the **assistant failed** emphasizes the conversation and
  explicitly does **not** silence it — so it is emphasized while not escalated, which FR-029's
  enumeration does not cover.
- SC-009f pins that behaviour twice over: such a conversation "remains emphasized until a staff
  member replies, and its permanent mark survives that reply".
- FR-027e says a conversation whose only remaining marks are permanent must **not** be emphasized —
  so emphasis cannot be derived from the marks either, because `assistant_failed` is permanent and
  would emphasize forever.
- FR-017b says returning the assistant with the switch ends the silence and must **not** clear the
  emphasis.

One stored fact cannot satisfy those five at once; two can, and they are the same two the spec
already separates one level down. `attention_since` is set by **every** call to staff and by every
patient message that arrives while the assistant is silent; it is cleared by a staff message and by
nothing else. `escalated_at` is set only by the two silencing reasons and is cleared by a staff
message *or* the switch. FR-017b is then true by construction rather than by a rule someone has to
remember: the switch writes one column and never touches the other.

`attention_since` also carries FR-027's ordering ("the one waiting longest comes first"), which is
why it is a timestamp rather than a boolean — and why it is *not* re-stamped by a later call while
one is already outstanding: the conversation has been waiting since the first.

**Alternatives rejected**: *Derive emphasis entirely from the messages* — possible, as
`escalated OR EXISTS(clearable mark) OR EXISTS(mark newer than the last staff message)`, but the
third arm re-derives "has a person spoken since" on every listing from a join the stored column
answers directly, and it puts the FR-027e rule in a query rather than in a column whose lifetime
states it. *One `needs_attention` boolean* — loses FR-027's ordering. *Emphasis stored, escalation
derived* — nothing derives silencing; it is the state the write path reads.

---

## #2 — The pause is a deadline compared against the **database's** clock, not the visitor's

**Decision**: `chats.assistant_paused_until TIMESTAMPTZ NULL`, written as `now() + interval '2
minutes'` and evaluated as `assistant_paused_until > now()` — both in SQL, in the same statement
that reads or writes the state. `local_now` is not consulted.

**Rationale**: Every other date-time judgement in this system reads the visitor's own clock, and
that is deliberate (005 FR-035, 006's `local_now`) — those judgements are about *the patient's*
calendar. This one is not. The pause measures real elapsed time between two people in a shared
conversation, and the two sides of it are a staff member's browser and a patient's browser: there is
no single "visitor" whose clock is the right one. A client-supplied clock would additionally let a
patient's skewed or forged `local_now` end a staff member's pause early, which FR-015 forbids and
nothing else would catch.

Evaluating it in SQL rather than in Python is the same argument one level down: the deadline is
written by one request and read by another, and a Python-side `datetime.now()` on a different worker
is a second clock that can disagree with the one the deadline was written against. One clock, in the
store that holds the deadline.

**Alternatives rejected**: *An `asyncio` timer per paused chat* — dies with the process, so FR-018's
"survives a backend restart" fails, and two tabs would each hold their own. *A `paused_at` plus a
duration read at evaluation time* — the same information with an extra multiplication, and changing
the two-minute constant would retroactively re-time every pause already running.

---

## #3 — The silence gate lives in `POST /chat`, inside the existing advisory-lock section

**Decision**: `turn.py`'s `_event_stream` reads the chat's four state columns inside the
`lock_chat`/`unlock_chat` section it already holds, in the same transaction that inserts the
patient's message. If the assistant may not speak, the message is inserted carrying the `unanswered`
mark, `attention_since` is set if it is not already, and the turn returns without building a
registry, without classifying, and without constructing the graph at all.

**Rationale**: FR-009 and FR-015 are absolute about what must not happen — "no intent
classification, no retrieval, no tool call, and no generation call of any kind" — and SC-002 counts
it. A gate inside the graph (a conditional edge out of `classify_intent`, say) has already made the
classification call by the time it fires. The only place that provably precedes all of them is
before `run_turn` is entered.

Putting it inside the *existing* lock section is what makes it correct rather than merely early: the
state read, the message insert, and the mark must be one atomic decision, or a staff message landing
between the read and the insert produces a message that was answered by an assistant the staff
member had already silenced. That section exists for exactly this reason already (a concurrent
sibling message's history read).

**Alternatives rejected**: *A FastAPI dependency on the route* — reads the state in its own
transaction, so it is a check before the write rather than part of it, which is the shape this
project's own principles name as the smell. *A conditional edge in the graph* — see above.

---

## #4 — A silent turn ends with a new terminal event, not with `done` or `cancelled`

**Decision**: a fourth NDJSON terminal event, `{"type": "silent"}`. The frontend renders nothing for
it; the patient's message simply stays in the thread (FR-019).

**Rationale**: The stream has to terminate somehow, and the two existing terminals both already mean
something else. `cancelled` means *a newer message superseded this one, discard the tokens you have*
— a client that receives it after a silent turn would be told to discard a message that is in fact
being kept. `done` with an empty body means *here is the reply*, and the reply-rendering path would
show an empty assistant bubble. That is the project's "one value, one meaning" rule applied to a
wire protocol: a third situation gets a third value.

It is not a violation of FR-019's "the patient MUST be shown nothing further". FR-019 governs what
is *rendered*; this is how the request ends. The client's handler for it is empty by requirement.

**Alternatives rejected**: *Return HTTP 409 and no stream* — makes an ordinary, expected outcome an
error, and the message was accepted and stored, which is not what a 4xx says. *Close the stream with
no terminal line* — indistinguishable from a dropped connection.

---

## #5 — One escalation implementation, several callers, and the transition applied once at end of turn

**Decision**: a single `chat/agent/escalation.py` module owning the transition. Within a turn,
nothing writes it: the model's `escalate_to_staff` tool handler, the FAQ abstention branch, and the
failure path each **record a request** into one per-turn `EscalationRequests` collector, and
`turn.py` applies the collected result once, after the graph has completed, through one
`apply_escalation()` call.

**Rationale**: This is the only shape that satisfies FR-001a and FR-006 together. FR-001a demands
one implementation reachable by several callers, "the same state, the same record, the same reason
handling" — which rules out each caller writing its own transition. FR-006 demands the turn run to
completion first, with the state taking effect at the end of it — which rules out any caller writing
the transition at the moment it decides. Recording a request and applying it once satisfies both,
and it is what makes the mixed-intent edge cases fall out for free: an escalating FAQ half and a
succeeding booking half both finish, and the conversation transitions after the merged reply.

The collector is a plain mutable object reachable from `ToolContext` (for the model caller) and from
graph state (for the two direct callers). It is deliberately **not** a LangGraph state key: the two
specialists can run concurrently, and concurrent writes to one state key are exactly what LangGraph
rejects. Appending to a shared object is not a state write, and the collector's result is
order-independent by #6's precedence, so the two branches' interleaving cannot change the outcome.

**Alternatives rejected**: *Three call sites writing the state directly* — FR-001a forbids it in
terms, and the three would drift on which of the four things a call has to do. *A reducer on a
LangGraph state key* — works, but makes the escalation a first-class part of the graph's channel
contract for a fact the graph never reads. *Escalate immediately and let the turn finish anyway* —
violates FR-006's "takes effect at the end of that turn", and the streamed reply would be racing a
state its own tokens contradict.

---

## #6 — One mark per message, resolved by precedence; the log keeps every call

**Decision**: `messages.attention_mark` is a single nullable column. When one turn raises more than
one call to staff for the same patient message, the mark stored is the highest of:

`patient_asked_for_person` > `corpus_could_not_answer` > `assistant_failed`

and `unanswered` never competes, because it is set only when nothing ran at all. Every call is still
recorded in full by FR-033's log events — the precedence decides the mark, not the record.

**Rationale**: One patient message can genuinely cause two calls — a mixed-intent turn whose FAQ
half abstains and whose booking half fails, which the spec's Edge Cases contemplate for the delivery
of both halves. The spec models the mark as singular ("A **patient** message may additionally carry
an *attention mark*: which of FR-027a's four kinds it is"), so a second mark has nowhere to go.

The order is the strength of the claim on a person: a patient asking for a human is a person
wanting a person; a corpus gap is a hole in the clinic's own documents; a failure is a thing to
retry. Silencing follows the mark, which is the conservative direction — a turn that both abstained
and failed silences, and the failure's "the patient can just retry" is the weaker of the two claims
to give up.

**The cost is named**: with two calls on one message, the console shows the stronger and the weaker
survives only in the log. FR-027b's "no call without a mark on the message that caused it" holds —
there *is* a mark on that message — but "one mark per call" does not, and it is not required to.

**Alternatives rejected**: *A `message_marks` child table* — the honest model, and rejected as
complexity beyond what a requirement needs (Constitution VII): it buys a second row on a case the
console renders identically at conversation level (FR-029), and it turns every listing into a join.
*A JSONB array of kinds* — the same cost without the referential shape, and it makes "is this mark
permanent" a property of a set rather than of a value.

---

## #7 — A mark is cleared by being set to NULL, and permanence is a property of the kind

**Decision**: `attention_mark VARCHAR(32) NULL` holding one of four values. A staff message runs one
statement — `UPDATE messages SET attention_mark = NULL WHERE chat_id = :id AND attention_mark IN
('patient_asked_for_person','unanswered')` — and the permanent kinds are simply absent from that
`IN` list. There is no `cleared_at`, no `cleared_by`, and no boolean beside the kind.

**Rationale**: FR-027c makes lifetime a function of the kind alone, and the Key Entities section
says so in terms: "The kind is the whole of the mark; there is no separate 'cleared by' field to
disagree with it." A stored lifetime flag would be a second copy of a fact the kind already
determines — the same duplication the spec's own withdrawn readiness flag was rejected for. The
clearable set living in the `IN` list of one statement, derived from one Python-level constant, is
what makes "one staff message clears every clearable mark at once" a single write rather than a loop
with a rule in it.

**Alternatives rejected**: *`cleared_at TIMESTAMPTZ`* — keeps a history nothing reads, and creates a
representable state (a permanent mark with a `cleared_at`) that the kind says is impossible.

---

## #8 — `staff` is a third `MessageSender` value, and the history layer already handles it

**Decision**: `MessageSender` gains `STAFF = "staff"`. **No migration is needed for it**: `sender` is
a plain `String(16)` column, chosen in 003 precisely so a value could be added without one.

`split_into_bursts` and `to_claude_messages` need **no change** — both were written against the
patient/not-patient distinction rather than against `sender == ASSISTANT`, and both docstrings say
so explicitly. A staff message therefore joins the clinic's side of the conversation and reaches
Claude as role `assistant`, which is FR-026.

**Rationale**: This is 003's extension point being used as designed, and it is worth recording that
the design held: the only work is the enum member and `MessageOut` gaining one nullable field for
the message's mark.

FR-021's label is **not** a field at all. `sender` already carries everything the label states, so
rendering *"Staff"* / *"AI assistant"* is the frontend reading a value it already receives (#10). A
`staff_name`-shaped field would be a second source for something the sender already says.

**Alternatives rejected**: *A separate `staff_messages` table* — FR-020 requires one flat ordered
log, and two tables would need a merge on every read with the ordering rule living in the merge.

---

## #9 — FR-019b is implemented by splitting the trailing burst on the surviving `unanswered` mark

**Decision**: a new `history.exclude_silent_window(bursts)` runs after `split_into_bursts`. If the
trailing (patient-sided) burst contains messages carrying the `unanswered` mark, the burst is split
at the last of them: the marked prefix becomes its own preceding burst, and only the remainder is
the trailing burst that `derive_reply_to_message_ids` reports and that the specialists answer.

**Rationale**: FR-019a/FR-019b need a signal that says "this message arrived while the assistant was
silent" and that is still available on the later turn that must not answer it. The `unanswered` mark
is exactly that signal and is already stored: it is cleared only by a staff message (FR-027c), and a
staff message would have broken the burst anyway by being non-patient-sided. So the two cases where
a marked message can still sit in a trailing patient burst — the pause expired, or the switch was
turned on without a reply (FR-017b) — are precisely the two FR-019a names, and the mark is present
in both.

The marked messages stay in the history as context (the spec requires it: "they remain part of the
conversation it reads for context"), which is why they become a *preceding burst* rather than being
dropped. Two consecutive patient-sided bursts result, which `to_claude_messages` renders as two
consecutive `user` entries — the Messages API requires strict alternation, so the two must be
rejoined into one `user` entry at render time. That is a small, contained change to
`to_claude_messages` and is the only place FR-019b touches the model-facing shape.

**Alternatives rejected**: *A `silenced` boolean on the message* — a second column meaning what the
mark already means, and it would disagree with the mark the moment a staff reply cleared one and not
the other. *Compare each message's `created_at` against the pause window* — the window is gone by
the time the later turn runs (the deadline is cleared or expired), and an escalation has no window
at all.

---

## #10 — There is no staff entity and no staff name: messages are labelled by role

**Decision**: nothing is stored, derived, or computed for the staff member. `MessageView` renders
two labels from the `sender` value it already receives — **"Staff"** and **"AI assistant"** — and
the patient's own messages stay unlabelled. No pool, no derivation, no `staff_names` module, and no
`staff_name` field on any response (FR-021, FR-022, FR-023).

**Rationale**: The question this decision answers is "what is a staff name actually *for*", and the
honest answer is one label in a message list. FR-021 is the only requirement that consumes it, and
what FR-021 needs is for a patient to tell a human's reply from a generated one — which a role label
does exactly, and a person's name does only incidentally.

A name is also *worse* than the label at the thing it was for. This system has one anonymous session
on both ends of the conversation; there is no real person behind the reply, and a human-sounding
name invites the patient to believe there is. Patients and practitioners get pool names because they
are records standing in for real people. Staff is not somebody — it is a side of the conversation,
and a role label states that and nothing more.

Dropping it collapses a surprising amount. An earlier draft of this decision derived the name from
the session id with BLAKE2b, and that derivation was **not** over-engineering: SC-011c required the
same session to yield the same name across restarts, and Python's `hash()` is salted per process by
`PYTHONHASHSEED`, so the obvious implementation would rename the staff member on every deploy while
passing every single-process test. That trap is now simply unreachable — with nothing derived, there
is nothing whose stability can be got wrong. The pool, the module, its tests, and the stability
requirement all go with it.

**What it costs**: staff cannot be told apart from each other, which costs nothing while a session
has exactly one (FR-022). Giving staff any real attribute later means introducing the record then,
and this decision makes that no harder — it removes a derived value, not an extension point.

**One thing it adds**: the assistant's replies were never labelled either, because two senders
distinguished by position and styling needed no labels. With three senders that stops being true, so
FR-023 covers both labels rather than only the new one.

**Alternatives rejected**: *A derived pool name* — the previous answer, above. *A `staff_members`
table* — the spec had already settled this before the name was dropped: a one-to-one table with one
meaningful column, a uniqueness constraint for an invariant construction guarantees, and a migration
for every existing session. *Label staff messages with the clinic's name* — invents an organization
this project does not model, and would need configuring. *Label the patient's own messages too* —
they are the reader's own; a label would say nothing they do not already know.

---

## #11 — FAQ ownership and the live revision are two `NOT NULL` columns, on a store that is reset first

**Decision**: the deployment **empties every store** — sessions with their chats and messages, every
FAQ entry with its chunks, and the scheduling side's patients, practitioners and appointments
(FR-039e). Onto that empty system, `faq_entries` gains

- `session_id VARCHAR(26) NOT NULL REFERENCES sessions(id) ON DELETE CASCADE`, indexed;
- `live_revision VARCHAR(26) NOT NULL` — the ULID of the one revision retrieval may search.

**No CHECK constraint.** The one an earlier draft needed — making the two nullable columns null
together — has nothing left to constrain.

**Rationale**: The reset is what makes the columns required, and the columns are what make FR-040's
invariant a schema rule instead of a convention: *every entry that exists is retrievable*, because
every entry has an owner and names a live revision, and a published revision always holds at least
one chunk. An entry belonging to nobody is not excluded by the retrieval filter — it cannot be
written.

This is worth stating as the general move rather than a local tidy-up: this feature already refuses
to *detect* the disagreement between a row and its chunks, and removes the ability to reach it
instead (FR-042b). Ownerless rows are the same shape of problem one level up, and the earlier
nullable design was the same shape of answer that was rejected there — tolerate the bad state, and
make every reader carry a predicate that excludes it. A reader that forgets the predicate is then a
cross-corpus leak; a column that cannot be null has no such failure mode.

The FK's `ON DELETE CASCADE` is what makes FR-039c's "its rows first" a consequence of deleting the
session rather than a step someone has to sequence.

**What the reset costs, and why it is affordable here**: it is destructive and irreversible, and it
takes real conversations with it. It is acceptable on exactly the precondition FR-045a already
states for this system's *absence* of a retention policy — synthetic data, fictional patients drawn
from a name pool, no real clinical content. That precondition is doing double duty now, which is
worth noticing: if it ever stops holding, this decision and FR-045a both have to be revisited, and
the nullable-plus-CHECK design below is what this would revert to.

**Alternatives rejected**: *Nullable columns plus a two-armed CHECK, tolerating ownerless rows* — the
earlier design, and it works; it keeps a representable state that only a filter excludes, and it
costs an extra constraint, an extra requirement, an edge case and two clarifications to say so.
*Backfill legacy entries to a sentinel session* — makes them retrievable by whoever owns the
sentinel, which is worse than either. *A separate `faq_revisions` table* — FR-042b says the row
keeps "no history of the others"; a history table would be rows nothing reads, and the sweep's
predicate (#17) needs none.

---

## #12 — `nextval` reserves the entry id before the chunks are written

**Decision**: a create runs `SELECT nextval('faq_entries_id_seq')` to obtain its id, then chunks,
embeds, writes the chunks carrying that id, and only then `INSERT`s the row. The `id` column stays
an autoincrementing integer.

**Rationale**: FR-042b requires every chunk to carry the entry it belongs to, and FR-042c requires
the chunks to be written **before** the commit that publishes the row. On an update those two are
compatible for free; on a create they are not, because the id normally comes into existence with the
row. `nextval` separates *allocating identity* from *publishing it*, which is precisely the
distinction the additive-revision design is built on.

The alternative that first suggests itself — make `id` a caller-minted ULID — is far more expensive
than it looks: `Citation.entry_id` is an `int` in the chat schema, in the frontend's TypeScript, and
in every `faq.retrieved` / `turn.retrieval_completed` log event, and 001's contract is built on it.
`nextval` changes none of that.

**The failure mode is named**: a create that writes its chunks and then fails to insert leaves
chunks for an entry that never existed, and no per-entry sweep will ever address them, because a
retry allocates a *different* id. They are unreachable throughout (no row names their revision
live), and the session's deletion removes them (FR-039c). FR-042i accepts exactly this trade, and
this is the one case where the leak outlives the entry's next save.

**Alternatives rejected**: *ULID primary keys* — see above. *Insert the row first with a NULL
`live_revision`* — forbidden by #11's CHECK, and it reintroduces the entry-that-is-not-retrievable
state FR-040 exists to make unrepresentable.

---

## #13 — The chunk payload carries session, entry, and revision; retrieval filters on the last two

**Decision**: `ChunkPayload` gains `session_id: str` and `revision: str` alongside the existing
`faq_entry_id` and `chunk_index`/`chunk_text`. Retrieval passes a Qdrant `Filter` of
`must=[MatchAny(key="revision", any=<the session's live revisions>)]`. The session-wide delete uses
`MatchValue(key="session_id")`; the per-entry sweep uses `faq_entry_id` and `revision` (#17).

**Rationale**: FR-042d requires the live-revision restriction to be "the same filter that scopes it
to the session … rather than a check applied to the results afterwards". A revision id is minted by
one session's save and is never shared, so filtering to *that session's live revisions* enforces
both scopes in one term — the session predicate and the live-revision predicate are the same
predicate, which is what the requirement asks for and is one fewer thing to forget.

`session_id` is still carried in the payload, and it is not redundant: FR-039c's session delete runs
**after** the rows are gone (the FK cascade), so at that moment nothing can enumerate the session's
revisions any more. The payload's own `session_id` is the only handle left, and it is what makes the
session delete the backstop sweep FR-039c requires it to be.

**Alternatives rejected**: *Filter on `session_id` and let a `live` boolean on the point say the
rest* — a mutable flag on an immutable chunk, which is the destructive update this design removes.
*Derive the filter from entry ids rather than revisions* — an entry id does not distinguish a live
revision from a superseded one, so SC-015b would fail.

---

## #14 — Live revisions are read in the turn's existing Postgres section, so FR-042j cannot arise

**Decision**: `turn.py` reads `faq_repository.live_revisions(db_session, session_id)` inside the
same locked section that inserts the patient message, and threads the resulting `list[str]` through
graph state into `search_faq`. A Postgres failure therefore fails the turn before the FAQ path is
entered. An **empty** list short-circuits: `search_faq` returns `[]` without embedding and without
calling Qdrant.

**Rationale**: FR-042j requires an empty corpus and an unreadable one to produce different outcomes,
and names the exact accident: both collapse to "no revisions" if the read happens inside the FAQ
path. The spec itself blesses this route as "an implementation route to the requirement". It is
better than a typed result because it removes the ambiguous value rather than handling it — by the
time `answer_faq` runs, an empty list provably means an empty corpus, because the alternative
already failed the request with a dependency error.

The short-circuit is not just an optimisation: with no live revisions there is no filter value that
could match, so calling Voyage and Qdrant would spend two dependencies to be told what the empty
list already said. It also makes SC-011b's "a session created while the retrieval store is
unreachable still yields a working chat" hold on the first FAQ turn too, not only at provisioning.

**Alternatives rejected**: *A `LiveRevisions | ReadFailed` result type inside `answer_faq`* — correct,
and it keeps the ambiguity alive in one more place than necessary. *Read them in `search_faq` from
its own session* — a second transaction, and the failure then has to be distinguished downstream,
which is the requirement's own warning.

---

## #15 — The staleness guard is the `WHERE` clause of the publishing `UPDATE`

**Decision**: an update publishes with

```sql
UPDATE faq_entries SET content = :new_content, live_revision = :new_revision
 WHERE id = :id AND session_id = :session_id AND live_revision = :expected_revision
```

where `:expected_revision` is the value read when the operation began, inside the same request.
Rowcount 0 is a failed save, reported as retryable (FR-042e); it is never a 404, which is a separate
read (`id`+`session_id` absent) done only to classify.

**Rationale**: This is 006's rule, unchanged: a change is one conditional `UPDATE` whose predicate
carries identity, session scope and the staleness guard together, never a check performed before the
write. FR-042c says so directly and says where the expected value comes from — the server's own
read, not the client's — because the window the guard protects is the one between this request's
chunk write and its commit. Two saves racing on one entry write disjoint revisions, one commit
wins, and the loser is reported failed rather than publishing a revision whose predecessor is gone.

The session predicate is on the same statement for the same reason it is on every other write in
this project: an id from another session must resolve to nothing, not be caught afterwards.

**Alternatives rejected**: *`If-Match` carrying the revision the client loaded* — the spec considers
and defers it: it solves lost updates between two tabs, which is a different problem, and it would
put revisions into the API contract and revision tracking into the frontend for a P5 story.
*`SELECT … FOR UPDATE` then update* — two statements where one suffices, and the lock is held across
nothing that needs it.

---

## #16 — A delete removes the row first, and the chunk removal is not part of the result

**Decision**: `DELETE FROM faq_entries WHERE id = :id AND session_id = :session_id`, then a
best-effort chunk delete by `faq_entry_id`. A failure of the second step is swallowed and the delete
is reported as successful.

**Rationale**: FR-042f reverses 001's deindex-before-delete ordering, and the reversal is safe only
because of #11: unretrievability now comes from the row, not from the index being empty. The instant
the row is gone, nothing names any of its revisions live, so every chunk it had is already outside
the retrieval filter — the entry is unanswerable before the second step is attempted. Reporting a
failed chunk removal as a failed delete would be reporting a leak as a data loss, and would invite a
retry of an operation that already succeeded.

**Alternatives rejected**: *Keep 001's ordering* — under revisions it buys nothing and costs the
guarantee: a deindex that succeeds followed by a row delete that fails leaves an entry the console
lists and the assistant cannot answer from, which FR-040 forbids.

---

## #17 — The sweep is per-entry, silent, and never load-bearing

**Decision**: after a successful publish, delete this entry's non-live chunks:
`Filter(must=[faq_entry_id == :id], must_not=[revision == :live_revision])`. It runs after the
commit, is idempotent, and its failure is caught and **not logged** — in particular it must not
raise `critical.dependency_unreachable`.

**Rationale**: One predicate covers both leftovers FR-042h names — a revision superseded by a later
commit, and a revision written by a save that never published — because both are simply not the live
one. FR-042h forbids widening it to a session-wide predicate, and the reason is worth restating
because it is not obvious: between a concurrent save's chunk write and its publishing commit, that
save's chunks are *not live*, so a session-wide sweep would delete them and the commit would then
publish a revision whose chunks no longer exist. The per-entry form cannot reach that state, because
the staleness guard (#15) already fails any competing commit on the same entry.

The silence is a requirement, not an omission (FR-042h). The rest of this path raises
`critical.dependency_unreachable` to mean *an operation could not be completed*; a sweep is not an
operation, and an event that means one thing must not be raised for another.

**Alternatives rejected**: *Log the failure at `warning`* — the spec rules it out and gives the
reason: housekeeping that reports nothing cannot be mistaken for an operation that failed. *A
background retry loop* — needs a worker this phase does not have, and #23's outbox argument applies
verbatim.

---

## #18 — The corpus cap is one setting, counted inside the creating transaction

**Decision**: `Settings.FAQ_MAX_ENTRIES_PER_SESSION: int = 200`, read in one place. A create counts
the session's entries in its own transaction and refuses beyond the cap with a message naming the
reason, before chunking, embedding, or touching either store.

**Rationale**: FR-039f requires a single configured value and requires the refusal to change
nothing — hence the count *before* the expensive steps, not after them. The cap exists because
FR-042d puts the session's live revisions on the hot path of every FAQ turn as a `MatchAny` term
(#13), so corpus size is a per-turn cost; the console's unpaged listing is a side-effect.

**The residual race is accepted and named**: two creates arriving together can both read 199 and
both insert. FR-031 gives a session exactly one staff member, so this needs two browser tabs
submitting simultaneously, and exceeding the cap by one breaks no invariant — the cap bounds a
filter list, it is not a correctness rule. The advisory-lock pattern already exists (`lock_chat`) if
that ever changes; using it here would be a lock taken on every create for a case that costs one
extra row.

**Alternatives rejected**: *A database-level constraint* — Postgres cannot express "at most N rows
per group" without a trigger or a counter table, both of which are more machinery than the bound
deserves. *Enforce on edit too* — FR-039f explicitly requires editing and deleting to keep working
on a full corpus.

---

## #19 — Both panes are kept in step by polling one endpoint, not by a push channel

**Decision**: one `GET /console/conversations` returning every conversation's state, polled every
2 seconds by the single-page app. It serves **both** panes: the staff list renders it directly, and
the patient pane refetches the active chat's messages when that chat's `last_message_at` advances
past what it holds.

**Rationale**: SC-004's requirement is observable and modest — a staff reply reaching the patient's
thread, and a new escalation reaching the staff list, within 3 seconds with no manual refresh. A
2-second poll meets it with margin.

The argument for polling over SSE is not simplicity, it is correctness under the constraints this
phase actually has. FR-029b makes every mark and every emphasis a property of the **stored**
conversation, and a poll reads exactly that stored state — so it cannot disagree with it, and it
self-heals: a poll that fails, or a tab that was asleep, is correct again on the next tick. A push
channel is a second path to the same fact, and a dropped event leaves a pane wrong indefinitely with
nothing to correct it, which is precisely SC-005's "the list and the conversations never disagree".
Push also needs the event source and the subscriber in one process; this deployment does not promise
that, and the broker that would fix it is Phase 3+ (Constitution I).

One endpoint serving both panes is what keeps the cost flat: the patient side needs no stream of its
own, and the two panes cannot disagree about a conversation because they read one answer.

**This is a deliberate wording deviation from `docs/ROADMAP.md`**, which says "a live push and an
unread count on the staff side". The *observable* requirement — the count, and arrival without a
refresh — is met; the transport is not. Recorded here rather than passed over, and revisited when
Phase 3+ brings a broker that makes push correct rather than merely faster.

**Alternatives rejected**: *SSE from the chat backend* — above; also collides with the existing
NDJSON turn stream, since the patient pane would then hold two long-lived responses per chat.
*WebSocket* — everything SSE costs, plus a protocol this project uses nowhere else. *Propagate
in-process, since both panes are one React app* — fails FR-029b's second tab and every reload.

---

## #20 — Practitioner administration crosses the boundary over the scheduler's **existing REST** surface

**Decision**: the chat backend gains `/console/practitioners` routes that forward to the scheduler's
`/practitioners` REST API over HTTP, attaching `X-Session-Id` from the cookie session. One attempt,
a 5-second timeout, and the scheduler's own status codes and refusal messages relayed unchanged.
No retry.

**Rationale**: FR-036 is the whole reason a proxy exists: the session identity lives in an
`HttpOnly` cookie the page cannot read (SC-012), and the scheduler's practitioner API expects the
session as an explicit header — so the browser cannot call it directly without giving up exactly the
property being protected. Something server-side must carry the credential.

Given that, the question is which of the scheduler's two contracts to carry it over. The practitioner CRUD
already exists as REST, is already tested, and already produces FR-035's refusals in the shape a
screen needs. Re-encoding it as three new RPCs would put a **second copy of one contract** across
the boundary — the schedule shape, the typed refusals, the defaults — and every future rule change
would have to land in both. Constitution III asks a boundary to have *its own API contract*, not for
all traffic to share one transport: the agent path needs typed, deadline-bounded RPCs, and the
console path needs the human-facing CRUD the REST surface already is.

**No retry is deliberate.** The gRPC path retries because an agent turn must not fail on a restart;
a console form must not silently create two practitioners because the first POST's answer was slow.
A timeout is surfaced to the staff member as the unknown outcome it is, exactly as `chats.py`
already does for a rename (503 for "nothing happened", 504 for "unknown").

**Recorded as a deviation** in the plan's Complexity Tracking: this is a second transport across one
boundary, and it deserves to be seen rather than buried.

**Alternatives rejected**: *Three new RPCs* — above. *The frontend calls the scheduler directly* —
FR-036 and SC-012 forbid it. *Give the browser a readable second credential* — the same thing with
extra steps.

---

## #21 — Session deletion crosses the boundary over gRPC, beside `DeletePatientForChat`

**Decision**: one new RPC, `DeleteSession(session_id) -> {patients_deleted, practitioners_deleted,
appointments_deleted}`, added to the existing service. It is system-to-system, so it takes the
transport the system-to-system contract already uses.

**Rationale**: #20's argument does not transfer, and the difference is the point: practitioner
administration is a human CRUD form for which the scheduler already publishes a contract, while
session deletion is a capability the scheduler does not have at all (FR-047) and no human form
drives. `DeletePatientForChat` — its nearest neighbour in every respect — is already an RPC, and a
new capability belongs beside the one it generalizes.

Scheduler-side it is one transaction: practitioners and patients for the session, with appointments
following by the FK cascades that already exist (006 research #4 made those cascades deliberately
status-blind, so cancelled appointments go too). The counts come back because FR-051 needs the
admin to be told what actually happened.

**Alternatives rejected**: *A REST route on the practitioner API* — puts "delete everything this
session owns" on a surface whose only guard is the session id it is deleting. *Delete via
`DeletePatientForChat` per chat* — leaves practitioners, and turns one call into N.

---

## #22 — The admin routes: header secret, constant-time, schema-invisible, fail-closed

**Decision**: `DELETE /admin/sessions/{session_id}` and `DELETE /admin/sessions`, on the chat
backend, in a router registered with `include_in_schema=False`. The secret is read from
`Settings.ADMIN_SECRET` (default `""`), carried in an `X-Admin-Secret` header, and compared
with `hmac.compare_digest`. An unset or empty configured secret refuses every request before the
comparison. `ADMIN_SECRET` is added to `chat/core/logging.py`'s `_SECRET_SETTINGS_FIELDS`.

**Rationale**: FR-048a makes four things requirements rather than choices, and each has a default
that gets it wrong. A query string reaches access logs and browser history, which the redaction
processor does not follow — hence the header. A `==` comparison leaks prefix length by timing —
hence `compare_digest`. A route present in the generated OpenAPI page is discoverable, which defeats
FR-049 without anyone noticing — hence `include_in_schema=False`, which must be on the route
decorators, since `app.include_router` cannot retroactively hide them from `/openapi.json`. And an
empty configured secret compared against an empty header **matches**, so the fail-closed check must
come first and must test the *configured* value, not the supplied one.

Adding the field to the existing secret-fields tuple is FR-050 in one line: the redaction processor
already matches known live secret values anywhere in an event, and `_SECRET_KEY_PATTERN` already
catches keys containing "secret". No new redaction path is introduced, which is what FR-050 asks
for.

**Alternatives rejected**: *A management CLI command* — considered in the spec and rejected there
for needing shell access to a running deployment. *HTTP Basic* — an auth scheme, which FR-049
forbids introducing. *A signed token* — rotation, expiry and a key store for a maintenance action
the spec explicitly says is not an authentication system.

---

## #23 — A partial delete is reported per session, and re-running converges

**Decision**: deleting one session runs the scheduler call **first**, then the local delete; the
result names the session and whether both halves completed. Deleting all sessions applies the same
per-session sequence and returns one entry per session, reporting `incomplete` for any that did not
finish. Neither reports success on a partial outcome.

**Rationale**: FR-051 is 006's rule about unknown outcomes applied to two stores with no transaction
between them. The scheduler-first ordering is `chats.py`'s existing delete ordering and is chosen
the same way: of the two orderings, only this one has a benign failure mode. A crash between the
steps leaves a chat service session whose scheduler-side rows are already gone, which a re-run
clears; deleting locally first would strand patients, practitioners and appointments with no session
left to name them.

Convergence is free rather than engineered: `DeleteSession` on an absent session succeeds with zero
counts, and the local delete of an absent session likewise, so re-running is an ordinary repeat.

The Qdrant chunk removal is the third store, and it is the one place this differs from a two-store
story — it runs after the local rows are gone, by `session_id` on the payload (#13), and a failure
there is a leak rather than an incomplete delete, exactly as FR-042f treats the per-entry case. That
asymmetry is deliberate: chunks nobody's row vouches for are unreachable, and reporting a leak as an
incomplete deletion would send an admin back to re-run something that already achieved every
observable effect.

**Alternatives rejected**: *Two-phase commit* — infrastructure Phase 3+ does not even call for.
*Report the whole batch as failed if any session was incomplete* — loses which ones, which FR-051
requires by name.

---

## #24 — The switch works in both directions, and "off" is the pause that already exists

**Decision**: `POST /console/chats/{chat_id}/assistant` takes `{"enabled": true|false}`.
**True** clears `escalated_at`, `escalation_reason` and `assistant_paused_until`.
**False** sets `assistant_paused_until = now() + 2 minutes` — the identical write a staff message
performs — restarting it if a pause was already running, and cancels any generation in flight on
FR-013a's terms (FR-017c).
**Neither** touches `attention_since` or any message mark. The rendered switch always states the
derived answer (FR-017a) and shows the remaining seconds while a pause is running.

**Rationale**: The case that forces the off direction is a staff member who opens a conversation to
*read* it, or to work out what to say, before writing anything. They have taken the conversation as
surely as one who has started typing, and with an on-only control they had no way to say so — so
the assistant could answer the patient out from under them mid-thought. FR-013's rationale
generalizes rather than bending to accommodate this: the trigger was never "a person speaking", it
was **a person taking the conversation**, and speaking is the usual way of doing that rather than
the definition of it.

**What keeps this from adding complexity is that off writes no new state.** It sets the same
deadline column, with the same duration and the same expiry, so "a pause a person asked for" and "a
pause a message caused" are the same pause. Nothing downstream distinguishes them: not the gate,
not the countdown, not `GET /console/conversations`, not a reload, not a second tab. The only place
the difference is visible at all is the log's `paused_by` field, which exists to make a silence
traceable rather than to make it behave differently.

The two directions stay asymmetric in one respect, and deliberately so: **on** ends an escalation as
well as a pause, **off** starts only a pause. That is not an oversight — an escalation means the
assistant asked for a person and none has dealt with it, which is a fact about what happened, not a
switch position. A staff member cannot manufacture one, and nothing in the feature would be better
if they could.

**Alternatives rejected**: *On-only* — the previous answer, and it left a switch that could not be
switched plus the read-first gap above. *A separate "take this conversation" button beside the
switch* — two controls for one outcome, which is the "one value, one meaning" failure this project's
principles name; the switch's off position already says exactly that. *A distinct `taken_until`
column so a manual pause can be told from a message's* — a second deadline meaning the same thing,
and every reader would then have to consult both to answer one question. *A one-way "return to the
assistant" button that only appears while silent* — FR-017 rules it out in terms ("not as a control
that only appears while something is wrong").

---

## Cross-cutting: what this feature does **not** add

Recorded because each was reachable from a requirement and each was declined by one:

- **No outbox, broker, or background worker.** ROADMAP puts the outbox in Phase 3+, Constitution I
  forbids pulling it forward, and after #12–#17 there is no dual write left for it to close:
  publishing a revision is a single-store commit, and the only asynchronous thing left is a sweep
  that is already idempotent and already converges (FR-042h).
- **No audit table for escalations.** FR-033/FR-034 are log events, best-effort, exactly as 006's
  change records are.
- **No stored `may_assistant_reply`.** FR-017a requires it derived, so it is computed in the one
  query that also decides whether a reply is generated (#1, #2).
- **No accessibility or localization work** (FR-045b), and **no retention policy** (FR-045a). Both
  are declared boundaries in the spec with their costs named; neither is an omission this plan can
  quietly fill in.
