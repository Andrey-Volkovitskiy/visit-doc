# Phase 0: test impact

All 12 test files read; 234 tests currently pass. FR-038 governs every row below: where a test fails
because behaviour deliberately changed, the property it protected is re-expressed against the new
behaviour, never deleted.

## Unaffected

`chatStream.test.ts`, `consoleApi.test.ts`, `sendKey.test.ts`, `useConsolePoll.test.ts` — pure logic
behind the contracts FR-037 freezes. Nothing in this feature can reach them.

## Affected

| File · test | FR | Property still true? | Change |
|---|---|---|---|
| App · reads nothing session-scoped until provisioned | FR-020, FR-025 | Yes — the gate is unchanged | Open the tab before asserting. The pre-mint half needs the tab opened *before* mint to stay meaningful |
| App · shows the provisioned roster without a reload | FR-020, FR-025 | Yes | Open the Practitioners tab first |
| App · reads them straight away for an existing session | FR-020, FR-025 | Yes | Open each tab first |
| App · shows no empty roster when provisioning failed | FR-020, FR-025 | Yes but **becomes vacuous** | Open the tab, *then* assert absence — see below |
| App · leaves the create control usable after a failed creation | FR-011 | Yes | `getByText("New chat")` breaks if the control becomes `+`; retarget to a stable accessible name |
| App · keeps the attention total visible | FR-021 | Yes | None — and it is the canary for a duplicated total |
| StaffConsole · renders how many need a person | FR-021 | **Not here** — moves to the header | Re-express in `App.test.tsx` |
| StaffConsole · renders a zero total as zero | FR-021 | Yes, wrong home | Move to `App.test.tsx` |
| StaffConsole · server's total, not a row count | FR-021 | Yes, wrong home | Move to `App.test.tsx` |
| StaffConsole · `renderConsole` helper | FR-021 | — | Drop the `attentionTotal` param and prop; 3 positional call sites |
| ChatWindow · red warning and blocks sending over the limit | FR-019a, FR-019b | Partly | `toHaveStyle({color:"rgb(255,0,0)"})` asserts an **inline** style that utility classes replace, and jsdom computes nothing for a class. Drop the colour assertion; add `toBeDisabled()` |
| ChatWindow · clears the warning when shortened back | FR-019a | Yes | **Only if the counter gets its own hook** — see below |
| ChatWindow · re-enables Send when askChat rejects | FR-019b | Yes, and strengthens | None |
| StaffThread · renders citations, unlike the patient pane | FR-026, FR-027 | **No** — behind a click now | Expand the marker first |
| StaffThread · marks an unreranked answer, and only that one | FR-026, FR-027 | **No** | Expand both, then count |
| StaffThread · shows no score anywhere | FR-026, FR-027 | Yes | Expand first, then scan |
| StaffThread · passes every outcome through to its own block | FR-026, FR-027 | **No** | Expand first |
| StaffThread · no outcome block when no FAQ half ran | FR-026 | Yes but **becomes vacuous** | Strengthen to "no *marker* is rendered" |
| StaffThread · switched mid-post can still send | FR-019b | Yes | `not.toBeDisabled()` **fails** — the new box is empty, a second reason to disable. Type first, then assert |
| StaffThread · sends nothing for whitespace | FR-019b | Yes but **becomes vacuous** | Add `toBeDisabled()` |
| StaffThread · assistant switch (7 tests) | FR-024a | Yes | None expected; the new sentence must not collide with `getByText` |
| MessageView · preserves newlines | FR-003 | Yes | `white-space: pre-wrap` stays inline — see decision below |
| **All component tests** | FR-004b | Yes, if they render at all | Vendored Radix primitives may need jsdom polyfills; `App.test.tsx` renders the whole tree, so a missing one fails tests unrelated to any dialog. **T000 settles this before anything is built** |
| MessageView · outcome tests (6) | FR-027 | Depends on ownership | Each gains an expand step — see decision below |
| PractitionerAdmin · ~15 of 20 | FR-035a | Yes | Each mutating test enters the edit view first |
| FaqAdmin · ~9 of 13 | FR-035a | Yes | Same |

## Four tests that would keep passing without testing anything

Left as they are, these become green for the wrong reason. Each is strengthened in the stage that
breaks it.

1. **App · "shows no empty roster when the session could not be provisioned at all"** — the serious
   one. It asserts the panel is absent and its fetch never happened; both become automatic once the
   panel sits behind a shut tab. **It would then pass with the session gate deleted outright** — and
   that gate is the regression it was written for. It must open the tab first.
2. **StaffThread · "sends nothing for whitespace alone"** and **ChatWindow · "blocks sending when
   over the limit"** — both click a control FR-019b disables, and a click on a disabled button never
   reaches the handler, so "not called" is free. Both must assert the disabled state explicitly.
3. **StaffThread · "draws no outcome block for a reply that ran no FAQ half"** — with no marker
   there is no block, so the absence proves nothing. Re-point it at the marker.

Two more are fragile rather than vacuous, and are noted for whoever touches them: MessageView's
staff-label test asserts exact whole-container `textContent`, so any visually-hidden label breaks
it; StaffConsole's ordering test compares row text to bare names, so FR-022's non-colour marking
breaks it if the marking is text inside the row.

## Plan decisions this analysis forces

**`white-space: pre-wrap` stays an inline style.** It is behaviour — it decides whether the newlines
a patient typed survive — not appearance, and a test can honestly assert it inline. As a utility
class (`whitespace-pre-wrap`) it becomes unassertable under jsdom, which computes nothing for a
class name, so the existing test would have to be deleted rather than adapted. The rest of
`MessageView`'s inline styles become utility classes. This is the one place the styling change would
otherwise cost a real assertion, which is why it is called out rather than swept along.

**Portalled content is not inside its component.** Radix's dialog and dropdown-menu — so the
discard confirmation (FR-035b) and the chat overflow (FR-012) — render their content into a portal
on `document.body`, outside the subtree the component returns. Three consequences the task list has
to respect: a query scoped with `within(container)` will not find them, and `MessageView.test.tsx`
already uses `within()` five times; `contracts/testids.md` lists `discard-confirm` and
`chat-overflow-item` in the components that *own* them, which is where they belong logically but
not in the DOM; and `screen.*` queries still work, because RTL queries `document.body`. Prefer
`screen` for anything behind a dialog or a menu, and reach for `within` only on content you know is
inline. A related trap: `data-testid` reaches a shadcn component only if it forwards its props —
verify the forwarding rather than assuming it, since a silently swallowed testid looks exactly like
a missing element.

**The counter and the error need separate hooks.** `char-count` (FR-019a) and `length-error` are
distinct: an existing test requires `length-error` to be *absent* at exactly 2000 characters, while
FR-019a requires the counter to be *present* there. One hook for both makes FR-019a unimplementable
without breaking a test that is still right.

**`OutcomeDisclosure` owns its own open state.** `MessageView` renders it; the marker and the block
live inside it; the open/closed boolean is its own `useState`. Nothing else reads that state, and
lifting it to `StaffThread` would put a `Set` in a component with no other interest in it. It
survives the 2-second poll repaint because the thread is keyed by message id, so React reconciles
to the same instance rather than remounting. This is the ownership question the analysis flagged,
and it costs MessageView's six outcome tests one expand step each.
