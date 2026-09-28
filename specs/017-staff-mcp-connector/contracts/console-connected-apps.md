# Contract: `/console/connected-apps`

Staff-console routes in `api/console.py`, resolving the session from the cookie like every console
route. A request with no session cookie is `404`, matching `_resolve_chat`'s behaviour.

## `GET /console/connected-apps`

```json
{
  "connector": {"available": true, "address": "https://visitdoc.ngrok.app/mcp"},
  "pairing_code": {"expires_in_seconds": 412},
  "grants": [
    {"id": "01J…", "client_name": "Claude", "paired_seconds_ago": 7200,
     "last_used_seconds_ago": 300}
  ]
}
```

- `connector` is either `{"available": true, "address": …}` or `{"available": false, "reason": …}`,
  where `reason` is one of `not_configured`, `not_https`, `has_path`. The SPA words each.
- `pairing_code` is `null` when no unused, unexpired code exists. The code itself is never
  returned here.
- `grants` lists active grants only, newest first. Revoked grants and grants expired after 30 days
  of disuse are omitted, so every listed grant is one that works. `last_used_seconds_ago` is
  `null` when never used.
- Ages are whole seconds computed on the database's clock (`now() - created_at`). The SPA renders
  them as relative text ("2 h ago") and never compares a server time with the browser's clock, the
  same rule `pause_seconds_remaining` follows.

## `POST /console/connected-apps/pairing-code`

Body: none.

| Case | Response |
|---|---|
| Connector available | `201 {"code": "K7QM-4XPD", "expires_in_seconds": 600, "address": "https://…/mcp"}`. Any earlier unused code of this session stops working. |
| Connector unavailable | `409 {"detail": "<reason>"}`. No code issued (FR-004). |

## `POST /console/connected-apps/{grant_id}/revoke`

Body: none.

| Case | Response |
|---|---|
| Grant belongs to this session (already revoked or not) | `204`. `revoked_at` is set if it was null. |
| Grant not in this session, or unknown | `404` |

The `WHERE` carries `session_id`, so another session's grant id does not resolve.
