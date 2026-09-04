# Quickstart: Scheduling Service and End-to-End Booking

**Feature**: `005-scheduling-and-booking` | **Date**: 2026-08-12

Runnable validation for Phase 1c. Details live in the design artifacts rather than here:
[data-model.md](./data-model.md), [contracts/scheduling.proto](./contracts/scheduling.proto),
[contracts/chat-api.yaml](./contracts/chat-api.yaml),
[contracts/scheduler-admin-api.yaml](./contracts/scheduler-admin-api.yaml),
[contracts/agent-tools.md](./contracts/agent-tools.md).

---

## Prerequisites

```bash
make db-up                      # Postgres + Qdrant
uv sync                         # picks up scheduler's new dependencies
```

**Rename the chat database first** (FR-059, research.md #26). The init scripts only run on a *fresh*
volume, so an existing one needs this by hand — `visitdoc` is still what your current `.env` points
at:

```bash
# keep your existing data (no active connections — stop the chat service first)
docker exec visitdoc-postgres psql -U visitdoc -d postgres \
  -c "ALTER DATABASE visitdoc RENAME TO visitdoc_chat;" \
  -c "ALTER DATABASE visitdoc_test RENAME TO visitdoc_chat_test;"

# …or start clean instead, which recreates every database from the init scripts
make db-reset && make db-up
```

The scheduler's databases come from `docker/postgres-init/02-create-scheduler-dbs.sql`, again on a
fresh volume only. On a pre-existing one, create them by hand too:

```bash
docker exec visitdoc-postgres psql -U visitdoc -d postgres \
  -c "CREATE DATABASE visitdoc_scheduler;" \
  -c "CREATE DATABASE visitdoc_scheduler_test;"
```

`.env` changes and additions:

```bash
# chat — the database segment changes; nothing else about this line does
DATABASE_URL=postgresql+asyncpg://visitdoc:visitdoc@localhost:5432/visitdoc_chat
SCHEDULING_GRPC_TARGET=localhost:50051
SCHEDULING_TIMEOUT_SECONDS=2.0     # FR-047
SCHEDULING_MAX_ATTEMPTS=2          # FR-047

# scheduler
SCHEDULER_DATABASE_URL=postgresql+asyncpg://visitdoc:visitdoc@localhost:5432/visitdoc_scheduler
SCHEDULER_GRPC_PORT=50051
SCHEDULER_HTTP_PORT=8001
```

The test databases need no configuration of their own: `conftest.py` derives `visitdoc_chat_test`
from whatever `DATABASE_URL` names, so it follows the rename automatically.

Run all three processes, each in its own shell:

```bash
make run-scheduler-dev    # alembic upgrade head, then uvicorn :8001 + gRPC :50051
make run-chat-dev         # alembic upgrade head, then uvicorn :8000
make run-frontend-dev     # Vite :5173
```

Sanity checks before validating anything:

```bash
curl -sf localhost:8001/health && echo "scheduler http ok"
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler \
  -c "\d appointments" | grep -i exclude       # both exclusion constraints present
```

---

## Scenario 1 — Book an appointment by chatting (US1, P1)

1. Open http://localhost:5173 in a clean browser profile. A chat appears immediately.
2. Ask: **"What specialists do you have?"** → the seeded practitioner is listed with their
   specialty (FR-030).
3. Ask: **"I'd like to book with them next Tuesday afternoon."** → the assistant offers times inside
   the practitioner's working hours only.
4. Pick one and confirm. → the assistant confirms practitioner and time in plain local time.

Verify from outside the conversation:

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler -c \
  "SELECT a.starts_at, a.ends_at, p.full_name AS patient, d.full_name AS practitioner
     FROM appointments a JOIN patients p ON p.id = a.patient_id
                         JOIN practitioners d ON d.id = a.practitioner_id;"
```

**Expected**: exactly one row, matching what the assistant said. `ends_at - starts_at` equals the
practitioner's `appointment_duration_minutes`.

**Negative checks** (each must leave the row count unchanged):
- Ask for a time outside working hours, or one already taken, or one in the past → the assistant
  explains why and offers alternatives (FR-029, US1-4).
- Choose a time but do **not** confirm → nothing is created (FR-027, US1-3).
- Ask a *different* practitioner for availability on the same day → the hour already booked is **not
  among the offered times** (FR-024, US1-5, first half); name it anyway → refused with the conflict
  explained (FR-016, US1-5, second half). Both halves matter: the first is the offer path, the second
  is the guard behind it.
- Ask for the slot starting exactly when the existing appointment ends (10:00 after a 09:00–10:00
  booking) → offered and bookable, since intervals are half-open (FR-061).
- Ask for a time that falls on no working day at all (a Sunday, for the default schedule) → refused
  as *outside the practitioner's hours*, not as an off-grid time (FR-065's precedence).
- Ask "anything in the next three months?" → the answer covers roughly the next two weeks and the
  assistant offers to look further ahead rather than implying that is everything (FR-067).

---

## Scenario 2 — First visit survives a scheduling outage (US2, P2)

```bash
# stop only the scheduler process (Ctrl-C its shell); leave Postgres and chat running
```

1. Open the site in a **second** clean browser profile.
2. **Expected**: a chat is created and listed as unnamed with its creation time (FR-054); an FAQ
   question still gets a grounded answer (FR-044, SC-005).
3. Ask to book → "booking is temporarily unavailable", no fabricated result (FR-046, US2-3), and the
   reply arrives within 5 seconds (SC-013 — time it).
4. Restart the scheduler, send another message, reload → the chat now shows a real patient name
   (FR-045).
5. Reload once more → still exactly one patient for that chat, no duplicate (US2-4):

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler -c \
  "SELECT chat_id, count(*) FROM patients GROUP BY chat_id HAVING count(*) > 1;"   # must be empty
```

---

## Scenario 3 — Read-only questions (US3, P3)

In the profile from Scenario 1: ask **"What appointments do I have booked?"** → the booking from
Scenario 1, in local time. Then, in the profile from Scenario 2, ask the same → "you have none",
not an error and not the other session's data (SC-004).

To check the past-appointment rule (FR-031) without waiting: book a slot, then edit the appointment's
`starts_at` into the past directly in the database and ask again — it must disappear from the list
while still blocking a booking that overlaps it (FR-023).

---

## Scenario 4 — Several patients from one browser (US4, P4)

1. Create two more chats from the chat list. **Expected**: three chats, three distinct patient names
   drawn from the writer pool in order (FR-011).
2. Send a message in each, switch between them → each shows only its own history (FR-038).
3. Reload → the chat holding the newest message opens (FR-056).
4. Book an appointment as the second patient with the same practitioner, at the time the first
   patient already holds → refused (FR-017, US4-8).
5. Delete the second chat → its chat, messages, patient, and appointments are gone; the others are
   untouched (FR-039, SC-011):

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler -c \
  "SELECT (SELECT count(*) FROM patients) AS patients,
          (SELECT count(*) FROM appointments) AS appointments;"
```

6. Delete the remaining chats → the session survives with zero chats and the chat area is muted, with
   the create-chat control still working (FR-040, FR-041).

---

## Scenario 5 — Admin API (US5, P5)

Read the session id straight from the browser's cookie jar, or from the chat service's log line for
the request, then:

```bash
SID=<session id>

# Bare create: every default applies, immediately bookable (FR-057)
curl -sX POST localhost:8001/practitioners -H "X-Session-Id: $SID" \
     -H 'Content-Type: application/json' -d '{}'

# A dentist with a split shift
curl -sX POST localhost:8001/practitioners -H "X-Session-Id: $SID" \
     -H 'Content-Type: application/json' -d '{
       "specialty": "Dentistry", "appointment_duration_minutes": 30,
       "schedule": [{"weekday": 1, "start_time": "08:00", "end_time": "12:00"},
                    {"weekday": 1, "start_time": "13:00", "end_time": "16:00"}]}'

# Grandfathering: narrow a schedule past an existing appointment — the edit must SUCCEED (FR-022)
curl -sX PATCH localhost:8001/practitioners/<id> -H "X-Session-Id: $SID" \
     -H 'Content-Type: application/json' \
     -d '{"schedule": [{"weekday": 1, "start_time": "08:00", "end_time": "09:00"}]}'

# Name collision must be refused (FR-050)
curl -isX PATCH localhost:8001/practitioners/<id> -H "X-Session-Id: $SID" \
     -H 'Content-Type: application/json' -d '{"full_name": "<an existing practitioner name>"}' \
     | head -1
# expect: HTTP/1.1 409

# A patient's name is assigned once and never edited (FR-048 as amended after 007)
curl -isX PATCH localhost:8001/patients/<id> -H "X-Session-Id: $SID" \
     -H 'Content-Type: application/json' -d '{"full_name": "Anything"}' | head -1
# expect: HTTP/1.1 404 — there is no such route

# Another session's data is invisible (US5-5)
curl -isX GET localhost:8001/practitioners -H "X-Session-Id: 00000000000000000000000000" | head -1
# expect: HTTP/1.1 200 with an empty array — never another session's rows
```

The specialty list is closed and enumerable (FR-005, FR-060, research.md #25):

```bash
# The dropdown's source — ten values, name-sorted. No session header needed.
curl -s localhost:8001/specialties
# expect: ["Cardiology","Dentistry","Dermatology","General Practice","Gynecology","Neurology",
#          "Ophthalmology","Orthopedics","Pediatrics","Psychiatry"]

# Anything outside the list is refused, on create and on edit alike
curl -isX POST localhost:8001/practitioners -H "X-Session-Id: $SID" \
     -H 'Content-Type: application/json' -d '{"specialty": "Paediatric dermatology"}' | head -1
# expect: HTTP/1.1 422 — no free text, no "other" escape hatch

# Changing a practitioner's specialty to another list value succeeds
curl -sX PATCH localhost:8001/practitioners/<id> -H "X-Session-Id: $SID" \
     -H 'Content-Type: application/json' -d '{"specialty": "Cardiology"}'
```

With two dentists present, ask **"I'd like to see a dentist"** → the assistant lists both and asks
which, offering no times until you choose (FR-052, US1-8). Then ask in words that never name the
specialty — **"I've chipped a tooth"** → the same two are offered, since matching is by meaning
rather than string comparison even though the vocabulary is now closed.

---

## Scenario 6 — Mixed intent (research.md #2)

Ask a single message carrying both intents: **"What should I bring to a first visit, and can I book
Friday morning?"**

**Expected**: one coherent reply covering both halves — a grounded, cited answer to the question and
a real booking step for the appointment. Confirm the fan-out actually happened in the chat log — the
two specialists' lines interleave, and `node` is what separates them (research.md #24):

```bash
# in the chat service's output, all sharing one turn_id
node.completed  node=classify_intent  result={'specialists': ['answer_faq', 'handle_booking'],
                                              'merge_required': True}
node.started    node=answer_faq
node.started    node=handle_booking
faq.retrieved   node=answer_faq       chunk_count=3
booking.tool_called   node=handle_booking  tool_name=check_availability  iteration=1
booking.tool_result   node=handle_booking  tool_name=check_availability  status=ok
node.completed  node=answer_faq       result={'grounded': True, 'mode': 'collected'}
node.completed  node=handle_booking   result={'outcome': 'booked', 'iterations': 2}
node.completed  node=compose_answer   result={'answer_source': 'merged', 'merged': True}
turn.completed  ...
```

Then run a single-intent message and confirm the shape still holds: only one specialist starts,
`compose_answer` reports `merged: False`, and `turn.completed` appears exactly once either way.

If retrieval is weak for the question half, the reply must say it has no confident answer to that
part while still handling the booking part — never fill the gap from model knowledge
(Constitution V).

---

## Scenario 7 — Concurrency and idempotency

Double booking (SC-002), driven straight at the scheduler so the race is real:

```bash
grpcurl -plaintext -d '{...same starts_at, different idempotency_key...}' \
        localhost:50051 scheduling.v1.Scheduling/BookAppointment &
grpcurl -plaintext -d '{...same starts_at, different idempotency_key...}' \
        localhost:50051 scheduling.v1.Scheduling/BookAppointment &
wait
```

**Expected**: exactly one `appointment`, one `failure` with `PRACTITIONER_BUSY`, one row in the
table. The scheduler logs `booking.race_lost`.

Idempotent replay (FR-051, SC-012): repeat one `BookAppointment` call with the **same**
`idempotency_key`. **Expected**: the same appointment id both times, `idempotent_replay: true` on the
second, still one row — never a `PATIENT_BUSY` conflict with itself.

---

## Automated suites

```bash
make test              # unit (chat, scheduler, shared-models, shared-proto) + frontend
make test-integration  # chat <-> scheduler over real gRPC against a real scheduler database
make lint && make typecheck
```

Per Constitution VIII, the tests for each contract above are written and observed failing before the
implementation that satisfies them. `make test-integration` stops being a placeholder in this
feature and runs in CI alongside the unit tier.
