# Feature Specification: Scheduling Service and End-to-End Booking (Phase 1c)

**Feature Branch**: `005-scheduling-and-booking`

**Created**: 2026-08-11

**Status**: Draft

**Input**: User description: "Create a spec for phase 1c. The main scheduling entity is appointment
that has the following attributes: patient, practitioner, start time, end time, session. A patient
is a person connected with a particular chat. A chat always has one and only one person and vice
versa... A pool of 100 famous internationally recognized writers who passed away more than 50 years
ago should be predefined in the app... A practitioner has full name, specialty, schedule, session...
If a user asks 'What specialists do you have?' a list of practitioners connected to the session
should be provided... Once an app user visits the website first time a new session, one chat with a
patient and one doctor should be created. Later the user can add/edit/hard delete either patients or
practitioners (via API, no UI for that yet). When chatting with a user his local time should be
displayed in a conversation."

## Clarifications

### Session 2026-08-11

- Q: ROADMAP scopes 1c as a standalone Scheduling service built *before* the agent calls it, but the
  description also asks for chat-facing behavior. What should 1c deliver? → A: The whole booking
  flow. Revise ROADMAP so 1c covers the Scheduling service *and* booking through conversation;
  rescheduling, cancellation, and escalation move to 1d. (`docs/ROADMAP.md` was revised accordingly
  in the same change.)
- Q: Session and Chat live in the core backend's datastore, but Scheduling gets its own — where do
  Patient and Practitioner live? → A: Scheduling owns Patient, Practitioner, and Appointment. The
  deciding factor is that the "no appointment outside the practitioner's schedule" rule needs the
  schedule and the appointment rows visible in one transaction. Cross-boundary references are
  opaque ids only: a Patient carries its `chat_id` (unique, so creation is idempotent on retry) and
  the owning session id; a Chat caches its `patient_id`.
- Q: What happens on first visit if the scheduling capability is unavailable? → A: Chat creation is
  the priority and must never block on it. The visitor still gets a working (unnamed) chat and
  grounded FAQ answers; the patient record is created later.
- Q: How is appointment length decided? → A: Each practitioner has a fixed appointment duration that
  every appointment with them uses. Default 60 minutes.
- Q: Which write operations are in scope? → A: Booking only. Appointment cancellation and
  rescheduling are deferred to 1d, knowingly accepting that a mis-booked appointment has no in-chat
  undo during this phase.
- Q: A practitioner's schedule is wall-clock text ("Mon 8am–4pm") — what timezone is it anchored to,
  and what happens when the user's device reports a different timezone later? → A: *Superseded on
  2026-08-12 — see "the app has no concept of a timezone at all" below.* The original answer stored
  one timezone per session, captured from the browser, with appointments held as absolute instants
  and converted for display.
- Q: Within a practitioner's working hours, where may an appointment start? → A: On a grid running
  from each working block's own start — a 60-minute practitioner working 8am–4pm offers 8:00, 9:00,
  … 15:00. A leftover remainder shorter than one full duration at the end of a block is not offered.
- Q: What happens when an edit to a practitioner would invalidate appointments already booked
  (narrowing the schedule, or changing the duration)? → A: Allow the edit and grandfather the
  existing appointments — they keep their original times and length untouched. Only *new* bookings
  are validated against the new settings. The schedule-bounds rule is therefore enforced at
  appointment-creation time, not as an always-true invariant; the no-overlap rules remain always
  true.
- Q: A session currently holds exactly one chat, but the model needs many (100 names per session,
  per-session name uniqueness, "add a patient" meaning "create a chat"). Does 1c introduce
  multi-chat sessions? → A: Yes, in both the API and the UI — the app user gets a list of their
  chats, can switch between them, and can create new ones.
- Q: Deleting a chat deletes its patient, but the management interface also allows deleting a
  patient directly — what happens to the chat? → A: There is only one deletion operation. Today's
  "clear chat" is replaced by a single "delete chat + patient + appointments" action: the user picks
  a chat/patient from the list and deleting it removes all three together. There is no separate
  delete-the-patient and no separate delete-the-chat.
- Q: What happens when the user deletes the only chat in their session — is a replacement
  provisioned? → A: No. The session survives with zero chats until the user adds one. The chat area
  (message history, send button) is muted while the session has no chats.
- Q: A booking is written but its confirmation is lost to a timeout; a retry then hits the overlap
  guard and looks like a conflict with the patient's own appointment. How is that resolved? → A:
  Booking attempts carry a caller-supplied idempotency key. A retry with the same key returns the
  original appointment as a success rather than a conflict, so a lost confirmation is invisible to
  the patient.
- Q: A patient asks for "a dentist" and the session has several. How is the practitioner chosen? →
  A: The assistant lists the matching practitioners and asks the patient to choose, then offers that
  practitioner's times. It never picks on the patient's behalf.
- Q: How long may a call to the scheduling capability take before it is treated as unavailable? →
  A: 2 seconds per call, at most two attempts (one retry), so the "temporarily unavailable" reply
  reaches the patient within 5 seconds.
- Q: Where can a user change their session's timezone? → A: *Superseded on 2026-08-12 — no timezone
  is stored, so there is nothing to change.*
- Q: The chat list identifies chats by patient name, but a chat created while scheduling is down has
  no patient yet. How are those listed? → A: With a placeholder label and their creation time
  ("Unnamed · 14:32"), replaced by the real name once the patient record is created. Chat creation
  stays available throughout an outage.

### Session 2026-08-12

- Q: Does "what appointments do I have booked?" include appointments that have already happened? →
  A: No. Upcoming appointments only — those whose start time is still in the future — earliest
  first. Past appointments are not listed and there is no way to ask for them this phase.
- Q: Should messages carry a visible local-time timestamp, to satisfy "his local time should be
  displayed in a conversation"? → A: No. FR-032 and FR-033 are the whole requirement — the assistant
  reasons in local time and expresses every time it mentions in local time. No per-message
  timestamps are added to the chat UI.
- Q: What happens if a chat is deleted while its assistant reply is still streaming or a booking call
  is still in flight? → A: The delete goes ahead immediately and the in-flight turn is abandoned, as
  a superseded turn already is — no assistant reply is recorded. An appointment can never outlive
  its patient: a booking that landed just before the delete is removed by the same cascade, and one
  landing just after is rejected because its patient no longer exists.
- Q: Which chat is shown when the app loads or reloads? → A: The most recently active one — the chat
  holding the newest message. Derived from stored messages rather than a remembered selection. A
  session whose chats have no messages at all falls back to the most recently created.
- Q: What specialty and schedule does a practitioner get when they are created without one — both
  from the management interface and from first-visit seeding? → A: Every field defaults: General
  Practice, Monday–Friday 09:00–17:00, 60-minute appointments, next unused pool name. A bare create
  therefore yields an immediately bookable practitioner, and seeding uses the same defaults. Any
  field can be overridden.
- Q: The timezone handling looks over-engineered. Can it be simpler, given that every patient,
  practitioner, and staff member in a session is always in the same timezone? → A: Yes — the app has
  no concept of a timezone at all. Because a session never spans two zones, no time is ever
  converted between zones, so storing a zone identifier buys nothing. Every time in the system —
  working schedules, appointment start and end, "now" — is a plain local date-time with no zone
  attached, and the client supplies the current local date and time so relative phrasing resolves.
  This supersedes both timezone answers from 2026-08-11 and removes the stored session timezone,
  absolute-instant storage, the daylight-saving rule, and the travelling-user case.
- Q: With no stored timezone, which clock decides whether a time is "in the past" or within the
  booking horizon — the client-supplied local date-time or the server's? → A: The client-supplied
  local date-time, for every such check. Stored times are the user's local wall-clock, so the only
  clock comparable to them is the user's own; a wrong client clock affects nothing outside that
  user's session.
- Q: In what order are names drawn from a pool, and which name gets a number appended once a pool is
  exhausted? → A: Fully deterministic, in pool order, identically for patients and practitioners.
  Always the first name in the pool not already used in that session; once every name is taken, walk
  the pool again appending " 2", then " 3", and so on. A session's first patient is therefore always
  the first pool name, and its 101st is that same name plus " 2".
- Q: Is a specialty a value from a fixed list, or free text? → A: *Superseded on 2026-08-13 — see the
  ten-specialty list below.* The original answer fixed the set at General Practice and Dentistry; an
  intermediate revision made it free-form text.

### Session 2026-08-13

- Q: This phase adds a second database, so the chat service's is no longer "the" database — should it
  keep the generic name `visitdoc`? → A: No. It is renamed to `visitdoc_chat` (and `visitdoc_test` to
  `visitdoc_chat_test`), so both services' databases are named for their owner and neither reads as
  the default one (FR-059).
- Q: FR-051 said a key already used always returns the original appointment as a success. What if the
  attempt asks for a *different* time than the one that key booked? → A: It is refused, not replayed
  (FR-063). As written, FR-051 would have returned the original appointment for a request that asked
  for something else, and the assistant would have confirmed a time the patient never chose. The
  key's scope, lifetime, and the rule that a refusal does not consume a key are now stated too
  (FR-062, FR-064).
- Q: Does an appointment ending at 10:00 conflict with one starting at 10:00? → A: No. An appointment
  occupies its start up to but not including its end, so back-to-back grid slots are always bookable
  (FR-061). The opposite reading would make every slot block its own neighbour and leave a contiguous
  grid unbookable.
- Q: FR-024 filtered availability by the practitioner's existing appointments only — so a patient
  already booked elsewhere at that hour could be offered a slot that FR-016 then refuses. Which rule
  gives way? → A: Neither; availability becomes patient-relative. It is computed for a named patient
  and also removes slots colliding with that patient's own appointments, so the offer and the booking
  rules agree (FR-024, FR-025, SC-009).
- Q: FR-025 said the system must "never" offer a time a booking would reject, but the
  simultaneous-booking edge case requires the race loser to be told the slot is taken — a rejected
  offer. → A: FR-025 is a guarantee about the moment of offering, now stated as such. Losing a race
  to another patient is the one acceptable cause of an offered-then-rejected time; anything else is a
  defect.
- Q: Free-form specialties, or a fixed list after all? → A: A fixed list of ten, superseding both
  earlier answers: Cardiology, Dentistry, Dermatology, General Practice, Gynecology, Neurology,
  Ophthalmology, Orthopedics, Pediatrics, Psychiatry. A practitioner holds exactly one. New
  practitioners default to General Practice and the value can be changed later to any other on the
  list — presented, once a management screen exists, as a name-sorted single-select dropdown. Nothing
  outside the list is accepted, and there is no "other" escape hatch (FR-005, FR-060).
- Q: Is an appointment starting at exactly the current local date-time "in the past"? → A: Yes —
  treated as past and refused (FR-020). The deciding factor is not what "past" means literally but
  that FR-031 lists only appointments starting *strictly* after the same clock: accepting the
  boundary would let a patient book an appointment that instantly failed to appear in their own list.
- Q: Is the 90-day horizon measured from the date or the exact time, against the start or the end,
  and is exactly 90 days in or out? → A: From the exact local date-time, against the start, inclusive
  at the boundary (FR-021). A start exactly 90 days out is bookable; a second later is not.
- Q: FR-023 said grandfathered appointments are "excluded from availability listings" — excluded from
  the offered slots, or from the overlap check that computes them? → A: From the offered slots only.
  They are counted in full when deciding which slots are free (FR-023, FR-024); the other reading
  would have the system offer a slot the booking rules then refuse.
- Q: A start that falls inside no working range at all — is that refused as outside-schedule or as
  off-grid, and what decides which reason a patient hears when several rules are broken at once? →
  A: Outside-schedule; off-grid presupposes a range to be off the grid of. The full precedence over
  all eight refusal reasons is now fixed (FR-065), so the reason is a property of the request rather
  than of the order the implementation checks things in.
- Q: What does a patient see when they name a practitioner or patient belonging to another session? →
  A: Exactly what they would see if it did not exist (FR-066). A distinct "that's not yours" refusal
  would confirm a guessed identifier is real, which is the leak FR-002 exists to prevent.
- Q: Is the period an availability request may cover bounded anywhere? → A: Yes — 14 days and 50
  returned start times, clamped rather than refused, with truncation reported (FR-067). The caps were
  already in the plan; nothing in the spec required them, so nothing stopped them drifting.
- Q: Are two contiguous working ranges (08:00–12:00, 12:00–16:00) allowed, and may an appointment
  span their junction? → A: Allowed, and no (FR-006, FR-018). Each range keeps its own grid, so the
  junction is where one grid ends and the next begins. Relatedly, a working range shorter than the
  practitioner's appointment duration simply yields no slots, exactly as a trailing remainder does
  (FR-019).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Book an appointment by chatting (Priority: P1)

A patient describes what they want in plain language — "I'd like to see a dentist next Tuesday
afternoon" — and the assistant offers real, bookable times from a real practitioner's calendar. The
patient picks one, confirms, and the appointment exists. No forms, no identifiers, no calendar
widget.

**Why this priority**: This is the reason the phase exists. Everything else in 1c either feeds this
flow (practitioners and patients to book between) or reports on its results.

**Independent Test**: With a session that already has a patient and at least one practitioner, hold
a conversation that ends in a booking, then verify from outside the conversation that the
appointment exists with the expected patient, practitioner, and time.

**Acceptance Scenarios**:

1. **Given** a session with a patient and a practitioner who works Tuesdays, **When** the patient
   asks to book with that practitioner for next Tuesday, **Then** the assistant offers only times
   that are inside the practitioner's working hours, unbooked, free in the patient's own calendar,
   and in the future.
2. **Given** the assistant has offered a set of times, **When** the patient chooses one and confirms,
   **Then** the appointment is created and the assistant confirms it back, stating the practitioner
   and the time in local time.
3. **Given** the assistant has offered a set of times, **When** the patient chooses one but does not
   confirm, **Then** no appointment is created.
4. **Given** the patient asks for a time that is outside the practitioner's working hours, already
   taken, or in the past, **When** the assistant responds, **Then** it explains why that time is
   unavailable and offers alternatives, without creating anything.
5. **Given** the patient already has an appointment at 10:00, **When** they ask about a different
   practitioner's availability that day, **Then** 10:00 is not among the times offered; and **When**
   they name 10:00 anyway, **Then** the booking is refused and the conflict is explained.
6. **Given** the booking attempt fails for any reason, **When** the assistant responds, **Then** it
   never states or implies that an appointment was made.
7. **Given** a booking was written but its confirmation was lost, **When** the attempt is retried,
   **Then** the patient is told their appointment is booked, exactly one appointment exists, and
   they are never told their own booking conflicts with itself.
8. **Given** a session with two dentists, **When** the patient asks to see "a dentist", **Then** the
   assistant lists both and asks which one, and offers no times until the patient has chosen.
9. **Given** a session with no dentist, **When** the patient asks to see one, **Then** the assistant
   says none is available and names the specialties that are, without offering times.

---

### User Story 2 - A first visit produces a usable clinic, even when scheduling is down (Priority: P2)

Someone opens the site for the first time and can immediately start a real conversation. Behind the
scenes they get a named patient identity and two practitioners of different specialties to book
with, so the very first thing they try can be a booking. If the scheduling capability happens to be unavailable, the
visit still works — they get a chat and grounded FAQ answers, just no booking yet.

**Why this priority**: Without provisioning there is nobody to book and nobody to book with, so
Story 1 has no data to act on. It is P2 rather than P1 only because Story 1 can be tested against
manually created data.

**Independent Test**: Open the site with no prior state and confirm a session, a chat, a named
patient, and both default practitioners all exist. Repeat with the scheduling capability stopped and confirm
the chat is still created and still answers an FAQ question.

**Acceptance Scenarios**:

1. **Given** a visitor with no prior session, **When** they open the site, **Then** a session, one
   chat, one patient with a name from the writer pool, and two practitioners — a general
   practitioner and a dentist — with names from the practitioner pool are created.
2. **Given** a first-time visitor, **When** the scheduling capability is unavailable, **Then** the
   chat is still created and still answers FAQ questions, the patient is shown as unnamed, and the
   patient record is created on a later interaction once scheduling recovers.
3. **Given** a chat whose patient record does not yet exist, **When** the visitor asks to book,
   **Then** the assistant states plainly that booking is temporarily unavailable and does not
   fabricate a result.
4. **Given** a returning visitor with an existing session, **When** they open the site, **Then** no
   duplicate patient or practitioner is created.

---

### User Story 3 - Ask who's available and what I've booked (Priority: P3)

A patient asks "what specialists do you have?" and gets the practitioners belonging to their own
session. They ask "what appointments do I have booked?" and get their own appointments, in their own
local time.

**Why this priority**: Read-only, low-risk, and independently useful — but a patient can complete a
booking without ever asking either question.

**Independent Test**: In a session with two practitioners and one booked appointment, ask both
questions and check the answers match the stored data exactly, with nothing from another session or
another patient.

**Acceptance Scenarios**:

1. **Given** a session with several practitioners, **When** the patient asks what specialists are
   available, **Then** the assistant lists exactly that session's practitioners with their
   specialties.
2. **Given** a patient with booked appointments, **When** they ask what they have booked, **Then**
   the assistant lists exactly their own appointments, with practitioner and time in local time.
3. **Given** a patient with no appointments, **When** they ask what they have booked, **Then** the
   assistant says they have none, rather than showing someone else's or an empty-looking error.
4. **Given** a patient whose only appointment has already started, **When** they ask what they have
   booked, **Then** the assistant says they have nothing upcoming and does not list the past one.
5. **Given** a session with two patients who each have appointments, **When** one of them asks what
   they have booked, **Then** only that patient's own appointments are listed.
6. **Given** two sessions each with their own practitioners and appointments, **When** either asks
   these questions, **Then** neither sees any of the other's data.

---

### User Story 4 - Run several patients from one browser (Priority: P4)

The app user is demonstrating the clinic, not being a single patient. They want several people on
the go at once: a list of their conversations, each belonging to a differently-named patient, the
ability to start another one, switch between them, and remove one completely when they are done
with it.

**Why this priority**: The per-session name pool, the per-session uniqueness rule, and per-patient
appointment lists all only become observable once a session holds more than one patient. It is
below the booking stories because a single seeded chat is enough to exercise those.

**Independent Test**: From one browser, create three chats, confirm each has a distinct patient
name, send a message in each, switch between them and confirm each shows its own history, then
delete one and confirm its patient and appointments are gone while the others are untouched.

**Acceptance Scenarios**:

1. **Given** a session with one chat, **When** the user creates another, **Then** a new chat and a
   new patient are created with a name distinct from every other patient in that session.
2. **Given** a session with several chats, **When** the user views their list, **Then** every chat in
   that session is listed by its patient's name, and no chat from another session appears.
3. **Given** two chats created while scheduling was unavailable, **When** the user views their list,
   **Then** both appear, are told apart by their creation times, and show their patients' real names
   once scheduling recovers.
4. **Given** a session with several chats of differing activity, **When** the user reloads the app,
   **Then** the chat holding the newest message is the one shown.
5. **Given** several chats, **When** the user switches to one, **Then** they see that chat's own
   message history and continue that patient's conversation, with no bleed from any other chat.
6. **Given** a chat whose patient has appointments, **When** the user deletes it, **Then** the chat,
   its messages, its patient, and that patient's appointments are all removed together, and the
   other chats in the session are unaffected.
7. **Given** a session whose only chat is deleted, **When** the deletion completes, **Then** the
   session survives with no chats at all, the chat area is muted, and the user is left able to
   create a new chat.
8. **Given** a practitioner shared by the session, **When** two different patients in that session
   book with them, **Then** both bookings are visible to their own patients and neither can take a
   time the other already holds.

---

### User Story 5 - Manage practitioners and patient names directly (Priority: P5)

The app user adds a second practitioner, gives one a different specialty, corrects a schedule, or
renames a patient — through a programmatic interface, with sensible names proposed automatically.
There is no screen for this.

**Why this priority**: Needed to set up richer scenarios and to demonstrate the full lifecycle, but
the seeded data plus Story 4's chat list is enough for every other story.

**Independent Test**: Create, edit, and delete a practitioner and rename a patient through the
interface, then verify the effects — including cascading appointment deletion — without touching the
chat UI.

**Acceptance Scenarios**:

1. **Given** an existing session, **When** the user creates a practitioner supplying nothing at all,
   **Then** it is created with the next unused pool name, General Practice, Monday–Friday
   09:00–17:00, and 60-minute appointments, and is immediately bookable.
2. **Given** an existing practitioner, **When** the user narrows their schedule so an existing
   appointment now falls outside it, **Then** the edit succeeds, that appointment keeps its original
   time and length, and new bookings are validated against the new schedule.
3. **Given** a practitioner with appointments, **When** the practitioner is deleted, **Then** those
   appointments are deleted with them, and the affected patients' other appointments are untouched.
4. **Given** a session that already has a patient named "Mark Twain", **When** the user renames
   another patient to "Mark Twain", **Then** the rename is refused.
5. **Given** a session belonging to another user, **When** the user tries to read or change anything
   in it, **Then** the attempt is refused.

---

### Edge Cases

- **Pool exhaustion**: a session that already holds all 100 writer names gets the next patient named
  by starting the pool over with " 2" appended — so the 101st is the first pool name plus " 2", the
  102nd the second plus " 2", and the 201st the first plus " 3". The same applies to the 20-name
  practitioner pool.
- **Cross-session name collision**: two different sessions may each have a patient named "Mark
  Twain"; uniqueness is only ever enforced within a session.
- **Simultaneous booking of the same slot**: if two attempts race for one practitioner's slot, at
  most one succeeds and the loser is told the slot is taken — never both succeeding, never a
  silently dropped booking.
- **Grandfathered appointments still occupy their slot**: an appointment left outside its
  practitioner's schedule by an edit still blocks overlapping bookings; it is invisible to
  availability listings but not to the overlap rules.
- **Remainder time in a working block**: a practitioner working 8:00–12:30 with a 60-minute duration
  offers 8:00 through 11:00; the trailing 30 minutes is never offered.
- **Relative dates near midnight**: "tomorrow" is resolved against the user's current local date as
  supplied by the client, not against the server's clock — the two can be on different calendar days.
- **A user whose device clock is wrong**: they may be offered or refused slots that look wrong to
  everyone else, but the damage is confined to their own session — no other session's availability
  or bookings are affected.
- **Booking the current day**: slots whose start time is not strictly after the user's current local
  date-time are not offered — including the slot starting at exactly that time, which is refused as
  in the past (FR-020) rather than sold as the last bookable moment.
- **A duration no working range can hold**: a practitioner with a 120-minute duration whose only
  range is 60 minutes long offers nothing at all — the range holds no whole slot, exactly as a
  trailing remainder does (FR-019). They are still listed among the session's specialists, and the
  assistant says they have no bookable times, as it does for an empty schedule.
- **Contiguous working ranges**: 08:00–12:00 and 12:00–16:00 on one weekday are permitted (they touch
  but do not overlap, FR-006), yet stay two ranges: each grid is anchored at its own range's start
  and no appointment may cross 12:00 (FR-018). A 90-minute practitioner therefore offers 08:00 and
  09:30, then nothing until 12:00 and 13:30 — the 11:00–12:00 and 15:00–16:00 remainders are lost.
- **A time asked for outside every working range**: refused as outside the schedule, never as off the
  grid (FR-065) — there is no range whose grid it could be off.
- **An identifier from another session**: a booking naming a practitioner or patient that exists but
  belongs to someone else's session is refused exactly as if no such record existed (FR-066), so
  nothing in the reply tells the caller which of the two it was.
- **An availability question wider than the cap**: "anything in the next three months?" is answered
  for the first 14 days and marked truncated (FR-067); the assistant offers to look further ahead
  rather than implying those are the only times the practitioner has.
- **An appointment that has already started**: it stops appearing in the patient's list the moment
  its start time passes, but still counts for overlap checks so a later booking cannot be placed on
  top of it.
- **A practitioner with an empty schedule**: appears in "what specialists do you have?" but offers no
  bookable times, and the assistant says so rather than returning a blank list.
- **Every matching practitioner is fully booked** for the period the patient asked about: the
  assistant says so and offers the nearest times outside that period rather than an empty list.
- **A patient booking with two practitioners at once**: prevented twice over — the clashing slot is
  never offered in the first place, because availability is computed for that patient and removes
  their own commitments (FR-024); and if they name the time anyway it is refused, because a patient
  cannot be in two places at once regardless of which practitioners are involved (FR-016).
- **Back-to-back appointments**: a patient may hold 09:00–10:00 with one practitioner and 10:00–11:00
  with another; these do not overlap (FR-061) and both are offered and bookable.
- **Deleting the chat currently being viewed**: the user is moved to another chat in the session; if
  it was the last one, the session is left with no chats and the chat area is muted until the user
  creates one.
- **Deleting a chat mid-turn**: the deletion goes through, the streaming reply is abandoned with
  nothing recorded, and no appointment is left behind whichever side of the deletion the booking
  landed on.
- **Creating a chat while scheduling is unavailable**: the chat is created unnamed and works for FAQ,
  exactly as on a first visit; several such chats stay distinguishable in the list by creation time
  and pick up their real names once scheduling recovers.
- **Scheduling becomes unavailable mid-conversation**: the assistant says booking is temporarily
  unavailable and continues to answer FAQ questions.

## Requirements *(mandatory)*

### Functional Requirements

#### Scheduling domain

- **FR-001**: The system MUST represent an appointment as a patient, a practitioner, a start time,
  an end time, and the owning session.
- **FR-002**: The system MUST scope every chat, patient, practitioner, and appointment to exactly one
  session, and MUST never expose any of them to a different session.
- **FR-003**: The system MUST tie each patient to exactly one chat and each chat to exactly one
  patient, permanently — a patient is never reassigned to another chat and never gains a second one.
- **FR-004**: The system MUST represent a practitioner as a full name, a specialty, a weekly working
  schedule, a fixed appointment duration, and the owning session.
- **FR-005**: The system MUST ship a fixed list of ten practitioner specialties: Cardiology,
  Dentistry, Dermatology, General Practice, Gynecology, Neurology, Ophthalmology, Orthopedics,
  Pediatrics, and Psychiatry. A practitioner MUST hold exactly one of them — never two, never a value
  outside the list. A new practitioner created without a specialty MUST default to General Practice
  (FR-057); a user MUST be able to change it afterwards to any other specialty in the list, and any
  attempt to set one outside the list MUST be refused.
- **FR-060**: The system MUST expose the FR-005 list, sorted by name, so a chooser can be populated
  from it rather than from a copy of the list embedded in the client. The eventual practitioner-
  editing screen presents the specialty as a single-select dropdown over exactly this list; that
  screen is not part of this phase (FR-048 ships no user interface), so what this phase delivers is
  the list, its ordering, and the refusal of anything outside it.
- **FR-006**: The system MUST express a weekly working schedule as zero or more non-overlapping
  time ranges per weekday, so a practitioner can work split shifts or not at all on a given day.
  Two ranges on one weekday MAY be contiguous (08:00–12:00 and 12:00–16:00): touching at an endpoint
  is not overlapping, and they remain two ranges rather than becoming one — each keeps its own slot
  grid (FR-019) and neither may host an appointment that crosses the junction (FR-018).
- **FR-007**: The system MUST default a new practitioner's appointment duration to 60 minutes and
  MUST use that practitioner's duration as the length of every appointment booked with them.
- **FR-008**: An appointment MUST reference a patient and a practitioner belonging to the same
  session.
- **FR-009**: All practitioners in a session MUST be bookable by every patient in that session.

#### Names

- **FR-010**: The system MUST ship a predefined pool of 100 internationally recognized writers who
  died more than 50 years ago, used for patient names, and a predefined pool of 20 internationally
  recognized physicians who died more than 50 years ago, used for practitioner names.
- **FR-011**: The system MUST name each newly created patient with the first name in the writer pool
  not already used by a patient in the same session, taking the pool strictly in order.
- **FR-012**: The system MUST guarantee that no two patients in one session share a full name, and
  likewise no two practitioners in one session.
- **FR-013**: When a session has exhausted a pool, the system MUST keep taking names in pool order,
  appending " 2" to each on the second pass, " 3" on the third, and so on ("Mark Twain", "Mark Twain
  2", "Mark Twain 3"), continuing to satisfy FR-012. Name assignment MUST be fully deterministic:
  the same sequence of creations in a session always yields the same sequence of names.
- **FR-014**: The system MUST allow the same full name to exist in two different sessions.
- **FR-015**: When creating a practitioner without an explicit name, the system MUST propose the next
  unused pool name; when a name is supplied explicitly, the system MUST use it, subject to FR-012.
- **FR-057**: Creating a practitioner MUST require no fields at all: an omitted specialty defaults to
  General Practice, an omitted schedule to Monday–Friday 09:00–17:00 local time, an omitted
  duration to 60 minutes (FR-007), and an omitted name to the next unused pool name
  (FR-015) — so a practitioner created with nothing supplied is immediately bookable. Every field
  MUST be overridable, and first-visit seeding MUST use these same defaults.

#### Booking integrity

- **FR-016**: The system MUST prevent a patient from holding two appointments whose times overlap,
  and MUST enforce this in the datastore itself rather than only in application logic, so concurrent
  attempts cannot both succeed.
- **FR-017**: The system MUST prevent a practitioner from holding two appointments whose times
  overlap, enforced the same way as FR-016.
- **FR-061**: An appointment MUST occupy its start time up to but **not including** its end time, so
  two appointments where one ends exactly when the other begins do NOT overlap for the purposes of
  FR-016 and FR-017. Back-to-back bookings on consecutive grid slots (FR-019) are therefore always
  permitted; without this rule every slot would block its own neighbour and a contiguous grid would
  be unbookable.
- **FR-018**: The system MUST reject, at the moment an appointment is created, any appointment that
  does not fall entirely within a **single** working range of its practitioner. An appointment MUST
  NOT span two ranges even when they are contiguous (FR-006) — a practitioner working 08:00–12:00 and
  12:00–16:00 accepts nothing that crosses 12:00.
- **FR-019**: The system MUST reject an appointment whose start time is not on the grid of
  consecutive slots running from the start of the working range that contains it, each slot being
  one appointment duration long. Every working range has its own grid, anchored at that range's own
  start, so contiguous ranges restart the grid at their junction. A slot counts only if it fits
  whole inside its range: a range shorter than one appointment duration therefore yields no slots at
  all, exactly as a trailing remainder does.
- **FR-020**: The system MUST reject an appointment whose start time is not **strictly** after
  FR-058's clock — a start earlier than the user's current local date-time is rejected, and so is one
  exactly equal to it. The boundary falls this way so that booking and listing agree: FR-031 lists
  only appointments starting strictly after that same clock, so accepting a start of exactly "now"
  would create an appointment the patient could never see listed.
- **FR-021**: The system MUST reject an appointment whose start time falls more than 90 days after
  FR-058's clock. The horizon is measured against the appointment's **start**, never its end, and as
  an exact local date-time — the clock's own time of day, 90 calendar days later — not as a whole-day
  boundary. A start exactly 90 days after the clock is therefore inside the horizon; one second later
  is outside.
- **FR-058**: Every judgement about whether a time is in the past, is still upcoming, or falls within
  the booking horizon MUST be made against the user's current local date and time as supplied by the
  client (FR-032), never against the server's own clock — stored times are local wall-clock times, so
  the user's clock is the only one comparable to them.
- **FR-022**: When an edit to a practitioner invalidates existing appointments, the system MUST
  accept the edit and leave those appointments unchanged, applying the new settings only to
  subsequent bookings.
- **FR-023**: A grandfathered appointment — one left outside its practitioner's current schedule or
  duration by an FR-022 edit — MUST be counted in full **both** when checking overlaps (FR-016,
  FR-017) **and** when computing availability (FR-024): a slot that overlaps it is dropped like any
  other conflict. What "excluded from availability listings" means is only that the grandfathered
  appointment's own time is not itself offered, which follows from the grid being generated from the
  practitioner's *current* working ranges — it does **not** mean the appointment is ignored while
  deciding which slots are free. The opposite reading would offer a slot that a booking attempt then
  refuses, breaking FR-025 and permitting a double booking to be proposed to the patient.
- **FR-051**: Every booking attempt MUST carry a caller-supplied idempotency key. A repeated attempt
  bearing a key already used, **and asking for the same patient, practitioner, and start time as the
  attempt that used it**, MUST return that original appointment as a success — never a second
  appointment and never a conflict — so that a booking whose confirmation was lost in transit is
  reported to the patient exactly once, correctly.
- **FR-062**: Two booking attempts for the same patient, practitioner, and start time MUST carry the
  same idempotency key, and attempts differing in any of those three MUST carry different keys — so
  that a booking re-issued in a later turn, after its confirmation was lost, is still recognized as
  the same booking rather than becoming a second one.
- **FR-063**: When an attempt presents a key already used but asks for a *different* patient,
  practitioner, or start time, the system MUST refuse it: it MUST NOT return the stored appointment,
  MUST NOT create a second one, and MUST NOT report anything as booked. The mismatch means the caller
  computed a key that violates FR-062, so it is reported as a caller error rather than a reason the
  patient is asked to choose differently, and the patient is told only that the booking could not be
  completed and nothing was created (FR-028).
- **FR-064**: An idempotency key MUST be recorded only when an appointment is actually created. A
  refused attempt MUST NOT consume its key, so a later attempt bearing that key is evaluated afresh
  rather than replaying the refusal. A key MUST be scoped globally rather than per session or per
  patient, and MUST live exactly as long as the appointment that recorded it — there is no expiry, no
  reuse window, and nothing to clean up.
- **FR-065**: The system MUST refuse a booking with exactly one reason, drawn from a closed set of
  eight, and MUST choose it by a fixed precedence when an attempt violates more than one rule — so
  the explanation FR-029 owes the patient is deterministic rather than dependent on the order the
  implementation happened to check things in:
  1. **practitioner not found** / **patient not found** — no such practitioner or patient in this
     attempt's session (FR-008, FR-066), including a patient deleted mid-turn (FR-055);
  2. **in the past** — the start is not strictly in the future (FR-020);
  3. **beyond the horizon** — the start is more than 90 days out (FR-021);
  4. **outside the schedule** — the appointment does not lie entirely inside a single working range
     (FR-018). A start that falls inside *no* working range at all is this refusal, never an
     off-grid one; off-grid presupposes a range to be off the grid of;
  5. **off grid** — the start lies inside a working range but not on that range's slot grid (FR-019);
  6. **practitioner busy** / **patient busy** — the time overlaps an appointment already held by that
     practitioner (FR-017) or by that patient (FR-016). These two are decided by the datastore at the
     moment of insert; when one attempt collides with both at once, either may be reported.

  Every refusal reason a patient can be given MUST be one of these eight, and each maps to exactly
  one rule above — no refusal exists that no requirement produces, and no rejection rule lacks a
  reason to report it with.
- **FR-066**: A patient or practitioner identifier belonging to a *different* session MUST be treated
  exactly as one that does not exist: the same refusal reason, revealing nothing that distinguishes
  "not yours" from "not there". This is what makes FR-002 hold against a caller who guesses an
  identifier — a distinct "belongs to another session" refusal would confirm that the identifier is
  real.

#### Availability

- **FR-024**: The system MUST be able to report a practitioner's bookable start times for a
  requested period **for a named patient**, being exactly those grid slots that lie fully inside a
  working range, do not overlap an appointment already held by that practitioner, **do not overlap an
  appointment already held by that patient with any practitioner** — grandfathered appointments
  included, on both counts (FR-023) — start strictly after FR-058's clock (FR-020), and are within
  the booking horizon (FR-021). Every one of those predicates MUST be evaluated exactly as the
  matching rejection rule evaluates it, boundaries included; that identity is what FR-025 rests on.
  Availability is therefore always patient-relative: the same practitioner's free slots differ
  between two patients in one session, because each patient's own commitments remove different slots
  (FR-016).
- **FR-025**: Every start time the system offers MUST be one that a booking attempt made *at the
  moment of the offer* would accept — the system MUST NOT offer a time that any rule in FR-016 to
  FR-021 would reject. This is a guarantee about the moment of offering, not a promise that the slot
  stays free: another patient may take an offered slot before this patient confirms it, in which case
  the booking is refused and explained like any other conflict (FR-029, and the simultaneous-booking
  edge case). No other cause of an offered-then-rejected time is acceptable.
- **FR-067**: A request for a practitioner's bookable times MUST be bounded on both axes: the period
  one request may cover is capped at **14 days**, and the number of start times returned at **50**.
  A request exceeding either cap MUST be clamped and answered, never refused, and the answer MUST
  state whether it was truncated — so the assistant can narrow the question or offer to look further
  ahead rather than presenting a partial list as if it were the practitioner's whole availability.
  Without the caps, "sometime in the next few months" would return hundreds of times, and nothing
  would distinguish a fully-booked window (an empty, untruncated answer) from one whose end was
  never reached.

#### Conversation

- **FR-026**: Patients MUST be able to book an appointment through conversation alone, without
  supplying or seeing any internal identifier.
- **FR-027**: The assistant MUST obtain the patient's explicit confirmation of a specific
  practitioner and time before creating an appointment.
- **FR-028**: The assistant MUST NOT state or imply that an appointment was created unless it
  actually was.
- **FR-029**: When a booking is refused, the assistant MUST explain the reason in plain language and
  offer alternatives where any exist.
- **FR-052**: When a patient's request matches more than one practitioner, the assistant MUST list
  the matching practitioners and obtain the patient's choice before offering times, and MUST NOT
  select a practitioner on the patient's behalf.
- **FR-053**: When a patient's request matches no practitioner in their session, the assistant MUST
  say so and name the specialties that are available, rather than offering times with an unrelated
  practitioner or inventing one.
- **FR-030**: On request, the assistant MUST list the practitioners belonging to the current session
  with their specialties.
- **FR-031**: On request, the assistant MUST list the current chat's patient's own upcoming
  appointments — those whose start time is still in the future — earliest first, with practitioner
  and time. Appointments that have already started MUST NOT be listed.
- **FR-032**: The assistant MUST be given the user's current local date and time, supplied by the
  client, so relative expressions such as "tomorrow" or "next Tuesday at 3" resolve to the intended
  calendar date and time rather than against the server's clock.
- **FR-033**: The system MUST treat every time it stores or shows — working schedules, appointment
  start and end, offered slots, confirmations, and appointment listings — as a plain local date-time
  with no timezone attached, and MUST NOT convert between timezones anywhere.
- **FR-034**: A message that is not about scheduling MUST continue to be answered by the existing
  grounded FAQ path, unchanged.

#### Chats and patients

- **FR-035**: A session MUST be able to hold many chats at once, each with its own patient.
- **FR-036**: Users MUST be able to list the chats in their own session, identified by their
  patients' names.
- **FR-054**: A chat whose patient record does not exist yet MUST still appear in the list,
  distinguishable from every other such chat by a placeholder label and its creation time, and MUST
  start showing its patient's real name once that record is created. Creating a chat MUST remain
  available while the scheduling capability is unavailable.
- **FR-037**: Users MUST be able to create a new chat, which also creates its patient (FR-003,
  FR-011).
- **FR-038**: Users MUST be able to switch between the chats in their session, each showing only its
  own message history.
- **FR-056**: On loading the app, the system MUST open the session's most recently active chat — the
  one holding the newest message — falling back to the most recently created chat when none of them
  has any messages yet.
- **FR-039**: The system MUST provide exactly one deletion operation covering a chat, its messages,
  its patient, and that patient's appointments — removing all of them together. There MUST be no
  separate operation that deletes only the chat or only the patient. This replaces the existing
  clear-the-current-chat behavior.
- **FR-040**: A session MUST be able to exist with no chats at all. Deleting the last chat MUST NOT
  provision a replacement and MUST NOT end the session — the session persists until the user creates
  a new chat.
- **FR-055**: Deleting a chat MUST succeed while a turn is in flight, abandoning that turn without
  recording an assistant reply. An appointment MUST never outlive the patient it belongs to: a
  booking completed just before the deletion is removed with it, and a booking arriving after it is
  rejected rather than creating an appointment with no patient.
- **FR-041**: While the current session has no chats, the system MUST mute the chat area — message
  history and message sending are unavailable and visibly inactive — while leaving the user able to
  create a chat.

#### Provisioning and degraded operation

- **FR-042**: On a visitor's first arrival, the system MUST create a session, one chat, one patient,
  and two practitioners — a general practitioner working Monday to Friday 09:00–17:00 and a
  dentist working Monday to Saturday 09:00–14:00. Two, differing in both specialty and hours, so a
  first booking request has a practitioner to choose rather than a single possible answer.
- **FR-043**: The system MUST NOT store a timezone for a session, a patient, or a practitioner.
  Everyone reachable from one session is assumed to share a single local time, so no timezone
  identifier is captured, stored, or configurable anywhere.
- **FR-044**: The system MUST create a chat successfully even when the scheduling capability is
  unavailable, leaving that chat's patient unnamed, and MUST keep FAQ answering fully functional in
  that state.
- **FR-045**: The system MUST create a missing patient record on a later interaction once the
  scheduling capability is reachable again, without creating a duplicate if an earlier attempt
  actually succeeded.
- **FR-046**: When the scheduling capability is unreachable, the assistant MUST tell the patient that
  booking and appointment lookup are temporarily unavailable, rather than failing silently or
  inventing an answer.
- **FR-047**: Each call to the scheduling capability MUST time out after 2 seconds and MUST be
  attempted at most twice (one retry), so a patient receives the FR-046 "temporarily unavailable"
  reply within 5 seconds rather than waiting on an unresponsive dependency. Retrying a booking write
  is safe because of FR-051.

#### Direct management

- **FR-048**: The system MUST provide a programmatic interface, scoped to the caller's own session,
  for creating, editing, and deleting practitioners, and for editing patients. No user interface is
  delivered for it in this phase.
- **FR-049**: Deleting a practitioner MUST delete that practitioner's appointments.
- **FR-050**: The management interface MUST refuse an edit that would violate FR-012.

#### Datastore naming

- **FR-059**: Each service's database MUST be named for the service that owns it. The chat service's
  database MUST be renamed from `visitdoc` to `visitdoc_chat`, and its test database from
  `visitdoc_test` to `visitdoc_chat_test`; the scheduling service's MUST be `visitdoc_scheduler` and
  `visitdoc_scheduler_test`. Every place that names either database — local environment
  configuration, container provisioning, the test harness, continuous integration, and the
  documentation describing them — MUST be updated in the same change, so no configuration still
  points at the old name.

### Key Entities

- **Patient**: a person who holds appointments, permanently paired one-to-one with a chat and owned
  by one session. Has a full name unique within its session. Referenced from the chat side, and
  references its chat, by opaque identifier only.
- **Practitioner**: someone appointments can be booked with, owned by one session and bookable by
  every patient in it. Has a full name unique within its session, a specialty, a weekly working
  schedule, and a fixed appointment duration.
- **Working range**: one continuous span of time on one weekday during which a practitioner accepts
  appointments. A practitioner's schedule is a set of these; several may fall on the same weekday
  as long as they do not overlap.
- **Appointment**: a booking of one patient with one practitioner over a specific time span, owned
  by one session. Its length equals the practitioner's appointment duration at the moment it was
  created.
- **Name pool**: a fixed, predefined list of full names shipped with the application — 100 writers
  for patients, 20 physicians for practitioners — from which new names are proposed.
- **Session** *(existing)*: the app user's identity, scoped to one browser. Extended in this phase to
  hold many chats. Owns every chat, patient, practitioner, and appointment created under it, all of
  which share one local time.
- **Chat** *(existing)*: one conversation thread within a session, now one of many. Extended in this
  phase to reference its patient.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A first-time visitor can go from opening the site to a confirmed appointment in under
  two minutes of conversation, without filling in a form or seeing any internal identifier.
- **SC-002**: Every attempt to create an overlapping appointment is rejected — for the same patient
  and for the same practitioner — including when two attempts race for the same slot, where exactly
  one succeeds and the other is told the slot is taken. Zero double bookings across the test suite.
- **SC-003**: Zero booking attempts succeed that fall outside the practitioner's working ranges, or
  off that range's slot grid, as evaluated against the practitioner's settings at the instant of the
  attempt. Measured at creation time — over every attempt the test suite makes, none of the accepted
  ones violated either rule when it was accepted. It is deliberately **not** an audit of stored rows:
  FR-022 grandfathers appointments through a later schedule or duration edit, and nothing records the
  settings that were in force when a row was written, so a stored appointment lying outside its
  practitioner's current schedule is a correct outcome rather than a violation.
- **SC-004**: No user ever sees a chat, patient, practitioner, or appointment belonging to another
  session, and no chat ever shows another patient's messages or appointments. Zero cross-session and
  zero cross-patient disclosures across the test suite.
- **SC-005**: With the scheduling capability stopped, 100% of visitors still get a working chat and a
  grounded FAQ answer, and zero chat creations fail.
- **SC-006**: Relative date expressions ("tomorrow", "next Tuesday at 3") resolve to the intended
  calendar date in 100% of tested cases, including when the user's local date differs from the
  server's. No time is ever converted between timezones anywhere in the system.
- **SC-007**: One session can create 100 chats and receive the 100 pool names in order; the 101st
  receives the first pool name with " 2" appended, rather than a collision or a failure. Repeating
  the same sequence of creations in a fresh session produces the identical sequence of names.
- **SC-008**: The assistant never reports an appointment as booked unless it exists — zero fabricated
  confirmations across the test suite, including runs where the scheduling capability is
  deliberately made unavailable mid-conversation.
- **SC-009**: Every start time offered to a patient is bookable at the moment it is offered — zero
  offered slots that an immediate booking attempt would reject, including zero slots that collide
  with an appointment the same patient already holds with a different practitioner. A slot taken by
  someone else between the offer and the patient's confirmation is excluded from this count; it is
  the race FR-025 and SC-002 already govern.
- **SC-010**: A booking conversation completes without the patient having to restate the
  practitioner, the date, or the time they already gave.
- **SC-011**: Deleting a chat removes its patient and all of that patient's appointments, and leaves
  every other chat, patient, and appointment in the session intact — verified across a session
  holding at least three chats.
- **SC-012**: Retrying a booking whose confirmation was lost produces zero duplicate appointments and
  zero false conflict reports, across every retry point in the flow.
- **SC-013**: When the scheduling capability stops responding entirely, the patient receives the
  "temporarily unavailable" reply within 5 seconds of sending their message, in 100% of attempts.

## Assumptions

- **Booking only.** Cancelling and rescheduling an appointment through conversation are Phase 1d.
  During this phase the only way to remove a mis-booked appointment is to delete the whole chat, and
  that is accepted.
- **No staff, no escalation.** Both are Phase 1d. Conversations remain two-party.
- **No calendar exceptions.** Holidays, vacations, sick days, and one-off overrides to a weekly
  schedule are out of scope; a practitioner's availability is fully described by their recurring
  weekly ranges.
- **Booking horizon of 90 days**, chosen as a plausible clinic default since none was specified.
  Its boundary is inclusive and measured as an exact date-time (FR-021) because a rule stated in days
  has to land somewhere, and "90 days from this moment" is the reading that needs no extra
  vocabulary about calendar days.
- **Availability requests are capped at 14 days and 50 start times** (FR-067). Both numbers are
  chosen, not derived: 14 days covers every phrasing a patient realistically uses in one breath
  ("this week", "next week", "the week after"), and 50 starts is well beyond what is useful to read
  back in a conversation. They exist to bound a request, not to express a clinic policy, and either
  can be raised without touching any other rule.
- **Confirmation before booking is required** (FR-027). Chosen deliberately because there is no
  in-chat way to undo a booking in this phase.
- **A patient may book with any practitioner in their session**, with no eligibility, referral, or
  specialty-matching rules.
- **Matching a request to a specialty is done by meaning, not by string equality** (FR-005). A
  patient asking for "a dentist", "my teeth", or "a filling" never types the word "Dentistry", so the
  assistant interprets their words against the ten specialties rather than matching text. The closed
  list makes this easier than free text would, not unnecessary: there is still no synonym table, and
  the mapping from what a patient says to which specialty they mean is the model's judgement.
- **Ten specialties, chosen as plausible clinic defaults**, since the request named a count rather
  than a list. Dentistry and General Practice are load-bearing — the former because several
  acceptance scenarios turn on asking for a dentist, the latter because it is FR-057's default.
  Extending the list later is a one-line change with no migration, by design.
- **Deletion is hard deletion** — no soft-delete or archive tier for chats, patients, practitioners,
  or appointments.
- **The chat database rename (FR-059) is a local-environment change, not a data migration.** No
  deployed environment exists yet, so nothing needs a rename procedure beyond a one-line
  `ALTER DATABASE` for a developer who wants to keep their existing local data, or a volume reset for
  one who does not. It is not an Alembic migration and does not touch any table, column, or row.
- **The proposed pool name is a default, not a restriction**: the management interface accepts any
  full name the user supplies.
- **The seeded practitioner** created on first visit is created with nothing supplied, so it takes
  every default from FR-057 — first unused pool name, General Practice, Monday–Friday 09:00–17:00,
  60-minute appointments — none of which was specified in the original request.
- **The chat list UI is minimal** — enough to see the session's chats by patient name, switch between
  them, create one, and delete one. It is not a redesign of the chat screen.
- **No per-message timestamps.** Local time reaches the user through what the assistant says
  (FR-032, FR-033), not through chat chrome. Adding visible message timestamps is not part of this
  phase.
- **The app has no concept of a timezone.** Every patient, practitioner, and staff member reachable
  from one session shares a single local time, so no time is ever converted and no zone identifier
  is stored. Times are plain local date-times throughout, and the client supplies the user's current
  local date and time on each turn. The cost of this simplification is that a session opened from
  two devices in different timezones would read the same stored times as different moments — out of
  scope for a single-user demo session, and the reason the earlier stored-timezone design was
  dropped.
- **Existing behavior is preserved**: grounded FAQ answering, multi-turn conversation history, and
  intent classification from Phases 0–1b continue to work unchanged.
- **Mixed-intent messages** ("what should I bring, and can I book Friday?") are answered coherently.

  > **Superseded during planning** (research.md #2, plan.md Complexity Tracking). This assumption
  > originally deferred concurrent specialist handling with a merge step to Phase 1d. It is built
  > here instead: once this phase has two real specialists, routing an ordinary sentence carrying
  > both intents to only one of them ships a visibly partial answer. A single-specialist turn does
  > not pay for the merge — the sole specialist streams its own reply and the merge step is a no-op.

## Dependencies

- **Phase 1b's intent classification** must already distinguish booking-flavored messages, since the
  booking path is what its `booking` label now routes to.
- **Phase 1a's conversation history** must remain available, since a booking is negotiated across
  several turns.
- **Today's one-chat-per-session behavior is replaced**, not extended: the existing
  get-or-create-the-session's-chat and clear-the-current-chat behaviors give way to a real chat list
  (FR-035 to FR-041). Existing sessions must keep working across that change.
- **Scheduling is a separate service with its own datastore** (`docs/ROADMAP.md`, "The data boundary
  follows the invariants"), reachable synchronously from the core backend. This phase is the first
  time that boundary carries real traffic, so its failure behavior (FR-044 to FR-047) is part of the
  deliverable, not a follow-up.
