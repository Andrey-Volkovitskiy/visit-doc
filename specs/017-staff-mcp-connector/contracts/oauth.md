# Contract: the sign-in server (OAuth 2.1 for Claude)

Served by the chat service. `{BASE}` is `PUBLIC_BASE_URL` (R4). When the connector is unavailable,
every route here answers `503` with `{"error": "temporarily_unavailable", "error_description":
"<reason>"}`, and `/oauth/authorize` renders the error page with that reason.

## Discovery

### `GET /.well-known/oauth-protected-resource/mcp` — served by the MCP SDK (R1)

```json
{
  "resource": "{BASE}/mcp",
  "authorization_servers": ["{BASE}"],
  "scopes_supported": ["console:read"],
  "bearer_methods_supported": ["header"]
}
```

### `POST /mcp` without a valid token — served by the MCP SDK

`401`, with `WWW-Authenticate: Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"`.
An expired or revoked token gets the same `401` with `error="invalid_token"` added.

### `GET /.well-known/oauth-authorization-server`

```json
{
  "issuer": "{BASE}",
  "authorization_endpoint": "{BASE}/oauth/authorize",
  "token_endpoint": "{BASE}/oauth/token",
  "registration_endpoint": "{BASE}/oauth/register",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"],
  "scopes_supported": ["console:read", "offline_access"]
}
```

`offline_access` is listed so Claude asks for a refresh token. It grants nothing beyond that.
`client_id_metadata_document_supported` is deliberately absent (R8).

## `POST /oauth/register` (JSON)

Request, as Claude sends it:
```json
{"client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
 "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
 "token_endpoint_auth_method": "none"}
```

| Case | Response |
|---|---|
| Valid | `201` with the request's metadata plus `client_id` and `client_id_issued_at`. No `client_secret`. |
| `redirect_uris` is not exactly Claude's callback | `400 {"error": "invalid_redirect_uri"}` |
| `token_endpoint_auth_method` other than `none`, or an unsupported grant or response type | `400 {"error": "invalid_client_metadata"}` |

## `GET /oauth/authorize`

Query: `response_type=code`, `client_id`, `redirect_uri`, `code_challenge`,
`code_challenge_method=S256`, `state` (optional), `scope` (optional, default `console:read`),
`resource` (optional).

| Case | Response |
|---|---|
| Unknown `client_id`, or `redirect_uri` not equal to the client's | `400`, error page, **no redirect** |
| Any other invalid parameter (missing challenge, method not `S256`, `response_type` not `code`, `resource` not `{BASE}/mcp`, a scope other than `console:read`/`offline_access`) | `302` to `redirect_uri?error=invalid_request&state=…` |
| Valid | `200`, the pairing page, with a new authorization request stored (R6) |

The pairing page shows:
- the client's registered name and the **redirect host** (`claude.ai`);
- that access is read-only and limited to two counts: conversations needing attention, and recent
  booking changes;
- a code field (`autocomplete="one-time-code"`, accepts upper or lower case, with or without the
  hyphen) and a hidden `request` field;
- a *Connect* button.

## `POST /oauth/authorize` (form)

Fields: `request`, `code`.

| Case | Response |
|---|---|
| `request` unknown, expired, completed or at 5 failures | `400`, error page: "This sign-in has expired. Start again from Claude." |
| Code wrong, expired or used | `200`, the pairing page again with "That code is not valid. Get a new one from the Staff Console if it has expired." The failure count goes up. At the 5th failure, the expired page instead. |
| Code right | `302` to `redirect_uri?code=<authorization code>&state=<state>` |

The same message covers wrong, expired and used codes, so a guesser learns nothing from which it
was.

## `POST /oauth/token` (`application/x-www-form-urlencoded`)

All errors are `400` JSON `{"error": "<code>", "error_description": "…"}` per RFC 6749 §5.2, with
`Cache-Control: no-store`.

**Authorization code**: `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`,
`code_verifier`, and optionally `resource`.

| Case | `error` |
|---|---|
| Code unknown, expired or used; `client_id` or `redirect_uri` not matching the code's; `BASE64URL(SHA256(code_verifier))` not equal to the stored challenge | `invalid_grant` |
| A required field missing | `invalid_request` |
| Unknown `client_id` | `invalid_client` |

**Refresh**: `grant_type=refresh_token`, `refresh_token`, `client_id`.

| Case | `error` |
|---|---|
| Token unknown or expired, or its grant revoked | `invalid_grant` |
| Token already used: the grant is revoked (reuse detection), then | `invalid_grant` |
| `client_id` not the grant's | `invalid_grant` |

**Success**, for both grants, `200`:
```json
{"access_token": "…", "token_type": "Bearer", "expires_in": 3600,
 "refresh_token": "…", "scope": "console:read"}
```

Every response must arrive within Claude's limits: 10 s for code exchange and 30 s for refresh.
Each is a handful of indexed statements.

## Errors unsupported on purpose

- No `client_credentials` grant, which Claude does not use.
- No `/oauth/revoke`. Revocation is a console action. A client that drops a token simply stops
  using it, and the refresh token's 30-day life bounds it.
