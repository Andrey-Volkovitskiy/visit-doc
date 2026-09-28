# Contract: the Connected apps tab

## Tab trigger

- The fourth trigger in the staff console's `TabsList`, after FAQ, pushed to the far right
  (`ml-auto`).
- It shows the lucide `Settings` gear icon at the same height as the text triggers, coloured
  `text-ink-muted` in every state, including selected. The selected state keeps the tab bar's
  existing underline treatment, so "secondary" reads from the colour, not from being unstyled.
- It has no visible text. `aria-label="Connected apps"` gives it an accessible name (FR-001), and
  a `title` with the same text shows on hover.
- `value="connected-apps"`. It takes part in the existing dirty-tab guard like the other tabs,
  though nothing in it is ever dirty.

## Panel (`ConnectedApps.tsx`)

In order:

1. **Explanation** (FR-002), a short paragraph:
   > Connect the Claude app to this console to ask, while you're away, how many conversations need
   > attention and how many bookings changed recently. Claude can read these two counts and
   > nothing else.
2. **Pairing** block:
   - Unavailable: one line naming the reason, e.g. *"Pairing is unavailable: no public address is
     configured (PUBLIC_BASE_URL)."* No button.
   - Available with no live code: a **Get pairing code** button.
   - A live code:
     - the connector address, with a copy button;
     - the code in large monospace (`XXXX-XXXX`), with a copy button;
     - "Expires in m:ss", counting down;
     - three numbered steps: add a custom connector in Claude with this address; press Connect;
       enter this code.
     - At zero the code block is replaced by "Code expired" and the button returns.
   - After a reload, only the remaining time is known, not the code (the server never re-sends
     it). The panel says "A code is active (expires in m:ss). Get a new one to see it." with the
     button.
3. **Paired apps** list:
   - one row per active grant: client name, "Paired 2 h ago", "Last used 5 min ago" or "Never
     used", and a **Revoke** button. An expired pairing is not shown, because the server does not
     return it;
   - revoking asks for confirmation in a dialog, then calls the route, and the row leaves on the
     response;
   - an empty list says "No apps are paired with this console."

The list re-reads `GET /console/connected-apps` on each console poll tick while the tab is open.
The countdown is `expires_in_seconds` from the last read minus time elapsed on the page.

## Test hooks

Role and name queries first, as `services/frontend/.claude/CLAUDE.md` requires. New `data-testid`s
only where role/name cannot address the element, added to that file's hook table:

| Hook | What it is |
|---|---|
| `pairing-code` | The shown pairing code |
| `connector-address` | The shown connector address |
| `pairing-countdown` | The remaining-time text |
| `connected-app` | One paired-app row |

## Network

Three functions in `src/lib/consoleApi.ts`, each checking `response.ok` through `ensureOk()`:
`fetchConnectedApps()`, `issuePairingCode()`, `revokeConnectedApp(grantId)`.
