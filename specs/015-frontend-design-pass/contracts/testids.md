# Contract: the `data-testid` surface

FR-036 makes every hook that exists today a contract with two consumers: the current vitest suite,
and the browser suite Phase 3b will write against the surface this feature leaves behind. **Existing
hooks may not be removed or renamed. New ones may be added.**

## The 37 that exist, and must survive

Extracted from `services/frontend/src/`, not from documentation.

| Component | Hooks |
|---|---|
| `App` | `patient-pane`, `staff-pane`, `chat-list-error` |
| `ChatList` | `chat-list`, `chat-list-item` |
| `ChatWindow` | `messages`, `no-chat`, `error`, `length-error` |
| `MessageView` | `message`, `role-label`, `attention-mark`, `request-outcome`, `outcome-question`, `outcome-unanswered`, `verdict-mark`, `citations` |
| `StaffConsole` | `staff-console`, `staff-conversations`, `staff-conversation`, `staff-no-conversations`, `attention-total` |
| `StaffThread` | `staff-thread`, `staff-no-thread`, `staff-error`, `staff-length-error`, `assistant-switch`, `pause-countdown` |
| `PractitionerAdmin` | `practitioner-admin`, `practitioner`, `working-range`, `no-practitioners`, `practitioner-error` |
| `FaqAdmin` | `faq-admin`, `faq-entry`, `no-faq-entries`, `faq-error` |

> **Doc drift found.** The list in `services/frontend/.claude/CLAUDE.md` holds 36 of these — it omits
> `staff-length-error`. FR-036 protects the hook regardless of whether the doc knows about it; the
> doc is corrected as part of this feature's Principle VI obligations.

### Two that move without being renamed

- **`attention-total`** moves from `StaffConsole` to the console header owned by `App` (FR-021).
  The hook keeps its name and meaning; only its owner changes. A test asserting it appears when
  `StaffConsole` is rendered *standalone* must move to `App`'s file — the property it protects
  (a zero renders as zero; the server's count is shown, not a row count) is still true and is
  re-expressed there, per FR-038.
- **The outcome hooks** (`request-outcome`, `outcome-question`, `outcome-unanswered`,
  `verdict-mark`, `citations`) move from `MessageView` into `OutcomeDisclosure`, and stop rendering
  unconditionally: they exist only once the marker is expanded (FR-027). Every existing assertion
  on them stays valid after one added step — activate the marker first.

## New hooks

Added only where there is no accessible handle. **Where a role and name already identify an
element, tests use that instead**: the staff tab set is `getByRole("tab", { name: "Practitioners" })`,
the composer is `getByLabelText("question")`, the disclosure is `getByRole("button", { expanded })`.
A testid for something already addressable is a second name for one thing.

| Hook | On | Requirement |
|---|---|---|
| `chat-overflow` | The control revealing tabs beyond the budget of four | FR-012 |
| `chat-overflow-item` | One chat inside that control | FR-012a |
| `working-indicator` | The three-dot indicator, one per in-flight turn | FR-016, FR-017 |
| `thread-greeting` | The empty-thread greeting in the patient pane | FR-019c |
| `staff-empty-thread` | A conversation open but holding no messages — **distinct from** `staff-no-thread`, which means none is selected | FR-025a |
| `char-count` | The composer's approaching-the-limit counter | FR-019a |
| `outcome-marker` | The `(i)` control itself | FR-026, FR-027 |
| `booking-outcome-stub` | The "not yet recorded" line inside an expanded block | FR-031 |
| `appointments-stub` | The 7-day appointments panel | FR-033 |
| `assistant-explanation` | The permanently visible sentence beside the switch | FR-024a |
| `practitioner-edit` | The edit view that replaces the practitioners tab | FR-035a |
| `faq-edit` | The edit view that replaces the FAQ tab | FR-035a |
| `discard-confirm` | The confirmation raised when leaving a dirty edit | FR-035b |
| `region-loading` | A region whose content has not arrived — carries `data-region` naming which | FR-010a |

### Three more added during implementation

The rule above is "new ones may be added", and these three were, for reasons the planning pass did
not foresee. Recorded here rather than left to be discovered in the source.

| Hook | On | Why it was needed |
|---|---|---|
| `sender-icon` | The burst indicator on the first message of a sender's run | FR-014 needs "an indicator appears on the first of a run and on none of the rest" to be assertable. `role-label` could not carry it: the patient's own messages deliberately have no text label, so a rule written against `role-label` would answer FR-014 for two senders out of three |
| `delete-confirm` | `ChatList`'s deletion confirmation | The confirmation moved into the vendored dialog, which renders its own close control *first*. `getByRole("dialog").querySelector("button")` therefore stopped meaning "the Delete button" and started meaning "the X", so the dialog needed a handle of its own |
| `char-count` on `StaffThread` | The staff composer's approaching-the-limit counter | Listed below only against the patient composer. FR-019a says **both** composers, so the staff side carries the same hook |

### Two of these live in a portal

`discard-confirm` and `chat-overflow-item` are rendered by Radix into `document.body`, not inside
the component that owns them. They are listed above under that component because that is where the
behaviour belongs, but a test must query them with `screen.*` rather than `within(container)`. See
the portal note in [`../test-impact.md`](../test-impact.md).

## Data attributes

Existing ones keep their meaning: `data-sender` on a message, `data-chat-id` on a chat row,
`data-emphasized` on a conversation, `data-position` and `data-verdict` on an outcome, `data-mark`
on an attention mark.

Three are added, each for the same reason: so a test can assert a state without asserting a colour
— which is also the point of FR-006, so the attributes and the requirement are one idea reached
from two directions.

- **`data-outcome-state`** on `outcome-marker`, valued `served` or `needs-person` (FR-030).
- **`data-mine`** on a message, present only on the ones taking the *reader's* own side. Each pane
  answers "whose side is this" for its own reader — the patient's in the messenger (FR-013), a
  staff member's in the console (FR-023) — so the two requirements are one mechanism and one
  attribute rather than two rules that could drift apart.
- **`data-burst-start`** on the first message of each consecutive run from one sender (FR-014),
  alongside `sender-icon`. The icon is what a reader sees; this is what says the run began, on the
  element the run belongs to.

## Rule for the implementation

A testid names *what a thing is*, never what it looks like. `outcome-marker`, not `blue-icon`;
`staff-empty-thread`, not `grey-panel`. A hook named after an appearance breaks when the appearance
changes, which in a design pass is the one thing guaranteed to happen.
