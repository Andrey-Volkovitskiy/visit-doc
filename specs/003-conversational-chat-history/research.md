# Research: Conversational Chat History

## 1. Anonymous identity: a `Session`, distinct from `Chat`

**Decision**: An httponly cookie whose value is a new `Session` row's own ULID primary key.
`Chat` gets its own primary key and a `session_id` foreign key back to `Session`, rather
than the cookie value being the `chats.id` directly.

**This reverses the original Phase-0 version of this decision** (originally: the cookie value *is*
`chats.id` directly, with no separate identity row — see "Alternatives considered" below),
after spec.md gained its Future Direction section: a later feature is expected to let one `Session`
own multiple **Patients**, each with its own chat(s). If `Session` and `Chat` stayed
the same row, introducing `Patient` later would require *renaming* the cookie's meaning (from "the
chat" to "the session") and *moving* chat ownership off of it — a breaking migration
touching the cookie/auth-adjacent code path specifically. Splitting them now costs one small table
and one FK column; the split itself changes no observable behavior in this feature (FR-009 — one
active chat per identity — is still enforced, just at the application layer instead of by
`Session` and `Chat` literally being the same row) and is symmetric with Constitution
Principle VII's clean-architecture/SOLID bias toward each row answering one question.

`httponly` keeps the cookie unreadable/untamperable from frontend JavaScript (the frontend never
needs its value — the browser attaches it automatically); `SameSite=Lax` is the standard default for
a same-site POST-driven app.

**Alternatives considered**:
- *Cookie value is `chats.id` directly, no separate `Session` row* (this decision's original
  form): simpler by one table, and was the right call before spec.md's Future Direction section
  existed — FR-009 alone gives a visitor-id → chat-id indirection "no behavior it enables."
  Revisited specifically because that's no longer the whole picture: the spec now explicitly
  documents an expected future need for that indirection, which changes the cost/benefit — the
  seam is cheap today and expensive to retrofit later (see Decision above).
- *`localStorage` identifier sent as a request header*: works, but requires frontend code to read,
  store, and attach it on every request — pure boilerplate the cookie approach avoids for no
  behavioral gain, since nothing about this feature needs the ID to be JS-visible. Orthogonal to the
  `Session`/`Chat` split either way.
- *Build the `Patient` layer now instead of just a seam for it*: rejected — spec.md is explicit that
  Patient management "will emerge later" and is "not built now"; Constitution Principle I
  (Phase-Gated Scope Discipline) requires scope to match what's actually being asked for, not what
  might be useful someday. The `Session`/`Chat` split is justified purely by avoiding a
  specific, named future breaking change at near-zero present cost — not as a first slice of Patient
  management itself.

**Non-guessability (FR-017)**: a ULID's 80-bit random payload (the timestamp portion is public/known
regardless — ULIDs are deliberately lexicographically sortable by creation time — but the random
payload is independent of it) already gives FR-017's non-guessable/non-enumerable requirement enough
margin: guessing a specific `Session.id` means guessing 80 bits of CSPRNG output, computationally
infeasible regardless of how narrowly an attacker can bound the creation-time window. This holds only
if `Session.id` is generated with fresh, independent CSPRNG randomness on every call — **not**
`python-ulid`'s bare `ULID()` constructor, which is the opposite of what its name suggests: in the
installed version (verified against `ulid/__init__.py`), `ULID()` and every `ULID.from_*` constructor
route through a module-level `default_generator` whose default `MonotonicityPolicy` is
`StrictMonotonicPolicy` — same-millisecond calls get `prev_randomness + 1`, not fresh entropy (empirically
confirmed: five bare `ULID()` calls in a tight loop produced `...GXJ, ...GXK, ...GXM, ...GXN, ...GXP`,
a trivially predictable sequence). Genuine per-call CSPRNG randomness requires explicitly constructing
`ulid.ULIDGenerator(policy=ulid.PureRandomPolicy())` and calling `.generate()` on it — `PureRandomPolicy`
is stateless (always calls `os.urandom` fresh, no `prev_randomness`/`prev_timestamp` tracking), so a
single module-level instance in `chat_repository.py` is safe to reuse across calls. `Session.id`
generation MUST use this explicit `PureRandomPolicy` generator, scoped to `chat_repository.py` only —
`core/correlation.py`'s existing `bind_turn_id()`/`bind_operation_id()` keep using bare `ULID()`
unchanged, since `turn_id`/`operation_id` were never required to be non-guessable and `Message.id`
reuse of a request's `turn_id` (§4) is unaffected either way — a `Message.id` is never used as a
bearer/authorization credential the way the session cookie is.

## 2. Cookie attributes

**Decision**: `HttpOnly=True`, `SameSite=Lax`, `Secure=False`, `Max-Age` set to 400 days (the
practical browser-enforced cap, e.g. Chrome) at issuance, not refreshed on subsequent requests.

**Rationale**: `Secure=False` is a deliberate, temporary choice — this phase runs over local HTTP
with no TLS termination anywhere in the stack (per ROADMAP, containerized/HTTPS deployment is an
optional Phase 3+ concern); a `Secure` cookie set over HTTP would silently fail to be stored, which
would break the feature entirely in local dev. This must be revisited the moment the app is served
over HTTPS. A long `Max-Age` (rather than a session cookie that dies when the browser closes) is
what actually makes FR-011 ("no automatic expiration... available indefinitely") true end-to-end —
a session-only cookie would let the *server-side* chat live forever while the *browser's
ability to reach it* quietly expired on browser restart, which would contradict SC-002's "closes and
reopens... sees their full prior chat." Not refreshing the `Max-Age` on every request is a
deliberate simplification: a rolling/sliding expiration is unnecessary complexity here since the
cookie already outlives any realistic demo/review session, and there's no session-timeout
requirement to satisfy. Because the cookie now identifies a `Session` rather than a `Chat`
(§1), it also never needs to be reissued when a chat is cleared (§7) — one fewer place the
`Max-Age`/reissuance question comes up at all.

**Alternatives considered**:
- *Session cookie (no `Max-Age`)*: simpler, but breaks persistence across a full browser restart —
  directly conflicts with the "no expiration" clarification answer.
- *Sliding expiration (refreshed on every request)*: more correct for a real product, but adds
  complexity (every response needs to reset the cookie) for no benefit at this phase's demo scale.

## 3. Message persistence: append-only, no pending/update phase

**Decision**: Each message is its own row, inserted exactly once, never updated afterward. A patient
message is inserted as soon as it's validated — `sender="patient"`, `content` set. An assistant
message is inserted **only** once the RAG pipeline completes successfully — `sender="assistant"`,
`content`/`grounded`/`citations` all set at insert time. If the pipeline fails (FR-012) or is
cancelled because a newer message superseded it (FR-015, §9), no assistant row is ever inserted for
that attempt — there is nothing to roll back and nothing to update, because nothing partial was ever
written.

**This supersedes this same feature's earlier `ConversationTurn` design** (patient message and
assistant reply as two nullable fields on one row, inserted "pending" and updated on success), which
predates spec.md's revision to a flat, sender-tagged `Message` entity (FR-013). The two-phase
insert-then-update dance that design needed only existed to give a single row both fields "eventually
consistent" — once patient and assistant messages are independent rows, that problem disappears
entirely: a row is either written (complete, by definition) or it was never written. This is a
direct simplification, not just a rename.

**Rationale**: This single rule produces both FR-012's and FR-015's required behavior ("retain the
patient message... but MUST NOT store a fabricated or partial assistant reply") with zero
special-case failure/cancellation handling in the API layer — the happy path's "insert the assistant
message" call simply never runs if an exception propagates past it or the pipeline's task is
cancelled first.

**Alternatives considered**:
- *Keep the paired-row, insert-then-update design*: rejected along with the `ConversationTurn`
  entity itself — see spec.md's Key Entities section for why the paired framing doesn't fit a
  flat, multi-sender, non-alternating chat (FR-013/FR-014).
- *Buffer the assistant message in memory and write once at the end regardless of outcome*: would
  require catching the exception/cancellation, writing a placeholder, then re-raising — more code
  than "the insert simply doesn't happen on failure or cancellation."

## 4. Message ID scheme

**Decision**: A patient message's `id` is the same ULID as the request's existing `turn_id`
correlation ID (already minted per chat request by `bind_turn_id()` in `core/correlation.py` for
structured logging), not a second, independently generated identifier. An assistant message's `id`
is a fresh ULID minted at insert time — it has no request of its own to borrow an ID from, since one
incoming request can, via cancellation (§9), end up contributing zero or one assistant messages, and
the request that finally succeeds may not be the same request whose `turn_id` a reader would
naturally associate with that reply.

**Rationale**: A chat request already gets exactly one `turn_id` for its whole lifetime (the
structured-logging feature, spec 002); that ID already uniquely and monotonically identifies "one
incoming patient message" — precisely what a patient `Message` row represents. Minting a second ID
for the same concept would be pure duplication with no distinguishing purpose, and this reuse means a
patient message's log entries (`turn.message_received`, etc.) and its persisted row share one ID for
free. The assistant message needs its own independently-generated ID precisely because, under
cancel-and-restart (§9), the request that inserts it isn't necessarily "the" request associated with
any single prior patient message in an obvious 1:1 way once a burst of several patient messages
preceded it.

**Alternatives considered**:
- *A separate auto-increment integer PK, like `FaqEntry.id`*: consistent with `FaqEntry`'s style,
  but throws away the free correlation this reuse gets for patient messages, and ULIDs already sort
  lexicographically by creation time, so ordering by `id` works identically either way.

## 5. Multi-turn context passed as Messages API history, not prompt concatenation

**Decision**: Existing `Message` rows for the chat are converted into a proper alternating
`user`/`assistant` list and prepended to the Claude `messages` call (`agent/history.py`'s
`build_history_messages`), rather than concatenated as extra text inside the single prompt string
`answer_faq` already builds for the current message. The current (just-validated, not-yet-persisted)
patient message is appended in-memory as the final `user` entry — it is not re-read from the
database.

**Rationale**: This is the Messages API's intended mechanism for multi-turn chat — Claude is
trained to treat the `messages` list as the actual dialogue, which is more reliable than asking it
to parse a hand-rolled "previous chat:\n...\ncurrent question:..." block out of a single
user turn. It also keeps `answer_faq`'s existing current-message prompt construction
(`"Context:\n{context}\n\nQuestion: {message}"`) completely unchanged — history is purely additive,
prepended before it. A prior message's `user`/`assistant` entry is that row's raw `content` — for an
assistant row that's the final answer/abstention text, *not* the retrieval-augmented
`"Context:...\n\nQuestion:..."` string built at generation time for that message; replaying retrieved
chunks for every prior message would needlessly bloat context with old citations that have nothing
to do with the current question.

**Consecutive same-sender merging, now a general rule (not a special case)**: Because `Message` rows
are independent (FR-013/FR-014), any stretch of consecutive same-sender rows — a burst of patient
messages sent before a reply (FR-014), or a patient message that got no reply at all because
generation failed or was cancelled (§3, §9) followed by another patient message — produces two or
more consecutive `user` entries in a row. The Messages API requires strict `user`/`assistant`
alternation and rejects that. Resolved by a merge pass: whenever the entry about to be appended has
the same role as the list's last entry, its text is merged into that entry (joined with a blank
line) instead of appended as a new one. This keeps every message informationally present while
guaranteeing valid alternation, with one generic rule covering every case that produces a same-sender
run — there is no longer a distinct "failed turn" special case to reason about separately, since a
skipped assistant reply and a burst of patient messages both just look like "consecutive rows with
the same sender" to this pass.

**Alternatives considered**:
- *Concatenate the whole transcript into the current message's prompt string*: simpler to reason
  about as "just one string," but fights the API's trained multi-turn behavior and would need its
  own ad-hoc formatting/escaping to stay unambiguous — the Messages API's `messages` list already
  solves this.
- *Drop an unanswered patient message from history entirely instead of merging*: simpler, but
  directly violates FR-012's requirement that the message stay "available as context for future
  turns."
- *Synthesize a placeholder `assistant` message (e.g. an empty string) for an unanswered message, to
  preserve strict pairing without merging*: avoids the merge-pass logic, but injects a fabricated
  assistant turn into the transcript — exactly the kind of made-up content FR-012 is written to
  prevent, just relocated from a reply field into a synthetic history entry.

## 6. Retrieval is scoped to the merged trailing patient-message run, not just the newest message

**Decision**: `search_faq` queries on the same merged `user` entry that §5's merge pass builds for
the *current* turn — i.e. the trailing run of consecutive, not-yet-answered patient messages,
joined — not on the newest raw message in isolation. No LLM-based query rewriting is introduced;
this reuses §5's existing merge function as its own input, it doesn't add a new mechanism. For the
common case (no burst, exactly one pending patient message), the merged run *is* that one message,
so retrieval behavior is byte-for-byte identical to before — this is purely additive for the burst
case, not a change for the normal one.

**This revises this feature's earlier "single-message-scoped" decision of the same section number**,
made before FR-014's bursty messaging was taken seriously. That earlier version was already
justified for the case it was written for (a *separate*, already-answered prior message providing
context, e.g. "I'm going to come on Tuesday" → "what are your working hours that day?" — retrieval
only needs "working hours," present in the current message alone; *resolving* "that day" →
"Tuesday" was already generation's job via §5's history). It breaks down for FR-014's actual bursts,
where a later fragment can be retrieval-meaningless on its own: a patient sending "When can I see"
then, before any reply, "Dr. Josh?" — cancelled-and-restarted per FR-015/§9 into one pipeline run —
would have retrieval search on "Dr. Josh?" alone, which carries no "availability/scheduling" signal
at all. Merging the pending run before retrieval ("When can I see\nDr. Josh?") fixes exactly that,
at zero extra cost: the merge already has to happen for the Messages-API history (§5), so reusing
its output as the retrieval query too is not a new LLM call or a new pipeline step, just a second
consumer of an already-computed string.

**Rationale**: FR-015's cancel-and-restart guarantees at most one pipeline run per settled burst, and
that run already builds a merged current-turn entry for generation (§5) — retrieval consuming the
same string is the natural, lowest-cost fix, not an addition of new complexity. An LLM-based query
rewrite (e.g. asking the model to turn "that day" into "Tuesday" as a standalone query) remains
unjustified and out of scope: it would cost an extra round-trip per message and duplicate work
Phase 1b's real intent classification will eventually do properly — the merge-reuse above solves the
concrete burst-retrieval failure mode without needing it.

**Known limitation, accepted (narrowed, not removed)**: A message that's purely informational and
not itself a question (e.g. "I'm going to come on Tuesday", sent as its own turn with a reply before
the next message — no burst, no merge) still goes through the same retrieve → groundedness-gate →
generate/abstain pipeline as any other message, typically failing the groundedness gate and
producing the generic abstention reply on its own. Merging only helps when such a fragment is part
of an *unanswered, still-pending* run (FR-014/FR-015) — it does nothing for the original, sequential
single-turn case spec 001 already had this limitation in. Fixing that properly still means
distinguishing statements from questions, which is intent-classification territory — explicitly a
Phase 1b concern (ROADMAP), not introduced here.

**Alternatives considered**:
- *Keep retrieval scoped to the newest raw message only* (this feature's own earlier decision):
  rejected once FR-014's bursts were taken seriously — see above.
- *LLM-based query rewriting*: would also fix the burst-retrieval problem (and more besides), but at
  the cost of an extra model call on every message; the merge-reuse fix gets the concrete case FR-014
  actually presents for free, so the added round-trip isn't justified by Constitution Principle I
  (Phase-Gated Scope Discipline).

## 7. Clearing a chat: FK cascade, and the session survives it

**Decision**: `messages.chat_id` is declared with `ON DELETE CASCADE`; clearing a
chat is a single `DELETE FROM chats WHERE id = :id`, letting Postgres remove all its
messages atomically, rather than the repository deleting messages in a loop before deleting the
chat row. `DELETE /chat` deletes only the `Chat` (and, via cascade, its
messages) — it does **not** delete the `Session` and does **not** reissue or clear the cookie. The
next `POST /chat` under the same still-valid session cookie lazily creates a brand-new `Chat`
for that same `Session` (`chat_repository.get_or_create_chat_for_session`), per
FR-006.

Symmetrically, `chats.session_id` is also declared `ON DELETE CASCADE` back to `sessions.id`
for referential integrity, even though nothing in this feature ever deletes a `Session` — it's the
same "let the database enforce the invariant" reasoning applied consistently, not something actively
exercised yet.

**Rationale**: Mirrors the project's existing hard-delete precedent and its "let the database
enforce the invariant" bias (Constitution Principle III) — a single statement can't leave orphaned
messages behind even under a crash mid-operation, which an application-level "delete messages, then
delete chat" two-step could. Leaving `Session` untouched by a clear is a direct, positive
consequence of §1's `Session`/`Chat` split: identity (the cookie, the `Session` row) and
"what that identity is currently talking about" (the `Chat` row) are now separate concerns,
so clearing the latter has no reason to touch the former — this is simpler than the original design
(where clearing necessarily meant reissuing a fresh cookie, since the cookie itself *was* the
chat being deleted).

**Alternatives considered**:
- *Soft delete (a `deleted_at` column, rows retained)*: rejected for the same reason spec 001
  rejected it for `FaqEntry` (spec 001 Assumptions) — no undo requirement exists, and it would leave
  the very data the "Clear chat" action promises to remove still sitting in the database,
  contradicting the clarification answer ("permanently removed from storage").
- *Delete the `Session` too, reissuing a fresh cookie on clear (the original, pre-split design)*:
  works, but unnecessarily discards a stable identity that has no reason to change just because its
  current chat was cleared — and reintroduces exactly the "cookie must be reissued on clear"
  logic the `Session`/`Chat` split was adopted to avoid.

## 8. History endpoint: no special-casing for an unanswered message

**Decision**: `GET /chat` returns every `Message` row for the current chat in order,
sender-tagged. No flag, and no frontend treatment, distinguishes "this patient message never got a
reply" from any other message — the list is simply rendered by `sender`, message by message.

An assistant row is only ever inserted on success (§3), so a patient message with no reply has no
assistant row immediately following it — but that's also the *normal, expected* shape of a mid-burst
message under FR-014, not a signal that something went wrong: a patient typing "When can I see" then
"Dr. Josh?" as two quick messages produces exactly this shape for the first one, with nothing unusual
about it. Adjacency alone can't tell that ordinary case apart from a message that genuinely never
got answered because generation failed (FR-012) or was cancelled (FR-015) — so the frontend doesn't
try to guess, and doesn't render any derived "no answer" indicator.

**This obsoletes both this feature's earlier decision of the same name** (which checked
`assistant_reply: null` on a now-superseded `ConversationTurn` row) **and this same feature's own
immediately-preceding revision** (which proposed deriving a "no answer — try asking again" UI
treatment from message adjacency instead). That adjacency-based treatment didn't survive FR-014 being
taken seriously: once a sender routinely posting several messages in a row is the expected shape of a
real chat, "no assistant message directly after this one (yet)" stops being a reliable
failure signal.

**Rationale**: The history endpoint already tells the truth on its own — every message the patient
actually sent is present, and only replies that were actually produced appear as assistant messages;
nothing is hidden or fabricated (Constitution Principle V). Layering a heuristic "no answer" UI
treatment on top of that would frequently misfire once real bursty chats exist, mislabeling
ordinary in-progress exchanges as failures, which is a worse outcome than simply not editorializing.
If a message genuinely never gets a reply and the patient wants one, they can just ask again — the
same expectation spec 001's single-turn abstention already sets, with no UI call-out needed to convey
it.

**Alternatives considered**:
- *An adjacency-derived "no answer" indicator, or an equivalent stored `has_reply` flag* (this
  feature's own immediately-preceding design): rejected once FR-014 made "no assistant row yet"
  the normal shape of a mid-burst message rather than a reliable failure signal — telling the two
  apart would need a real-time "still generating vs. never coming" signal, which is out of scope
  here.
- *Omit unanswered messages from the history response entirely*: simpler frontend, but hides real
  system behavior from the patient for no stated benefit.

## 9. Overlapping generation: cancel-and-restart via an in-process registry (FR-015)

**Decision**: The `chat` service keeps a process-local, in-memory registry —
`agent/generation_registry.py`, a module-level `dict[chat_id, asyncio.Task]` — of the
currently in-flight reply-generation task, if any, per chat. When a new message arrives for
a chat:
1. If a task is already registered for that `chat_id`, cancel it (`task.cancel()`) and await
   its cancellation before proceeding. The cancelled task's pipeline coroutine unwinds via
   `asyncio.CancelledError`; because no assistant message is ever inserted until the pipeline
   completes successfully (§3), cancelling it mid-flight leaves nothing to clean up.
2. The new message's own pipeline run is wrapped in a fresh `asyncio.Task`, registered under the
   same `chat_id` (replacing the just-cancelled entry), and run as normal: retrieve → gate →
   generate/stream. If it completes successfully, it inserts the assistant `Message` row and then
   clears its own registry entry — but only if it is still the currently-registered task for that
   chat (a third, even newer message could have already superseded it in turn).
3. If the cancelled task's stream has already sent some `token` events to its own HTTP client before
   being cancelled, its response ends with one final `{"type": "cancelled"}` event
   (`ChatCancelledEvent`, contracts/openapi.yaml) rather than a `done` event or an abrupt disconnect
   — telling that specific client "no reply is coming for this one" so it doesn't render a stale
   answer or surface a spurious error.

The frontend additionally aborts its own in-flight `fetch` (via `AbortController`) the moment the
patient submits a new message, for immediate UI responsiveness — it stops rendering tokens for a
reply that's about to be discarded without waiting for the server's `cancelled` event to arrive. This
is a client-side optimization, not the authoritative mechanism: the server-side registry is what
actually guarantees at most one in-flight generation per chat, since it also covers cases the
client-side abort can't (a second browser tab open on the same chat; a client whose abort
signal is delayed or lost in transit).

**Rationale**: This phase runs a single `chat` process (plan.md Scale/Scope — "a handful of
concurrent visitors," no horizontal scaling), so process-local state is sufficient to make
cancel-and-restart correct; `asyncio.Task.cancel()` is the stdlib-idiomatic way to interrupt an
in-flight coroutine — including one that's mid-stream on a Claude call — with no new dependency.
Emitting an explicit `cancelled` event (rather than just closing the connection) gives the superseded
request's own client a clean, contract-defined signal instead of an ambiguous truncated stream that
different HTTP clients might handle inconsistently.

**Alternatives considered**:
- *Serialize per chat (queue the new message, let the in-flight one finish first)*: this was
  the clarification's explicitly rejected option ("Serialize per chat") — the user chose
  cancel-and-restart specifically so a stale in-flight reply is discarded, not waited out.
- *A per-chat database advisory lock or `SELECT ... FOR UPDATE`*: would only provide mutual
  exclusion (blocking), not cancellation of already-running work — doesn't fit "cancel and restart,"
  and is unnecessary complexity for state that's purely in-process to begin with.
- *A distributed registry (Redis, pub/sub)*: would be needed if `chat` ever ran as multiple
  instances, but that's not this phase's deployment shape (ROADMAP Phase 3+ territory at the
  earliest) — introducing it now would be exactly the kind of unjustified infrastructure Constitution
  Principle I blocks.
- *Silently drop the superseded stream with no `cancelled` event*: simpler contract, but leaves the
  older request's client to infer cancellation from a bare disconnect, which is harder to
  distinguish from a genuine network/server error.

**Re-confirmed, not just assumed**: whether cancellation should still apply once a reply has
*already started* streaming visible tokens (as opposed to still being in retrieval/gate, before
anything is shown) was raised explicitly and re-decided: cancel-and-restart applies unconditionally,
with no "already streaming" exception. The alternative — let an in-flight stream finish once tokens
have appeared, queue the next message to be answered afterward — was rejected for two reasons: (1)
it reintroduces the "Serialize" queueing/state-machine complexity above through a side door, gated on
stream-progress instead of chat-level, and (2) it makes answer *quality* depend on inference-latency
timing rather than on what was actually asked — the same burst could either get one good merged
answer (§6) or a confusing stale answer followed by a corrective second one, decided purely by
whether the model happened to emit its first token before the next patient message arrived. §10
covers the resulting frontend concern (a visibly-appearing-then-cancelled reply) instead.

## 10. Frontend rendering across a cancellation: live-paint, then clean retraction

**Decision**: The frontend keeps painting `token` events live into the in-progress message bubble as
they arrive — unchanged from spec 001's incremental-streaming behavior (that spec's FR-004) — for
every message, burst or not. The one addition: if that bubble's stream ends with a
`ChatCancelledEvent` (§9) instead of `ChatDoneEvent`, the frontend removes the bubble and any partial
text it had painted entirely, rather than leaving it, freezing it, or transitioning it to an error
state. No assistant message ever appears for a cancelled patient message — matching what `GET /chat`
would show on reload (research.md #8: no row, no indicator, just no reply after that message).

**Rationale**: Buffering all tokens until the stream is guaranteed uncancelled (i.e. until `done`)
would fully eliminate any flicker, but it would regress spec 001's FR-004 incremental-streaming
requirement for *every* message, not just bursts — an existing requirement this feature doesn't own
and shouldn't quietly break to solve a burst-only edge case. Live-paint-with-clean-retraction keeps
the common case (no burst, nothing ever gets cancelled) exactly as it already behaves, and confines
the visible cost of a burst to a brief, self-caused flash: the only way a stream gets cancelled is a
patient sending another message to the same chat (§9), so any flicker is the direct, near-immediate
consequence of an action the patient themselves just took — closer to how a "typing…" indicator
flickering when you keep typing reads, not an unexplained disappearance from nowhere.

**Alternatives considered**:
- *Buffer everything, reveal only on `done`*: zero flicker, but regresses spec 001 FR-004 for all
  messages to fix a burst-only case — rejected as disproportionate.
- *Buffer only during some fixed initial window (e.g. the first N tokens or Xms), then switch to live
  painting*: reduces flicker likelihood without fully eliminating it, at the cost of a genuinely new
  piece of frontend timing logic and an arbitrary threshold to tune; live-paint-with-retraction gets
  a simpler, fully deterministic rule (never flickers in the no-burst case; always retracts cleanly
  in the cancelled case) for less code.
- *On cancellation, replace the bubble with a visible "cancelled" placeholder instead of removing it*:
  more informative, but adds a UI state (and copy) for something the patient already knows they did
  (they just sent a follow-up) — removing it silently is simpler and matches how `GET /chat` already
  represents a message with no reply (no placeholder row, just an absence, research.md #8).
