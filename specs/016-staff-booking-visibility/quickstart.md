# Quickstart: validating 016

The automated checks come first and are free. The manual walk-through spends real Claude and
Voyage calls on the booking turns, as every patient-pane check does (`docs/testing-strategy.md`).

## 1. Gates

```bash
make migrate            # applies the booking_acts table + the scheduler index
make lint typecheck
make test-unit          # chat, scheduler, harness, shared
make eval-compare BASE=evals/baselines/01M321DWRXSVSY7GW9RY3CR9YW \
                  NEW=evals/baselines/01M321DWRXSVSY7GW9RY3CR9YW   # committed run still re-scores; offline
make test-frontend      # vitest; `make typecheck` above already ran tsc over the SPA
make test-integration   # the practitioner-week read against a real scheduler DB
```

Expected: everything passes. Specifically:

- The scheduler's practitioner-week tests exclude the cancelled, ended, out-of-window,
  other-practitioner and other-session rows (SC-005).
- The registry tests cover every row of `contracts/booking-acts.md`'s recording table.
- The harness's existing tests pass with no edit to recorded data (SC-006).

## 2. Stack

```bash
LOG_FORMAT=json make services-up   # scheduler, chat and frontend in the background
```

Open `http://localhost:5173`. If the Windows browser cannot reach it, use `hostname -I`.

## 3. The practitioner week (Story 2)

1. On **Practitioners**, check that every block ends with **Show bookings**, and that no block
   shows appointments.
2. Click **Show bookings** on one practitioner.
   - Expected: either "Nobody is booked with … in the next seven days", or a day-grouped list.
   - The button now reads **Hide bookings**.
3. In the patient pane, ask the assistant to book that practitioner tomorrow at a free time, and
   confirm.
   - Expected: within about 5 seconds the appointment appears under tomorrow in the open block,
     with no reload (SC-004).
4. Click **Hide bookings**.
   - Expected: the list is gone and the block is its compact size again.
5. Open **Edit** on that practitioner.
   - Expected: no appointments panel anywhere in the edit view.
6. Stop only the scheduler with `scripts/dev-services.sh down scheduler`, then click
   **Show bookings**. Bring it back afterwards with `scripts/dev-services.sh up scheduler`.
   - Expected: "The appointments could not be read.", and never "Nobody is booked".

Scripted alternative for the endpoint alone:

```bash
scripts/dev-chat.sh session   # mints a session into .run/dev.jar, then:
curl -s -b .run/dev.jar \
  "http://localhost:8000/console/practitioners/<id>/appointments?local_now=$(date +%Y-%m-%dT%H:%M:%S)" | jq
```

## 4. Booking acts in the console (Story 1)

1. **Done.** In the patient pane, book an appointment, then cancel it in the next turn.
   - Open the conversation in **Conversations**. Each of the two patient messages carries a
     `served` marker.
   - Expanding them reads "Booked: …" and "Cancelled: …", with the practitioner, date and time.
2. **Refused.** Ask for a slot outside the practitioner's hours.
   - The act reads "Refused (outside the practitioner's hours) … Nothing was changed."
3. **Not sent.** Stop the scheduler mid-conversation, then ask to book.
   - The write fails before sending, so the act reads "Not sent … Nothing was changed."
   - The patient message is marked `assistant_failed`.
   - Unknown-outcome paths (timeout after the request was sent) are covered by the registry tests.
     Reproducing one by hand needs a deliberately slowed scheduler and is not part of this walk.
4. **Merged turn.** Ask "what's your address, and book me with Dr. X tomorrow at 10".
   - The reply's marker shows the FAQ outcome.
   - The patient message's marker shows the booking. Neither shows the other's content.
5. **Patient pane.** Throughout, the patient pane shows nothing new (FR-021).
6. **A settle with no reply.** With the conversation open in the console, book in a turn that fails
   after the write. The simplest way is to stop the chat service's Anthropic access, for example
   with a bad key, after the model has issued the booking call.
   - Expected: within about 5 seconds the patient message's act reads "Booked: …", not "unknown"
     (SC-007).
   - The version change is also covered by T042 and T044 without a live stack.

## 5. Record check

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat -c \
  "select operation, outcome, refusal_reason, practitioner_full_name, starts_at
     from booking_acts order by seq desc limit 10;"
```

Expected: one row per write attempt above, with no NULL outcome except for acts interrupted by
design.
