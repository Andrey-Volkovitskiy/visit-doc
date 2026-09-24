# Contract: Console UI (test hooks and behaviour)

## Test hooks

`services/frontend/.claude/CLAUDE.md` lists the hooks as a contract with the Phase 3b suite, and
says existing hooks may not be removed. This feature retires two hooks **on purpose**. Each named
an absence that no longer exists, so keeping it would mean keeping a hook that points at nothing
(FR-022). The CLAUDE.md table is updated in the same change, with a line recording why.

| Component | Retired | Added |
|---|---|---|
| `OutcomeDisclosure` | `booking-outcome-stub` | `booking-act`, carrying `data-operation` and `data-outcome` (`done` / `unchanged` / `refused` / `not_sent` / `unknown`, where a `null` outcome is rendered and attributed as `unknown`) |
| `PractitionerAdmin` | `appointments-stub` | `bookings-toggle`, `practitioner-week`, `week-day`, `week-appointment`, `week-empty`, `week-error` |

`bookings-toggle` is also addressable by role and name, `getByRole("button", { name: /show
bookings|hide bookings/i, expanded })`. The testid exists because a roster holds several such
buttons, and the test scopes each one to its `practitioner` block.

## Evidence marker (amends 015 FR-026 / FR-026a)

A marker is shown once per **turn**, on the message that anchors it — the reply, or the question
when nothing answered it. A turn is read from the reply's `reply_to_message_ids`; the messages it
names carry no marker of their own. (015 FR-026's supersession records why, and why this is not the
neighbour-pairing that requirement rejected.)

A marker is shown when the turn holds any of:

- non-empty `request_outcomes`;
- an `attention_mark`;
- non-null `booking_acts`.

Its state is `needs-person` when any of these hold:

- a message of the turn has a mark;
- a request is unanswered;
- **an act's outcome is `unknown` or `null`**.

Otherwise the state is `served`.

In the expanded block, each act is one line of words:

| Operation | Outcome | Wording |
|---|---|---|
| any | `done` | "Booked: *name*, *date* at *time*" / "Moved: *name*, *old date time* → *new date time*" / "Cancelled: *name*, *date* at *time*" |
| any | `unchanged` | "No change needed: …" |
| any | `refused` | "Refused (*reason in words*): …. Nothing was changed." |
| any | `not_sent` | "Not sent: …. Nothing was changed." |
| any | `unknown` or `null` | "Outcome unknown: … may or may not have been [booked / moved / cancelled]. Check the schedule." The word "unknown" appears in the text, never in colour alone. |

A missing name renders as "a practitioner not named in the record". The dates follow the format
the rest of the console uses. The 015 stub line is removed, and no replacement text appears on a
message without acts.

## Staff thread re-read

`StaffThread` re-reads when the poll row's `booking_acts_version` changes, as well as when its
`last_message_at` changes (contracts/booking-acts.md). An act settled after a turn failed without a
reply therefore replaces "unknown" within one poll interval (SC-007).

## Practitioner roster

- Every `practitioner` block ends with `bookings-toggle`, below the working hours.
  - It reads **Show bookings** with `aria-expanded="false"` while closed, and **Hide bookings**
    with `aria-expanded="true"` while open.
  - Every block starts closed.
- Opening a block reads `GET /console/practitioners/{id}/appointments?local_now=…` at that moment.
  While a block stays open it re-reads on every console poll tick, with at most one read in flight
  and late answers dropped. Closing a block stops its reads and removes `practitioner-week` from the
  DOM.
- `practitioner-week` renders exactly one of:
  - still loading: the `region-loading` pattern, with `data-region="practitioner-week"`;
  - a list: one `week-day` per local day that has appointments, in date order, each holding its
    `week-appointment`s in start order, and each appointment showing the patient name and
    start–end time;
  - `week-empty`: "Nobody is booked with *name* in the next seven days.";
  - `week-error`: "The appointments could not be read." for 503/504/network errors, or "This
    practitioner no longer exists." for a 404.

  A refresh that fails after a successful read shows `week-error` rather than silently keeping the
  stale list, so the screen never presents old data as current.
- The edit view (`practitioner-edit`) renders no appointments of any kind.
- The open or closed state lives in `PractitionerAdmin`, keyed by practitioner id. It survives a
  roster re-render, and resets when the component unmounts or the roster view is left.
