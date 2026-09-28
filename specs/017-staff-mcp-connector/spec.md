# Feature Specification: Staff in the loop from an AI assistant app (Phase 4a)

**Feature Branch**: `017-staff-mcp-connector`

**Created**: 2026-09-28

**Status**: Draft

**Input**: User description: "Create a spec for the Phase 4a. The Staff Console should get a new
tab (represented as a gray (as this feature is secondary for the app) gear icon, located on the
very right part of the tab bar) where a staff member can:
- see a short info that claude/openAI client can be paired to get two things...
- click a button to get a pairing code and a URL of the MCP server (currently nginx ip from the
  .env should be used)
- see a list of grants and be able to revoke them"

**Implements**: `docs/ROADMAP.md` Phase 4a — Staff in the loop from the Claude app.

## Why this exists

The staff console only helps a staff member who is looking at it. Once they step away, an
escalation or a burst of booking changes waits unseen until they come back. This feature lets a
staff member stay in the loop from the Claude app on their phone by asking two questions and
getting a number back:

1. How many conversations need staff attention right now.
2. How many appointments were booked, rescheduled or cancelled in the last N minutes or hours,
   answered as, for example, "Three appointments were booked, one cancelled and two rescheduled."

The app reaches VisitDoc as a **connector**: a server the assistant app calls on the staff
member's behalf. A connector that can read clinic data must know whose data it reads, and this
app has no staff login: the anonymous session is the only identity, and it owns both panes. So
the console becomes the place where a staff member **pairs** an assistant app with their session,
sees which apps are paired, and cuts any of them off.

**Three things this feature deliberately is not.**

- It does not return conversations. The two answers are counts. No patient name and no message
  text leaves through this surface, so nothing a patient wrote can reach the staff member's
  assistant as something that reads like an instruction. The details stay in the console.
- It does not let the assistant app change anything. There is no write capability to scope,
  hide or forget to guard.
- It does not add a staff login. The pairing code bridges the existing session to the assistant
  app. Accounts remain out of scope, as Phase 1d decided.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pair an assistant app with the session (Priority: P1)

At the console, the staff member opens the new gear tab at the far right of the tab bar. It
explains in a few lines that an AI assistant app can be connected to ask two questions while away
from the console. The staff member clicks **Get pairing code**. The tab shows the connector
address to add in the assistant app and a short, single-use code with a visible countdown. In the
assistant app they add the connector using that address and press connect. A VisitDoc page opens,
names the app asking for access and exactly what it may read, and asks for the code. They type it,
and the assistant app reports the connector as connected.

**Why this priority**: Nothing else in the feature works until an app is paired. It is also the
security-critical step: the code is what proves that the person connecting the app is the person
at this session's console.

**Independent Test**: Get a code in the console, complete the connect flow from a real assistant
app, and confirm the app shows the connector as connected and the console lists a new paired app.

**Acceptance Scenarios**:

1. **Given** the gear tab is open, **When** the staff member clicks *Get pairing code*, **Then**
   the tab shows the connector address and a code, with the time left before the code expires.
2. **Given** a valid unexpired code, **When** it is entered on the VisitDoc sign-in page during the
   assistant app's connect flow, **Then** the app is paired with this session and the code cannot
   be used again.
3. **Given** a code that has expired, was already used, or was mistyped, **When** it is entered,
   **Then** the page says the code is not valid, nothing is paired, and the staff member can get a
   new code from the console.
4. **Given** the staff member clicks *Get pairing code* again while a code is still showing,
   **Then** the new code replaces the old one, and the old one no longer pairs anything.
5. **Given** the sign-in page is open, **When** the staff member reads it, **Then** it names the
   assistant app's address (e.g. claude.ai) and states that access is read-only and limited to the
   two counts.

---

### User Story 2 - Ask the two questions from the phone (Priority: P1)

Away from the console, the staff member asks the assistant app "Does anything need me?" or "How
many bookings changed in the last hour?" The app answers with the numbers for this session.

**Why this priority**: This is the value the pairing exists for. It shares P1 with pairing
because neither is useful alone.

**Independent Test**: With a paired app, create a known state (e.g. two conversations needing
attention, three bookings, one cancellation and two reschedules in the last hour), ask both
questions, and compare the answers with the console.

**Acceptance Scenarios**:

1. **Given** a paired app, **When** the staff member asks how many conversations need attention,
   **Then** the answer equals the number the console's conversation list marks as needing
   attention at that moment.
2. **Given** three appointments booked, one cancelled and two rescheduled in the last hour,
   **When** the staff member asks "How many bookings changed in the last hour?", **Then** the answer
   gives one count each for booked, cancelled and rescheduled appointments, e.g. "Three
   appointments were booked, one cancelled and two rescheduled."
3. **Given** one appointment rescheduled twice inside the window, **When** the question is asked,
   **Then** it counts as one rescheduled appointment, not two.
4. **Given** a change whose outcome is unknown (the scheduler's answer never arrived, or the
   outcome was never recorded) inside the window, **When** the question is asked, **Then** it is
   reported as a separate "outcome unknown" count and is not included in the other counts.
5. **Given** a paired app, **When** any question is answered, **Then** the answer contains no
   patient name and no message text.
6. **Given** two sessions each with a paired app, **When** each asks, **Then** each receives only
   its own session's counts.

---

### User Story 3 - See paired apps and revoke one (Priority: P2)

On the gear tab the staff member sees the list of paired apps: which app, when it was paired, and
when it last asked a question. They click **Revoke** on one. From then on that app's questions are
refused, and it would have to be paired again with a new code.

**Why this priority**: Revocation is the control that makes pairing safe to offer, for a lost
phone or an app the staff member no longer recognizes. It ranks below the core flow only because
the flow must exist before there is anything to revoke.

**Independent Test**: Pair an app, revoke it in the console, ask a question from the app, and
confirm it is refused and the app asks to reconnect.

**Acceptance Scenarios**:

1. **Given** one or more paired apps, **When** the gear tab is opened, **Then** each is listed with
   its name, when it was paired and when it was last used ("never" if not yet used).
2. **Given** a paired app, **When** the staff member revokes it, **Then** its next question is
   refused, and the list no longer shows it as active.
3. **Given** no paired apps, **When** the gear tab is opened, **Then** the list says so rather than
   showing an empty table.
4. **Given** the session is deleted through the admin surface, **Then** every pairing it owned
   stops working.

---

### Edge Cases

- **No public connector address configured.** The tab still shows the explanation and the paired
  apps, but says pairing is unavailable because the connector address is not set, and does not
  issue codes that could not be used.
- **Code guessing.** Repeated wrong codes on one sign-in attempt stop that attempt after a small
  number of tries. Codes are short-lived and single use.
- **The same code entered twice at once** (two tabs, a double tap): exactly one pairing succeeds.
- **The staff member removes the connector inside the assistant app.** VisitDoc is not told. The
  pairing remains listed until revoked or until it expires from disuse. The console shows its last
  use, so it can be recognized and revoked.
- **A stolen, already-used renewal credential is presented again.** The whole pairing is revoked,
  and the staff member pairs again.
- **The time window asked for is very large or not positive.** The answer says what window it
  used or why it refused. It never silently answers over a different window.
- **The tab is open while an app is paired from the phone.** The list updates on the console's
  regular refresh without a manual reload.

## Requirements *(mandatory)*

### Functional Requirements

**The console tab**

- **FR-001**: The staff console MUST have a new tab shown as a gray gear icon, placed at the far
  right of the tab bar, after the existing tabs. It MUST have an accessible name (e.g.
  "Connected apps") because it has no text label.
- **FR-002**: The tab MUST show a short explanation: the Claude app can be paired with this
  session to ask how many conversations need attention and how many had booking changes recently,
  and it can read counts only.
- **FR-003**: The tab MUST offer a *Get pairing code* action that shows the connector address and
  a pairing code with its remaining lifetime.
- **FR-004**: The connector address MUST come from one deployment setting holding the public HTTPS
  base URL of the chat service (for local work, the address of an ngrok tunnel in front of it),
  not from the browser's own address, because the address the assistant app must reach is the
  public one. The tab shows that base URL with the connector's path appended. When the setting is
  unset, or is not an `https://` URL, the tab MUST say pairing is unavailable and why, and MUST NOT
  issue codes.
- **FR-005**: The tab MUST list the session's paired apps, each with its name, when it was paired
  and when it was last used, and a *Revoke* action per app.

**Pairing codes**

- **FR-006**: A pairing code MUST be single use, expire 10 minutes after it is issued, and be
  bound to the session that issued it.
- **FR-007**: A session MUST have at most one usable code at a time: issuing a new code MUST make
  any earlier unused one unusable.
- **FR-008**: The system MUST store a code only in a form from which it cannot be read back. It is
  shown in plain form once, to the console that requested it.
- **FR-009**: Consuming a code MUST succeed for exactly one attempt even when two arrive at once.

**Connecting the assistant app**

- **FR-010**: The connector MUST use the standard sign-in flow that assistant-app connectors
  require (OAuth 2.1 with discovery, dynamic client registration and PKCE), so that a supported app
  can connect by being given only the connector address.
- **FR-011**: The sign-in page MUST be served by VisitDoc, work in a browser with no VisitDoc
  session, show the address of the app that will receive access, state that access is read-only
  and limited to the two counts, and ask for the pairing code.
- **FR-012**: The sign-in flow MUST accept only Claude's return address
  (`https://claude.ai/api/mcp/auth_callback`), which covers the Claude web, desktop and mobile
  apps. Registration with any other return address MUST be refused.
- **FR-013**: A sign-in attempt MUST stop accepting codes after 5 wrong entries.
- **FR-014**: A successful pairing MUST create a grant tied to the session the code belonged to.
  Every later question from that app MUST be answered for that session alone, and the session
  MUST never be taken from anything the app sends as a question argument.

**The two questions**

- **FR-015**: The connector MUST offer exactly two read-only questions and no action that changes
  anything.
- **FR-016**: *Needs attention* MUST return the number of the session's conversations that the
  console's conversation list marks as needing attention at the same moment.
- **FR-017**: *Recent booking changes* MUST take the window the staff member asks about, in
  minutes or hours, passed to the connector as a whole number of minutes, and return, for that
  window measured back from now, the number of distinct appointments that were booked, cancelled
  and rescheduled by a change that completed, one count per operation, plus a separate count of
  changes whose outcome is unknown. An appointment changed twice by the same operation counts
  once for it; an appointment booked and then cancelled counts once under each.
- **FR-017a**: The *recent booking changes* answer MUST include a ready-to-read sentence in the
  form "Three appointments were booked, one cancelled and two rescheduled." (numbers as words up
  to ten, operations with a zero count left out, "No appointments were booked, cancelled or
  rescheduled." when all are zero), so the assistant app can relay it as it stands.
- **FR-018**: The window MUST be between 1 minute and 7 days. A request outside that range MUST be
  refused with a message stating the allowed range.
- **FR-019**: No answer MUST contain a patient name, a practitioner name or any message text.

**Grants and revocation**

- **FR-020**: Access granted by a pairing MUST expire after 1 hour and be renewable by the app
  without the staff member's involvement for up to 30 days of inactivity. Each renewal MUST
  replace the renewal credential, and presenting a replaced one MUST revoke the whole grant.
- **FR-021**: Revoking a grant in the console MUST cause that app's next question to be refused.
- **FR-022**: Deleting a session MUST remove its codes and grants.
- **FR-023**: Credentials MUST be stored only in a form that cannot be read back and MUST never
  appear in logs.
- **FR-024**: Each pairing, question, refusal and revocation MUST be logged with the session and
  grant it concerned and without any credential.

### Key Entities

- **Pairing code**: a short-lived, single-use secret issued by the console for one session. Has an
  expiry and a used/unused state.
- **Registered app**: an assistant app that introduced itself to the sign-in flow, with its name
  and its return address. Not tied to a session until a pairing succeeds.
- **Grant**: the standing permission of one registered app to ask questions for one session. Has
  when it was created, when it was last used, and whether it was revoked. Belongs to the session
  and disappears with it.
- **Access and renewal credentials**: the secrets an app presents on each question and on renewal,
  each belonging to one grant, with an expiry.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A staff member who has not seen the tab before can pair an assistant app within 3
  minutes of opening it, following only what the tab and the sign-in page say.
- **SC-002**: In a scripted comparison of at least 10 states, the *needs attention* answer equals
  the console's count every time.
- **SC-003**: For a scripted set of booking changes with known times, the *recent booking changes*
  answer is correct for every window tested, including changes with an unknown outcome.
- **SC-004**: After a revoke, 100% of that app's questions are refused, starting with the first.
- **SC-005**: No answer in the test suite contains a patient name, a practitioner name or message
  text, and no log line contains a code or credential.
- **SC-006**: Questions from an app paired with one session never return another session's counts,
  in every cross-session test.
- **SC-007**: A code is accepted at most once, including when submitted concurrently.

## Assumptions

- The connector is reachable at a public HTTPS address. Claude's servers, not the phone, call it,
  so an address reachable only on the developer's machine cannot serve it. Locally that address is
  an ngrok tunnel in front of the chat service. Only the chat service needs exposing: the scheduler
  and the SPA are not involved. Starting the tunnel is outside this feature.
- The ngrok address is stable (a reserved or static ngrok domain). A tunnel whose address changes
  on each start leaves existing pairings pointing at a dead address, and each one must be paired
  again.
- ngrok's free tier may show a one-time warning page before a browser reaches the sign-in page.
  The staff member clicks through it once. Claude's own server-to-server calls are not browsers and
  are not affected.
- Only Claude is supported. Other assistant apps with MCP connectors (e.g. ChatGPT) are out of
  scope, and nothing here is built to anticipate them.
- The gear tab's gray color follows the project's existing muted color token, marking it as
  secondary to the other tabs.
- The paired app's name shown in the list is the name the app gave when it registered.
- The list of paired apps refreshes on the console's existing poll; no push is added.
- "Needs attention" is defined entirely by the console's existing conversation list. This feature
  adds no new definition.
- Booking changes are counted from the existing record of what the assistant did to the schedule.
  Staff cannot change appointments today, so that record covers every change.
- The window is measured on the server's clock against when each change completed. No timezone is
  stored or needed.
- Code length (8 characters from an unambiguous alphabet), code lifetime (10 minutes), access
  lifetime (1 hour), inactivity limit (30 days) and the wrong-entry limit (5) are defaults that
  planning may adjust.
- Out of scope: staff accounts, write actions, returning conversation details, push notifications,
  and deployment.
