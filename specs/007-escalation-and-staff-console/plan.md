# Implementation Plan: Escalation and the Staff Console (Phase 1d, part 2)

**Branch**: `007-escalation-and-staff-console` | **Date**: 2026-09-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-escalation-and-staff-console/spec.md`

## Summary

Six columns, one tool, one rpc, and the project's first real screen. Unlike 006 — which was small
because 005 had already built the seam it rode on — this half of Phase 1d is large, and it is large
in a specific way: **three things that already work are changed**, and each is changed because the
console makes an existing shape untenable rather than because a new feature needs a new place to go.

**(1) A conversation gains two independent states, not one.** `chats` gains `escalated_at` +
`escalation_reason` (may the assistant speak) and `attention_since` (has a person acted), plus
`assistant_paused_until` (the 2-minute deadline). The spec states this grid for *message marks*
(FR-027c) and forbids collapsing its axes (FR-027d); the same two axes exist at conversation level
and five requirements pull apart if one column carries both — most sharply FR-003d, where a failure
and a corpus gap each emphasize without silencing, and FR-017b, where the switch ends the silence
and must **not** clear the emphasis (research #1). The pause is a stored deadline compared against the **database's** clock,
not the visitor's `local_now`: this is the one date-time judgement in the system that is not about
the patient's calendar, and a client clock would let a patient end a staff member's pause (#2).

The silence gate sits in `POST /chat`, inside the advisory-lock section that already serializes a
turn's history read and message insert — the only place that provably precedes classification,
retrieval, tool calls and generation, which is what FR-009/FR-015 demand and SC-002 counts (#3). A
silent turn terminates with a fourth NDJSON event, `silent`, because the two existing terminals mean
something else: `cancelled` tells a client to discard a message that is being kept, and an empty
`done` announces a reply that does not exist (#4).

**(2) Escalation is one implementation with three callers and one application point.** The model's
`escalate_to_staff` tool, the abstention gate, and the failure path each *record a request*; `turn.py`
applies the collected result once, after the graph completes. That is the only shape satisfying
FR-001a (one implementation, several callers) and FR-006 (the turn finishes first) together (#5).
The tool's schema is **empty** — no reason parameter, because the model can only ever raise one of
the three and a field with one legal value is a field it can get wrong; no summary, because the
thread already says what the patient wanted.

**(3) The FAQ write path is replaced, not called.** This is the deepest change and the one the spec
spends most of its clarifications on. Entries gain an owning session and name a **live revision**;
chunks become immutable and additive, carrying their entry, their revision and their session;
retrieval filters on the session's live revisions as **one term**, which is both the session
predicate and the live-revision predicate (#13). A save chunks and embeds *before* either store is
written, writes its chunks under a new revision, and publishes with **one** local commit whose
`WHERE` carries 006's staleness guard (#15). `_revert_faq_update` is deleted rather than repaired,
because a best-effort compensating write that half-succeeds and swallows its own failure is what
left the two stores silently disagreeing. A delete removes the row **first** (#16). The sweep is
per-entry, idempotent, and silent — its failure raises no event at all (#17). The accepted failure
mode is leaked storage, never a lost answer.

A `CHECK` pins FR-040 in the datastore: an owned entry always names a live revision, so *listed* and
*searchable* cannot come apart and the console has no retrievability state to render.

**(4) The screen.** Both panes at once, no login. One polled endpoint serves both — the staff list
renders it, and the patient pane refetches its active thread when that conversation's last message
advances (#19). Polling rather than SSE is a correctness argument, not a laziness one: FR-029b makes
every mark stored state, so a poll cannot disagree with it and self-heals, where a dropped push
leaves a pane wrong forever with nothing to correct it — which is exactly SC-005. Practitioner
administration is proxied through the core backend because the session credential is `HttpOnly` and
must stay unreadable (FR-036, SC-012), and it goes over the scheduler's **existing practitioner
REST API** rather than three new RPCs that would be a second copy of one contract (#20).

**(5) Maintenance.** Two admin routes with one header secret, constant-time, absent from the
published schema, fail-closed when unset (#22); and one new rpc, `DeleteSession`, because FR-039c
otherwise has no trigger and every visitor's data accumulates permanently.

**Out of scope** by spec: out-of-band notification (FR-043), staff scheduling capabilities
(FR-044), console analytics (FR-045), retention (FR-045a), accessibility and localization (FR-045b).
Phase 1e's *gate* is untouched — this feature builds the consequence its abstention already implies,
so 1e re-points an existing caller instead of inventing one.

## Technical Context

**Language/Version**: Python 3.12 across the workspace, and **TypeScript/React 19 on Vite** — the
first feature since 003 to do substantial frontend work, and by some distance the largest UI surface
the project has.

**Primary Dependencies**: **none added, in any member, Python or Node.** The HTTP proxy of FR-036
uses `aiohttp`, already a `chat` dependency (it backs the shared Voyage session). The console needs
no state library, no SSE/WebSocket client, and no date library: the countdown is a server-computed
integer of seconds, ticked locally and re-synced by each poll, so no clock arithmetic happens in the
browser at all. `shared-models` gains nothing — the mark kinds and escalation reasons are chat-side
enums with no second reader, and the deletion counts are proto fields.

**Storage**: `visitdoc_chat` and Qdrant for the schema; **all three stores for the reset**.
`visitdoc_scheduler`'s *schema* is untouched — `DeleteSession` is a new capability over what 005
created, using the FK cascades 006 deliberately left status-blind.

**This deployment is destructive, and that is a requirement rather than a side effect** (FR-039e).
Every pre-existing session goes, with everything it owns in every store. It is what lets the two new
`faq_entries` columns be `NOT NULL`, which in turn is what makes "an entry belonging to nobody" an
unwritable state rather than one every reader must filter out (research #11). It is defensible only
on the precondition FR-045a already states — synthetic data, fictional patients, no real clinical
content — and **must not be run against anything anyone wants back**. No downgrade restores it.

Three chat/scheduler migrations, plus one manual step, in this order:

1. **`chats`/`messages` — additive, non-destructive.** `chats` + `escalated_at`,
   `escalation_reason`, `assistant_paused_until`, `attention_since`; the CHECK making the first two
   null together; `ix_chats_session_attention (session_id, attention_since)`. `messages` +
   `attention_mark`, with a **partial** index `(chat_id, attention_mark) WHERE attention_mark IS NOT
   NULL` — the clearing statement and the "does this chat hold a mark" read both address only marked
   rows, which are a small minority. All four columns are nullable *by nature*: an ordinary open
   conversation is one where every one of them is NULL.
2. **`faq_entries` — destructive.** `DELETE FROM sessions;` (cascading chats and messages) and
   `DELETE FROM faq_entries;`, then `session_id` (FK to `sessions`, `ON DELETE CASCADE`, indexed)
   and `live_revision`, both **`NOT NULL`**, and **no CHECK**. The deletion belongs *inside* this
   migration rather than in a runbook step beside it, because it is the only place that can
   guarantee the table is empty at the instant `NOT NULL` is applied — and because a runbook step
   that gets skipped fails the `ALTER` at deploy time, which is a loud failure but a needless one.
3. **`visitdoc_scheduler` — destructive, data only.** `DELETE FROM practitioners;` and
   `DELETE FROM patients;` (appointments cascade). A data-only Alembic revision, so the reset is
   ordered and recorded with the deploy rather than remembered. **The scheduler's schema does not
   change**, and this revision is the only reason it gets a migration at all.

The new admin route (FR-046) deliberately does **not** perform this reset, and cannot: by the
time the chat store's sessions are gone there is no list of sessions left to iterate, and pre-existing
FAQ entries were never owned by one in the first place.

Qdrant: `ChunkPayload` gains `session_id` and `revision`, and `ensure_collection` gains idempotent
payload indexes on `revision`, `faq_entry_id` and `session_id` so all three filters are index-backed.
The collection is **dropped once, by hand, at deploy** (`DELETE /collections/faq_chunks`), and
recreated by the next startup. By hand rather than in `ensure_collection`, because nothing the
application does at startup should ever be capable of dropping a collection — a startup path that can
delete the corpus is one restart away from doing it for the wrong reason.

**Testing**: pytest in the tiers 003/005/006 established, plus the **frontend tier**
(`make test-frontend`, vitest) which this feature exercises properly for the first time. No new tier.

The centre of gravity moves compared with 006. There, the load-bearing rules were datastore rules;
here they split three ways:

- **Chat unit tier** — the state machine (every transition in data-model.md's diagram), the four
  mark kinds against FR-027c's grid, the silence gate's *absences* (SC-002 asserts that
  `intent.classified` and `turn.retrieval_completed` are **not** emitted, which is a stronger and
  more fragile assertion than any positive one in the suite), FR-019b's burst exclusion, the
  revision write path failed at each of its three steps, the retrieval filter, the empty-versus-
  unreadable corpus distinction, and the admin guard's four properties.
- **Frontend tier** — emphasis and ordering, the attention total surviving a pane switch, the
  countdown re-synced from the server rather than counted from a local start, the switch's derived
  position, and the two-tab agreement expressed as two components reading one polled answer.
- **Integration tier** — the cross-store session delete and its partial outcome, and the
  practitioner proxy's refusal passthrough.

Turn-exercising tests keep mocking `AsyncAnthropic` with scripted tool-use responses, which is how
`escalate_to_staff` is asserted at all: a model's decision to call it is only testable against a
script. Qdrant filters are asserted against a real Qdrant, as 001's indexing tests already are —
a `MatchAny` that was built as a post-filter passes every mocked test and fails SC-011a in the app.

**Target Platform**: unchanged — chat `:8000`, scheduler `:8001` HTTP + `:50051` gRPC, Vite `:5173`,
Docker Compose Postgres/Qdrant. **Four** new chat settings: `ADMIN_SECRET` (default `""`, which
refuses every request — a deployment that has not configured one has no admin, not an open door),
`SCHEDULING_HTTP_BASE_URL`, `FAQ_MAX_ENTRIES_PER_SESSION` (200, the single configured value FR-039f
requires) and `ASSISTANT_PAUSE_SECONDS` (120, chosen rather than derived and changeable without
touching another rule). The Vite dev proxy gains `/console`;
`/chat` already covers `/chats` by prefix, and `/faq` is already there. `/admin` is deliberately
**not** proxied — nothing in the browser calls it (FR-049).

**Performance Goals**: SC-004's 3 seconds is met by a 2-second poll of one indexed query per open
tab — the only recurring cost this feature adds, and it is bounded by the interval rather than by
traffic. Retrieval gains one `MatchAny` term of at most 200 values (FR-039f is what makes that a
fact rather than a hope), plus one indexed `SELECT live_revision` in a transaction the turn already
opens. Neither is on a path whose latency is dominated by anything but the model calls around it.
SC-017's "under 60 seconds of interaction" is a usability budget, not a latency one.

**Constraints**:
- **Two axes, never collapsed** — at message level (FR-027c/d) and, less obviously, at conversation
  level (research #1). This is the constraint most likely to be violated by an implementation that
  looks simpler.
- **The gate precedes everything** (FR-009, FR-015). Not "no reply is stored" but "no call is made".
- **One clock per question.** `local_now` for the patient's calendar, as established in 1c; the
  database's `now()` for the pause, because it is a deadline between two people (#2).
- **Every query carries its session predicate**, with no exception this time (FR-032) — including
  the Qdrant search, which carries it as a filter term rather than a post-check (FR-039a).
- **A failure before the publishing commit changed nothing observable**, so no compensating write
  exists to half-succeed (FR-042e). The deletion of `_revert_faq_update` is part of the feature.
- **A timeout never proves the server did nothing** (006's rule, applied here to the practitioner
  proxy and to the two-store session delete).
- **The sweep is never load-bearing and never logged** (FR-042h).
- **The browser never holds the session credential** (FR-036, SC-012).
- **Best-effort recording never gates a transition** (FR-034).

**Scale/Scope**: portfolio-demo scale, with one new bound — 200 FAQ entries per session, chosen
because retrieval carries corpus size on every turn (FR-039f), and deliberately not generalized to
anything else a session accumulates (FR-039g).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Phase-Gated Scope Discipline | PASS (one deviation recorded) | ROADMAP Phase 1d's second bullet, including the staff-facing interface it explicitly pulls forward from 2b, and the two admin surfaces it names. No new service, database, or platform layer. Three things the ROADMAP text *would* have licensed are declined: the transactional outbox (Phase 3+, and after research #12–#17 there is no dual write left for it to close), out-of-band notification (FR-043), and console analytics (FR-045). The **deviation is the transport**: the ROADMAP says "a live push"; this plan polls, for the correctness reasons in research #19, and meets the observable requirement (SC-004's 3 seconds, no refresh) rather than the wording. Recorded here rather than passed over. Phase 1e is not pre-empted: its *gate* is untouched, and building the consequence its abstention already implies leaves 1e strictly less to do. |
| II. AI Core Is the Centerpiece | PASS | The agent gains its first capability that is not about appointments, and the FAQ path gains the abstention *consequence* Principle V has been asking for since 001. The largest change in the feature — additive revisions — exists so that retrieval can never answer from a revision nobody vouches for. The console is UI, and it is here to make the AI core exercisable by hand: 1e's threshold work means editing a corpus and re-asking questions repeatedly, which is not a thing `curl` makes tractable. |
| III. Deliberate, Minimal Service Boundaries | PASS (one deviation recorded) | No new boundary. The existing one gains one rpc for a capability the scheduler does not have (FR-047), placed beside `DeletePatientForChat`, which it generalizes. Two integrity invariants land **in the datastore** rather than in application code: an escalation always has a reason, and an owned FAQ entry always names a live revision — the second is FR-040 made unrepresentable rather than detected. Failure handling across the boundary is designed, not deferred: the session delete's partial outcome is a first-class per-session result (FR-051), and the practitioner proxy deliberately does **not** retry, so an unknown outcome is reported as unknown rather than resolved by creating a second practitioner. **The deviation**: practitioner administration crosses the boundary over HTTP while everything else crosses it over gRPC — see Complexity Tracking. |
| IV. Structured Outputs & Decoupled Tool Interfaces | PASS | `escalate_to_staff` is a registry tool with a closed (empty) schema; its handler performs no I/O and the node importing it knows nothing about what a handoff does. `classify_intent` is untouched — `call_staff` already exists as a label and this feature finally gives it somewhere to go, so the cheap-model routing step is unchanged. The empty schema is the strongest form of the principle available here: the model supplies nothing, so it can misstate nothing (contracts/agent-tools.md). The three non-model callers are not given a tool, which is deliberate — a router that has already read `call_staff`, and a gate that has already concluded it cannot answer, must not then let a model decline to fetch a person. |
| V. Grounded Retrieval with Mandatory Abstention | PASS, and materially strengthened | Abstention stops being a dead end: it calls staff, with no empty-corpus exemption (FR-003c), and leaves the assistant free to answer the rest of the conversation (FR-003d). Retrieval gains a filter that makes a superseded, orphaned or foreign chunk unreachable rather than merely unlikely — never retrieved, never cited, never counted toward groundedness (FR-042d, SC-015b), which is the ".claude/CLAUDE.md" rule that Postgres decides what Qdrant may answer from, enforced instead of stated. FR-042j is the sharp edge and is designed out rather than handled: an unreadable corpus fails the turn *before* the FAQ path, so it can never be reported as an abstention. |
| VI. Documentation as a First-Class Deliverable | PASS | research.md records 24 decisions with rationale and rejected alternatives, including two places where the spec's own requirements conflict (#1, #6) and how each is reconciled. Four contract documents define the HTTP surface, the tool delta, the wire delta, and the log delta against their predecessors rather than restating them. The change carries its own edits: README entries for the three choices a reader would otherwise reverse-engineer (additive revisions, session-scoped retrieval, polling), the ROADMAP's 1d part-2 bullet corrected where it says "no new backend" and where it describes indexing state the console no longer shows, and `.claude/CLAUDE.md`'s superseded Postgres↔Qdrant ordering rule. |
| VII. Clean Architecture, SOLID & Design Patterns | PASS | Every new module has one reason to change: `escalation.py` owns the transition, `scheduler_rest.py` owns the proxy transport, and `console.py` owns the read model. Repositories keep the session-as-parameter shape; the chat client stays the only module importing `shared_proto`; the graph nodes still depend on domain types and never on a provider's wire format. Two pieces of complexity are **removed**: `_revert_faq_update` (a compensating write that could half-succeed silently) and, with it, the entire class of state the spec's withdrawn readiness flag existed to describe. The one shared mutable object — the escalation collector — is argued rather than assumed, including why it is not a LangGraph state key (research #5). |
| VIII. Test-Driven Development (NON-NEGOTIABLE) | PASS (procedural gate) | The contracts fix the testable surface before any code: five HTTP surfaces with their exact failure statuses, one tool with an empty schema and three callers, one rpc with its idempotence requirement, thirteen log events, and data-model.md's enforcement table naming which rule is enforced where. `/speckit-tasks` sequences tests-before-implementation against those. The assertions that matter most are the negative ones, and they are the ones an implementation can pass by accident and fail in production: that no classification call is made in a silent conversation, that a superseded revision is never cited, that a failed save leaves the previous text answering, and that turning the switch on clears the silence and **not** the emphasis. |

**Post-Phase 1 re-check**: re-evaluated against data-model.md, the four contracts, and quickstart.md.
The design adds two migrations, six columns, two CHECKs, three payload fields, one rpc, one tool,
two settings, and zero dependencies. Nothing moved a status. Two judgements are worth re-stating
after design.

First, **Principle III's deviation is real and is recorded in Complexity Tracking** rather than
argued away: one boundary now carries two transports. The alternative was a second copy of the
practitioner contract, which is the duplication the principle exists to prevent, so the deviation is
taken in the direction that keeps one rule in one place.

Second, **Principle I's polling deviation survived the design pass and got stronger.** Writing the
console read model made it plain that one endpoint serves both panes, which removes the patient
side's need for any channel of its own — so the push alternative would have added a subscription
mechanism to replace something that turned out not to exist.

## Project Structure

### Documentation (this feature)

```text
specs/007-escalation-and-staff-console/
├── plan.md                  # This file (/speckit-plan command output)
├── research.md              # Phase 0 output — 24 decisions, in four groups
├── data-model.md            # Phase 1 output — 6 columns, 2 CHECKs, 3 payload fields, the
│                            #   enforcement table, and the two state machines
├── quickstart.md            # Phase 1 output — 13 scenarios, incl. the three save failures,
│                            #   the superseded revision, and the partial delete
├── contracts/               # Phase 1 output — all four are DELTAS against their predecessors
│   ├── http-api.md          #   the console, the admin routes, /faq's new scope and write path
│   ├── agent-tools.md       #   1 tool added (empty schema), and its 2 non-model callers
│   ├── scheduling.proto     #   1 rpc added; nothing else changes
│   └── log-events.md        #   13 events, and the 3 things that must NOT be logged
├── checklists/
│   └── requirements.md      # pre-existing
└── tasks.md                 # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
packages/                                     # UNCHANGED except shared-proto.
├── shared-models/                            #   Nothing to add: the mark kinds and escalation
│                                             #   reasons have one reader (chat), and the deletion
│                                             #   counts are proto fields.
└── shared-proto/
    ├── protos/scheduling/v1/scheduling.proto # MODIFIED per contracts/scheduling.proto (+1 rpc,
    │                                         #   +2 messages)
    └── src/shared_proto/scheduling/v1/*_pb2*.py  # REGENERATED (+ the README's manual import fixup)

services/scheduler/                           # The small half — one rpc, and one data-only reset.
├── alembic/versions/
│   └── *_reset_session_data.py               # NEW and DESTRUCTIVE, data only: deletes every
│                                             #   practitioner and patient, appointments following by
│                                             #   the status-blind cascades. The SCHEMA is unchanged;
│                                             #   this revision exists only so the reset is ordered
│                                             #   and recorded with the deploy (FR-039e)
├── src/scheduler/
│   ├── grpc/servicer.py                      # MODIFIED: + DeleteSession, thin as ever
│   ├── grpc/converters.py                    # MODIFIED: the counts response
│   ├── repositories/practitioner_repository.py  # MODIFIED: + delete_for_session()
│   └── repositories/patient_repository.py    # MODIFIED: + delete_for_session()
│                                             #   Both scoped by session_id on the DELETE itself;
│                                             #   appointments follow by the existing status-blind
│                                             #   cascades (006 research #4)
└── tests/                                    # NEW: test_delete_session (counts, idempotence, the
                                              #   cascade taking cancelled appointments, and that
                                              #   another session's rows are untouched)

services/chat/                                # The bulk of the backend work.
├── alembic/versions/
│   ├── *_add_conversation_attention_state.py # NEW: 4 columns on chats + the reason CHECK + the
│   │                                         #   listing index; 1 column on messages + its PARTIAL
│   │                                         #   index. Pure ADD COLUMN, non-destructive.
│   └── *_scope_faq_entries_to_sessions.py    # NEW and DESTRUCTIVE: deletes every session and every
│                                             #   FAQ entry FIRST, then adds session_id (FK,
│                                             #   CASCADE, indexed) + live_revision, both NOT NULL.
│                                             #   No CHECK — nothing left to constrain. No
│                                             #   downgrade restores the data (FR-039e).
├── src/chat/
│   ├── domain/models.py                      # MODIFIED: Chat + 4 columns; Message + attention_mark;
│   │                                         #   FaqEntry + session_id/live_revision;
│   │                                         #   MessageSender + STAFF; + AttentionMark and
│   │                                         #   EscalationReason StrEnums with the CLEARABLE set
│   ├── domain/schemas.py                     # MODIFIED: + ChatSilentEvent; MessageOut +
│   │                                         #   attention_mark; + the console DTOs. NO staff_name
│   │                                         #   field: `sender` already says it (research #10)
│   ├── agent/escalation.py                   # NEW, and the heart of group (2): EscalationRequests
│   │                                         #   (the per-turn collector, resolved by precedence)
│   │                                         #   and apply_escalation() — the ONE writer, called
│   │                                         #   once per turn by turn.py (FR-001a + FR-006)
│   ├── agent/tools/staff_tools.py            # NEW: escalate_to_staff. Empty schema; the handler
│   │                                         #   records and performs no I/O
│   ├── agent/tools/registry.py               # MODIFIED (small): ToolContext gains the collector,
│   │                                         #   as ambient state a model cannot address
│   ├── agent/graph.py                        # MODIFIED: a `call_staff` label records a call to
│   │                                         #   staff and takes the whole turn, through a new
│   │                                         #   `hand_off` node that retrieves and generates
│   │                                         #   nothing; each node declares its own tool set and
│   │                                         #   builds its own registry; the
│   │                                         #   collector is threaded to both specialists
│   ├── agent/answer_faq.py                   # MODIFIED: the abstention branch records
│   │                                         #   corpus_could_not_answer BEFORE any generation
│   │                                         #   call - a call to staff that does NOT silence
│   │                                         #   (FR-003d); search_faq is passed the live
│   │                                         #   revisions
│   ├── agent/handle_booking.py               # MODIFIED: unavailable / unknown / raised map to
│   │                                         #   assistant_failed; a REFUSAL maps to nothing
│   │                                         #   (FR-003a) — the line this feature draws
│   ├── agent/history.py                      # MODIFIED: + exclude_silent_window(); and
│   │                                         #   to_claude_messages rejoins two consecutive
│   │                                         #   patient bursts, which the split creates and the
│   │                                         #   Messages API forbids (research #9)
│   ├── api/turn.py                           # MODIFIED, the most delicate file: the gate reads the
│   │                                         #   state INSIDE the existing lock; the live revisions
│   │                                         #   are read in the same transaction (research #14);
│   │                                         #   task registration moves inside the lock so a staff
│   │                                         #   post can never miss a turn; apply_escalation()
│   │                                         #   runs after the graph completes
│   ├── api/console.py                        # NEW: GET /console/conversations (the poll, serving
│   │                                         #   both panes), POST .../messages (one transaction,
│   │                                         #   six effects), POST .../assistant (BOTH ways: on
│   │                                         #   clears both silences, off writes the SAME pause a
│   │                                         #   message writes), and the four proxy routes
│   ├── api/admin.py                          # NEW: the two delete routes. include_in_schema=False
│   │                                         #   on the DECORATORS; compare_digest; fail-closed
│   │                                         #   BEFORE the comparison (research #22)
│   ├── api/faq.py                            # REWRITTEN, not extended: session scope on every
│   │                                         #   route, the cap, the three-step sequence, the
│   │                                         #   guarded publish, the reversed delete ordering —
│   │                                         #   and _revert_faq_update DELETED
│   ├── clients/scheduling.py                 # MODIFIED: + delete_session(). Still the only module
│   │                                         #   importing shared_proto
│   ├── clients/scheduler_rest.py             # NEW: the HTTP proxy transport. One attempt, 5s, no
│   │                                         #   retry; 503/504 mapped as chats.py already does
│   ├── rag/indexing.py                       # REWRITTEN: publish_revision() / sweep_entry(). No
│   │                                         #   delete-then-upsert, no revert, and the sweep
│   │                                         #   swallows silently — raising NO event (FR-042h)
│   ├── rag/retriever.py                      # MODIFIED: takes live_revisions; short-circuits on an
│   │                                         #   empty set before embedding
│   ├── repositories/chat_repository.py       # MODIFIED: the state read/writes, the mark clear (one
│   │                                         #   statement), the console listing with its derived
│   │                                         #   columns
│   ├── repositories/faq_repository.py        # MODIFIED: every function gains the session predicate;
│   │                                         #   + reserve_id(), + live_revisions(), + the guarded
│   │                                         #   publish
│   ├── repositories/qdrant_repository.py     # MODIFIED: payload + session_id/revision; the three
│   │                                         #   filters; payload indexes in ensure_collection
│   ├── core/config.py                        # MODIFIED: + ADMIN_SECRET (default ""),
│   │                                         #   + SCHEDULING_HTTP_BASE_URL,
│   │                                         #   + FAQ_MAX_ENTRIES_PER_SESSION (200),
│   │                                         #   + ASSISTANT_PAUSE_SECONDS (120)
│   ├── core/logging.py                       # MODIFIED (one line): ADMIN_SECRET joins the
│   │                                         #   existing secret-fields tuple — FR-050 wants the
│   │                                         #   existing path, not a new one
│   └── main.py                               # MODIFIED: the two new routers; a shared aiohttp
│                                             #   session for the proxy
└── tests/                                    # NEW: test_escalation (the collector, precedence,
                                              #   end-of-turn application), test_silence_gate (the
                                              #   ABSENCES — SC-002), test_attention_marks (FR-027c's
                                              #   grid, all four kinds x both properties),
                                              #   test_console_api, test_admin_api (the four
                                              #   guard properties), test_faq_revisions (failure at
                                              #   each of the three steps);
                                              #   MODIFIED: test_turn_api, test_faq_api,
                                              #   test_history, test_retriever, test_indexing,
                                              #   test_qdrant_repository, test_chat_repository

services/frontend/                            # The largest UI change in the project so far.
├── src/
│   ├── App.tsx                               # MODIFIED: two panes, one polled answer feeding both
│   ├── lib/consoleApi.ts                     # NEW: the console/practitioner/FAQ fetch layer and
│   │                                         #   its types
│   ├── lib/useConsolePoll.ts                 # NEW: the 2s poll. One hook, one endpoint, both panes
│   ├── lib/chatStream.ts                     # MODIFIED: the `silent` terminal event (render
│   │                                         #   nothing), + staff sender and attention_mark
│   ├── components/StaffConsole.tsx           # NEW: the list, emphasis, ordering, attention total
│   ├── components/StaffThread.tsx            # NEW: the thread, the composer, the two-way switch
│   │                                         #   and its countdown, and the per-message marks
│   ├── components/PractitionerAdmin.tsx      # NEW
│   ├── components/FaqAdmin.tsx               # NEW — and it renders NO retrievability state
│   ├── components/MessageView.tsx            # MODIFIED: a third sender, the two role labels
│   │                                         #   ("Staff" / "AI assistant"), and message marks
│   └── components/ChatWindow.tsx             # MODIFIED: refetch when the poll says this thread moved
├── tests/                                    # NEW: StaffConsole, StaffThread, PractitionerAdmin,
│                                             #   FaqAdmin, useConsolePoll;
│                                             #   MODIFIED: App, ChatWindow, MessageView, chatStream
└── vite.config.ts                            # MODIFIED: + /console to the dev proxy. NOT /admin

tests/integration/                            # + the cross-store session delete, its partial
                                              #   outcome and its re-run, and the practitioner
                                              #   proxy's refusal passthrough
docs/ROADMAP.md, README.md, .claude/CLAUDE.md # MODIFIED (see below)
```

**Structure Decision**: no new module, package, service, or layer — but, unlike 006, **five**
genuinely new backend files (plus six on the frontend), and that is the honest signal of what this
feature is. Four of them exist because something new has one reason to change: `agent/escalation.py`
owns the transition, `agent/tools/staff_tools.py` owns the capability the model sees,
`clients/scheduler_rest.py` owns the proxy transport, and `api/console.py` owns the read model. The
fifth, `api/admin.py`, is separate from `api/console.py` precisely because it must not be reachable
from the same place — a maintenance surface sharing a module with the console is one refactor away
from sharing its router and appearing in the schema.

The changes concentrate where the existing boundaries already put the decisions. The silence gate
lands in `turn.py` because that is where the message insert and its lock already are; the guarded
publish lands in `faq_repository` because that is where a `WHERE` clause belongs; the filters land in
`qdrant_repository` because it is the only module that knows what a point looks like. The one file
that is genuinely *rewritten* rather than extended is `api/faq.py`, and that is the feature's own
finding: its ordering was correct for a destructive save, and this design removes the destructive
save rather than making its consequences visible.

**Documentation changes carried by this feature** (Constitution VI — same change, not follow-up):

- **`.claude/CLAUDE.md`** — the "Key design decisions to preserve" bullet describing the Postgres↔
  Qdrant ordering is superseded and must be rewritten to the additive-revision rule. It currently
  states the general principle (the row is the sole authority on which indexed content is live) in
  terms this feature finally implements, so the edit sharpens it rather than reversing it. The
  service list also gains the console's routes.
- **`docs/ROADMAP.md`** — Phase 1d part 2's bullet says "the only new backend is what the escalation
  path itself needs", which is not so: FR-036's proxy and FR-047's rpc are two capabilities across
  the service boundary, and the spec's own Dependencies section already records the correction. The
  FAQ-management bullet says the console "surfaces indexing state so a staff member can tell whether
  what they just wrote is something the assistant can actually retrieve" — that state no longer
  exists, by design, and the bullet has to say so rather than describing a screen this feature
  deliberately does not build.
- **README.md** — a "Escalation and the Staff Console: technology choices" section, in the pattern
  the existing six follow, recording the three choices a reader would otherwise reverse-engineer
  from a migration: **additive chunk revisions** (why a save publishes instead of replacing, and
  that the trade is leaked storage rather than a lost answer), **session-scoped retrieval** (why a
  shared corpus stopped being tenable the moment a delete button existed), and **polling** (why the
  read model is stored state and what that buys over a push channel).
- **`docs/testing-strategy.md`** — no change. No tier is added and no harness convention changes;
  the frontend tier already exists and is simply used properly for the first time.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

One entry. Unlike 006's empty table, this feature has a real deviation, and burying it in prose
would be the wrong kind of tidy.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **A second transport across the chat↔scheduler boundary** (Principle III). Practitioner administration is proxied over the scheduler's existing practitioner REST API; everything else across that boundary is gRPC. | FR-036 and SC-012 require the browser never to hold the session credential, and that API expects the session as an explicit header — so something server-side must carry it. Given that, the console needs the human-facing CRUD the REST surface already **is**: its defaults, its typed refusals, and the error shapes FR-035 requires to reach the screen unchanged. | *Three new RPCs (`CreatePractitioner`, `UpdatePractitioner`, `DeletePractitioner`)* would keep one transport, and would put a **second copy of one contract** across the boundary — the schedule shape, the name-uniqueness and overlapping-range refusals, the seeded-name default — so every future rule change would have to land in two places or silently diverge in one. That is the duplication Principle III's "its own API contract" exists to prevent, and it is a worse failure than two transports. *The frontend calling the scheduler directly* is forbidden by the requirement that creates the problem. The deviation is therefore taken in the direction that keeps one rule in one place, and it is contained: `clients/scheduler_rest.py` is the only module that speaks it, exactly as `clients/scheduling.py` is the only one that speaks gRPC. |

Two further judgements came close to needing an entry and are recorded where they belong instead:
**polling rather than a push channel**, which deviates from the ROADMAP's wording but not from its
phase (research #19, and the Principle I note above); and **one mark per message resolved by
precedence**, which loses the weaker of two simultaneous calls from the console while keeping both
in the log (research #6).
