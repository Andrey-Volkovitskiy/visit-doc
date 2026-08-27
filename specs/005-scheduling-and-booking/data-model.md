# Phase 1 Data Model: Scheduling Service and End-to-End Booking

**Feature**: `005-scheduling-and-booking` | **Date**: 2026-08-12

Two independent datastores. They reference each other by opaque id only — no cross-database foreign
key exists or can exist.

```
┌─ chat: visitdoc_chat ─────────┐        ┌─ scheduler: visitdoc_scheduler ─────────┐
│  sessions                     │        │  patients ──┐                           │
│  chats ── patient_id (opaque) │∙∙∙∙∙∙∙▶│    chat_id  │  (opaque, UNIQUE)         │
│  messages                     │◀∙∙∙∙∙∙∙│  session_id ├──▶ appointments ◀── practitioners
│  faq_entries                  │        │             │                      │    │
└───────────────────────────────┘        │             └───────────────────── working_ranges
                                         └─────────────────────────────────────────┘
       ∙∙∙▶ = opaque id reference, no FK, no join
```

---

## Scheduler datastore (`visitdoc_scheduler`) — all new

All timestamps are **timezone-naive local wall-clock** (research #5): `TIMESTAMP WITHOUT TIME ZONE`
and `TIME WITHOUT TIME ZONE`. `created_at`/`updated_at` are the sole exception — audit metadata
written by `server_default=func.now()`.

**Every entity table carries both audit columns**, declared exactly as `FaqEntry` already does in
`services/chat/src/chat/domain/models.py`:

```python
created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now()
)
updated_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
)
```

Two consequences to be aware of rather than surprised by:

- `onupdate` is applied by SQLAlchemy on flush, not by a database trigger — the repo's existing
  convention, kept here for consistency. A row changed by raw SQL (a `psql` session, a fixture that
  writes directly) will **not** bump `updated_at`.
- `appointments.updated_at` is forward-looking: nothing in this phase mutates an appointment, so it
  equals `created_at` on every row until Phase 1d adds rescheduling. It is added now because the
  column costs nothing today and a migration on a table with two exclusion constraints costs more
  later. `working_ranges` deliberately has neither column: a schedule edit replaces its rows wholesale
  rather than updating them, so the practitioner's own `updated_at` is the meaningful record.

Neither column is exposed on the gRPC contract or in any admin-API response. They are operational
metadata for whoever is reading the database directly; no requirement surfaces them, and adding them
to a wire contract invites a consumer to depend on a value a raw-SQL write can silently leave stale.

Required extension and type, created in the initial migration:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;      -- equality + range in one exclusion constraint
CREATE TYPE timerange AS RANGE (subtype = time); -- no built-in range over `time` (research #7)
```

### `practitioners`

| Column | Type | Notes |
|---|---|---|
| `id` | `VARCHAR(26)` PK | ULID, minted by the repository |
| `session_id` | `VARCHAR(26)` NOT NULL | opaque; indexed |
| `full_name` | `VARCHAR(200)` NOT NULL | from the physician pool unless supplied |
| `specialty` | `VARCHAR(64)` NOT NULL | one of the ten `Specialty` values (FR-005) |
| `appointment_duration_minutes` | `INTEGER` NOT NULL | default `60` (FR-007) |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | audit only |

- `UNIQUE (session_id, full_name)` — FR-012, and the race guard for name allocation (research #12).
- `CHECK (appointment_duration_minutes BETWEEN 5 AND 480)`.
- `specialty` follows this codebase's existing convention (`Message.sender`): a closed set in Python
  (`Specialty`, FR-005 — ten values, research.md #25), a plain string column in the database, so an
  eleventh specialty needs no migration. No SQL enum and no lookup table. A practitioner holds
  exactly one; there is no "other" value. It is display and matching data only — no availability,
  grid, or booking rule reads it.

### `working_ranges`

One continuous span on one weekday. A practitioner's schedule is the set of its ranges; zero ranges
means a practitioner who is listed but never bookable (spec Edge Cases).

| Column | Type | Notes |
|---|---|---|
| `id` | `VARCHAR(26)` PK | ULID |
| `practitioner_id` | `VARCHAR(26)` NOT NULL | FK → `practitioners.id` `ON DELETE CASCADE` |
| `weekday` | `SMALLINT` NOT NULL | 0 = Monday … 6 = Sunday (`Weekday` enum) |
| `start_time` | `TIME` NOT NULL | |
| `end_time` | `TIME` NOT NULL | |

- `CHECK (weekday BETWEEN 0 AND 6)`, `CHECK (end_time > start_time)` — no midnight-spanning range.
- `EXCLUDE USING gist (practitioner_id WITH =, weekday WITH =, timerange(start_time, end_time) WITH &&)`
  — FR-006's non-overlap, enforced in the database.
- Default schedule for a practitioner created without one (FR-057): five rows, weekdays 0–4,
  `09:00`–`17:00`.

### `patients`

| Column | Type | Notes |
|---|---|---|
| `id` | `VARCHAR(26)` PK | ULID |
| `session_id` | `VARCHAR(26)` NOT NULL | opaque; indexed |
| `chat_id` | `VARCHAR(26)` NOT NULL | opaque; **UNIQUE** |
| `full_name` | `VARCHAR(200)` NOT NULL | from the writer pool unless renamed |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | audit only; `updated_at` moves on a rename (FR-048) |

- `UNIQUE (chat_id)` — FR-003's permanent one-to-one pairing, and the thing that makes
  `EnsureSessionProvisioned` idempotent on retry (FR-045, research #10).
- `UNIQUE (session_id, full_name)` — FR-012; may repeat across sessions (FR-014).
- There is no `DELETE /patients` endpoint: a patient dies only with its chat (FR-039).

### `appointments`

| Column | Type | Notes |
|---|---|---|
| `id` | `VARCHAR(26)` PK | ULID |
| `session_id` | `VARCHAR(26)` NOT NULL | opaque; indexed |
| `patient_id` | `VARCHAR(26)` NOT NULL | FK → `patients.id` `ON DELETE CASCADE` |
| `practitioner_id` | `VARCHAR(26)` NOT NULL | FK → `practitioners.id` `ON DELETE CASCADE` |
| `starts_at` | `TIMESTAMP` NOT NULL | local wall-clock |
| `ends_at` | `TIMESTAMP` NOT NULL | `starts_at + practitioner duration at creation time` |
| `idempotency_key` | `VARCHAR(64)` NOT NULL | **UNIQUE**, globally scoped; written only on a created appointment and deleted with it (FR-051/FR-064, research #8) |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | audit only; equal on every row this phase (see above) |

```sql
CHECK (ends_at > starts_at)
EXCLUDE USING gist (patient_id      WITH =, tsrange(starts_at, ends_at) WITH &&)  -- FR-016
EXCLUDE USING gist (practitioner_id WITH =, tsrange(starts_at, ends_at) WITH &&)  -- FR-017
```

- `tsrange(starts_at, ends_at)` is half-open `[start, end)`, so a 09:00–10:00 appointment does **not**
  conflict with a 10:00–11:00 one — required for a contiguous slot grid to be bookable at all, and now
  stated as a requirement in its own right (FR-061) rather than left implicit in the column type. The
  availability walk must use the same semantics, or the offer path and the write path disagree about
  what "overlap" means.
- Both FK cascades are load-bearing: patient cascade is FR-039/FR-055, practitioner cascade is FR-049.
  The patient cascade is also what makes "a booking arriving after the deletion is rejected" true
  without application logic — the insert fails the foreign key.
- `session_id` is denormalized onto the row (derivable from the patient) so every read path can filter
  by session with no join, which is what FR-002/SC-004 are enforced with.
- Nothing here records the practitioner's duration or schedule as of creation; the row's own
  `starts_at`/`ends_at` *are* that record, which is what makes FR-022's grandfathering free.

---

## Chat datastore (`visitdoc_chat`, renamed from `visitdoc` — FR-059) — one changed table

### `chats` — MODIFIED

| Column | Type | Notes |
|---|---|---|
| `id`, `session_id`, `created_at` | unchanged | |
| `patient_id` | `VARCHAR(26)` **NULL** | NEW — opaque reference into the scheduler; indexed |
| `patient_name` | `VARCHAR(200)` **NULL** | NEW — the cached display name; never authored here |

- Nullable by design, not by omission: a chat created while scheduling is unavailable has no patient
  yet (FR-044/FR-054), and every pre-existing chat is in exactly that state after the migration
  (research #19). Both new columns are written together, by the one provisioning call.
- No foreign key, and no `Patient` model in the chat service — it holds an opaque id and a cached
  display name it never authors.
- **Why the name is cached rather than fetched.** `GET /chats` must render a patient name per row
  (FR-036), including after a reload, and the gRPC contract has no "list this session's patients"
  RPC — `EnsureSessionProvisioned` returns one patient, for one chat. Without the cache the chat
  list would need one round trip per row on every render. The tradeoff is staleness in exactly one
  direction: renaming a patient through the scheduler's admin surface (FR-048) leaves this copy
  showing the old name until the next provisioning call refreshes it. Acceptable because that
  surface ships no UI this phase and is a developer/script caller, and because the scheduler stays
  the single authority — this side never writes a name of its own.
- Application-level "one chat per session" is dropped (FR-035). `get_or_create_chat_for_session` and
  `get_chat_for_session` are removed; `list_chats_for_session` (FR-056 ordering, research #13),
  `create_chat`, `get_chat` (session-scoped), `set_patient`, and `delete_chat` replace them.

### `sessions`, `messages`, `faq_entries` — unchanged

No migration touches them. `Message.grounded` simply stays NULL for a booking-only reply (research
#4), which the column already permits.

---

## Cross-cutting Python types

### `packages/shared-models` (research #17)

```python
class Specialty(StrEnum):        # FR-005 — exactly ten; values ARE the display names (research #25)
    CARDIOLOGY = "Cardiology"
    DENTISTRY = "Dentistry"
    DERMATOLOGY = "Dermatology"
    GENERAL_PRACTICE = "General Practice"   # FR-057's default for a bare create
    GYNECOLOGY = "Gynecology"
    NEUROLOGY = "Neurology"
    OPHTHALMOLOGY = "Ophthalmology"
    ORTHOPEDICS = "Orthopedics"
    PEDIATRICS = "Pediatrics"
    PSYCHIATRY = "Psychiatry"

class Weekday(IntEnum):          # 0 = Monday … 6 = Sunday
    MONDAY = 0
    ...

class BookingFailureReason(StrEnum):   # research #9 — mirrors the proto enum 1:1
    PRACTITIONER_BUSY = "practitioner_busy"
    PATIENT_BUSY = "patient_busy"
    OUTSIDE_SCHEDULE = "outside_schedule"
    OFF_GRID = "off_grid"
    IN_PAST = "in_past"
    BEYOND_HORIZON = "beyond_horizon"
    PRACTITIONER_NOT_FOUND = "practitioner_not_found"
    PATIENT_NOT_FOUND = "patient_not_found"

def parse_local_datetime(value: str) -> datetime   # rejects any offset/`Z`; returns naive
def format_local_datetime(value: datetime) -> str  # rejects tz-aware input
```

### `services/chat/src/chat/domain/schemas.py` — additions

| Type | Purpose |
|---|---|
| `ChatRequest` (MODIFIED) | `+ chat_id: str`, `+ local_now: datetime` (naive-only validator, FR-032) |
| `ChatDoneEvent` (MODIFIED) | `grounded: bool \| None`, `+ answer_source: AnswerSource` (research #4) |
| `AnswerSource` | `StrEnum`: `faq` / `booking` / `merged` |
| `ChatSummary` | one row of `GET /chats`: `id`, `patient_name: str \| None`, `created_at`, `last_message_at: datetime \| None` |
| `ChatListResponse` | `chats: list[ChatSummary]` in FR-056 order, plus `session_exists: bool` — the only thing that tells a first arrival (FR-042) apart from a session the user emptied (FR-040), which need opposite handling. The client cannot tell on its own: the session cookie is `HttpOnly` |
| `CreateChatResponse` | the created `ChatSummary` |

`patient_name` is `None` for a chat whose patient does not exist yet; the frontend renders the
FR-054 placeholder (`"Unnamed · 14:32"`) from `created_at` rather than the server inventing a label.

### `services/chat/src/chat/agent/` — new domain types

| Type | Purpose |
|---|---|
| `FaqResult` | `answer_text`, `citations: list[Citation]`, `grounded: bool` — what `answer_faq` writes to state in collect mode |
| `BookingResult` | `reply_text`, `outcome: BookingOutcome` — what `handle_booking` writes to state |
| `BookingOutcome` | `StrEnum`: `booked` / `refused` / `unavailable` / `awaiting_confirmation` / `informational` |
| `_GraphState` (MODIFIED) | `+ intents`, `+ merge_required`, `+ local_now`, `+ session_id`, `+ chat_id`, `+ patient_id: str \| None`, `+ faq_result`, `+ booking_result`. `bursts` stays the **whole** history — each node applies its own 5-turn bound (research.md #23) |

`BookingOutcome` is what keeps FR-028 checkable: `compose_answer` is forbidden from composing a
`booked` claim out of any other outcome, and the assertion in the test suite is against this field,
not against generated prose.

---

## State transitions

**Chat lifecycle** — the only two states that matter are whether a patient exists:

```
(none) ──POST /chats──▶ chat, patient_id = NULL ──provisioning succeeds──▶ chat, patient_id set
                              │  (FR-044: created regardless)   ▲
                              │                                 │ retried on any later turn (FR-045)
                              └─────────────────────────────────┘
        any state ──DELETE /chats/{id}──▶ (gone: chat + messages + patient + appointments, FR-039)
```

**Appointment lifecycle** — there is exactly one transition in this phase:

```
(none) ──BookAppointment──▶ booked ──patient or practitioner deleted──▶ (gone, cascade)
```

No cancellation, no rescheduling, no status column (spec Assumptions: both are Phase 1d). An
appointment is immutable between creation and cascade deletion — which is why "grandfathering"
(FR-022) needs no mechanism beyond *not* revalidating stored rows.

**A booking attempt's outcome**, as seen by the chat service:

| Outcome | Cause | Assistant behavior |
|---|---|---|
| appointment returned | inserted, or the idempotency key matched an earlier attempt (FR-051) | confirm with practitioner and local time (FR-027) |
| `BookingFailure` | one of the eight refusal reasons (research #9) | explain in plain language, offer alternatives (FR-029) |
| transport error after 2 attempts | scheduler unreachable/timed out (FR-047) | "temporarily unavailable", never a fabricated result (FR-046, FR-028) |
| `INVALID_ARGUMENT` | a used key with a mismatched request — a caller defect, not a domain refusal (FR-063) | same wording as unavailable: nothing was booked. Non-retryable; logged as a defect, never presented as a conflict the patient can resolve |

---

## Validation rules, and where each is enforced

| Rule | FR | Enforced |
|---|---|---|
| No two overlapping appointments for one patient | FR-016 | scheduler DB — exclusion constraint |
| No two overlapping appointments for one practitioner | FR-017 | scheduler DB — exclusion constraint |
| A practitioner's ranges on a weekday do not overlap | FR-006 | scheduler DB — exclusion constraint |
| Appointment lies inside a **single** working range, never spanning two contiguous ones | FR-018 | scheduler application code, at creation only (research #6) |
| Appointment starts on the grid of the range containing it; each range has its own | FR-019 | scheduler application code, at creation only |
| Appointment starts strictly after `local_now` — the boundary itself is refused | FR-020, FR-058 | scheduler, against the caller's `local_now` |
| Appointment start within 90 days of `local_now`, boundary inclusive, exact date-time | FR-021, FR-058 | scheduler, against the caller's `local_now` |
| One refusal reason per attempt, chosen by a fixed precedence over the closed set of eight | FR-065 | scheduler application code — the validator's evaluation order *is* the precedence; the two BUSY reasons come last, from the constraints |
| Another session's patient/practitioner id is reported as not-found, indistinguishably | FR-066 | scheduler — every lookup is filtered by `session_id`, so a foreign id simply does not resolve |
| Availability window capped at 14 days and 50 starts, clamped not refused, truncation reported | FR-067 | scheduler application code, in `CheckAvailability` (research #21) |
| Repeat booking with a used key **and a matching request** returns the original | FR-051 | scheduler — UNIQUE column + lookup |
| A used key with a *different* patient/practitioner/start is refused, not replayed | FR-063 | scheduler application code — the lookup compares all three fields before returning (research #8) |
| A refused attempt does not consume its key | FR-064 | scheduler — the key is written only on a successful insert |
| Specialty is one of the ten, and exactly one | FR-005 | scheduler application code — membership of `Specialty`, checked at both the admin-API and gRPC boundaries (research #25) |
| Names unique within a session | FR-012, FR-050 | scheduler DB — `UNIQUE (session_id, full_name)` |
| One patient per chat, permanently | FR-003 | scheduler DB — `UNIQUE (chat_id)` |
| Patient and practitioner belong to one session | FR-008 | scheduler application code, in the booking transaction |
| Nothing crosses a session boundary | FR-002, SC-004 | every scheduler query filters on `session_id`; chat scopes every `/chats` route to the cookie's session |
| Deleting a practitioner deletes their appointments | FR-049 | scheduler DB — FK cascade |
| Deleting a chat deletes patient and appointments | FR-039 | chat orchestrates (research #11); scheduler DB cascades |
| No appointment outlives its patient | FR-055 | scheduler DB — FK (a post-deletion booking fails the constraint) |
| `local_now` carries no timezone | FR-033, FR-043 | chat — Pydantic validator; scheduler — gRPC ingress check |
| Overlap is half-open — back-to-back appointments do not conflict | FR-061 | scheduler DB — `tsrange`'s `[start, end)`; the availability walk uses the same semantics |
| Availability excludes the requesting patient's own conflicts | FR-024 | scheduler — `CheckAvailability` takes `patient_id` and filters on it (research #21) |
| Grandfathered appointments block overlaps **and** remove availability slots | FR-023 | scheduler — they are ordinary rows to both the exclusion constraints and the availability walk's overlap filter; only the *grid generation* ignores them, since it is built from the current ranges |
| Every offered slot is bookable at the moment it is offered | FR-025, SC-009 | scheduler — availability and booking share one validator (research #21); a slot taken by another patient afterwards is the race SC-002 governs, not a violation |
