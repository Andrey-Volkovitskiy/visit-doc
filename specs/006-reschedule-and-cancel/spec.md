# Feature Specification: Rescheduling and Cancellation (Phase 1d, part 1)

**Feature Branch**: `006-reschedule-and-cancel`

**Created**: 2026-08-29

**Status**: Draft

**Input**: User description: "Create spec for the 1st part of Phase 1d - Rescheduling and
cancellation. App rescheduling means that it's start/end timedate is changed. If a patient wills to
change existing booking seeing another practitioner then his old appt should be cancelled and
created a new one. A patinet confirmation should be requested mentioning start datetime and
paractitioner name and speciality before actual rescheduling/cancellation. If an appt is rescheduled
or cancelled then is't ID, old and new start datetime should be logged."

## Clarifications

### Session 2026-08-29

- Q: Should a practitioner change be carried out as a cancellation plus a new booking, as the
  original description said? → A: No — changed during specification. A practitioner change
  **modifies the existing appointment** rather than cancelling it. The appointment keeps its
  identifier; its practitioner, start, and end change together. This removes the two-halves problem
  entirely: there is one write, so a partial outcome where the patient holds two appointments or
  none is not reachable.
- Q: When a patient cancels, is the appointment record retained (marked cancelled) or removed? →
  A: **Retained, marked cancelled.** (This supersedes an earlier answer of "removed" given in the
  same session.) The appointment keeps its row and its identifier and gains a status; cancelling
  sets that status. The consequence, and the cost of the choice, is that every read now has to say
  which statuses it means — an omitted filter silently resurrects a cancelled appointment into an
  availability calculation or a patient's list (FR-009, FR-010, FR-012).
- Q: Should a patient's cancelled appointments be visible to them? → A: Not by default. What the
  patient is given by default is their **future, standing** appointments — both filters apply at
  once, so neither a cancelled appointment nor one that has already started appears (FR-014).
- Q: A patient cancels, then rebooks the same practitioner at the same time — what happens to the
  booking idempotency key that created the cancelled appointment? → A: Cancelling **releases** the
  key. It binds to a standing appointment, not to a stored row, so rebooking the freed slot is an
  ordinary new booking with a new identifier rather than a replay of the cancelled one. Phase 1c
  fixed the key's lifetime as "exactly as long as the appointment that recorded it" (005 FR-064)
  when cancelling deleted that appointment; retaining the record changes what that phrase means, so
  it is restated here as "as long as the appointment stands" (FR-011).
- Q: What can the patient ask for beyond that default? → A: Both filters lift independently, on
  request. Cancelled appointments come back past and future alike; past appointments that went ahead
  as booked come back too, as their own request. So the listing has two axes — time and status — and
  every combination is answerable, with the default being the narrowest corner of the grid
  (FR-013 to FR-015). Any listing reaching into the past is capped at the 20 most recent (FR-016),
  since past appointments accumulate without limit while future ones are already bounded by the
  90-day booking horizon.
- Q: How should a retried reschedule or cancellation be recognised as the same change rather than a
  second one? → A: By the shape of the operation, not by a key. A change names the state the
  appointment is to end in, so replaying it finds the appointment already there (FR-019). Neither
  operation carries an idempotency key, because neither can produce a duplicate: a reschedule writes
  to a row that already exists and a cancellation sets a status that is already set. Booking keeps
  its key because booking creates. A key **derived** from a change's target state was rejected
  outright: states recur, so an appointment moved 09:00 → 10:00 → 09:00 → 10:00 would derive the
  first move's key on the third and be answered by replaying it, leaving the appointment at 09:00
  while reporting success (FR-020).
- Q: What if the appointment changes between the assistant reading it back and the patient
  confirming? → A: The change is refused, with a reason of its own. A change request carries the
  start date-time and practitioner the appointment had when it was described, and the server checks
  them before acting; the assistant then says the appointment has changed since it read it out,
  describes it as it now stands, and asks again. The guard is on the facts the patient was actually
  shown rather than on a revision counter, so the refusal can be explained in the patient's own
  terms instead of as "something changed" (FR-021, FR-022).
- Q: Does a change call get the one retry that every other call to the scheduling capability gets,
  given that a retry re-sends the state the appointment was in when it was described? → A: **Yes —
  the call policy is uniform, and the staleness guard is widened to make the retry safe.** A change
  is attempted at most twice like every other call (005 FR-047), and the guard of FR-021 accepts the
  appointment in *either* of two states: the one described to the patient, or the target state the
  request itself asks for. Without that second state a retry of a move that had already landed would
  arrive quoting the old start time, be refused as stale, and report a conflict for a change that
  succeeded — the false conflict SC-008 forbids. An appointment already in the state the caller
  asked for is not a surprise to that caller, so the replay returns the original outcome instead.
  FR-023's prohibition on retrying is therefore about the assistant, after both attempts of the one
  call have gone unanswered, not about the call itself.
- Q: Does a change that finds the appointment already in the state it asked for write a second
  change record? → A: **It is recorded, but as its own kind.** A record of a change that altered the
  appointment and a record of a request answered without altering anything are different entries, so
  one change record still means one move. Recording nothing would leave an answered request with no
  trace of its own, which is out of step with FR-040, where every non-completing outcome is recorded
  as the thing it is; recording it as an ordinary change would make the log over-count, showing two
  moves where one happened. The server cannot tell a replay from a patient asking for the state they
  already hold, and does not need to — both transitioned nothing (FR-036, FR-040).
- Q: When a listing spans both past and future, does the cap of 20 apply to the whole list? → A:
  **No — the past and the future are separate legs, bounded and ordered separately.** The future leg
  is returned in full, soonest first, since the 90-day horizon already bounds it; the past leg is
  capped at its 20 most recent. Applying one cap across both would let a patient with a full
  calendar ask for their past appointments and be shown none of them, crowded out by future ones,
  and would order the combined list furthest-future first. The cap exists because past appointments
  accumulate without limit, which is a fact about one leg only (FR-016).
- Q: A booking key is released by cancellation — what then answers a late duplicate of the
  *original* booking request bearing that key? → A: **A new appointment, the same as any other
  booking.** 005 FR-062 derives the key from the patient, practitioner, and start time alone, so
  once the key is released a stray retry and a deliberate rebooking of the freed slot are the same
  request; there is nothing in either to tell them apart by, and the system must not pretend
  otherwise. The race is also unreachable in practice: a booking call's two attempts close within
  about four seconds (005 FR-047), long before the patient can be told they have an appointment and
  ask to cancel it (FR-011).
- Q: FR-006 claimed booking's closed set of eight was extended by exactly one reason, but FR-005,
  FR-017 and FR-018 each require an outcome none of the eight can express. How many reasons does a
  change actually add? → A: **Four, decided ahead of every placement reason**: appointment not
  found, already cancelled, already started, and stale confirmation, in that order. Each is a
  genuinely different situation, and FR-017 already forbids one value standing for two of them.
  "Already started" in particular is not booking's "in the past" — that reason is about the new
  start time asked for, whereas an appointment that has already begun must be refused even when the
  time it is asked to move to is perfectly valid. Staleness comes last of the four because
  re-describing and asking again (FR-022) only makes sense for an appointment that could still be
  changed. A cancellation places nothing, so only these four are available to refuse it — and only
  three can actually be reached, since "already cancelled" is a cancellation's target state rather
  than a refusal of one; a reschedule can be refused by any of the twelve (FR-006).
- Q: Is the staleness guard checked before the write, or as part of it? → A: **As part of it — a
  predicate on the write.** The change alters the appointment only where its stored start and
  practitioner still match one of the two states FR-021 accepts, so a concurrent change that lands
  first leaves the second matching nothing, and it is refused as stale. Checking first and writing
  after leaves a window in which two changes both pass and the second overwrites the first, after
  its patient was already told it succeeded — and the datastore's overlap constraint does not catch
  that, since a cancellation racing a move collides with nothing. This mirrors how 005 has the
  datastore decide practitioner-busy and patient-busy at the moment of insert (FR-021).
- Q: How long does a pending confirmation stay valid within a conversation? → A: **The offer
  survives an intervening turn; the confirmation does not.** FR-029 had conflated the two by
  treating any reply that was not a yes as a decline, which would make a single clarifying question
  destroy the flow and force the patient to restate what FR-034 says they must never restate. So a
  turn that neither confirms nor declines is answered on its own terms, changes nothing, and the
  assistant re-states the confirmation in full; a bare "yes" arriving after any intervening turn
  binds only against that restatement. This is the same reasoning FR-021 applies to the appointment,
  moved to the conversation: a yes is binding only for a description the patient was just given
  (FR-029, SC-002).
- Q: Must the confirmation tell the patient that the change alters how long the appointment will
  be? → A: **Yes, whenever the length differs from the one it has now.** The Edge Cases had settled
  this in passing — "the length was never something they chose" — which holds for a move that keeps
  the practitioner, since the length is then whatever it always was. It does not hold for a
  practitioner swap, where a 15-minute appointment can become an hour: the patient would be agreeing
  to a change in how long they must be at the clinic without being told it changed. A change that
  leaves the length alone still says nothing about it, so the common move gains no extra clause
  (FR-025, SC-003).
- Q: What happens when a change lands but its record fails to be written? → A: **Nothing —
  recording is best-effort and never gates a change.** SC-009's "100% recoverable from the logs"
  could otherwise be read as requiring a record row in the change's own transaction, or an outbox,
  which would add a durable audit store this phase has no other use for and a new half-done state
  (change committed, record pending) that nothing would consume. The Assumptions already say change
  records are logs rather than a stored history surface; this makes the consequence explicit and
  scopes SC-009's measurement to runs where the logging path is working (FR-041).
- Q: Which of FR-006's twelve refusal reasons "admit an alternative", as FR-032 requires? → A:
  **Pinned by group, so the requirement is testable per reason rather than per judgment call.** The
  six placement reasons are answered with other times to choose from; stale confirmation is answered
  by re-describing the appointment and asking again, which is the alternative that reason admits;
  and already started, already cancelled, and appointment not found admit none — the assistant says
  plainly what is so and invents nothing. This only writes down offers the spec already promised
  elsewhere, and it removes the last unquantified adjective standing between a requirement and a
  test (FR-032, SC-018).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cancel an appointment I no longer need (Priority: P1)

A patient who has an upcoming appointment tells the assistant they cannot make it. The assistant
finds the appointment, reads it back to them — the date and time it starts, the practitioner's full
name, and that practitioner's specialty — and asks them to confirm they want it cancelled. Once they
say yes, the appointment is cancelled and the assistant says so. The slot it was holding becomes
bookable again, and the appointment stops appearing when the patient asks what they have booked —
it is kept, marked cancelled, and the assistant mentions it only if they ask for cancelled ones.

**Why this priority**: Today the only way to undo a mis-booked appointment is to delete the whole
chat, losing the conversation with it. Cancellation is the smallest change that removes that dead
end, and it establishes the "find it, read it back, get a yes" conversation that every other story
here reuses. It stands alone — a clinic where patients can book and cancel is already usable.

**Independent Test**: Book an appointment, then in the same chat ask to cancel it. Verify the
assistant states the start date-time, practitioner name, and specialty before acting; that nothing
changes until the patient confirms; that after confirming, the appointment is gone from the
patient's list of upcoming appointments and its slot is offered again by the availability check,
but still comes back — marked cancelled — when the patient asks specifically for cancelled ones.

**Acceptance Scenarios**:

1. **Given** a patient holding exactly one upcoming appointment, **When** they say "cancel my
   appointment", **Then** the assistant states that appointment's start date-time, the
   practitioner's full name, and that practitioner's specialty, and asks for confirmation — and
   nothing is cancelled yet.
2. **Given** the assistant has asked for that confirmation, **When** the patient confirms, **Then**
   the appointment is cancelled and the assistant says so in plain local time without naming any
   internal identifier.
3. **Given** the assistant has asked for that confirmation, **When** the patient declines or changes
   the subject, **Then** nothing is cancelled and the assistant says the appointment still stands.
4. **Given** a patient holding three upcoming appointments, **When** they say "cancel my
   appointment", **Then** the assistant lists all three by start date-time, practitioner name, and
   specialty, and asks which one — it never picks one for them.
5. **Given** a patient with no upcoming appointments, **When** they ask to cancel, **Then** the
   assistant says they have nothing booked and does not offer a list to choose from.
6. **Given** an appointment that was just cancelled, **When** anyone asks for that practitioner's
   available times covering that slot, **Then** the slot is offered again.
7. **Given** a patient with one cancelled and one standing appointment, **When** they ask what they
   have booked, **Then** only the standing one is listed — the cancelled one is not mentioned.
8. **Given** that same patient, **When** they ask specifically about their cancelled appointments,
   **Then** the cancelled one is described and identified as cancelled.
9. **Given** a patient whose appointment last Tuesday went ahead as booked, **When** they ask what
   they had in the past, **Then** it is listed — and nothing they cancelled is listed alongside it
   unless they asked for cancelled ones too.

---

### User Story 2 - Move an appointment to a different time (Priority: P2)

A patient wants to keep seeing the same practitioner but at a different time. They say so, the
assistant offers times that practitioner actually has free, and the patient picks one. Before
anything changes, the assistant reads back both ends of the move — the time the appointment starts
now, the time it would start instead, and the practitioner's name and specialty — and asks them to
confirm. On confirmation the appointment moves: it is the same appointment, with a new start and a
new end, not a new one.

**Why this priority**: This is the operation the feature is named for, and it depends on Story 1's
"which appointment do you mean, and are you sure" conversation being in place. It is a strictly
better outcome for the patient than cancelling and rebooking, because the two halves cannot come
apart — they never end up having given up their slot without getting another.

**Independent Test**: Book an appointment, ask to move it to another time with the same
practitioner, confirm, and verify the patient still has exactly one appointment; that it starts at
the new time and ends one appointment-length later; that its identifier is unchanged; and that the
old slot is offered again while the new one is not.

**Acceptance Scenarios**:

1. **Given** a patient with an upcoming appointment, **When** they ask to move it to a different
   day, **Then** the assistant offers only start times that practitioner genuinely has free, and
   never invents or rounds one.
2. **Given** the patient has picked a new time, **When** the assistant asks for confirmation,
   **Then** it states the current start date-time, the proposed start date-time, the practitioner's
   full name, and that practitioner's specialty — and nothing has changed yet.
3. **Given** the patient confirms, **When** the move succeeds, **Then** the patient holds exactly
   one appointment, with the identifier it already had, at the new time, ending one of that
   practitioner's current appointment lengths later.
4. **Given** the patient confirms, **When** the new time was taken by someone else in the meantime,
   **Then** the move is refused, the appointment is still there unchanged at its original time, and
   the assistant explains why and offers other times.
5. **Given** a patient whose current appointment is at 09:00, **When** they ask to move it and the
   assistant checks what is free, **Then** 09:00 with that same practitioner is itself offered —
   the appointment being moved does not count as blocking its own slot.
6. **Given** a patient holding a second appointment with a different practitioner at 14:00, **When**
   they ask to move an appointment to 14:00, **Then** that time is neither offered nor accepted,
   because they cannot be in two places at once.

**Acceptance Scenarios — a move repeated over the same two times (09:00 → 10:00 → 09:00 → 10:00)**:

These run as one sequence against a single appointment, in order. They exist because a repeated move
is where a change could most plausibly be mistaken for a replay of an earlier one — the mistake
FR-019 and FR-020 rule out, whose symptom is an appointment reported as moved while it sits where it
was.

7. **Given** an appointment at 09:00 that the patient has already confirmed moving to 10:00,
   **When** they ask to move it back to 09:00, **Then** 09:00 is offered as free — the slot the
   appointment vacated is not held for it — and the assistant reads back 10:00 as the current start
   and 09:00 as the proposed one.
8. **Given** the patient confirms that second move, **When** it completes, **Then** they hold one
   appointment, with the identifier it has had throughout, starting at 09:00 with the same
   practitioner, and 10:00 is offered as free again.
9. **Given** the appointment is back at 09:00 having been to 10:00 and returned, **When** the
   patient asks to move it to 10:00 for the second time and confirms, **Then** the move is evaluated
   on its own and takes effect: the appointment starts at 10:00. It is not recognised as a repeat
   of the first move, and is not answered by replaying it — the failure that would leave the
   appointment at 09:00 while success was reported (FR-019, FR-020).
10. **Given** each of the three moves in the sequence, **When** the assistant asks for confirmation,
    **Then** it states the start the appointment has *at that moment* rather than the one it started
    the sequence with — 09:00 for the first move, 10:00 for the second, 09:00 for the third — and
    each move takes a confirmation of its own (FR-024 to FR-026).
11. **Given** the first move has already completed, **When** that same change is sent again —
    quoting 09:00 as the state it was described in and 10:00 as its target — **Then** it is
    accepted rather than refused as stale, the appointment stays at 10:00, and the patient is not
    told of a second move (FR-021, FR-023).

---

### User Story 3 - See a different practitioner instead (Priority: P3)

A patient decides they want a different practitioner — a dentist rather than the GP they booked, or
simply someone else. This is the same appointment changing hands, not a cancellation and a rebooking:
the assistant offers the new practitioner's free times, reads back what the appointment is now and
what it would become — both practitioners, both specialties, and the start time on each side — and
asks for one confirmation. On confirmation the appointment's practitioner, start, and end all change
together, and the patient still holds the one appointment they started with.

**Why this priority**: It is the least common of the three requests and touches two practitioners'
calendars. It adds no new mechanism beyond Story 2 — one more field changes in the same write — and
no new conversational shape beyond a confirmation that names two practitioners instead of one.

**Independent Test**: Book with practitioner A, ask to see practitioner B instead, confirm, and
verify the patient holds exactly one standing appointment, with the same identifier as before, now
with B;
that A's slot is free again; and that a forced refusal of the change leaves the appointment with A
entirely untouched.

**Acceptance Scenarios**:

1. **Given** a patient booked with a General Practice practitioner, **When** they say they would
   rather see a dentist, **Then** the assistant offers the session's dentists and their free times,
   and asks which they want.
2. **Given** the patient has chosen a new practitioner and time, **When** the assistant asks for
   confirmation, **Then** it names the practitioner and start date-time the appointment has now and
   the practitioner and start date-time it would have instead, with both specialties — and nothing
   has changed yet.
3. **Given** the patient confirms, **When** the change succeeds, **Then** they hold exactly one
   appointment, with the identifier it already had, now with the new practitioner, and the old
   practitioner's slot is offered again.
4. **Given** the patient confirms, **When** the change is refused for any reason, **Then** the
   appointment still stands entirely unchanged — the old practitioner, the old start, the old end —
   and the assistant says so rather than reporting a partial outcome.
5. **Given** the patient asks for a practitioner nobody in their session matches, **When** the
   assistant answers, **Then** it says so, names the specialties that do exist, and leaves the
   appointment alone.
6. **Given** a patient who wants the same time but a different practitioner, **When** the new
   practitioner is free then, **Then** the change is accepted — the appointment does not block its
   own time, and the patient is not overlapping themselves.

---

### User Story 4 - Trace every change that was made (Priority: P4)

Whoever is running the system can read, from the logs alone, every appointment that was rescheduled
or cancelled: which appointment it was, when it used to start, and when it starts now. A
cancellation is distinguishable from a move at a glance, because a cancelled appointment has no new
start time rather than a blank one. A change that was proposed but refused, or that the patient
declined, does not appear as a change.

**Why this priority**: It is an operator-facing outcome rather than a patient-facing one, and it is
independently verifiable once any one of the three preceding stories works. It matters because a
scheduling change is destructive from the patient's point of view — "my appointment moved and I do
not know when" is only answerable from a record of what happened.

**Independent Test**: Perform one cancellation, one move, and one practitioner change; read the
logs and confirm each produced a record carrying the appointment identifier and the old start
date-time, with the new start present for the two changes and absent for the cancellation, all tied
to the turn that caused them. Then decline one confirmation and force one refusal, and confirm
neither produced a change record.

**Acceptance Scenarios**:

1. **Given** an appointment is rescheduled, **When** the change completes, **Then** a record is
   written carrying that appointment's identifier, its old start date-time, and its new start
   date-time.
2. **Given** an appointment is cancelled, **When** the change completes, **Then** a record is
   written carrying that appointment's identifier and its old start date-time, marked as a
   cancellation with no new start time.
3. **Given** a change that swapped the practitioner but kept the time, **When** the logs are read,
   **Then** the record shows the practitioner before and after — so it cannot be mistaken for a
   change that did nothing.
4. **Given** a patient declines a confirmation, or a change is refused, **When** the logs are read,
   **Then** no change record exists for that appointment; the refusal is recorded as a refusal with
   its reason.
5. **Given** any change record, **When** it is read, **Then** it carries the same turn identifier as
   every other log entry produced by that turn.
6. **Given** the sequence 09:00 → 10:00 → 09:00 → 10:00 has completed as three confirmed moves,
   **When** the logs are read, **Then** there are exactly three change records — 09:00 to 10:00,
   10:00 to 09:00, and 09:00 to 10:00 — each carrying the same appointment identifier, each tied to
   the turn that caused it, and none of them a cancellation.
7. **Given** a move from that sequence is sent again after it had already completed, **When** the
   logs are read, **Then** it appears as a request that transitioned nothing, and the number of
   change records is still three — the log shows three moves because three happened (FR-036,
   FR-040).

---

### Edge Cases

- **"Cancel my appointment" with several upcoming**: the assistant lists them and asks which. It
  never resolves the ambiguity by picking the soonest, and it never acts on more than one
  appointment from a single confirmation.
- **An appointment that has already started**: it is not eligible to be moved or cancelled and does
  not appear among the options, exactly as it already drops out of the patient's upcoming list. A
  patient asking to cancel it is told it has already passed.
- **Moving an appointment to the time it already has**: the confirmation reads back the same time on
  both sides, and confirming it succeeds and changes nothing observable. It is recorded as a request
  that transitioned nothing (FR-040) — not as a move, and not as a cancellation.
- **Swapping practitioner while keeping the time**: allowed when the new practitioner is free then.
  This is a real change — the appointment changed hands — so it writes an ordinary change record,
  one showing equal start times and two different practitioners. That is why the practitioner
  belongs in the record and not only in the appointment.
- **The appointment being changed must not block itself**: the times offered for a change, and the
  overlap rules applied when it is committed, both ignore the appointment being changed. Otherwise a
  patient could never move an appointment to a time overlapping the one it currently occupies,
  including its own slot.
- **A grandfathered appointment** — one left outside its practitioner's current schedule by a later
  edit — can still be cancelled, and can still be moved, but only onto a time the practitioner's
  *current* schedule and grid actually allow. Being grandfathered exempts it from being disturbed,
  not from the rules that govern where it may go next.
- **The appointment length differs after the change**: whether because the practitioner's length was
  edited or because a different practitioner now holds it, the end time is recomputed from the
  practitioner who will hold the appointment, at the moment of the change. An appointment can
  therefore come out of a change longer or shorter than it went in. The patient is told the new
  start time and, when the length changes, how long the appointment will now be (FR-025) — a
  practitioner swap can turn a 15-minute appointment into an hour, which is not something to
  discover on arrival. The length remains something they are told rather than something they
  choose.
- **A new time in the past, beyond the booking horizon, off the slot grid, or outside the
  practitioner's hours**: refused for exactly the reason booking would refuse it, and the
  appointment is untouched.
- **The slot is taken between the offer and the confirmation**: the change is refused, the patient
  still holds their appointment as it was, and the assistant offers alternatives rather than leaving
  them to work out what they now have.
- **Two attempts to cancel the same appointment**: at most one of them is a cancellation. The second
  is told the appointment is already cancelled, which is not the same answer as "no such
  appointment".
- **A patient who asks to un-cancel**: cancellation is final. The assistant says the appointment
  cannot be restored and offers to book the time again, which produces a new appointment rather than
  reviving the old one — and only if nobody else took the slot in the meantime.
- **A cancelled appointment's slot is taken by someone else**: expected and allowed. The slot was
  released the moment it was cancelled, and the retained record reserves nothing.
- **The patient answers the confirmation with a question**: they get an answer to their question and
  the confirmation again, not a decline and not a change. The yes they give afterwards is given
  against that restatement rather than against a description they were shown two turns earlier
  (FR-029).
- **The appointment changes between the read-back and the yes**: the change is refused as stale, the
  patient is shown the appointment as it now stands, and asked again. Their earlier yes does not
  carry over — it was given for a description that has stopped being true.
- **Two changes to the same appointment race each other**: at most one takes effect. The loser
  matches neither the state it was described in nor the state it asked for, so the write alters
  nothing and it is refused as stale (FR-021). This holds for the pairing the datastore cannot
  catch — a cancellation racing a move collides with no other appointment — as much as for two moves
  onto the same slot.
- **A stale confirmation that would have been harmless**: refused anyway. The guard does not try to
  work out whether the drift mattered; a patient confirming "cancel my 9am" when the appointment is
  no longer at 9am is told so, even though cancelling it may well still be what they want.
- **Moving an appointment away and back again**: 09:00 → 10:00 → 09:00 is two ordinary changes, each
  evaluated on its own, and a third move back to 10:00 is a third. Nothing recognises the repeat as
  a replay, because a change asserts where the appointment should be rather than replaying an
  attempt to move it (FR-019, FR-020). The sequence is covered end to end by User Story 2 scenarios
  7 to 11, User Story 4 scenarios 6 and 7, and SC-017.
- **Booking the exact slot you just cancelled**: succeeds, and produces a new appointment with a new
  identifier rather than reviving or replaying the cancelled one — the cancelled appointment's
  booking key was released with it (FR-011). A stray retry of the original booking arriving after
  the cancellation does the same, being indistinguishable from that rebooking. It is a stated
  consequence rather than a reachable race: the booking call's two attempts close within about four
  seconds (005 FR-047), long before a patient could be told about the appointment and ask to cancel
  it.
- **Asking "what do I have booked?" with everything cancelled**: the assistant says they have
  nothing booked, not that they have cancelled appointments — those surface only when asked for.
- **A cancelled appointment whose start time has passed**: it stays in the record and is listed when
  the patient asks about cancelled appointments. It is not eligible to be changed, for both reasons
  at once.
- **More than 20 appointments in a backward-looking list**: the 20 most recent are listed and the
  assistant says that part of the list is not complete, rather than presenting them as everything
  there ever was. If the same request also reached forward, the future leg is unaffected by that
  cap and is still returned in full.
- **A patient asking about their past appointments**: answerable, and it returns the ones that went
  ahead as booked — not the cancelled ones. Those are a separate request, because "what did I
  attend?" and "what did I call off?" are different questions (FR-015).
- **A patient asking for everything**: both axes widen at once, past and future, standing and
  cancelled. The future leg comes first and in full, the past leg follows under its own cap of 20
  (FR-016), and every cancelled one is labelled as such.
- **Deleting the chat**: removes the patient's appointments outright, cancelled ones included. A
  cancelled appointment is retained against a cancellation, not against the deletion of the patient
  who held it.
- **Deleting a practitioner**: the same, from the other side. 005 FR-049 already deletes that
  practitioner's appointments, and a cancelled one is still that practitioner's appointment — it
  keeps the practitioner it held (FR-009) — so it goes with them. Retention protects a cancelled
  appointment from its own cancellation, not from the removal of either party to it.
- **The scheduling capability stops responding after a change was sent**: the one retry the call
  policy allows is made and goes unanswered too, after which the assistant says the outcome is not
  known, does not claim the appointment was moved or cancelled, and attempts it no further — the
  patient is told to check with the clinic. Neither that retry nor any later attempt at the *same*
  change produces a second change (FR-019, FR-021, FR-023).
- **The scheduling capability is unreachable before a change was sent**: the assistant says changes
  are temporarily unavailable and that nothing was changed, and continues to answer FAQ questions.
- **An appointment identifier from another session**: resolves as not found, identically to one that
  never existed, so nothing in the reply distinguishes the two.
- **The practitioner is deleted between the offer and the confirmation**: the change is refused for
  the same reason a booking naming an unknown practitioner is refused, and the appointment is
  untouched.
- **The chat is deleted mid-turn**: the deletion goes through, the turn is abandoned with nothing
  recorded, and no appointment is left half-changed on either side of the deletion.
- **A patient asks to move an appointment and to book another in one message**: the two are handled
  as separate decisions, each with its own confirmation. One confirmation never covers two
  appointments.

## Requirements *(mandatory)*

### Functional Requirements

#### What a change is

- **FR-001**: Rescheduling an appointment MUST mean changing the appointment that already exists —
  its start and end date-time, and where the patient asked for it, its practitioner — while it
  remains the same appointment, with the same identifier and the same patient.
- **FR-002**: A patient's request to see a *different* practitioner MUST be carried out as a change
  to the existing appointment, not as a cancellation followed by a new booking. The appointment's
  practitioner, start, and end change together and its identifier is preserved.
- **FR-003**: A change MUST be all-or-nothing: every field it touches changes, or none does. There
  is no state in which an appointment has taken its new time but kept its old practitioner, or the
  reverse.
- **FR-004**: A changed appointment's end date-time MUST be derived from the appointment length of
  the practitioner who will hold it, read at the moment of the change — never carried over from the
  appointment's previous end date-time.
- **FR-005**: Only an appointment that is **not already cancelled** and whose start is **strictly
  after** the patient's current local date-time MUST be eligible to be rescheduled or cancelled. An
  appointment that has already started is not eligible, and neither is one already cancelled — a
  cancelled appointment cannot be reinstated by rescheduling it (see Assumptions). The two
  ineligibilities are separate refusal reasons, **already started** and **already cancelled**
  (FR-006).
- **FR-006**: Every rule that governs where a booking may be placed MUST govern a **rescheduled**
  appointment, evaluated at the moment of the change: the practitioner must be free, the patient
  must be free, the time must lie inside one working range, on that range's grid, strictly after the
  patient's current local date-time, and within the booking horizon. A refusal MUST name exactly one
  reason, resolved by a fixed precedence, drawn from booking's closed set of eight (005 FR-065)
  extended by **four** reasons of this feature's own. The four are decided first, in this order,
  because each settles whether the appointment can be changed at all before any question of where it
  may go is worth asking:
  1. **appointment not found** — no such appointment in this session, whether it never existed or
     belongs to another (FR-018);
  2. **already cancelled** — the appointment exists but stands cancelled (FR-005, FR-017), which is
     a different answer from not found;
  3. **already started** — the appointment's *current* start is not strictly after the patient's
     current local date-time (FR-005). This is **not** booking's "in the past", which is about the
     new start time being asked for: an appointment that has already begun must be refused even when
     the time it is asked to move to is perfectly valid;
  4. **stale confirmation** — the appointment no longer matches the start date-time and practitioner
     it was described with (FR-021). It sits last of the four because re-describing it and asking
     again (FR-022) only makes sense for an appointment that could still be changed.

  Booking's eight then follow, unchanged in order and meaning, for a reschedule. A **cancellation**
  places nothing, so it can be refused only from among the four above — and only by **three** of
  them. `already_cancelled` is unreachable as a refusal of a cancellation, because for a
  cancellation that state is the state being asked for: it is answered as a request that
  transitioned nothing (FR-017, FR-019), not as a failure. A **reschedule** can be refused by any of
  the twelve.
- **FR-007**: When evaluating whether a changed appointment's time is free, and when computing the
  times to offer for a change, the system MUST exclude the appointment being changed from the
  patient's and the practitioner's existing commitments — so an appointment never blocks its own
  change, including a change to the time it already holds.
- **FR-008**: When a reschedule or a cancellation is refused, the appointment MUST be left exactly
  as it was, in its entirety — start, end, practitioner, and identifier.
- **FR-009**: An appointment MUST carry a status distinguishing one that stands from one that has
  been cancelled. Cancelling an appointment MUST set that status rather than remove the record: the
  appointment keeps its identifier, its practitioner, and the start and end date-time it held.
- **FR-010**: A cancelled appointment MUST stop occupying its slot, so the time it held becomes
  bookable by any patient, and MUST NOT be counted as a commitment of either its patient or its
  practitioner by any overlap rule or availability calculation.
- **FR-011**: Cancelling an appointment MUST release the idempotency key that created it, so the
  freed slot can be booked again — by the same patient with the same practitioner at the same time,
  or by anyone else — as an ordinary new booking producing a new appointment. A booking key lives as
  long as the appointment it created **stands**, not as long as its record exists. This amends the
  key lifetime Phase 1c fixed (005 FR-064), which was written when cancelling removed the record.
  A released key carries no memory of the appointment it created: because 005 FR-062 derives the key
  from the patient, practitioner, and start time alone, a late duplicate of the original booking
  request and a deliberate rebooking of the freed slot are the *same* request. Both MUST produce a
  new appointment, and the system MUST NOT try to tell them apart — there is nothing in the request
  to tell apart by.
- **FR-012**: Every read that asks what an appointment blocks, what times are free, or what a
  patient has booked MUST state which statuses it means as part of the query, rather than filtering
  the results afterwards. There is no read in this system for which "every appointment regardless of
  status" is the right default. A cascading delete is not such a read: when a chat, patient, or
  practitioner is removed, every one of their appointments goes with it regardless of status, so
  scoping that cascade to standing appointments would strand cancelled ones behind a party that no
  longer exists.
- **FR-013**: A patient's appointments MUST be selectable along two independent axes — **time**
  (still to come, already started, or both) and **status** (standing, cancelled, or both) — and
  every combination of the two MUST be answerable. The axes are independent because the questions
  are: "what did I have last month?" and "what did I call off?" are different requests, and one
  answer must not stand for both.
- **FR-014**: When the patient asks what they have booked, without qualifying it, the assistant MUST
  list only the appointments that are **still to come and standing**. Both filters apply at once,
  and neither is widened unless the patient asks for it: an appointment that has already started
  does not appear, and neither does a cancelled one.
- **FR-015**: Widening one axis MUST NOT silently widen the other. A request for past appointments
  returns past *standing* ones, not cancelled ones as well; a request for cancelled appointments
  returns cancelled ones from either side of now, since a cancellation is not something the patient
  is still waiting for. Wherever a cancelled appointment appears in any listing, it MUST be
  identified as cancelled.
- **FR-016**: The past and the future are separate legs of a listing and MUST be bounded and ordered
  separately. The past leg MUST be ordered by start date-time with the most recent first and bounded
  to at most 20 appointments; the future leg MUST be ordered by start date-time with the soonest
  first and needs no bound, because the booking horizon already limits how many there can be. A
  listing spanning both MUST return the future leg followed by the past leg, so that neither can
  crowd the other out of a list the patient explicitly asked for. When the past leg's bound elides
  some, the assistant MUST say that part of the list is not complete rather than implying those are
  all of them.
- **FR-017**: A repeated attempt to cancel an appointment that is already cancelled MUST be answered
  distinguishably from an attempt to cancel an appointment that never existed or belongs to another
  session. One value must not stand for both: they are the **already cancelled** and **appointment
  not found** reasons of FR-006. "Already cancelled" reports the appointment as being in the state
  that was asked for and MUST NOT be reported to the patient as a failed cancellation (FR-019).
- **FR-018**: An appointment identifier belonging to a *different* session MUST be treated as not
  found, identically to one that never existed, and MUST NOT be rescheduled or cancelled — both are
  the **appointment not found** reason of FR-006. Every lookup MUST carry the session as part of the
  query rather than checking it after the fact.
- **FR-019**: Every reschedule and every cancellation MUST be expressed as the state the appointment
  is to end in — *this appointment, with this practitioner, starting at this time*, or *this
  appointment, cancelled* — never as a change relative to the state it is in now. A repeated attempt
  therefore finds the appointment already in that state, reports the same outcome as the first, and
  produces no second change — though it is recorded as a request that transitioned nothing rather
  than as a second move (FR-040).
- **FR-020**: Neither a reschedule nor a cancellation MUST carry an idempotency key. The duplicate a
  key exists to prevent is unreachable for both: a reschedule writes to an appointment that already
  exists and cannot bring a second into being, and a cancellation sets a status that is already set.
  Booking keeps its key because booking creates (005 FR-062). A key MUST NOT be derived from a
  change's target state, because a state can recur — an appointment moved from 09:00 to 10:00, back
  to 09:00, and to 10:00 again would derive the key of the first move on the third, and be answered
  by replaying it while the appointment sat at 09:00.
- **FR-021**: A reschedule or cancellation request MUST carry the start date-time and practitioner
  the appointment had at the moment the assistant described it to the patient, and MUST be refused,
  with a reason of its own, when the appointment no longer matches them. The match MUST be a
  predicate on the write itself — the appointment is altered only where its stored start and
  practitioner still match — rather than a check performed before it. A check that has already
  returned leaves a window in which a second change passes it too and then overwrites the first.
  A confirmation is only binding for the appointment that was actually confirmed: without this guard
  a patient who says yes to "your 9am with Dr Hardy" would have an appointment changed that had
  since become something else, on the strength of an agreement to a description that was already
  false. The guard applies
  to cancellation as well as to rescheduling. It MUST accept the appointment in **either** of two
  states: the one described to the patient, or the target state the request itself asks for. The
  second is what makes a change safe to re-send (FR-023) — an appointment already in the state the
  caller asked for is not a surprise to that caller, so a replay reports the original outcome rather
  than a stale refusal.
- **FR-022**: When a change is refused as stale (FR-021), the assistant MUST tell the patient the
  appointment has changed since it was read out, describe it as it now stands per FR-025, and ask
  again — it MUST NOT re-issue the change, and MUST NOT treat the earlier yes as covering the new
  state.
- **FR-023**: A call carrying a reschedule or a cancellation MUST follow the same call policy as
  every other call to the scheduling capability — a 2-second timeout, attempted at most twice, one
  retry (005 FR-047). That retry is safe for the same reason any replay is: both operations assert a
  target state (FR-019) and the staleness guard accepts that state (FR-021), so re-sending a change
  that already landed returns its outcome rather than producing a second change or a false conflict.
  When both attempts fail to answer, the system MUST report the outcome as unknown: it MUST NOT
  report that nothing was changed, and MUST NOT make any further attempt on the patient's behalf.

#### Confirming a change with the patient

- **FR-024**: The assistant MUST obtain the patient's explicit confirmation before any reschedule or
  cancellation takes effect. A request to change or cancel is not itself a confirmation.
- **FR-025**: The confirmation request MUST state, for the appointment it affects: the start
  date-time, the practitioner's full name, and that practitioner's specialty. When the change would
  give the appointment a **different length** from the one it has now — because a different
  practitioner will hold it, or because that practitioner's length was edited — the confirmation
  MUST also state how long it will now be, or the time it will now end. A change that leaves the
  length as it is says nothing about it.
- **FR-026**: For a reschedule, the confirmation request MUST state both the current start date-time
  and the proposed start date-time, so the patient can see what is moving and where.
- **FR-027**: When the change also swaps the practitioner, the confirmation request MUST name both
  the practitioner the appointment has now and the one it would have, each with their specialty, and
  MUST make clear that this is the same appointment changing rather than a second one being booked.
- **FR-028**: The assistant MUST NOT state or imply that an appointment was rescheduled or cancelled
  unless the change actually completed in that turn.
- **FR-029**: When the patient declines the confirmation, the assistant MUST leave every appointment
  unchanged and MUST say that the appointment still stands. A reply that neither confirms nor
  declines — a question, a change of subject, anything else — MUST likewise change nothing, but it
  is not a decline: the assistant MUST answer what the patient actually said and then re-state the
  confirmation in full (FR-025 to FR-027), so that they never have to restate the appointment,
  practitioner, or time they already gave (FR-034). A confirmation binds only for the turn it was
  asked in. A bare "yes" arriving after any intervening turn MUST NOT be acted on until the
  confirmation has been re-stated and confirmed against that restatement.
- **FR-030**: When the patient's request could refer to more than one of their upcoming
  appointments, the assistant MUST list the candidates — each per FR-025 — and ask which one. It
  MUST NOT choose for them, and MUST NOT act on more than one appointment per confirmation.
- **FR-031**: When the patient has no upcoming appointments, the assistant MUST say so rather than
  offering an empty list to choose from.
- **FR-032**: When a change is refused, the assistant MUST explain its single reason in plain
  language, without restating that reason as a fact about the clinic that it was not given, and MUST
  offer what the reason admits:
  1. the six **placement** reasons — practitioner busy, patient busy, outside the schedule, off
     grid, in the past, beyond the horizon — MUST be accompanied by other times to choose from,
     drawn from the availability capability per FR-033;
  2. **stale confirmation** MUST be answered by re-describing the appointment as it now stands and
     asking again (FR-022), which is the alternative that reason admits;
  3. **already started**, **already cancelled**, and **appointment not found** admit no alternative
     to the change itself. The assistant MUST say plainly what is so and MUST NOT invent an offer —
     booking afresh is proposed only where this spec already provides for it, as when a patient asks
     to undo a cancellation (see Edge Cases).
- **FR-033**: Every new start time the assistant offers MUST be one the availability capability
  returned for the practitioner who would hold the appointment, computed per FR-007. The assistant
  MUST NOT invent, round, or infer one.
- **FR-034**: The assistant MUST be able to complete a reschedule or a cancellation through
  conversation alone, without the patient seeing or supplying any internal identifier, and without
  them having to restate the appointment, practitioner, or time they already gave.
- **FR-035**: Every date-time the assistant states MUST be plain local time, with no timezone
  mentioned and none stored, resolved against the patient's current local date-time supplied by the
  client rather than against any server clock.

#### Recording what happened

- **FR-036**: Every reschedule that actually altered the appointment MUST be logged with the
  appointment's identifier, its start date-time before the change, and its start date-time after the
  change. A request that altered nothing is recorded under FR-040 instead, so that one change record
  means one move.
- **FR-037**: Every completed cancellation MUST be logged with the appointment's identifier and its
  start date-time before the change, and MUST be distinguishable from a reschedule by carrying no
  new start date-time at all — not an empty or placeholder one.
- **FR-038**: When a reschedule also changed the practitioner, its record MUST name the practitioner
  before and the practitioner after. Without this, a swap that kept the same start time would be
  recorded as a change from a time to the identical time, which reads as a change that did nothing.
- **FR-039**: Every record this feature writes — the change records of FR-036 to FR-038, and the
  refusal, unknown-outcome, and no-transition records of FR-040 — MUST carry the same turn
  identifier as every other log entry produced by that turn, so what happened to an appointment can
  be read together with the conversation that caused it.
- **FR-040**: A change that was proposed but not completed — declined by the patient, refused by the
  scheduling rules, or of unknown outcome — MUST NOT be recorded as a completed change. A refusal
  MUST be recorded as a refusal carrying its single reason; an unknown outcome MUST be recorded as
  an unknown outcome. A request that completed but **transitioned nothing** — the appointment was
  already in the state asked for, whether because the request was a replay of one that already
  landed or because the patient asked for the state they already hold — MUST be recorded as its own
  kind, distinct from a change that altered the appointment. The two are one outcome, not two: the
  system cannot tell them apart and does not need to. Recording them as ordinary changes would show
  two moves where one happened; recording nothing would leave an answered request with no trace.
- **FR-041**: Change records MUST be structured into consistent, identifiable fields, following the
  system's existing logging conventions, so a reader can filter for every change to one appointment.
  Writing one is **best-effort**: it follows the change rather than gating it. A failure to write
  MUST NOT fail, retry, or roll back a change that has already happened, and MUST NOT alter what the
  patient is told. No record is held in an outbox or made durable ahead of the change — this phase
  adds no audit store.

#### Scope boundary

- **FR-042**: The escalation path — handing a conversation to human staff — MUST remain out of scope
  for this feature. Conversations stay two-party, and an assistant that cannot help a patient with a
  change says so rather than routing them anywhere.

### Key Entities

- **Appointment** *(existing)*: gains a status and a lifecycle. Until now an appointment was created
  and then either existed or was deleted with its chat; it can now also be changed — a new start and
  end, and optionally a different practitioner — and cancelled by the patient who holds it. Its
  identifier survives both. A cancelled appointment is still a record: it keeps the practitioner and
  the times it held, but is no longer a commitment for anyone and is no longer eligible to change.
  Its status is the single fact separating "this is happening" from "this was called off", which is
  why every read has to name the statuses it means (FR-012).
- **Appointment change record**: what happened to one appointment in one turn — which appointment,
  what kind of change, the start date-time it had, the start date-time it has now where one applies,
  and the practitioner on each side where that changed. Its kind also separates a change that
  altered the appointment from a request answered without altering anything (FR-040). Tied to the
  turn that caused it.
- **Pending confirmation** *(conversational, not stored)*: a change the assistant has described to
  the patient and is waiting on. It names the appointment, the practitioner or practitioners, and
  the time or times involved. Two of those facts — the start date-time and practitioner the
  appointment had when it was described — travel with the change request as the guard of FR-021, so
  what the patient was shown is checked against what is there rather than assumed. It has no effect
  until the patient confirms, and it does not outlive the conversation. The offer survives a turn
  that neither confirms nor declines; the confirmation itself does not, and must be re-stated before
  another yes can bind (FR-029).
- **Practitioner** *(existing)*: unchanged, but now read at two more moments — when describing the
  appointment a patient is about to change, and when offering the times it could move to.
- **Patient** *(existing)*: unchanged.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A patient can cancel an appointment they booked earlier in the same conversation in
  under 30 seconds of chat, without seeing an internal identifier and without being asked to restate
  anything they already said.
- **SC-002**: Zero reschedules or cancellations take effect without an explicit confirmation from
  the patient first, across the whole test suite — including turns where the patient's message is
  ambiguous, repeats an earlier request, or answers a different question — and including a bare
  "yes" arriving after an intervening turn, which binds only against a confirmation re-stated in
  that turn.
- **SC-003**: 100% of confirmation requests state the affected appointment's start date-time, the
  practitioner's full name, and that practitioner's specialty; for a move, both the current and
  proposed start times; where the practitioner changes, both practitioners with their specialties;
  and where the change alters the appointment's length, the new length or end time.
- **SC-004**: Zero changed appointments end up violating the rules that govern a new booking at the
  moment they were changed — measured over every accepted change in the test suite, none of which
  overlapped another commitment of the same patient or practitioner, fell outside the practitioner's
  hours, off the slot grid, in the past, or beyond the horizon.
- **SC-005**: Zero refused or failed changes leave an appointment altered. After every refused
  change or cancellation in the test suite, the appointment is exactly the one that went in — same
  start, same end, same practitioner, same identifier.
- **SC-006**: A change never multiplies or loses an appointment: across every test, including runs
  where the change is forced to fail, the patient's appointments are the same records with the same
  identifiers they had before — only their fields, or their status, differ.
- **SC-007**: The assistant never reports a change that did not happen — zero fabricated
  confirmations of a move or a cancellation across the test suite, including runs where the
  scheduling capability is stopped mid-change.
- **SC-008**: Retrying a change whose outcome was lost produces zero duplicate changes and zero
  false conflict reports, at every retry point in the flow.
- **SC-009**: Every completed change is recoverable from the logs alone: for 100% of changes and
  cancellations performed in the test suite, the appointment identifier, the old start date-time,
  and the new start date-time (or its documented absence for a cancellation) are all present and
  tied to the turn that caused them. Across a suite that re-sends changes and asks for states the
  appointment already holds, the number of change records equals the number of appointments actually
  altered — zero over-counted moves — while every request that transitioned nothing is present as
  that. Measured over runs in which the logging path is working, since recording is best-effort and
  never gates a change (FR-041).
- **SC-010**: Zero change records exist for changes that did not complete, across a suite that
  includes declined confirmations, every refusal reason, and an unreachable scheduling capability.
- **SC-011**: A cancelled slot is bookable again immediately — in 100% of tests, the availability
  check run right after a cancellation offers the freed start time, and a booking placed on it
  succeeds.
- **SC-012**: A cancelled appointment is never presented as a standing one. Across the test suite,
  zero cancelled appointments appear in an answer to "what do I have booked?", zero are offered as
  candidates to change or cancel, and zero are counted by any overlap or availability calculation —
  while 100% of them are returned, labelled cancelled, when the patient asks for cancelled ones —
  including those whose start time has already passed, up to the 20 most recent.
- **SC-013**: Each of the four time/status combinations returns exactly the appointments it should
  and no others, across a suite where the patient holds at least one appointment in each: future
  standing, future cancelled, past standing, past cancelled. Zero appointments leak from one
  combination into another, and the unqualified question returns only the future standing ones. A
  listing spanning both directions returns its future leg in full and its past leg capped at 20,
  with neither leg displacing any of the other's.
- **SC-014**: No patient can change or cancel an appointment belonging to another session or another
  patient. Zero cross-session and zero cross-patient changes across the test suite.
- **SC-015**: When the scheduling capability stops responding entirely, the patient receives a reply
  saying so within 5 seconds of sending their message, in 100% of attempts, and existing FAQ
  answering continues to work.
- **SC-016**: Zero changes take effect against an appointment that no longer matches what the
  patient was shown. Across every test that mutates an appointment between the read-back and the
  confirmation, 100% are refused as stale and re-described rather than applied. Where two changes to
  one appointment are issued concurrently — including a cancellation racing a move, which collides
  with nothing — exactly one takes effect and the other is refused as stale; zero overwrite a change
  whose patient was already told it succeeded.
- **SC-017**: A move repeated over the same two times is never mistaken for a replay of itself.
  Running 09:00 → 10:00 → 09:00 → 10:00 against one appointment, all three moves take effect in the
  order given, the appointment finishes at 10:00 holding the identifier it started with, and the
  logs carry exactly three change records. Zero moves are answered by replaying an earlier one, and
  re-sending a move that had already landed adds zero change records and zero stale refusals.
- **SC-018**: Every refusal is both explained and answered. Across a suite exercising all twelve
  reasons of FR-006, 100% of refusals state their single reason in plain language; 100% of the six
  placement refusals are accompanied by at least one alternative start time the availability
  capability actually returned; 100% of stale refusals re-describe the appointment and ask again;
  and zero refusals of the three eligibility reasons offer an alternative the spec does not provide.

## Assumptions

- **This is the first half of Phase 1d.** The escalation path — `escalate_to_staff`, staff posting
  into the patient's thread, and escalation as a conversation-level state — is the second half and
  is deliberately excluded (FR-042). Nothing here should presuppose a third party in the
  conversation.
- **One confirmation covers one appointment.** A change that moves the time and swaps the
  practitioner at once is still one appointment and one confirmation; two unrelated appointments
  take two.
- **No lead-time policy.** A patient may cancel or change an appointment starting five minutes from
  now, exactly as they may one starting in three months. A clinic notice period is a policy this
  phase does not model.
- **No limit on how many times an appointment may be changed**, and no record of its intermediate
  times beyond the log entries FR-036 produces.
- **No notification of anyone else.** Nothing is emailed, texted, or pushed when an appointment
  changes; neither practitioner is told. The log is the only record outside the patient's own
  conversation.
- **No cancellation reason is collected.** The patient is not asked why, and no reason is stored.
- **Cancellation is final and one-way.** There is no un-cancel, reinstate, or restore. A patient who
  changes their mind books again, which creates a new appointment; the cancelled one stays cancelled.
  This is why a cancelled appointment is not eligible for rescheduling (FR-005) rather than being a
  second route back to a standing appointment.
- **Retention is scoped to the appointment, not to the parties to it.** A cancelled appointment
  survives its own cancellation, but not the deletion of the chat or patient that held it, nor the
  deletion of the practitioner who was to hold it (005 FR-049) — both deletions already remove
  appointments outright and are unchanged by this phase.
- **Cancelled appointments are reachable through conversation only**, when the patient asks for them
  (FR-013 to FR-015).
- **Idempotency is not uniform across the three writes, deliberately.** Booking carries a key;
  reschedule and cancel carry none. The asymmetry is the operations' shapes, not an oversight: only
  booking can bring a second appointment into being. Adding keys to the other two would be ceremony
  that protects nothing, and deriving one from a change's target state would actively introduce the
  replay bug FR-020 rules out.
- **The cap of 20 on a backward-looking listing is chosen, not derived**, in the same spirit as
  005's 14-day and 50-start availability caps: it exists to bound what is read back in a
  conversation, not to express a clinic policy, and it can be raised without touching any other
  rule. It applies to the **past leg** of any listing, because past appointments accumulate without
  limit; the future leg is bounded by the 90-day horizon instead, which is why a listing spanning
  both bounds each leg on its own rather than sharing one cap.
- **Past appointments become patient-visible in this phase**, which they were not before — every
  listing until now dropped an appointment the moment its start time passed. This follows from
  retaining cancelled records, since a cancellation is usually asked about after the fact, and it
  extends to appointments that went ahead as booked so that "what did I have last month?" and "what
  did I call off?" are answerable as the different questions they are.
- **No listing is a history feature.** All of them are conversational answers bounded by FR-016. No
  screen, filter, export, or management endpoint is added for past or cancelled appointments.
- **Cancelled appointments have no retention limit.** Nothing expires, archives, or prunes them in
  this phase.
- **Everything 005 established still holds** and is inherited rather than restated: session scoping,
  the closed set of refusal reasons and their precedence, the 90-day horizon, the 14-day/50-start
  availability cap, half-open intervals, the absence of any timezone, the client-supplied local
  date-time, the 2-second timeout with at most two attempts against the scheduling capability, and
  grandfathering of appointments through practitioner edits.
- **Existing behavior is preserved**: grounded FAQ answering, conversation history, intent
  classification, booking, listing practitioners, and listing upcoming appointments all continue to
  work unchanged. A message about changing an appointment is recognised by the existing booking
  intent rather than needing a new one.
- **Availability for a change is the existing availability capability**, asked in a way that
  discounts the appointment being changed (FR-007). It is not a new kind of search.
- **The freed slot is not offered to anyone proactively.** A cancellation makes a slot bookable; it
  does not notify a waitlist, because there is no waitlist.
- **Change records are logs, not a queryable history surface.** FR-036 to FR-041 are satisfied by
  structured log entries; no API, UI, or stored audit table is required by this phase, and adding
  one is not part of it. That is also why recording is best-effort: a log entry that fails to be
  written cannot un-happen a change that already did.
- **A patient may be moved to any practitioner in their session**, with no eligibility, referral, or
  specialty-matching rules — the same freedom booking already gives them.

## Dependencies

- **Phase 1c's scheduling service and booking flow** (`specs/005-scheduling-and-booking/`) must be
  in place: appointments, the practitioner roster, availability, the booking refusal set, the
  session scoping rules, and the conversational booking loop are all extended here rather than
  rebuilt.
- **Phase 1c's tool seam.** Rescheduling and cancellation are exposed to the agent as capabilities
  alongside the existing scheduling tools, through the same registry, so agent reasoning stays
  decoupled from how the change is carried out.
- **Phase 1c's booking idempotency rule is amended, not merely reused.** 005's FR-064 tied a key's
  lifetime to the existence of the appointment that recorded it, which was the same thing as the
  appointment standing only because cancelling deleted it. FR-011 restates the rule in terms of the
  appointment standing. Existing booking behaviour is unchanged for every appointment that was never
  cancelled. FR-062's derivation of the key from patient, practitioner, and start time is itself
  untouched — which is precisely why a released key cannot distinguish a replayed booking from a
  fresh one (FR-011).
- **Phase 1c's database-level guards.** The overlap constraints that make double-booking impossible
  must also cover an appointment's new time and new practitioner; a change is a write that has to
  pass the same guard a booking does, not an update that sidesteps it.
- **Phase 002's structured logging.** The change records of FR-036 to FR-041 are written through the
  existing logging conventions and carry the existing turn correlation identifier.
- **Phase 1a's conversation history** must remain available, since a change is negotiated across
  several turns and the confirmation refers back to what was already said.
