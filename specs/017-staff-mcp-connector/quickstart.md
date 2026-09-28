# Quickstart: Staff in the loop from the Claude app (Phase 4a)

How to see the feature working end to end with a real Claude account. The automated tiers cover
every rule in the contracts. This walk-through is the one thing they cannot do: the real Claude
connecting over a real public address.

## Prerequisites

- The stack runs as usual: `make migrate`, then `make services-up`.
- An **ngrok** account with a **static domain** (the free tier includes one), and `ngrok` installed
  in WSL. A domain that changes on each start breaks every pairing when it changes.
- A Claude account on the web and the Claude mobile app signed in to it. Custom connectors are
  available on every plan, including Free (one connector).

## 1. Expose the chat service and configure the address

```bash
ngrok http --url=<your-domain>.ngrok-free.app 8000     # chat, not the SPA and not the scheduler
```

In `.env`:
```
PUBLIC_BASE_URL=https://<your-domain>.ngrok-free.app
```

Restart the chat service (`make services-down && make services-up`): settings are read at start.

Check from WSL:
```bash
curl -s https://<your-domain>.ngrok-free.app/.well-known/oauth-authorization-server | jq .issuer
curl -si -X POST https://<your-domain>.ngrok-free.app/mcp | grep -i www-authenticate
```
Expected: the issuer equals `PUBLIC_BASE_URL`, and the second prints a `WWW-Authenticate: Bearer
resource_metadata="…/.well-known/oauth-protected-resource/mcp"` header.

## 2. Unavailable state (no Claude needed)

With `PUBLIC_BASE_URL` empty, or set to `http://…`, open the console's gear tab.
Expected: the explanation shows, pairing says why it is unavailable, and there is no button.
`POST /console/connected-apps/pairing-code` answers `409`.

## 3. Pair (User Story 1)

1. Open the SPA, then the gear tab at the far right of the staff console's tab bar. Click **Get
   pairing code**. Note the address and code, and watch the countdown run.
2. In claude.ai: **Customize → Connectors → Add custom connector**. Paste the address. Keep the
   default authentication and OAuth client settings, which use dynamic registration.
3. Press **Connect**. If ngrok shows its browser warning page, click through it once.
4. The VisitDoc page names the requesting app and `claude.ai`, and says access is read-only and
   limited to two counts. Type the code in lower case, without the hyphen (both are accepted).
5. Expected: Claude shows the connector as connected. Within 2 s the console's list shows a new row
   "Claude — Paired just now — Never used".
6. In ngrok's inspector (`http://localhost:4040`), open a `POST /mcp` request from Claude and note
   whether it carries an `Origin` header, and its value. This settles research R4's open point
   on the transport's allowed origins: with no `Origin` on any of them, remove `https://claude.ai`
   from `allowed_origins` in `chat/connectors/mcp_server.py`; with `Origin: https://claude.ai`,
   keep it. Record which in `evaluation/manual-walk.md`.

Negative checks on the same page, each from a fresh **Connect**:
- A wrong code shows "That code is not valid…", and the page stays.
- Five wrong codes show "This sign-in has expired…".
- The code from step 1, entered again after it was used, is refused the same way as a wrong code.
- Getting a new code in the console, then entering the older one, is refused.

## 4. Ask (User Story 2)

Prepare the state from the patient pane and the console:
- Escalate two conversations, e.g. "I want to speak to a person" in two chats.
- Through the patient pane, book three appointments, cancel one existing appointment and
  reschedule two.

In the Claude mobile app, with the connector enabled for the chat:
- "How many conversations need my attention?" Expected: 2, matching the console's attention count.
- "How many bookings changed in the last hour?" Expected: "Three appointments were booked, one
  cancelled and two rescheduled." Claude may reword the sentence; the counts must match.
- "…in the last 3 weeks?" Expected: Claude reports that the window is limited to 7 days.

Also check that no answer names a patient or quotes a message. The tools cannot return either, so
anything like that in Claude's reply would be invented by the model, not read from VisitDoc.

The console row's "Last used" updates to "just now".

## 5. Revoke (User Story 3)

1. Click **Revoke** on the row and confirm. The row disappears.
2. Ask Claude again. Expected: the connector fails and Claude asks to reconnect.
3. `DELETE /admin/sessions/<id>` on a session with a paired app, then ask again. Expected: the same
   refusal.

## 6. Automated tiers

```bash
uv run pytest services/chat/tests/test_connector_*.py services/chat/tests/test_oauth_*.py \
  services/chat/tests/test_pairing_code_repository.py
cd services/frontend && npm test -- ConnectedApps
```
Then the full `make test-unit`, once, at the end.
