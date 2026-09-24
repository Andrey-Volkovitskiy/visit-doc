# Feature Specification: What staff can see of the schedule (Phase 3a leftovers)

**Feature Branch**: `worktree-appts-to-frontend`

**Created**: 2026-09-24

**Status**: Draft

**Input**: User description: "Create a spec for `docs/ROADMAP.md` phase 3a leftovers:
**A practitioner's standing appointments for the next 7 days** — the Practitioners tab shows them
grouped by day beneath the selected practitioner; nothing serves them today, so this needs a console
endpoint over Scheduling's appointment listing, scoped to the session like every other console read,
with the practitioner and the 7-day window as its predicate rather than a filter applied to a wider
answer. **A booking outcome a staff member can read** — the `(i)` marker on a patient message opens
what the assistant did with that request, for a booking the change it actually made ('Appt with
dr. Andreas at 9:00 12.01.2027 is cancelled'); `request_outcomes` cannot carry it, because it
describes a FAQ answer and NULL there means no FAQ half ran, so a booking's outcome needs its own
shape on the message rather than a sixth `FaqVerdict` value or an `answer` string standing in for
one."

## Why this exists

Spec 015 gave the SPA its design and drew two things the backend could not serve. It rendered each
as a deliberate, labelled stub rather than omitting it, so the screen is laid out for the real thing
and the gap stays visible. This feature replaces both stubs with the real data.

Both gaps are about what staff can know of the **schedule**, the one kind of fact the console still
cannot show:

1. **Whose appointments stand with a practitioner this week.** The scheduler holds every appointment,
   but the console can reach the scheduler only through its practitioner and specialty proxies.
   Scheduling's appointment listing answers for **one patient**, never for one practitioner, and has
   no upper time bound. So the Practitioners tab can edit a practitioner's hours, but cannot say
   whether anyone is booked into the hours it is about to change.
2. **What the assistant actually did to the schedule.** When the assistant books, moves or cancels
   an appointment, the chat service's record of the turn keeps **nothing** about it. The act goes to
   the logs and the trace, and the only stored trace of it is the prose of the reply. A staff member
   opening a flagged conversation cannot tell from the record whether an appointment was made. That
   matters most in exactly the case they are paged for: a write whose answer never arrived, which
   the assistant reported as unknown and which a person now has to check.

The second gap is the more important one. A performed act is a fact about the clinic's records, not
a claim about them. It happened whether or not the reply that described it was ever delivered,
whether or not staff took the conversation over first, and whether or not the patient read it.
Today, the one record of it that a person can read is the one most likely to be missing when
something went wrong.

**Three things this feature deliberately is not.**

- It does not put booking outcomes into `request_outcomes`, into a new `FaqVerdict` value, or into
  an `answer` string. 011 gave that structure one meaning, "what the corpus let the assistant say
  about a question". An act on the schedule is a different kind of thing, and folding it in would
  make `NULL` there mean two things.
- It does not show anything new in the patient pane. The patient already reads the reply. The record
  is for staff.
- It does not change what the assistant knows or does. The booking record is for staff. The agent
  never reads it: no prompt, history window or tool result changes, so the golden-set baseline
  stays comparable. Feeding it to the model is a separate, measured change.
- It does not make the booking list editable. Staff still change appointments only through
  the patient's conversation, or not at all.

## Clarifications

### Session 2026-09-24

- Q: Which message carries a booking outcome — the patient's message or the assistant's reply? →
  A: **The patient message the turn was answering.** This follows `docs/ROADMAP.md` Phase 3a, which
  is binding and says so ("the `(i)` marker on a patient message opens what the assistant did with
  that request"). It is also the only anchor that always exists when the act does. A reply is
  missing in three ordinary cases where the act still happened:
  - The turn failed after the write landed. This is the `assistant_failed` case, the one a person is
    paged for.
  - Staff took the conversation over before the reply could be stored.
  - A newer patient message cancelled the turn.

  Recording the act on the reply would lose it in exactly the cases where a person most needs it.
  This does not contradict 015's FR-026. That rule says a marker describes the message it sits on
  and is never assembled by pairing a message with its neighbour, and this record is written to the
  patient message and read from it. The FAQ outcomes stay on the reply, for 015's reason: they
  describe a reply's content.
- Q: Which acts are recorded? → A: **Every attempt to change the schedule**: book, reschedule and
  cancel. Each is recorded with what came of it, including an attempt that was refused, one that
  changed nothing, one that never reached the scheduler, and one whose outcome is unknown. Reads
  (listing practitioners, checking free slots, listing the patient's appointments) are not
  recorded, because they change nothing a person could need to verify or undo. The reply already
  reports what they found.
- Q: Where do a practitioner's bookings appear — beneath the practitioner being edited, as 015's
  stub placed them, or on the roster? → A: **On the roster, behind a Show/Hide bookings toggle at
  the bottom of each practitioner's block.** The list is long enough to swamp the screen, so it is
  collapsed until asked for and can be closed again, and it sits where a staff member scans the
  whole clinic rather than one practitioner at a time. The edit view shows no appointments. This
  also narrows the roadmap's "beneath the selected practitioner": the "selection" is the toggle.
- Q: Does the agent read the booking record (for example, as context for later turns)? → A: **No.
  It is staff-only.** Prompts, history and tool results are unchanged (FR-021a). Giving the model
  its own past acts is an AI-behaviour change that has to be measured against the golden-set
  baseline, so it belongs in a phase of its own.
- Q: Can a staff member go from a booking to that patient's conversation? → A: **No. The name is
  plain text** (FR-009). Linking the two tabs would join scheduler patients to chat conversations,
  which the console does not do today. It is additive and can be added later.
- Q: Is a write call rejected for invalid arguments a recorded attempt? → A: **No.** An attempt
  begins when a write call's arguments are accepted (FR-010). A malformed call cannot have changed
  the schedule, and recording it would clutter the staff view with the model's own retries. A call
  with valid arguments that could not be sent (for example, the chat has no patient record) is
  still recorded as **not sent**.
- Q: What happens if the booking record itself cannot be stored? → A: **The record is written first.**
  The attempt is stored before the scheduler is called, and its outcome is filled in afterwards
  (FR-012a). If it cannot be stored, the write is not sent and reports unavailable. Writing the
  record after the act would let a failed record write leave a message with no booking section,
  which reads as "nothing attempted" about an act that happened. An attempt with no outcome yet is
  shown as unknown (FR-012b).
- Q: How does an open staff thread learn that a booking act was recorded or settled, when no
  message changed? → A: **The console's conversation list carries a per-conversation booking-record
  version.** It changes whenever an act in that conversation is recorded or settled. The staff
  thread re-reads when it changes, just as it already re-reads when a message arrives (FR-016a).
  Without it, a turn that booked and then failed before replying would leave the thread saying
  "unknown" about a booking that was made, until some later message happened to trigger a re-read.
  That is the case a person is paged for.
- Q: What is "the next seven days"? → A: **Seven local calendar days, starting with today, as the
  viewer's own clock reads them.** An appointment counts if it has not yet ended and starts before
  the end of the seventh day. So one already under way stays listed until it ends, and one that
  ended earlier today does not. The app has no timezone, and the scheduler has no clock of its own
  and judges past and future against the caller's local time. So "today" is the viewer's, sent with
  the read, as the patient pane already sends it with every turn.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A staff member reads what the assistant did to the schedule (Priority: P1)

A patient asks the assistant to cancel their Friday appointment and it does. Later, a staff member
opens that conversation in the console. The marker on the patient's message opens a block that says,
in plain words: *"Cancelled: appointment with Dr. Andreas at 09:00 on 12.01.2027."* If the assistant
tried to book a slot that turned out to be taken, the block says the booking was refused and why. If
the scheduler never answered, the block says the outcome is **unknown** and must be checked. It
never says that nothing happened.

**Why this priority**: It closes the larger gap. A person paged for a failed turn can see whether an
appointment exists without trusting prose the model wrote, and every change the assistant makes to
real records becomes auditable from the screen.

**Independent Test**: Drive one patient turn that books an appointment, and one that attempts a
write while the scheduler is unreachable. Open each in the staff console and check that the marker
states the act and its outcome, with the details recorded when the act ran.

**Acceptance Scenarios**:

1. **Given** a turn in which the assistant booked an appointment, **When** the staff member opens the
   marker on the patient message that turn answered, **Then** the block states that an appointment
   was booked, with whom, and the day and time.
2. **Given** a turn in which the assistant rescheduled an appointment, **When** the block opens,
   **Then** it states both the time it moved from and the time it moved to, and the practitioner for
   each where they differ.
3. **Given** a turn in which the assistant cancelled an appointment, **When** the block opens,
   **Then** it states which appointment was cancelled: practitioner, day and time.
4. **Given** a write the scheduler refused (for example, the slot was taken while the patient was
   deciding), **When** the block opens, **Then** it states that the attempt was refused and why, and
   that no change was made.
5. **Given** a write whose outcome is unknown (the request may have reached the scheduler but no
   answer came back), **When** the block opens, **Then** it states that the outcome is unknown and
   must be checked. It MUST NOT say that nothing was changed, and the marker takes the
   needs-a-person state.
6. **Given** a turn that attempted two writes (for example, a failed booking followed by a
   successful one at another time), **When** the block opens, **Then** both appear in the order they
   were attempted, each with its own outcome.
7. **Given** a turn that only read the schedule (it listed free slots and booked nothing), **When**
   the thread renders, **Then** the patient message carries no booking record. It carries a marker
   only if something else about it calls for one.
8. **Given** a turn that booked an appointment and then failed before its reply was stored, **When**
   the staff member opens the conversation, **Then** the patient message carries both the failure
   mark and the booking record.

---

### User Story 2 - A staff member sees who is booked with a practitioner this week (Priority: P2)

On the Practitioners tab, every practitioner in the roster has a **Show bookings** control at the
very bottom of their block. A staff member clicks it on Dr. Vesalius's block, and the next seven days
of standing appointments open inside that block, grouped by day. Each shows the patient and the
time. Before shortening Dr. Vesalius's Thursday hours, the staff member can see that two patients
are booked on Thursday afternoon. The list takes a lot of screen space, so the control now reads
**Hide bookings**, and clicking it closes the list again.

**Why this priority**: It turns the practitioner screen from a form into a working view of the
schedule. It ranks below Story 1 because nothing is ever *wrong* for want of it. It is a view over
data that is already correct.

**Independent Test**: Seed a practitioner with three appointments: one today, one in four days, and
one in ten days. Also give one of them a cancelled appointment, and give another practitioner an
appointment today. Click **Show bookings** on the first practitioner's block and check that exactly
the first two appear, under their days. Then click **Hide bookings** and check that the list is
gone.

**Acceptance Scenarios**:

1. **Given** the Practitioners tab showing the roster, **When** it renders, **Then** each
   practitioner's block ends with a **Show bookings** control, and no block shows bookings until its
   control is used.
2. **Given** a practitioner with standing appointments in the next seven days, **When** a staff
   member clicks **Show bookings** on that block, **Then** those appointments appear inside the
   block, below the practitioner's details, grouped under their day and in time order. Each shows
   the patient's name and its start and end time. The control now reads **Hide bookings**.
3. **Given** a block whose bookings are shown, **When** the staff member clicks **Hide bookings**,
   **Then** the list closes, the block returns to its compact size, and the control reads
   **Show bookings** again.
4. **Given** a practitioner with appointments outside the window, cancelled appointments, or
   appointments that have already ended, **When** their bookings are shown, **Then** none of those
   appear.
5. **Given** a practitioner with no standing appointments in the window, **When** their bookings are
   shown, **Then** the block states that nobody is booked with them in the next seven days. It does
   not show an empty list, which could be read as "not loaded".
6. **Given** the scheduler is unreachable, **When** bookings are shown, **Then** the block states that
   the appointments could not be read, and does not claim there are none.
7. **Given** a block showing a practitioner's week, **When** the assistant books an appointment with
   that practitioner from the patient pane, **Then** the new appointment appears in that block
   without the staff member reloading the page or hiding and re-showing the bookings.
8. **Given** two practitioners' bookings shown at once, **When** the roster renders, **Then** each
   block shows its own practitioner's week, and hiding one leaves the other open.
9. **Given** the edit view of a practitioner, **When** it renders, **Then** it shows no appointments
   panel. The bookings live on the roster only.
10. **Given** a practitioner id that does not belong to the viewer's session, **When** its
   appointments are requested, **Then** the answer is the same "not found" any other console read
   gives for another session's id, and reveals nothing about whether that id exists.

---

### Edge Cases

- **A turn answering a burst of patient messages.** The booking record goes on the message the turn
  was answering (the latest of the burst), the same message the turn's attention mark goes on. It
  is never copied to every message in the burst.
- **The booking record cannot be written.** The write is not sent (FR-012a). The patient is told
  that booking is unavailable, as when the scheduler is unreachable, and the failure is handled as
  any other booking-tool failure is, so it calls a person under `assistant_failed`.
- **Staff open a marker while the turn is still running.** An act already sent but not yet answered
  reads as unknown (FR-012b). Settling it changes the conversation's booking-record version
  (FR-016a), so the open thread re-reads and shows the outcome within one console poll. That holds
  even when the turn then fails without a reply.
- **A turn superseded mid-write.** A newer patient message cancels the turn after it sent a write
  but before the answer came back. The attempt is still recorded, with its outcome unknown. A
  cancelled turn is not a failed turn and records no failure (011's rule stands), but an act it may
  have performed is still an act.
- **An attempt that could not be sent at all** (for example, the chat has no patient record, or
  every connection attempt failed). It is recorded as not sent, and nothing changed. This is the one
  failure the record may call "no change", because in this case it is known.
- **A write call with invalid arguments** (for example, a malformed time). It is not recorded
  (FR-010). Nothing could have changed, and the loop's corrected call that usually follows is the
  act staff see.
- **An unchanged result** (for example, a reschedule to the time the appointment already has). It is
  recorded as "no change was needed", distinct from a refusal.
- **A merged turn** (a FAQ question plus a booking). The FAQ outcomes stay on the reply and the
  booking record goes on the patient message, so each marker describes its own message. Neither is
  folded into the other.
- **A practitioner later renamed or deleted, or an appointment later changed again.** The booking
  record still states what was done **at the time**, under the names in use then. It is a record of
  an act, not a live view of an appointment, so a later change never rewrites an earlier record.
- **A practitioner deleted while their bookings are shown.** Their block leaves the roster, and with
  it the list. If a read answers "not found" before the roster catches up, the block says the
  practitioner no longer exists. It does not show an empty week.
- **Bookings shown, then the practitioner edited.** Opening the edit view replaces the roster, per
  015's FR-035a. Returning to the roster shows every block collapsed again, because nothing about
  shown or hidden is remembered across leaving the roster. A reload also collapses every block.
- **A shown list is not a cached one.** Every **Show bookings** reads fresh, so a list hidden an hour
  ago and shown again is today's, not the one from an hour ago.
- **The viewer's clock crosses midnight while bookings are shown.** The next read uses the new day,
  so the window moves with the clock.
- **A day on which nobody is booked.** It is simply absent from the grouped list. The
  "nobody is booked" statement appears only when the whole window is empty.
- **A staff message posted into the conversation.** Marks the staff message clears (per 009) are
  cleared as before. The booking record is never cleared, because it is not a request for attention
  but a fact about what happened.
- **A booking record on a message that has no other reason for a marker.** The message gets a
  marker in the served state, unless an act's outcome is unknown, in which case it takes the
  needs-a-person state.

## Requirements *(mandatory)*

### Functional Requirements

#### Reading a practitioner's week

- **FR-001**: The console MUST offer a read of one practitioner's standing appointments over a
  seven-day window. It MUST be scoped to the requesting session exactly as every other console read
  is: no session gives the same "no session" answer the practitioner proxy gives, and another
  session's practitioner id gives "not found".
- **FR-002**: The session, the practitioner and the window MUST be predicates of the scheduler's own
  query, applied where the appointments are stored. The read MUST NOT fetch a wider set (a patient's
  appointments, a session's, or an unbounded future) and narrow it afterwards in the chat service or
  the browser.
- **FR-003**: The window MUST be computed from the viewer's local date and time, supplied with the
  read and validated the way a turn's local time is validated (a value carrying a timezone offset is
  rejected). The window is: not yet ended at that moment, and starting before the end of the
  seventh local day, counting today as the first.

  > **Superseded: no far end.** The seven-day bound hid bookings a staff member has to be able to
  > find — one made for next spring simply was not there, and nothing on screen said the list had
  > been cut. The read is now everything not yet ended at that moment, however far ahead, capped
  > at a page of 20 with the cut reported (`contracts/practitioner-week.md`). The near end and its
  > validation are unchanged.
- **FR-004**: Only standing appointments MUST be returned. Cancelled appointments MUST NOT be.
- **FR-005**: Each appointment returned MUST carry its patient's display name, its start and its
  end. The whole answer MUST be ordered by start time.
- **FR-006**: The read MUST distinguish its outcomes, and never let one stand for another:
  - appointments were found;
  - the practitioner has none in the window;
  - the practitioner does not exist in this session;
  - the scheduler could not be reached or did not answer.

  Because the read changes nothing, a failure to reach the scheduler MAY be retried.
- **FR-007**: 015's appointments stub MUST be removed from the practitioner edit view, and the edit
  view MUST NOT show appointments at all. The practitioner's week is shown on the **roster**
  instead, inside each practitioner's block.
- **FR-007a**: Each practitioner's block in the roster MUST end with a toggle control, placed below
  everything else in the block. It is labelled **Show bookings** while the list is closed and
  **Hide bookings** while it is open, and it exposes its open or closed state to assistive
  technology. Every block MUST start closed.
- **FR-007b**: Showing a block's bookings MUST read that practitioner's week at that moment. A closed
  block MUST NOT read the scheduler. Hiding MUST remove the list from the block entirely, returning
  it to its compact size.
- **FR-007c**: An open list MUST show the practitioner's appointments grouped by local day, in time
  order, each with patient, start and end. It MUST state plainly when nobody is booked in the window
  and when the read failed. Each of those two MUST be distinguishable from the other and from a list
  still loading.
- **FR-007d**: Any number of blocks MAY be open at once. Each block's open or closed state is its
  own, and MUST survive the roster being re-read or re-rendered while the staff member stays on the
  roster.
- **FR-008**: An open list MUST reflect an appointment made, moved or cancelled through the patient
  pane without the staff member reloading the page or hiding and re-showing it, within 5 seconds
  of the change (SC-004).
- **FR-009**: The booking list MUST be read-only. Its only control is the show/hide toggle, and it
  offers none that changes an appointment. A patient's name in it is plain text, not a link to that
  patient's conversation.

#### Recording what the assistant did to the schedule

- **FR-010**: Every attempt the assistant makes to book, reschedule or cancel an appointment MUST be
  recorded as a **booking act**, whatever its outcome. Reads MUST NOT be recorded as acts. An
  attempt begins when a write call's arguments are accepted. A call rejected for invalid arguments
  never reaches the point where the schedule could change, so it is not an attempt and MUST NOT be
  recorded.
- **FR-011**: A booking act MUST record:
  - its operation: book, reschedule or cancel. The console states a done act as "booked",
    "moved" or "cancelled";
  - its outcome, which is exactly one of:
    - **done**: the change was made;
    - **unchanged**: no change was needed;
    - **refused**: the scheduler declined, with its reason;
    - **not sent**: it is known that the scheduler made no change and gave no refusal reason.
      Either the request never reached it, or it rejected the request before acting on it (the
      case the tools already report as `unavailable`);
    - **unknown**: the request may have reached the scheduler and no answer came back;
  - the practitioner and appointment time it concerned, as known when it ran. For a reschedule that
    is both the previous and the new practitioner and time.
- **FR-012**: An act's outcome MUST be reported as **unknown** whenever it cannot be established.
  That includes when the turn ends (by failure, cancellation or supersession) after the request
  could have been sent but before its answer was recorded. **Not sent** MAY be recorded only when it
  is known that the scheduler made no change: no attempt reached it, or it answered that it had
  done nothing. No act that may have changed the schedule may go
  unrecorded.
- **FR-012a**: An attempt MUST be stored **before** the request is sent to the scheduler, with no
  outcome yet, and its outcome filled in when the answer arrives. If the attempt cannot be stored,
  the request MUST NOT be sent, and the write MUST be reported to the model as unavailable,
  exactly as a scheduler it could not reach is today. This makes a missing booking record mean
  one thing only: nothing was sent to the scheduler.
- **FR-012b**: An act stored with no outcome MUST be presented exactly as **unknown**. That is true
  whether the turn is still running or ended before the outcome was filled in, so the display never
  depends on which of the two it is.
- **FR-013**: Booking acts MUST be stored on the patient message the turn was answering, in the order
  they were attempted. They MUST NOT depend on a reply being stored, delivered or streamed.
- **FR-014**: Booking acts MUST have their own shape on the message. They MUST NOT be carried by
  `request_outcomes`, by a new `FaqVerdict` value, by an outcome's `answer` text, or by the attention
  mark. `request_outcomes` keeps its 011 meaning unchanged. A message on which no act was attempted
  carries no booking record, and "no acts" MUST NOT be stored as an empty record that could be read
  as "a booking half ran and did nothing".
- **FR-015**: A booking act MUST be a snapshot. Later changes to the appointment, the practitioner
  or the patient MUST NOT alter it. Staff actions (a staff post, the assistant switch) MUST NOT clear
  it.
- **FR-016**: The thread a staff member reads MUST carry each message's booking acts, so the console
  can render them without any further request.
- **FR-016a**: The console's conversation listing MUST carry, for each conversation, a
  booking-record version that changes whenever an act in that conversation is recorded or settled,
  and never otherwise. An open staff thread MUST re-read when that version changes, exactly as it
  re-reads when a new message arrives. The version MUST NOT be derived from a clock, because two
  changes within one clock tick would look like none.

#### Showing it in the console

- **FR-017**: A patient message MUST carry an evidence marker when it carries booking acts, in
  addition to 015's existing conditions (FR-026). A message with none of the three (FAQ outcomes,
  an attention mark, booking acts) MUST carry no marker.
- **FR-018**: The marker MUST take the needs-a-person state when the message holds an act whose
  outcome is **unknown**, in addition to 015's existing conditions (FR-026a). Other outcomes
  (including refused, which the assistant already reported to the patient) do not on their own
  change the state.
- **FR-019**: The expanded block MUST list each act in order, stating in words its operation, the
  practitioner and time it concerned (both before and after, for a reschedule), and its outcome. For
  a refusal it MUST give the reason. For an unknown outcome it MUST state that the change may or may
  not have been made and must be checked. For an unknown outcome it MUST NOT state or imply that
  nothing changed. Each outcome MUST be distinguishable by more than colour.
- **FR-020**: 015's "booking outcome not yet recorded" line MUST be removed. It MUST NOT be replaced
  by any statement on messages without acts. The absence of a booking section is what says no write
  was attempted.
- **FR-021**: This feature MUST NOT change what the patient pane shows.
- **FR-021a**: Booking acts MUST NOT be read by the agent. No prompt, conversation history passed to
  a model, or tool result may include them, so every model call a turn makes is unchanged by this
  feature.

#### Contracts and documentation

- **FR-022**: The test hooks that 015's stubs exposed (`appointments-stub`, `booking-outcome-stub`)
  MUST be retired. Their successors, including the roster's show/hide bookings control, MUST be
  listed in the frontend's test-hook table, since that table is a contract with the Phase 3b
  browser suite.
- **FR-023**: Existing readers of the stored thread MUST keep working. That includes the golden
  harness, which parses stored messages strictly, and every committed evaluation run, which must
  still re-score.
- **FR-024**: `docs/ROADMAP.md` Phase 3a MUST record both capabilities as shipped, and that the
  practitioner's week lives on the roster behind a show/hide toggle rather than beneath a selected
  practitioner. The README MUST record the tradeoff of where a booking act is anchored and why it
  is a snapshot. The project
  `CLAUDE.md`'s key design decisions MUST record the booking-act rule alongside 011's
  `request_outcomes` entry.

### Key Entities

- **Booking act**: One attempt by the assistant to change the schedule during a turn. It records:
  - operation: book, reschedule or cancel;
  - outcome: done, unchanged, refused, not sent or unknown;
  - the refusal reason, when refused;
  - the practitioner's name and the appointment's start and end, as known when the act ran;
  - for a reschedule, the previous practitioner and time as well;
  - its order among the turn's acts.

  It is stored before the scheduler is called and has no outcome until the answer arrives. While it
  has none, it reads as unknown.

  It belongs to exactly one patient message, the one the turn was answering. It is never edited
  once its outcome is settled.
- **Practitioner week**: The standing appointments of one practitioner that have not ended and start
  before the end of the seventh local day from the viewer's today. It is shown inside that
  practitioner's roster block, on demand. Each appointment has a patient display name, a start and
  an end. It is read from the scheduler on demand and never stored on the
  chat side.
- **Evidence marker** (existing, from 015): now opened by any of three things on its own message:
  FAQ outcomes, an attention mark, or booking acts.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every turn in which the assistant attempted a change to the schedule, the patient
  message it answered shows that attempt with its outcome in the staff console: 100% of attempts,
  including turns that failed, were superseded, or were taken over before a reply was stored.
- **SC-002**: No write whose outcome is unknown is ever presented to staff as "no change". Checked
  across every unknown-outcome path: 0 occurrences.
- **SC-003**: A staff member can answer "is anyone booked with this practitioner on Thursday?" from
  the roster with one click, with no other screen, tool or query, and can close the answer again
  with one more.
- **SC-004**: A booking made from the patient pane appears in that practitioner's open booking list
  within
  5 seconds, with no reload.
- **SC-005**: The practitioner week shows exactly the practitioner's standing, not-yet-ended
  appointments in the seven-day window. In a seeded check containing out-of-window, cancelled,
  ended and other-practitioner appointments, none of those appear, and none from another session.
- **SC-006**: Every committed evaluation run still re-scores unchanged, and the harness's existing
  tests pass without edits to recorded data.
- **SC-007**: In an open staff thread, a settled act's outcome replaces "unknown" within 5 seconds of
  being recorded, including for a turn that ended without a reply.

## Assumptions

- **The viewer's clock is the clinic's clock.** The app has no timezone concept (Phase 1c). The
  scheduler already judges past and future against the caller's local time, and the patient pane
  already sends one with every turn. A staff read sends the viewer's in the same form. A skewed
  browser clock skews the window, which is the same trade the booking path already accepts.
- **The scheduler's appointment listing is extended or complemented, not proxied as is.** It
  answers only per patient and has no upper time bound, so serving FR-002 needs a practitioner- and
  window-scoped read on the scheduler side, one read per open list. Whether that is a new call or an extension of the
  existing one is a planning decision. Either way it crosses the existing boundary. It adds no
  service.
- **The seven-day window is small and bounded by working hours.** A practitioner has at most as many
  appointments as their schedule has slots in seven days, so the read needs no paging or cap.

  > **No longer true, and replaced by a cap.** With no far end the calendar grows without bound, so
  > the read is capped at a page the caller names — 20 for the console — and says when it stopped.
- **"Within a few seconds" reuses the console's existing rhythm.** The console already re-reads what
  changed every 2 seconds. How an open list learns of a change (reusing that rhythm, or its own) is
  a planning decision, bounded by SC-004 and by not reading the scheduler more often than that
  rhythm. Only open lists are ever refreshed, which is also what keeps a long roster from reading
  the scheduler once per practitioner.
- **Reads are not audited.** Checking availability or listing appointments changes nothing, and the
  reply reports what was found. If staff later need "what did the assistant look at", that is a
  separate, additive record.
- **Staff are the only readers.** The booking record is not added to the patient-facing reply
  stream. The console reads it from the stored thread, which is how it reads FAQ outcomes and marks
  today.
- **Existing data is not backfilled.** Messages written before this feature carry no booking record.
  The acts they may have performed are in the logs only, as they always were.

## Dependencies

- **`docs/ROADMAP.md` Phase 3a** is binding on scope: exactly the two leftovers it lists, nothing
  wider.
- **Spec 015** supplies the stubs this feature replaces, the evidence marker and its state rules
  (FR-026, FR-026a), and the test-hook contract (FR-036).
- **Spec 011** fixes the meaning of `request_outcomes`, which FR-014 preserves.
- **Specs 005/006** define the write outcomes (booked, changed, unchanged, refused, unavailable,
  unknown) and the rule that a timeout never proves nothing happened. FR-011/FR-012 record those
  outcomes, not new ones.
- **The golden harness** (`evals/harness`) reads the stored thread strictly. FR-023 requires it to
  keep reading it.
