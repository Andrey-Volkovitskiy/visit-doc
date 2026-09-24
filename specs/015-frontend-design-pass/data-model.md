# Phase 1 data model

**This feature persists nothing and changes no wire type.** FR-037 freezes `chatStream.ts` and
`consoleApi.ts`, so every type below already exists and is reproduced only where this feature reads
it. What is genuinely new is *display state* — state that lives in a component, describes what is on
screen, and dies with the mount.

## What is not changing

The payload types this feature consumes, unchanged: `ChatSummary`, `ChatListing`, `Message`,
`RequestOutcome`, `Citation`, `FaqVerdict`, `AttentionMark`, `ChatEvent` and its four variants
(`token`, `done`, `cancelled`, `silent`), `ConsoleConversation`, `ConsoleListing`, `AssistantState`,
`Practitioner`, `PractitionerWrite`, `WorkingRange`, `FaqEntry`.

No field is added, removed, renamed or reinterpreted. Where a requirement seemed to need a new
field, the spec recorded it as a roadmap gap instead — that is what FR-031 and FR-033 are.

## New display state

### `staffTab` — App

```
"chats" | "practitioners" | "faq"        default: "chats"
```

Which console section is open (FR-020). Owned by `App` rather than by the console, because the
attention count sits in the console header outside the tabbed region (FR-021) and both are the
header's concern.

**Consequence worth stating**: `PractitionerAdmin` and `FaqAdmin` now mount when their tab is first
opened, not on page load. Their existing mount-time fetch is unchanged, and the `sessionExists`
guard around them is unchanged — but it now guards a later moment as well as an earlier one. A
panel that mounts before a session exists still holds the refusal forever, which is the regression
that guard was added for.

### `dirtyTab`, `pendingTab` — App

```
dirtyTab:   "practitioners" | "faq" | null     default: null
pendingTab: "chats" | "practitioners" | "faq" | null   default: null
```

Which console section holds work that leaving would lose, and the section a switch was asked for
while it did (FR-035b).

**Owned by `App` because only `App` can act on it.** The *back* control out of an edit view is
guarded inside the section that owns the form, where `dirty` already lives. A **tab** switch cannot
be: Radix destroys the inactive panel to perform one, so by the time the section could notice, the
typed text is already gone. Only the shell that performs the switch can hold it back.

Two rules keep it honest:

- **A section may only clear its own flag.** `markDirty(tab, dirty)` sets it to `tab` or clears it
  only when the flag already names `tab`. One section is mounted at a time so they cannot currently
  collide — but a flag any section could clear is one a future second mounted section could clear
  on somebody else's behalf, and the bug that produces is a prompt that silently stops appearing.
- **The flag is retracted on unmount**, by the editor that set it, via an effect cleanup. This is
  the load-bearing half: the editor is destroyed by the very switch the flag guards, so a flag that
  outlived it would sit in `App` describing a form that no longer exists — and the next switch,
  from a section holding nothing, would be blocked by a prompt about work nobody can see or answer
  for. There is a test for exactly that lock-out.

`pendingTab` is not merged into `dirtyTab`. "Which section is dirty" and "where the reader asked to
go" are two facts, and one value carrying both would have to mean "not dirty" and "no switch
pending" with the same `null`.

### `chatsLoaded`, `historyLoaded`, `threadLoaded` — App, ChatWindow, StaffThread

```
boolean          default: false
```

Whether each region's read has come back at all.

**Deliberately not inferred from an empty array.** FR-010a needs waiting, arrived-empty and failed
told apart, because they ask three different things of the reader — and `length === 0` answers the
first two identically. Each latch is what makes the middle one nameable: it is the only condition
under which the patient thread greets (FR-019c), the staff thread says the conversation is empty
(FR-025a), and the conversation rail says there are none rather than that it is still looking.

Reset on chat/conversation switch along with the rest of the pane's display state, so an arriving
reader never sees the previous thread's answer treated as this one's.

### `expanded` — OutcomeDisclosure

```
boolean          default: false
```

Whether this one marker's block is open (FR-027). **Owned by the disclosure itself, not lifted.**
Nothing else reads it, and FR-028's "more than one open at once" falls out for free when each
instance holds its own — a `Set` in `StaffThread` would put the state in a component with no other
interest in it.

**It survives the 2-second poll repaint** because the thread is keyed by message id: the poll
replaces the array, React reconciles to the same instances, and the state rides along. Keyed by
array index it would not — an arriving message would shift every open block onto a different
message. The key is load-bearing, not cosmetic.

### `pinnedToBottom` — ChatWindow, StaffThread

Not stored. Computed at the moment new content arrives, from the scroll container's own numbers:

```
isPinnedToBottom(scrollTop, scrollHeight, clientHeight, threshold) -> boolean
```

A pure function in `src/lib/scroll.ts` (FR-015a; `research.md` Decision 4). Deriving it on demand
rather than holding it in state is what keeps it correct when the reader scrolls without any React
event firing — a stored flag would go stale on exactly the interaction it exists to detect.

`threshold` exists because a container scrolled to its end can be a fraction of a pixel short of
`scrollHeight - clientHeight` at fractional zoom levels. A small tolerance makes "at the bottom"
mean what a reader means by it.

### `editing` — PractitionerAdmin, FaqAdmin

```
{ mode: "list" }
| { mode: "edit", id: <entity id> }
| { mode: "create" }                    default: { mode: "list" }
```

Which view the tab shows (FR-035a). A discriminated union rather than two booleans and a nullable
id, so "creating and editing at once" and "editing with no id" are unrepresentable rather than
guarded against.

### `dirty` — the edit views

```
boolean          default: false
```

Whether the open form differs from what was loaded (FR-035b, `research.md` Decision 5). Gates the
confirmation on leaving; a clean form leaves silently.

### `confirmingDiscard` — the edit views

```
boolean          default: false
```

Whether the discard confirmation is open. The same idea as `ChatList`'s existing `confirmingId`,
but not the same shape: a vendored dialog takes `open` and `onOpenChange`, so this is a controlled
boolean handed to a component rather than a flag a conditional render reads. The state is ours; the
open/close mechanics are the primitive's.

## Derived values — no state added

| Value | Derived from | Requirement |
|---|---|---|
| Which tabs show directly, which overflow | `chats`, `activeChatId`, the constant 4 | FR-012, FR-012a |
| Whether a message starts a sender burst | the previous message's `sender` | FR-014 |
| Whether a message carries a marker | that message's own `request_outcomes !== null \|\| attention_mark !== null` — never a neighbour's | FR-026 |
| Marker state: served or needs-a-person | the message's own outcomes' verdicts, or its attention mark | FR-026a, FR-030 |
| Whether the working indicator shows | the turn is in flight **and** `assistantMayReply` | FR-016, FR-017 |
| Whether Send is disabled | message empty/whitespace, or over 2000 | FR-019b |
| Whether the greeting shows | history loaded **and** thread empty | FR-019c |
| `assistantMayReply` for the patient pane | the poll row for the active chat, `?? true` | FR-017, `research.md` Decision 3 |

Each is computed at render from data already held. None becomes state: a second copy of a value the
props already carry is a second thing that can disagree with them.

## One constant, one place

```
CHAT_TAB_BUDGET = 4
```

FR-012 requires the number be declared once and never derived from a measurement. It is a module
constant in `ChatList.tsx`, exported so its test can reference it rather than hard-coding `4` in
two places and drifting.

`MAX_MESSAGE_LENGTH` (2000) and `MAX_REPLY_LENGTH` (2000) already exist in `ChatWindow.tsx` and
`StaffThread.tsx` respectively and stay where they are. They are deliberately not merged: each
mirrors its own endpoint's `max_length`, and a single shared constant would quietly couple two
contracts that happen to agree today.
