# Phase 0 — Research

Ten questions the spec left to planning, and what looking at the code returned. Two of them
(R3, R1) changed what this phase has to build.

---

## R1. The log has no machine-readable form

**Decision**: add a **JSON renderer** to `shared-logging`, selected by a new `LOG_FORMAT` setting,
defaulting to the console renderer everywhere. The harness requires the chat service to be running
with `LOG_FORMAT=json`, and says so rather than tolerating either.

**Why this is a finding and not a detail**: `shared_logging.logging.configure_logging` ends its
processor chain with `_build_console_renderer()` and nothing else. There is no other output shape,
and `structlog.dev.ConsoleRenderer` renders every value as a Python `repr` inside a coloured,
ANSI-escaped line.

Several things one would expect to break do not, and are not the reason: the line stays one line
(strings are repr'd, so a newline or a `key=` inside a patient message cannot split it or fake a
field), the ANSI codes strip with a regex, `_truncate_long_strings` recurses per string rather than
clipping a line mid-structure, and a payload of plain data — `faq.retrieval_completed`'s 25
candidates, all ints, floats, bools and strings — does round-trip through `ast.literal_eval`.

What breaks is **any value that is not a Python literal**, and two of them sit on the events this
phase must read:

```
intent.classified   segments=[{'position': 0, 'intent': <IntentLabel.FAQ_QUESTION: 'faq_question'>, …}]
booking.tool_called arguments={'starts_at': datetime.datetime(2026, 3, 9, 9, 0)}
```

`ast.literal_eval` raises `SyntaxError` on the first and `ValueError` on the second. The first is
`intent.classified`, which FR-021a names as the only source of the produced segmentation — so the
single event the classification metrics depend on is the one that cannot be recovered. Worse, the
rendering is **depth-dependent**: the same `StrEnum` prints as `answered` at the top level, where
the renderer takes its `str`, and as `<IntentLabel.FAQ_QUESTION: 'faq_question'>` one level down
inside a list. A parser would need different rules per nesting depth for the same type.

Underneath the mechanics is the design objection: `ConsoleRenderer` is structlog's *development*
renderer, for people. Its output carries no format-stability guarantee, while
`specs/008-.../contracts/log-events.md` froze field names and types as a **data** contract. Parsing
a display format to satisfy a data contract means a structlog minor release can change every
retrieval metric silently — and it would surface as "retrieval got worse", which is precisely the
confusion this phase exists to prevent.

**Why it is a small change and a safe one**: rendering is decided in exactly one place, and that
module's own docstring says so — *"switching the rendering later is a one-line change here, not a
rewrite of every call site"*. The renderer sits **last** in the chain, after
`make_redact_secrets_processor`, so a JSON line is rendered from an already-redacted event dict:
the security control is upstream of the change and is not touched by it. It is the first of
the two changes FR-051 permits; the second, a startup event stating the service's settings, is R11.

**Alternatives considered**: parsing the console output (rejected: the truncation cap and the
`repr` of nested structures make it unreliable in exactly the cases that matter most, the long
candidate lists); adding a read endpoint to the chat service that replays a turn's events (rejected:
a much larger change to a published surface, and it would store events nothing else needs);
in-process capture (impossible — the spec's seam is HTTP, so the service is another process).

---

## R2. Attributing log lines to a case

**Decision**: **byte offsets plus a `turn_id` check.** The harness records the log file's size
immediately before posting the case's turn and reads from there once the turn's terminal event has
arrived. Within that slice it groups lines by `turn_id`, and selects the group whose
`turn.message_received` carries the case's own patient message id in `message_ids_unified`.

**Why both**: the offset alone is exact *because the run is sequential* (FR-003a) — there is no
second case in flight to interleave with. It is not exact against a human using the same deployment
from a browser, and the spec permits that (FR-003a's last sentence). The `turn_id` check makes the
slice airtight at no cost, and it fails loudly rather than quietly mixing two turns' candidates.

**Why not correlate by `turn_id` alone**: it is generated server-side in `bind_turn_id()` and never
returned to the client, so there is nothing to correlate against until the slice is already read.

---

## R3. The pinned corpus hash cannot be reproduced

**Decision**: 2b **re-takes the pin** with a construction written down beside it, and records that
the previous value is unverifiable history rather than a check anyone can run.

**What was found**: `corpus.json` carries `sha256:9552b098bcc15fde51207409f514f2da246c730cea55281a0bc405027c55a267`
and describes it only as "a `sha256` over the entry texts". The construction is recorded nowhere.
The nine texts in `corpus.json` are byte-identical to `DEFAULT_FAQ_ENTRIES` today — the corpus has
**not** drifted — yet none of seventeen plausible constructions reproduces the value: the texts
joined by newline, with and without a trailing one; concatenated; joined by NUL, by double newline,
by a record separator, by `\r\n`, by `\n---\n`; each on its own line; `json.dumps` of the list in
four variants; `repr`/`str` of the list and of the tuple; the entries array with and without sorted
keys; the questions alone; a hash of the per-entry hashes; and an incremental update per entry with
and without a separator.

**Why this matters rather than being a curiosity**: FR-010 requires the run to verify the live
corpus against that hash before spending a model call, and FR-011 requires it to stop on a
mismatch. A check whose expected value cannot be recomputed is a check that always fails, which in
practice means a check that gets deleted. The pin is a good idea implemented as an unverifiable
number, and the fix is to make it verifiable, not to drop it.

**The construction to adopt**: `sha256` over the entries' texts in index order, each encoded UTF-8
and terminated by `\n`, with nothing else in the digest — stated in `corpus.json` itself as an
`algorithm` field so the next reader does not have to guess, and covered by a test that recomputes
it from `DEFAULT_FAQ_ENTRIES`. The old value is kept in `PROVENANCE.md` as what was recorded, with
a note saying it could not be reproduced.

**Alternatives considered**: keep brute-forcing (rejected — the search space of plausible
constructions is unbounded, and even a hit would not make the algorithm *documented*); drop the pin
(rejected — every `cites` label is meaningless without the text it names, which is the whole reason
2a wrote it down).

---

## R4. Where the harness lives

**Decision**: a new uv workspace member at **`evals/harness/`**, distribution `golden-harness`,
module `golden_harness`, with `src/` and `tests/` like every other member.

**Why there**: FR-050 puts it outside `specs/`, which is excluded from ruff and mypy. `packages/` is
documented as the place for *code shared across services*, and this is not shared library code —
it is a tool. `evals/` already holds the thing it reads, and keeping the reader beside the data is
what makes "2b reads it, 2c gates on it" legible in the tree.

**What this costs**: three config edits, because the repo's tooling is centralized on purpose — the
root `[tool.uv.workspace] members` list gains `evals/harness` (it is not under the `packages/*`
glob), `[tool.mypy]`'s `files` and `mypy_path` gain its `src`, and `[tool.pytest.ini_options]`'s
`testpaths` gains its `tests`. Ruff needs nothing: its hierarchical discovery walks up to the root
config on its own.

---

## R5. The harness depends on `chat`

**Decision**: `golden-harness` takes a workspace dependency on `chat`, and reads its domain schemas
— `IntentLabel`, `FaqVerdict`, `RequestOutcome` — rather than re-declaring them.

**Why**: a scorer with its own copy of the eight intents is a scorer that keeps working, wrongly,
the day a ninth is added. Importing the real enum makes drift an `ImportError` or a validation
failure at the first case, which is the loud failure the spec asks for everywhere else. It also
lets the harness parse a stored `request_outcomes` blob through `RequestOutcome` itself, so the
record's own invariants (an answered request carries text and citations; an abstained one carries
neither) are re-checked on the way in rather than assumed.

**Why it is not a boundary violation**: the *run* crosses the boundary over HTTP, as the spec
requires. This is a build-time dependency of a development tool on schemas in the same monorepo —
the same relationship `services/chat/tests` already has.

---

## R6. Planting history, and reading the chat's ids

**Decision**: the harness writes history rows directly into the chat database through `shared-db`,
and reads `chats.session_id` and `chats.patient_id` from the same connection.

**What the rows need**: `messages` takes a caller-supplied ULID `id`, `chat_id`, `sender` (a plain
string column, written from `MessageSender`), `content`, and `created_at`. Everything else is
nullable and stays NULL for a planted row: `request_outcomes` (no FAQ half ran), `reply_to_message_ids`,
`attention_mark`. Ordering is by `created_at`, so planted rows are written with ascending timestamps
strictly before the turn is posted.

**Why direct writes**: three of the five history cases are assistant-role, and no published surface
posts as the assistant — the console composes as *staff*, which is a different sender and would
change what the classifier reads. The spec already states this as a capability (FR-005).

**Why the ids are read the same way**: `patient_id` is cached on the chat row and is what
`BookAppointment` and `ListAppointments` are addressed by. The harness is already connected for the
history writes, so no second mechanism is introduced to fetch it.

---

## R7. Fixtures go over gRPC, and the clock has to suit the roster

**Decision**: preconditions are planted with `BookAppointment` and the post-state is read with
`ListAppointments`, both against the scheduler's own gRPC contract; practitioners are resolved by
pool name through `ListPractitioners`. The run clock is pinned to **a Monday at 08:00**, and every
fixture time is expressed as an offset from it.

**What the seeded roster actually is**: `SESSION_SEED` gives a fresh session exactly **two**
practitioners — one of the default specialty working Mon–Fri 09:00–17:00, and one in Dentistry
working Mon–Sat 09:00–14:00 — drawn from `PHYSICIAN_POOL` in allocation order, so they are *William
Osler* and *Andreas Vesalius*. Three consequences the fixture labels have to respect:

1. A fixture appointment must fall inside its practitioner's weekly range, on the 60-minute grid,
   strictly after `local_now` and within `BOOKING_HORIZON_DAYS` (90) — otherwise `BookAppointment`
   refuses it with `OUTSIDE_SCHEDULE`, `OFF_GRID`, `IN_PAST` or `BEYOND_HORIZON` and the fixture
   never plants.
2. A Monday-08:00 clock puts the whole working week ahead of it, so "tomorrow", "Thursday",
   "Friday" and "Monday" all resolve to days a fixture can name. Any other weekday would make some
   of those resolve backwards.
3. **A fresh session has no cardiologist.** G049 ("which cardiologists do you have?") and G053 ("can
   I see a cardiologist next week?") are correctly labelled with `list_practitioners` and
   `check_availability` — the tool is called and the honest answer is *none* — and their expected
   post-state is simply their precondition set, since neither writes anything. This is not a defect
   in the labels; it is what the roster is, and the fixture makes it explicit instead of leaving it
   to be rediscovered when the metric reads oddly.

**Why not plant through the agent**: a fixture the agent creates is the thing under measurement
setting up its own exam (FR-040), and a booking loop that failed to plant would be indistinguishable
from a booking loop that failed the case.

**Cleanup between cases (added 2026-09-14).** The two practitioners are shared by every case in the
session, so an appointment one case leaves standing would refuse a later `given` or take a slot a
later turn asks for. Once a case's post-state is stored, the harness lists that patient's standing
appointments and cancels each with `CancelAppointment` — its guard fields (`expected_starts_at`,
`expected_practitioner_id`) taken from the listing just read, `local_now` the run clock. A cancelled
appointment leaves the partial exclusion constraint (`WHERE status = 'standing'`), so the slot is
free at the datastore and no application filter is trusted to agree (spec FR-041b).

**Confirmation takes two turns (added 2026-09-14).** The booking prompt confirms both practitioner
and start time before `book_appointment`, and forbids `cancel_appointment` without a confirmation
given in the current turn — so the specified exchange for "cancel tomorrow" is a read-back and a
question, then the write on the patient's "yes". A fixture for such a case carries a `reply`, posted
as a second full turn; the calendar is read after each turn (spec FR-037b). The reply answers every
choice the loop is told to leave to the patient, so a correct loop needs no third turn.

---

## R8. Run artifacts, resume, and what "pure scoring" needs

**Decision**: one directory per run, holding a `run.json` (the conditions of FR-047) and one
`cases/<id>.json` per driven case; scoring reads only those files.

**Why per-case files**: FR-007's resume is then a directory listing rather than a parse of a
half-written aggregate, and a case that failed mid-write leaves one unreadable file rather than a
truncated run. It is also what makes FR-044 true in the strong sense — a scorer can be pointed at a
run recorded weeks earlier, including the one committed under FR-048a, and produce numbers without
the stack being up at all.

**What a case file must therefore hold**: everything a metric could ever need — the terminal event,
both stored messages, the turn's log slice as parsed events, the scheduler state read after the
turn, the attempt count, and the exclusion reason if there is one. A field left out here is a
re-run later, at full cost.

---

## R9. Which failures are "the measurement never happened"

**Decision**: retry on transport failure, on a 429 or other rate-limit response, and on a 5xx that
is not the turn's own failure — bounded at three attempts. Never on a turn that completed.

**How the two are told apart**: a turn that ran and broke comes back as a *completed* exchange whose
stored patient message carries the `assistant_failed` mark (`agent/escalation.py` applies it on
every failure path since 011 FR-026). A turn that never happened has no stored assistant message and
no mark — the request did not reach the server, or the server never answered it. The distinction is
already drawn in the system; the harness reads it rather than inventing its own.

**Narrowed on 2026-09-14, twice.** For a case carrying a scheduling fixture, only a failure proving
the pipeline never ran is retried — no connection, or a status line instead of a stream. A read
timeout or a stream cut off after it began may have left an appointment nothing can un-book, so the
case records `outcome_unknown` and is not retried (spec FR-007d; `.claude/CLAUDE.md`, *a timeout
never proves the server did nothing*). And `assistant_failed` alone does not make a run error: a turn
carrying it that still stored a reply — a booking tool failed and the loop said so — is scored on
what it left behind and listed (FR-007e).

---

## R10. Testing the harness

**Decision**: the harness's unit tier lives at `evals/harness/tests/`, runs in the existing
`make test-unit` tier, and spends **no** live model call — every test drives the scorer from
recorded case files and the driver from a stubbed HTTP/gRPC surface.

**Why this is possible at all**: because FR-044 makes scoring pure. The scorer is the part carrying
the arithmetic that can be wrong — alignment, denominators, exclusions, MRR — and it takes files,
not a stack. Committing a handful of small, hand-written case files as fixtures gives every metric
a test with a known answer, including the ones the spec says should be zero.

**Constitution VIII, in order**: the contracts in this directory are written first, the tests are
derived from them and observed to fail, and the implementation follows. The two metrics with a
target of zero get their tests *before* the first real run, so the first run's numbers are read
against a scorer that was already proven on cases whose answers were known.

---

## R11. Conditions come from the service, not the harness (added 2026-09-14)

**Decision**: the chat service emits one `service.configured` event at startup carrying the model
identifiers, thresholds, caps and limits FR-047 names, and the harness takes a run's conditions from
it (spec FR-047c; field contract in `contracts/log-access.md`).

**Why**: the harness reads the same `.env` the service does, but reading it there describes the
harness. A service started with an overridden `RERANK_FLOOR`, or still running an older build, would
be recorded under settings it was not using, and nothing would notice. Floors and caps do appear on
the per-turn gate events; the classification, generation and embedding models appear on none.

**Cost**: a second change to the chat service beside the JSON renderer — one log call at startup,
nothing a turn does (FR-051).
