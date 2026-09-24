# Feature Specification: The frontend design pass (Phase 3a)

**Feature Branch**: `create-ui`

**Created**: 2026-09-23

**Status**: Draft

**Input**: User description: "I'm going to reshape the frontend to finally make it look nice and
convenient for real user use" — followed by a hand-drawn Excalidraw sketch of five screens, and four
decisions taken in that conversation: both audiences stay on **one page**; **light theme only**; the
header reads **"AI Clinic Receptionist"**; and of three mockups spanning conventional to
experimental, direction **A — "Front Desk"** was chosen (cool grey-green page, teal accent, one type
family, structure from hairline rules rather than floating cards). The `(i)` evidence marker opens on
**click**, not hover. `docs/ROADMAP.md`'s Phase 3a section is binding, including the two backend
gaps it records.

## Why this exists

Every phase so far treated the frontend as the thinnest surface that made backend work
demonstrable. The result is not a restrained aesthetic — it is the absence of one. The SPA ships
**no stylesheet at all**: no `.css` file exists, `index.html` links none, and the entire visual
design is fifteen scattered inline `style` props over browser defaults. What a visitor opens is
unstyled HTML: Times New Roman, blue underlined buttons, a list of chats as bullet points, and every
one of the four surfaces — patient thread, staff console, practitioner admin, FAQ admin — stacked
down a single column with nothing separating them.

That matters beyond looks. Three things the product actually does are currently invisible because
nothing distinguishes them: which messages are the patient's own and which are the clinic's, which
conversations need a person, and why the assistant answered the way it did. The information is all
in the payload and on the page; it is just undifferentiated text.

This phase gives the SPA its first real design, bounded by the sketch, and makes those three things
legible.

**Three things it deliberately is not.** It does not split the two audiences onto separate routes:
patient and staff stay side by side on one screen, because a single screen is what lets one person
exercise an escalation from both ends, which is the reason the second pane exists at all. It does
not change any network contract — `src/lib/chatStream.ts` and `src/lib/consoleApi.ts` keep their
shapes, and no endpoint is added, so this is a presentation-layer feature end to end. And it does
not build the two capabilities the sketch draws that the backend cannot serve: those are recorded in
Phase 3a of the roadmap and appear here as visible, deliberate stubs.

## Clarifications

### Session 2026-09-23

- Q: One page with both panes, or separate routes for patient and staff? → A: **One page.** The
  panes sit side by side above 1100px and stack below it. Separate routes were considered and
  rejected: the demonstrable value of this build is that one person can raise an escalation as the
  patient and answer it as staff without navigating.
- Q: Light, dark, or both? → A: **Light only.** A second palette is a second set of contrast pairs
  to verify, and nothing here is written to make adding one hard — every colour is a token, so a
  dark theme would redefine token values rather than edit any rule that reads them.
- Q: Does the `(i)` evidence marker open on hover or on click? → A: **Click**, expanding inline
  beneath the message. A floating popover was drawn in the sketch but is wrong here for two
  reasons: it is clipped by the thread's own scroll container, and it covers the conversation it is
  describing. Inline also means two markers can be open at once and compared, and it degrades to a
  plain disclosure with no positioning logic.
- Q: How does the chosen typeface reach the browser — a font service, or the application itself?
  → A: **Self-hosted.** The font ships with the application and is requested from it. A font
  service was rejected on four counts: it is a third-party runtime dependency, which FR-004
  forbids; it fails with no network, which is how this is developed; it discloses every visitor's
  address to that third party; and it makes the product's typography contingent on someone else's
  uptime. The mockup under `design/` uses a font service and is wrong on this point — see the note
  in its README.
- Q: While the assistant is paused and a person is coming, what does the patient see in place of
  the running indicator? → A: **Nothing.** Their message sits in the thread as sent, and the
  interface says nothing about who will reply or when. This is a deliberate silence rather than an
  omission: the alternative — a notice that a person is handling the conversation — is a claim
  about a response the clinic has not committed to, and this build has no data behind any
  timescale it might offer. The accepted cost is recorded in Assumptions: a patient cannot
  distinguish "a person is coming" from "nothing happened", and if that proves to be the wrong
  trade it is a later change, not an oversight.
- Q: What does the page show while its first requests are still in flight? → A: **Structure
  immediately, content when it lands.** The header, both panes and their headings render before
  any request returns, because they are known without one; each region still waiting says so in
  plain words. Skeleton placeholders were rejected for the same reason FR-033 refuses to render an
  empty appointment list: a grey shape standing in for three chat tabs asserts a shape the server
  has not confirmed, and it is wrong exactly when the answer turns out to be none.
- Q: What decides how many chat tabs show before the overflow takes the rest — measured width, or
  a fixed count? → A: **A fixed count.** Measuring the strip is the better-looking answer and the
  worse engineering one here: it needs a resize observer, and jsdom reports zero width for
  everything, so the rule would be untestable in the tier that runs on every push and would have
  to wait for Phase 3b's browser suite. A fixed count is deterministic, testable today, and
  indistinguishable to a reader who simply sees a sensible strip.
- Q: Are message times or waiting times shown anywhere? → A: **Nowhere.** The sketch draws none and
  this feature adds none, in either thread or in the staff conversation list. The known cost is on
  the staff side: the header count says how many conversations need a person and nothing says how
  long any of them has waited, so a queue of three cannot be put in priority order from the screen.
  The data for it is already on the wire — each conversation carries when its attention began — so
  this is a scope decision rather than a missing capability, and it is recorded in Assumptions
  rather than left to be rediscovered.
- Q: Where does editing a practitioner or an FAQ entry happen — in place, in an overlay, or on its
  own view? → A: **It replaces the tab's content**, with a control back to the list, which is what
  the sketch draws. An overlay was rejected because it needs a focus trap, a scroll lock and an
  escape route, and because it would cover the patient pane — the half a staff member is often
  cross-checking against. Expanding in place was rejected because the practitioner form is too tall
  for a list row in a pane this narrow. Replacing the panel behaves identically whether the panes
  are side by side or stacked.
- Q: When a message arrives while a thread is already open, does the view follow it? → A: **Only
  when the reader is already at the bottom.** Otherwise their scroll position is held and nothing
  announces the arrival. Always following was rejected because it makes reading back impossible for
  the whole time a reply is streaming; never following was rejected because a reply would then
  arrive off screen even for a reader who had not moved. The cost of omitting the announcement —
  a message can land unseen — is accepted and recorded in Assumptions. This applies to both
  threads.
- Q: How do the composers handle their 2000-character limit? → A: **Warn before, and disable Send
  when it would do nothing.** A count appears as the message nears the limit and becomes the error
  past it; Send is disabled while the message is empty or over-long, which both composers already
  silently enforce by refusing to send. Today the control accepts the click and discards it, and a
  visual pass makes that read as a broken button rather than an unstyled one. Input is not
  truncated at the limit: swallowing a long paste loses text the patient wrote.
- Q: What does a chat with no messages show? → A: **A short greeting naming what the assistant can
  do**, as interface copy rather than as a message in the thread. This is the screen a first
  arrival meets, since provisioning creates a chat and opens it, so it is the product's first
  impression and the one place an empty state should invite rather than report. It must not be
  mistaken for something the assistant said, and it must not promise more than the assistant
  serves. The staff console's equivalent — a conversation selected but holding no messages — gets a
  plain statement instead: a staff member already knows what the console is for.
- Q: FR-004 forbade every new dependency. Does that hold, and is Tailwind + shadcn/ui worth
  adopting? → A: **The prohibition does not hold, and the stack is adopted.** The ban was asserted
  on a "minimal dependencies" convention this repository does not have: no such rule appears in
  `.claude/CLAUDE.md`, `README.md` or `docs/ROADMAP.md`, and the chat service alone carries twenty
  runtime dependencies. What the repository actually requires is a recorded tradeoff per choice —
  twelve "technology choices" sections in `README.md` set that pattern. The feature therefore
  adopts **Tailwind CSS v4 and shadcn/ui**, with each dependency documented. The narrower
  alternative — unstyled Radix primitives over hand-written scoped CSS — was offered and declined
  in favour of the stack a reviewer expects to see; the tradeoff, a larger migration and a
  recognizable default aesthetic to theme away from, is accepted and recorded in Assumptions.
  Self-hosting the typeface (FR-004) is unaffected and still required.
- Q: How is the assistant switch's explanation presented — the sketch's `?`, or something else? →
  A: **A permanently visible sentence**, not a tooltip or a `?`. This control's effect reaches a
  real patient the moment it is flipped, and the pause then expires on a timer the patient cannot
  see, so its explanation should not be something a staff member has to suspect is there and go
  looking for. A hover-revealed `?` also has to be given keyboard and touch equivalents before it
  is usable at all, which costs more than the sentence it was saving space over.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A patient holds a conversation (Priority: P1)

A patient arrives, is given a session and a chat, and talks to the clinic. They can tell at a glance
which messages are theirs and which came from the clinic, they can see that a reply is being
composed, they watch it arrive, and they can move between their chats and start or delete one.

**Why this priority**: It is the product's front door and the only surface a real patient would ever
see. Styled alone, with the console left as it is, the build is already worth showing.

**Independent Test**: Open the app as a first arrival, send a message, and read the reply — with the
staff console untouched. Every claim below is observable in that one pass.

**Acceptance Scenarios**:

1. **Given** a browser opening the page for the first time, **When** the first paint happens and
   before any request has returned, **Then** the header, both panes and their headings are already
   on screen, and each region awaiting content says in words that it is waiting — distinguishably
   from a region that arrived empty and from one that failed.
2. **Given** a session holding three chats, **When** the patient pane renders, **Then** the chats
   appear as a horizontal strip of tabs labelled by patient name, the open one is visibly distinct
   from the rest, and controls to start a chat and to delete the open one are present.
3. **Given** a session holding more than four chats, **When** the pane renders, **Then** four tabs
   show directly and the rest are reachable through a single overflow control, rather than the
   strip widening the pane or scrolling the page sideways.
4. **Given** a session of six chats in which the open one is the last by the server's ordering,
   **When** the pane renders, **Then** the open chat is among the four shown directly and the tab
   it displaced has moved into the overflow.
5. **Given** a thread holding messages from the patient, the assistant and staff, **When** it
   renders, **Then** the patient's own messages are aligned opposite the others and carry a distinct
   background, and a sender icon appears on the first message of each consecutive run from one
   sender and not on the rest of that run.
6. **Given** a chat with more messages than fit, **When** it is opened, **Then** the thread is
   scrolled to its most recent message without the patient scrolling.
7. **Given** the assistant is permitted to reply, **When** the patient sends a message, **Then** a
   running indicator appears within half a second and remains until that turn produces a reply, is
   superseded, or fails.
8. **Given** the assistant has been paused by staff, **When** the patient sends a message, **Then**
   no running indicator appears, because the wait is now for a person and is measured in minutes.
9. **Given** the assistant is replying, **When** its tokens arrive, **Then** they appear
   progressively in a single bubble for that turn; a staff message, by contrast, appears whole.

---

### User Story 2 - A staff member works the console (Priority: P2)

A staff member sees how many conversations need them, picks one, reads it, takes it over by pausing
the assistant, and replies — and can move to the practitioner and FAQ screens without losing sight
of the count.

**Why this priority**: It is the second audience and the half that makes the escalation design
visible, but a patient can be served without it having been touched.

**Independent Test**: With the patient pane as-is, drive a conversation into an escalation and
handle it from the console alone.

**Acceptance Scenarios**:

1. **Given** the console renders, **When** a staff member looks at it, **Then** the number of
   conversations needing a person is visible in the console's own header, and stays visible when
   they move to the practitioner or FAQ screen.
2. **Given** conversations of which some need a person, **When** the list renders, **Then** every
   conversation in the session is listed, those needing a person are marked in a way that does not
   rely on colour alone, and the server's ordering is rendered as received.
3. **Given** a staff member opens a conversation, **When** the thread renders, **Then** staff
   messages are aligned opposite the patient's and the assistant's and carry their own background,
   so the patient's side of the exchange is distinguishable from the clinic's two voices.
4. **Given** an open conversation, **When** the thread header renders, **Then** it carries the
   assistant on/off control, the remaining pause time when the assistant is paused, and an
   explanation of what the control does.
5. **Given** the console's three sections, **When** they render, **Then** they are operable as a tab
   set: one open at a time, each reachable by keyboard, and announced as a tab set by assistive
   technology.

---

### User Story 3 - A staff member sees why the assistant answered as it did (Priority: P3)

Reading a conversation, a staff member finds a marker on a message. Opening it shows what the
assistant understood the patient to be asking, what it answered from, and — where it could not
answer — why, so they know the request is theirs to handle. A marker on an assistant reply carries
what that reply did; a marker on a patient message carries why that message needs a person.

**Why this priority**: It is the console's most distinctive capability and the thing that makes the
per-request outcome work of earlier phases visible at all. It depends on Story 2's thread existing.

**Independent Test**: Open a conversation containing one served request and one abstention, and
confirm both read correctly from the marker alone.

**Acceptance Scenarios**:

1. **Given** an assistant reply carrying per-request outcomes and a patient message carrying an
   attention mark, **When** the thread renders, **Then** each carries its own marker, in the state
   its own data implies; **and given** a message carrying neither, **Then** it carries none.
2. **Given** a marker, **When** a staff member activates it, **Then** a block expands beneath that
   message; activating it again collapses it; and its expanded or collapsed state is announced to
   assistive technology.
3. **Given** an expanded block for a turn that answered, **When** it renders, **Then** each request
   appears in the order recorded, with what was asked, that it was answered, and the clinic
   documents it drew on.
4. **Given** an expanded block for a turn that abstained on a request, **When** it renders, **Then**
   that request is shown as unanswered with the reason named, distinguished from the answered ones
   by more than colour.
5. **Given** any expanded block, **When** it renders, **Then** it states that the booking outcome is
   not yet recorded, in a way that reads as deliberately absent rather than as an error or an empty
   result.

---

### User Story 4 - A staff member manages practitioners and the FAQ (Priority: P4)

A staff member adds a practitioner, sets their specialty, appointment length and working hours, and
edits the clinic's FAQ entries — on screens that look like the rest of the product.

**Why this priority**: Real capability, already built and working, but the least-visited surface and
the one whose current rawness costs least.

**Independent Test**: Add a practitioner with two working days, then add and edit an FAQ entry.

**Acceptance Scenarios**:

1. **Given** the practitioner screen, **When** it renders, **Then** the roster, the create control
   and each practitioner's specialty, appointment length and working hours are readable as a
   structured record rather than a run of form fields.
2. **Given** a practitioner is selected, **When** the screen renders, **Then** a panel for their
   standing appointments over the next seven days is present and states that this is not yet
   available, naming what will appear there.
3. **Given** the FAQ screen, **When** it renders, **Then** each entry's question and answer are
   distinguishable from each other, and creating, editing and deleting an entry are each available
   from the entry they act on.
4. **Given** a staff member chooses to edit a practitioner, **When** the edit view opens, **Then**
   it has replaced the content of the practitioners tab rather than covering the page, the page
   header and the tab set are still on screen and operable, and a control returns to the roster.
5. **Given** an edit view holding unsaved changes, **When** the staff member leaves it by the back
   control or by choosing another tab, **Then** the changes are not saved and are not discarded
   silently.
6. **Given** either screen reports an error, **When** it renders, **Then** the error says what
   failed and is visually distinct from the content around it.

---

### User Story 5 - The screen survives a narrow window and a keyboard (Priority: P5)

Someone opens the app on a small laptop, in a half-width window, or drives it entirely from the
keyboard, and nothing is unreachable or cut off.

**Why this priority**: Cross-cutting and verifiable independently of any single screen; it is the
floor the other four stories are built on rather than a journey of its own.

**Independent Test**: Walk the whole page with Tab at 1440px, then repeat at 900px and 375px.

**Acceptance Scenarios**:

1. **Given** a viewport of 1100px or wider, **When** the page renders, **Then** the two panes sit
   side by side; **and given** a narrower one, **Then** the console sits beneath the patient
   messenger and both use the full width.
2. **Given** any viewport width from 375px to 1920px, **When** the page renders, **Then** the
   document does not scroll horizontally, including with an unbroken 200-character message in the
   thread.
3. **Given** a keyboard user, **When** they move through the page, **Then** every control that can
   be operated by mouse is reachable and operable, and the control holding focus is visibly
   indicated.
4. **Given** a visitor who has asked their system to reduce motion, **When** the assistant is
   composing, **Then** the running indicator conveys its state without animating.

---

### Edge Cases

- **A session with no chats at all.** Valid — a patient may delete their last one. The pane says so
  and offers to start one, rather than rendering an empty strip and a blank thread.
- **A chat whose patient record does not exist yet.** The server sends no name; the tab is labelled
  by creation time, as it is today, and the label must not collapse to an empty tab.
- **An unbroken 200-character token in a message.** Wraps inside its bubble; it must not widen the
  bubble, the pane, or the page.
- **Several turns in flight at once.** Each in-flight turn owns its own bubble and its own running
  indicator; one turn completing must not clear another's.
- **A turn that produced no reply at all.** Its bubble is removed; nothing is left mid-animation.
- **A message with an attention mark but no recorded outcomes** — an urgent condition, say, which
  never reached retrieval. The marker appears in its unserved state and names the mark as the
  reason, rather than expanding to an empty block.
- **A turn with `request_outcomes` of `null`** — no FAQ half ran. No marker, because there is
  nothing recorded to show, and a marker opening onto nothing is worse than none.
- **A conversation list arriving mid-edit.** The two-second poll repaints the console; it must not
  move focus, close an expanded evidence block on a message that is still listed, or discard text
  typed into a composer.
- **A practitioner with no working hours set, and one with seven days of them.** Both render; the
  second must not push the panel wider than its column.
- **The assistant switch repainted by the poll rather than optimistically.** Its rendered position
  must follow the server's answer, so two tabs cannot disagree about who holds a conversation.

## Requirements *(mandatory)*

### Functional Requirements

#### The design system

- **FR-001**: The application MUST define its colours, type scale, spacing scale, radii and
  typeface in one place as named design tokens, and every screen MUST take its values from those
  tokens rather than from literals.
- **FR-002**: The interface MUST present a single light theme. No dark theme is provided, and the
  token definitions MUST be the only thing a later dark theme would need to change.
- **FR-003**: Component-level styling MUST be scoped to the component it belongs to, so that no
  component's styling can alter another's. Styling MUST NOT rely on a shared global class namespace
  in which one component's rule can match another's element. Exactly one stylesheet may be global,
  and it MUST be limited to the theme declaration, element defaults and the framework's own entry
  point.
- **FR-004**: The application MUST issue no request to a third party at runtime. The chosen typeface
  MUST be served by the application itself rather than fetched from a font service, and MUST be
  declared with a fallback chain so that a font which fails to load degrades to a named stack rather
  than to a browser default.
- **FR-004a**: Dependencies MAY be added, and each one MUST be recorded in `README.md` with its
  tradeoff, following the pattern the twelve existing "technology choices" sections set. This
  supersedes an earlier prohibition on new dependencies, which was asserted on a "minimal
  dependencies" convention that does not exist in this repository — there is no such rule in
  `.claude/CLAUDE.md`, `README.md` or `docs/ROADMAP.md`, and the chat service alone carries twenty
  runtime dependencies. The governing rule here has always been *document the tradeoff*, not *do
  not add*.
- **FR-004b**: Interactive controls whose correct keyboard and assistive-technology behaviour is
  non-trivial — the tab set, the modal confirmation and the overflow menu — MUST be built on an
  accessible primitive library rather than hand-rolled. Each of those three requires focus
  management the application would otherwise reimplement: a roving tabindex with arrow-key
  navigation, a focus trap with restore-on-close, and a menu with typeahead and collision-aware
  positioning. FR-039, FR-040 and SC-002 are the requirements this serves; hand-rolling them would
  leave that floor resting on a manual audit.
- **FR-005**: One colour MUST be reserved to mean "a person is needed" and MUST NOT be used
  decoratively anywhere in the interface.
- **FR-006**: Any state distinguished by colour MUST also be distinguished by something that is not
  colour — text, shape, weight or position.

#### The page

- **FR-007**: The page MUST present a header naming the product "AI Clinic Receptionist", the
  patient messenger, and the staff console, on one screen with no navigation between them.
- **FR-008**: At viewport widths of 1100px and above the two panes MUST sit side by side; below
  1100px the console MUST sit beneath the patient messenger, both at full width.
- **FR-009**: The document MUST NOT scroll horizontally at any viewport width from 375px to 1920px,
  and no single piece of content may widen a pane past its column.
- **FR-010**: Failures that belong to the page rather than to a pane — loading or changing the chat
  list — MUST be reported once, at page level, visually distinct from surrounding content.
- **FR-010a**: The page's structure — the header, both panes, and each pane's own headings and
  section controls — MUST render before any request has returned, since none of it depends on one.
  Each region whose content has not yet arrived MUST say so in words. Placeholder shapes standing
  in for content that has not arrived MUST NOT be used: they assert a shape the server has not
  confirmed, and are wrong precisely when the answer turns out to be nothing. A region that is
  waiting MUST be distinguishable from one that arrived empty and from one that failed, because
  those three call for three different things from the reader.
- **FR-010b**: No timestamp, date, or elapsed-time value MUST be rendered as a property of a
  message, a conversation, or a thread — not a sent time, not a received time, not how long a
  conversation has waited. The values are present in the payloads and are deliberately not shown.
  Two things that look like exceptions are not, and both are already specified: the assistant's
  remaining pause time (FR-024), which is a countdown on a control rather than a record of when
  anything happened; and the creation time inside the label of a chat whose patient has no name
  yet (FR-011), which exists to tell two otherwise identical labels apart and carries no meaning
  beyond that. Nothing else may be added.

#### The patient messenger

- **FR-011**: Chats MUST be presented as a horizontal strip of tabs labelled by patient name, with
  the open chat visibly distinct, a control to start a chat, and a control to delete a chat that
  confirms before deleting.
- **FR-012**: The strip MUST show at most a fixed number of tabs directly — **four** — with the
  remainder reachable through a single overflow control. The number MUST be declared in one place
  rather than repeated, and MUST NOT be derived from a measurement of the rendered strip: a
  measured rule cannot be exercised by the existing test tier, which reports every element as
  having no width.
- **FR-012a**: Which tabs show directly MUST follow the server's ordering, which already places the
  most recently active chat first. The open chat MUST always be shown directly: when it would
  otherwise fall into the overflow it takes the last direct position, and the tab displaced by it
  moves into the overflow. The overflow control MUST NOT be rendered when every chat fits.
- **FR-013**: The patient's own messages MUST be aligned opposite, and given a different background
  from, messages from the assistant and from staff.
- **FR-014**: A sender indicator MUST appear on the first message of each consecutive run of
  messages from one sender, and MUST NOT be repeated on the rest of that run.
- **FR-015**: The thread MUST be a scroll container that shows its most recent message when a chat
  is opened, without the patient scrolling.
- **FR-015a**: While a chat is open, the thread MUST follow new content to the bottom **only when
  the reader is already at the bottom**. When they have scrolled up, their position MUST be held:
  arriving messages and streaming tokens MUST NOT move the view. No affordance announcing the
  arrival is shown — see the accepted cost in Assumptions. Returning to the bottom by scrolling
  MUST restore the following behaviour.
- **FR-015b**: FR-015a applies to the staff thread on the same terms. A staff member reading back
  through a conversation is doing the same thing a patient is, and the poll that repaints their
  thread arrives just as unbidden.
- **FR-016**: While a turn is awaiting a reply and the assistant is permitted to reply, a running
  indicator MUST be shown for that turn, and removed when the turn produces a reply, is superseded,
  or fails.
- **FR-017**: No running indicator may be shown while the assistant is paused for that
  conversation, because the wait is then for a person and is of a different order. Nothing MUST be
  substituted for it: the patient's message sits in the thread as sent, and the interface makes no
  statement about who will reply or when. A notice, a placeholder, or a countdown in its place is
  out of scope for this feature and MUST NOT be added as a convenience.
- **FR-018**: Assistant text MUST render progressively as it streams, into one bubble per turn;
  staff messages MUST render whole.
- **FR-019**: The patient messenger MUST NOT display per-request outcomes, attention marks or
  citations. They are the clinic's working notes on how an answer was produced, and the staff
  console is where they are read.
- **FR-019a**: Both composers — the patient's and the staff member's — MUST make the length limit
  visible before it is exceeded rather than only after. A character count MUST appear as the
  message approaches the limit and MUST become the over-limit error past it. The count MUST NOT be
  shown for a message nowhere near the limit, which is almost all of them.
- **FR-019b**: A send control MUST be disabled exactly when activating it would do nothing — when
  the message is empty or whitespace, and when it is over the limit — and the reason MUST be
  available to assistive technology rather than conveyed by appearance alone. Both composers
  already refuse to send in those cases; this makes the control's appearance agree with what it
  will do, instead of accepting a click and discarding it. Input MUST NOT be truncated or blocked
  at the limit: text the patient wrote, including a long paste, stays in the box to be edited down.
- **FR-019c**: An open chat whose history has **loaded** and holds no messages MUST show a short
  greeting naming what the assistant can do — make, change and cancel appointments, say who practises at the clinic and when
  they are free, and answer questions about the clinic from its documents. It MUST be rendered as
  interface copy, distinct from a message bubble and carrying no sender, so it cannot be read as
  something the assistant said. It MUST disappear once the chat holds a message. It MUST NOT name a
  capability the assistant does not have, and MUST NOT be shown in place of a thread that failed to
  load, which is a different situation under FR-010a.

#### The staff console

- **FR-020**: The console MUST present three sections — conversations, practitioners and FAQ — as a
  tab set, one open at a time, operable by keyboard and exposed as a tab set to assistive
  technology.
- **FR-021**: The count of conversations needing a person MUST be rendered in the console's header,
  outside the tabbed region, so that it is visible whichever section is open. It MUST be rendered
  when it is zero.
- **FR-022**: Every conversation in the session MUST be listed, in the order the server sent, with
  the ones needing a person marked. The interface MUST NOT re-derive that ordering or that count.
- **FR-023**: Staff messages MUST be aligned opposite, and given a different background from,
  messages from the patient and from the assistant.
- **FR-024**: The thread header MUST carry the assistant on/off control, the remaining pause time
  when paused, and an explanation of what the control does. The control's rendered position MUST
  follow the server's answer rather than being set optimistically.
- **FR-024a**: That explanation MUST be a permanently visible sentence beside or beneath the
  control, not a tooltip, a `?` affordance, or anything else requiring hover, focus or a click to
  reveal. It MUST say what turning the control off does — the assistant stops replying to this
  patient and a person is expected to — and that the pause expires on its own. This is the one
  control on the page whose effect reaches a real patient immediately, and it MUST NOT be the one
  whose explanation has to be discovered.

  > **Narrowed after shipping, twice.** The sentence no longer says the pause expires on its own:
  > the countdown beside it says so better, naming how much is left rather than that something is,
  > and re-read from the server on every poll instead of asserted in prose. What the sentence keeps
  > is the half nothing else carries — that turning the control off stops the assistant replying to
  > this patient and puts a person in its place.
  >
  > **And "permanently visible" no longer holds:** the sentence is shown when
  > the assistant is off or a pause is counting down, and not in the resting state — assistant on,
  > no pause — where it describes nothing. Every other clause stands where it is shown: plain text
  > beside the control, never revealed on hover, focus or a click. The two states the sentence
  > distinguishes are all this pane has; a pause that expired and a conversation that was never
  > paused report the same `assistant_may_reply` and `pause_seconds_remaining`, so "the countdown
  > ran out" is not a state it could render differently. The cost accepted is that the explanation
  > is absent immediately before the act it warns about — turning the control off — and it appears
  > as soon as that act is taken.
- **FR-025**: Sections that read session-scoped data MUST NOT be rendered before a session exists;
  while none does, the section MUST say so rather than render an empty result.
- **FR-025a**: A conversation that is open but holds no messages MUST say plainly that it is empty.
  It MUST NOT borrow the patient side's greeting (FR-019c), which exists to orient a first-time
  visitor, and MUST be distinguishable from no conversation being selected at all and from a thread
  that failed to load.

#### The evidence marker

- **FR-026**: A message MUST carry an evidence marker when **that message itself** carries
  per-request outcomes or an attention mark, and MUST NOT carry one otherwise. The two conditions
  fall on different messages and this is not an inconsistency to be smoothed over: outcomes are
  written to the **assistant's reply**, because they describe what the assistant did; an attention
  mark is written to the **patient's message**, because that is the thing a person has to act on
  (`chat/api/turn.py` passes `patient_message.id`). So a turn may put a marker on both of its
  messages, each describing its own, and the marker is never assembled by pairing a message with
  its neighbour. Pairing was rejected: a patient message may have no reply yet, may be superseded,
  or may be one of a burst, and every one of those makes "the reply to this message" ambiguous.
- **FR-026a**: The marker's state MUST be derived from the message it sits on — served, when the
  outcomes it holds were answered; needs-a-person, when it holds an attention mark or an
  abstention. A message holding both an answered and an unanswered request takes the
  needs-a-person state, since something in it is still owed to a person.
- **FR-027**: The marker MUST open on activation — not on hover — expanding a block beneath the
  message, and MUST close on a second activation. Its expanded state MUST be exposed to assistive
  technology.
- **FR-028**: More than one marker in a thread MUST be able to be open at once.
- **FR-029**: An expanded block MUST list each request in the order recorded, and for each: what was
  asked, whether it was answered, and — when answered — the clinic documents the answer drew on.
- **FR-030**: A request that was not answered MUST be shown as unanswered with its reason named,
  distinguished from an answered one by more than colour.
- **FR-031**: An expanded block MUST state that the booking outcome is not yet recorded, presented
  as deliberately absent rather than as an error, an empty result, or a failure.

#### The practitioner and FAQ sections

- **FR-032**: The practitioner section MUST present the roster, a control to add a practitioner,
  and for each: name, specialty, appointment length and working hours, editable and deletable.
- **FR-033**: The practitioner section MUST present a panel for the selected practitioner's standing
  appointments over the next seven days, stating that this is not yet available and naming what will
  appear there. It MUST NOT show an empty list, which would assert there are no appointments.
- **FR-034**: The FAQ section MUST list the session's entries with the question distinguishable from
  the answer, and MUST offer create, edit and delete from the entry each acts on.
- **FR-035**: Errors raised by either section MUST name what failed and be visually distinct from
  the content around them.
- **FR-035a**: Editing a practitioner or an FAQ entry, and creating either, MUST replace the
  content of its own tab with an edit view carrying a control back to the list. It MUST NOT be
  presented as an overlay over the page, and MUST NOT expand in place within the list. Only the
  tab's own content is replaced: the page header, the console header and its attention count, and
  the tab set itself all remain on screen and operable, so a staff member can leave an unfinished
  edit by choosing another tab.
- **FR-035b**: Leaving an edit view — by the back control or by choosing another tab — MUST NOT
  save what was typed, and MUST NOT silently discard work the reader would expect to survive. The
  interface MUST either confirm before abandoning unsaved changes or make the discard explicit;
  which of the two is a plan decision, but doing neither is not permitted.

#### Preservation

- **FR-036**: Every test hook currently exposed by the components MUST survive this change, because
  the existing suite and the browser suite planned for Phase 3b both select by them. New hooks may
  be added; existing ones may not be removed or renamed.
- **FR-037**: No module that talks to the backend may change shape: no endpoint, request, response
  type or error-handling rule is altered by this feature.
- **FR-038**: Behaviour that the existing automated suite pins MUST continue to hold. Where a test
  fails because the behaviour it pinned was deliberately changed, the property it protected MUST be
  re-expressed against the new behaviour rather than deleted.

#### The floor

- **FR-039**: Every control operable by mouse MUST be reachable and operable by keyboard, with the
  focused control visibly indicated.
- **FR-040**: Interactive elements MUST be their proper semantic elements — buttons, form controls
  with labels, and landmark regions — rather than styled generic containers.
- **FR-041**: Text MUST meet WCAG 2.1 AA contrast against its background.
- **FR-042**: Motion MUST be suppressed for visitors who have asked their system to reduce it, and
  any state it conveyed MUST remain conveyed without it.

### Key Entities

These are display concepts, not new data; each already exists in a payload the frontend receives.

- **Chat tab**: one of the session's chats as the patient sees it — its label, whether it is open,
  and whether it fits the strip.
- **Message**: one entry in a thread — its sender, its text, whether it begins a run from that
  sender, and whether it is complete or still streaming.
- **Request outcome**: what one request within a turn got — what was asked, whether it was answered,
  and the clinic documents it drew on. Several may belong to one message.
- **Attention mark**: why a message needs a person. Distinct from an outcome: a turn may carry a
  mark with no outcomes at all.
- **Conversation row**: one conversation as staff see it — its patient, whether it needs a person,
  whether the assistant may reply, and how long a pause has left.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At every viewport width from 375px to 1920px the page never scrolls horizontally, and
  the two panes are side by side at 1100px and above and stacked below it.
- **SC-002**: Every interactive control on the page can be reached and operated using only the
  keyboard, and at every stop the focused control is visibly indicated.
- **SC-003**: All text passes WCAG 2.1 AA contrast against its background — verified, not assumed.
- **SC-004**: A staff member can state how many conversations need them within five seconds of the
  page appearing, from whichever of the three sections is open.
- **SC-005**: A patient sees acknowledgement that a reply is coming within half a second of sending
  a message, whenever the assistant is permitted to reply — and sees none when it is paused.
- **SC-006**: Reading only the screen, a viewer can correctly say which messages are the patient's
  and which are the clinic's, and which conversations need a person, without relying on colour
  perception.
- **SC-007**: For any message carrying an evidence marker, a staff member can state which requests
  the assistant answered and which it did not, and on what the answers rested, in one interaction.
- **SC-008**: There is no place in the interface that asserts data the backend cannot supply: both
  gaps recorded in Phase 3a read as deliberately unavailable, and neither renders as an empty result
  or an error.
- **SC-009**: The full automated frontend suite passes, and every behaviour it pinned before this
  change is still pinned by a test afterwards.
- **SC-010**: The running application makes no request to any third-party host, and every
  dependency added by this feature is recorded in `README.md` with its tradeoff.
- **SC-011**: The header and both panes are on screen before any data has arrived, and at every
  moment a reader can tell for each region whether it is waiting, empty, or failed — the three are
  never rendered the same way.

## Assumptions

Reasonable defaults taken where the sketch and the conversation did not decide, recorded so they can
be overturned deliberately rather than discovered later.

- **The visual direction is fixed.** Direction A, "Front Desk", as chosen from three browser
  mockups: cool grey-green page, white surfaces, a teal accent, one sans-serif family, structure
  from hairline rules and background tint rather than floating shadowed cards, and modest corner
  radii. Producing further visual alternatives is out of scope.
- **The overflow control reveals its chats as a list**, opened from the strip rather than on a
  screen of its own. Which chats it holds is no longer an assumption — FR-012 and FR-012a fix the
  count and the ordering.
- **The evidence marker's two states are "served" and "needs a person".** The sketch describes a
  third — a booking state change — which cannot be rendered until the gap in FR-031 is filled, so
  this feature ships two.
- **The delete-a-chat confirmation stays as it is.** It already explains what is destroyed; this
  feature restyles it and does not reword or remove it.
- **The patient pane learns whether the assistant is paused from the existing console poll**, which
  already carries that per conversation. No new endpoint and no new request are introduced for
  FR-017.
- **A paused conversation is silent to the patient, and that cost is accepted.** Under FR-017 a
  patient waiting on a person sees exactly what a patient whose message failed to send sees:
  their own message and nothing else. The design pass makes this *more* noticeable rather than
  less, because the assistant's own replies now announce themselves. It is accepted here on the
  grounds that any notice is a claim about a human response time this system cannot make, and it
  is recorded so that a later phase can revisit it as a decision rather than discover it as a bug.
- **A staff queue cannot be prioritised from the screen, and that cost is accepted.** FR-010b shows
  no waiting time anywhere, so the console says how many conversations need a person but never
  which has waited longest. The ordering the server sends already puts the longest wait first, so
  the information is implicit in the list's order — but it is not stated, and a staff member
  reading three flagged rows cannot tell a two-minute wait from a two-hour one. `attention_since`
  is already on the wire, so filling this in later is a presentation change and nothing more.
- **A message can arrive unseen, and that cost is accepted.** Under FR-015a a reader who has
  scrolled up keeps their position and is told nothing when new content arrives below them. They
  find it by scrolling back down. The alternative — an affordance saying new messages arrived — was
  considered and dropped to keep the feature bounded; it is additive, and nothing in FR-015a has to
  change for it to be added later.
- **The "already at the bottom" test is not exercisable in the existing test tier.** Every element
  reports zero for scroll height, client height and scroll position under jsdom, so the condition
  evaluates as "at the bottom" for every test and the hold-position branch is never taken. The plan
  MUST decide how FR-015a is verified — by stubbing those measurements, or by deferring the branch
  to Phase 3b's browser suite — rather than leaving a requirement that passes vacuously.
- **Both stub panels are presentation only.** Neither polls, retries, nor holds a disabled control
  implying it could be turned on; each states what will appear and why it is not there.
- **This feature is scoped to `services/frontend`**, plus the Phase 3a section of `docs/ROADMAP.md`
  it implements. No backend service, schema or endpoint is touched.
- **Phase 3b's browser suite is not written here.** `tests/e2e/` stays as it is; this feature's own
  verification is the existing vitest tier plus manual checks in a real browser, which is what the
  roadmap's ordering intends — 3b is written against the surface 3a leaves behind.

## Dependencies

- **`docs/ROADMAP.md` Phase 3a** is binding on scope, including its record of the two capabilities
  the backend cannot yet serve. This feature implements that section; it does not widen it.
- **The existing frontend test hooks**, listed in `services/frontend/.claude/CLAUDE.md`, are a
  contract with both the current suite and the Phase 3b suite. FR-036 preserves them.
- **The two network modules** are consumed as they stand. If a requirement here appears to need a
  new field, that is a finding for the roadmap, not a change to make inside this feature.
