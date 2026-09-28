# Research: Staff in the loop from an AI assistant app (Phase 4a)

Each entry records a decision, why it was made, and what else was considered. The spec left no
`[NEEDS CLARIFICATION]` open. The unknowns resolved here are the ones planning found: which
library carries MCP, who implements the sign-in server, how the connector address is configured,
and how each of the spec's security rules becomes a storage or query shape.

External facts this relies on, all checked on 2026-09-28:
- Claude's connector requirements: [Authentication for connectors](https://claude.com/docs/connectors/building/authentication)
  and [Add a connector that isn't in the directory](https://claude.com/docs/connectors/custom/remote-mcp).
- The MCP Python SDK v2 docs: [Authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/),
  [Add to an existing app](https://py.sdk.modelcontextprotocol.io/run/asgi/) and
  [Testing](https://py.sdk.modelcontextprotocol.io/get-started/testing/).

---

## R1 — The MCP SDK serves `/mcp` and checks tokens; it does not run the sign-in server

**Decision**: Add the official `mcp` package (v2 line) to `chat`. Use it only as the **resource
server**:
- an `MCPServer` with the two tools;
- a `TokenVerifier` implementation that looks the bearer token up in Postgres (R7);
- `AuthSettings` with `issuer_url` and `resource_server_url` derived from `PUBLIC_BASE_URL` (R4),
  and `required_scopes=["console:read"]`.

The SDK then answers an unauthenticated call with the `401` and its
`WWW-Authenticate: Bearer resource_metadata="…"` header, and serves the RFC 9728 document at
`/.well-known/oauth-protected-resource/mcp`. Tools read the caller with `get_access_token()`.

**Rationale**:
- The SDK's own docs say its embedded authorization server (`auth_server_provider=`) "predates the
  AS/RS separation that the MCP authorization spec is built around. New servers should not reach
  for it." The resource-server half has no such warning and is the part the spec's discovery
  behaviour depends on.
- Writing the MCP transport (JSON-RPC, session headers, protocol versions) ourselves buys nothing.
  Writing the token check ourselves is one indexed lookup, and that part is ours anyway (R7).

**To verify first at implementation**, because the docs describe them but nothing here has run them
yet:
- that mounting `streamable_http_app()` into the FastAPI app serves **both** `/mcp` and
  `/.well-known/oauth-protected-resource/mcp`, and nothing else;
- the exact v2 names (`MCPServer`, the stateless option) against the installed version;
- that the chat lifespan must enter `mcp.session_manager.run()`, since mounting disables the
  sub-app's own lifespan.

The first test of the MCP surface asserts those three paths' status codes, so a wrong assumption
fails there rather than in front of Claude.

**Verified against the installed `mcp` 2.2.0 (T002, 2026-09-28)**, by a scratch server mounted in
a FastAPI app and driven over `httpx.ASGITransport`:

| Name the plan uses | Actual name in 2.2.0 |
|---|---|
| server class | `mcp.server.mcpserver.MCPServer(name, token_verifier=, auth=, tools=)` |
| stateless option | `streamable_http_app(stateless_http=True, json_response=True, transport_security=…)` returns a Starlette app |
| session manager | `MCPServer.session_manager.run()`, an async context manager that **raises if entered twice on one instance**. The chat lifespan runs once per `TestClient`, so the server is built per lifespan, not at import |
| `TokenVerifier` | `mcp.server.auth.provider.TokenVerifier` (a `Protocol` with `verify_token(token) -> AccessToken | None`); `AccessToken(token, client_id, scopes, expires_at, resource, subject, claims)` |
| `AuthSettings` | `mcp.server.auth.settings.AuthSettings(issuer_url, resource_server_url, required_scopes, validate_token_resource)`. `validate_token_resource` must be set explicitly or construction warns; it is `False` here because the verifier's lookup is the audience check - every token this server issues is for its one resource |
| `get_access_token()` | `mcp.server.auth.middleware.auth_context.get_access_token()`; it does reach a tool handler in stateless mode |
| `TransportSecuritySettings` | `mcp.server.transport_security.TransportSecuritySettings(allowed_hosts, allowed_origins)`; a `Host` not in the list answers `421` |
| tool errors | `mcp.server.mcpserver.exceptions.ToolError`, rendered as `isError: true` with the text prefixed `Error executing tool <name>: ` |

The three things to verify:
- **Only the two paths.** Mounting the Starlette app at `/` would hand every unmatched path to it
  and replace FastAPI's JSON `404` with Starlette's plain one. Instead the sub-app is added as two
  plain `Route`s, `/mcp` and `/.well-known/oauth-protected-resource/mcp`, each with the sub-app as
  its ASGI endpoint; the sub-app routes on the unchanged path. An unknown path keeps FastAPI's `404`.
- **The unauthenticated answer** is `401` with `WWW-Authenticate: Bearer error="invalid_token",
  error_description="Authentication required", resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"`.
  The SDK adds `error="invalid_token"` to every `401`, including a request that sent no token.
- **The lifespan.** Confirmed: the sub-app's own lifespan never runs when it is routed to this way,
  so the chat lifespan enters `session_manager.run()` itself.

Two details the contract needs that the decorator does not give: the SDK's argument validation
reports a Pydantic error rather than the contract's sentence, and its generated input schema has no
`additionalProperties: false`. So `minutes` is declared with an explicit JSON schema and checked in
the handler, which raises `ToolError` with the contract's message, and the tools are built as `Tool`
objects whose `parameters` are closed before they are passed to `MCPServer(tools=…)`.

**Alternatives considered**:
- *The SDK's embedded authorization server.* Rejected on the SDK's own advice above.
- *No SDK, hand-written JSON-RPC.* Rejected: the protocol surface is the part most likely to change
  between spec revisions, and the SDK tracks it.

## R2 — The sign-in server is four small routes written in `chat`

**Decision**: A new router, `api/oauth.py`, serves:
- `GET /.well-known/oauth-authorization-server` (RFC 8414 metadata);
- `POST /oauth/register` (RFC 7591 dynamic client registration);
- `GET /oauth/authorize` and `POST /oauth/authorize` (the pairing-code page and its submission);
- `POST /oauth/token` (authorization-code exchange and refresh).

The logic behind them lives in a service module, `chat/connectors/`, with repositories taking the
`AsyncSession` as their first parameter, as every repository here does.

**Rationale**:
- The spec's identity bridge is a pairing code tied to an anonymous session. No off-the-shelf
  authorization server has that concept, so the one step that matters would be custom code in any
  of them.
- The surface is small and fully specified by Claude's requirements: public clients only, PKCE S256
  only, one scope, one redirect URI, authorization-code and refresh grants only.

**Alternatives considered**:
- *Authlib.* A general authorization-server framework for a four-route server with one client type
  and one grant pair, with no first-class FastAPI integration. Its generality is exactly the
  configuration surface this feature does not want.
- *An external identity provider (Auth0, WorkOS).* Needs staff accounts, which the spec rules out
  and Phase 1d decided against, and adds a third-party dependency to a local demo.

## R3 — The sign-in page is server-rendered with Jinja2, autoescaping on

**Decision**: Add `jinja2`. `/oauth/authorize` renders two small templates: the pairing form and an
error page. No JavaScript, one inline style block using the SPA's colour values.

**Rationale**:
- The page opens in a browser that has no VisitDoc cookie and may not be able to reach the SPA's
  dev server at all: ngrok points at the chat service (R4). It must be served by the chat service.
- Every value on the page comes from the caller (client name, redirect host, state). Autoescaping
  by default is safer than remembering `html.escape` at each interpolation.
- `markupsafe` is already in the lock, so the addition is Jinja2 itself.

**Alternatives considered**:
- *f-strings with `html.escape`.* One forgotten call is an injection on the page that asks for a
  secret.
- *A route in the SPA.* The SPA is not what ngrok exposes, and the page must work before any
  session exists in that browser.

## R4 — One setting, `PUBLIC_BASE_URL`, and everything public derives from it

**Decision**: `Settings.PUBLIC_BASE_URL: str = ""`, e.g. `https://visitdoc.ngrok.app`. From it:
- the issuer: `PUBLIC_BASE_URL`;
- the connector address shown in the tab: `PUBLIC_BASE_URL + "/mcp"`;
- the resource in the protected-resource metadata: the same;
- the MCP transport's allowed host: its hostname (the SDK refuses any other `Host` with `421`, and
  ngrok forwards the public hostname).

A pure function `connector_config(settings) -> ConnectorConfig | ConnectorUnavailable` decides
availability once. It is unavailable when the value is empty, is not `https://`, or carries a path,
query or fragment. `ConnectorUnavailable` carries the reason the tab shows.

When unavailable, the console refuses to issue codes (FR-004), and the OAuth and MCP routes answer
`503` with a fixed body. They cannot publish metadata naming an issuer that does not exist.

**Rationale**: the spec requires the address to come from configuration, and an address that is
not HTTPS cannot be used by Claude at all. Deciding availability in one function keeps the tab, the
metadata and the transport from disagreeing about it.

**The transport's allowed origins (decided 2026-09-28, T064).** Besides the host, the SDK's
transport guard refuses a request whose `Origin` header is not on its list, with `403`; a request
with no `Origin` passes. The list holds the issuer and `https://claude.ai`. The plan named only
the configured host. Claude is kept because it is not yet known whether the calls Claude makes to
`/mcp` carry `Origin: https://claude.ai`: if they do and it is not listed, every tool call fails
with `403`, and that would surface only in the live walk. Allowing it costs almost nothing: the
guard exists to stop a web page in someone's browser reaching an MCP server with no
authentication, and every call here needs a bearer token, which a browser never attaches on its
own. **To settle in T061**: read the `Origin` of Claude's `/mcp` requests in ngrok's inspector
(`http://localhost:4040`). If none carries one, the entry is dead weight and is removed; if they
do, it is proven necessary, and this note is updated to say so.

**Alternatives considered**:
- *Derive the URL from the request's `Host`.* The SPA runs at `localhost:5173`, which is exactly
  the wrong answer. Trusting `X-Forwarded-Host` would let any caller choose the issuer.

## R5 — A pairing code is a row keyed by its session

**Decision**: `mcp_pairing_codes` with `session_id` as its **primary key**.
- Issuing a code upserts that row with a new hash, a new expiry and `used_at = NULL`. The earlier
  code stops working because its hash is no longer stored (FR-007). There is no invalidation step
  that could be forgotten.
- Consuming a code is one conditional `UPDATE … SET used_at = now() WHERE code_hash = :h AND
  expires_at > now() AND used_at IS NULL RETURNING session_id`. Exactly one of two concurrent
  submissions gets a row back (FR-009).
- The code is 8 characters from the Crockford base32 alphabet (40 bits), shown as `XXXX-XXXX`, and
  compared after uppercasing and removing the hyphen. Stored as SHA-256 (FR-008).
- The expiry is compared against the database's clock, as the assistant pause already is.

**Rationale**: "at most one usable code per session" becomes a property of the key, not a rule
each writer must remember.

On the hash: 40 bits is small enough that an unsalted SHA-256 could be brute-forced offline from a
stolen database. It is kept because the code is valid for 10 minutes and once, so a stolen hash is
worth nothing by the time it has been cracked. A keyed hash would need a new server secret, which
buys nothing against that window.

**Alternatives considered**:
- *A codes table with many rows per session and an "invalidate others" update.* Two writes where
  one suffices, and the invariant lives in application code.

## R6 — The authorization request is stored server-side; its id is the form's only secret

**Decision**: `GET /oauth/authorize` validates the query and stores an `oauth_authorization_requests`
row holding client id, redirect URI, code challenge, `state`, scope, resource, a failed-attempt
count and a 10-minute expiry. Its id is 32 random bytes. The form carries only that id. `POST`
reloads the row by id and trusts nothing else from the form except the code.

A wrong code increments `failed_attempts` with a conditional `UPDATE`. At 5 (FR-013) the request is
dead and the page says to start again from Claude.

Invalid `client_id` or `redirect_uri` renders the error page **without** redirecting, as OAuth
requires for an unverified redirect target. Other invalid parameters redirect back with
`error=invalid_request` and the original `state`.

**Rationale**:
- Echoing the parameters back through hidden fields would let a caller change the redirect URI or
  code challenge between the check and the use.
- No separate CSRF token is needed. The page authenticates nothing by cookie, so there is no
  ambient credential for a forged post to borrow. The only thing a post can prove is knowledge of
  the pairing code, and the unguessable request id already ties the post to the request it
  answers.
- The per-request attempt cap and the code's 40 bits and 10-minute life together bound guessing: an
  attacker opening fresh requests gets 5 tries each against 2^40 codes.

## R7 — Tokens are opaque, hashed, and grouped under a grant

**Decision**:
- Access and refresh tokens are 32 random bytes, URL-safe, stored as SHA-256 in `oauth_tokens`
  with a `kind`, an expiry and, for refresh tokens, a `used_at`.
- A **grant** (`oauth_grants`) is created at the code exchange, not at the pairing. It carries the
  session, the client, the client's name as registered, and `created_at`, `last_used_at`,
  `revoked_at`. Every token belongs to one grant.
- The access token lives 1 hour. The refresh token lives 30 days and is replaced on every use, so
  30 days is an **inactivity** limit (FR-020).
- Refresh is one conditional `UPDATE … SET used_at = now() WHERE token_hash = :h AND kind =
  'refresh' AND used_at IS NULL AND expires_at > now() RETURNING grant_id`, then the new pair is
  inserted in the same transaction. A refresh token that matches but is already used revokes its
  grant (reuse detection) and answers `invalid_grant`.
- `verify_token` is one `SELECT` joining the token to its grant, with `revoked_at IS NULL` and the
  expiry in the `WHERE`. It updates `last_used_at` at most once a minute, so a busy app does not
  write on every call.
- The session a tool answers for comes from the grant, carried in the SDK's `AccessToken.subject`.
  No tool takes a session argument (FR-014).

**Rationale**:
- A database lookup makes revocation effective on the next call (FR-021). A JWT would stay valid
  until its expiry. The lookup is one primary-key read.
- Creating the grant at exchange time means an abandoned sign-in leaves no grant in the console
  list.

**Alternatives considered**:
- *JWT access tokens.* Revocation needs a deny-list, which is the database lookup again plus a
  signing key to manage.

## R8 — Registered clients are global rows, and only Claude's callback is accepted

**Decision**: `oauth_clients` holds `client_id` (random), `client_name` and the one redirect URI.
`POST /oauth/register` accepts a registration only when `redirect_uris` is exactly
`["https://claude.ai/api/mcp/auth_callback"]` (FR-012), `token_endpoint_auth_method` is absent or
`none`, and the grant types are within `authorization_code` and `refresh_token`. The client name is
trimmed to 100 characters. Anything else answers `400 invalid_redirect_uri` or
`invalid_client_metadata`.

The server metadata advertises `token_endpoint_auth_methods_supported: ["none"]` and **does not**
advertise `client_id_metadata_document_supported`, so Claude uses DCR as the spec's FR-010 names.

**Rationale**: a client row is not session data and can read nothing. It becomes session-bound only
through a grant, and every read of a grant carries the session predicate. Claude registers a new
client on each fresh connection, so rows accumulate slowly. Pruning clients with no grant is
housekeeping and is deferred.

## R9 — The two counts are two scoped queries over existing state

**Decision**:
- **Needs attention**: `chat_repository.count_needing_attention(session, session_id)` counts chats
  of the session matching the existing `_EMPHASIZED` expression. The console listing and this
  count therefore share one definition (FR-016). The listing computes its total in Python from the
  same rows, so the two agree by construction.
- **Recent booking changes**: `booking_act_repository.count_recent_changes(session, session_id,
  minutes)` returns, for the window `now() - minutes`, on the database's clock:
  - per operation, the number of **distinct** `appointment_id`s with an act `outcome = 'done'`
    and `settled_at` in the window. The staff member asks about appointments ("three appointments
    were booked"), so the unit is the appointment, not the conversation or the act. Two reschedules
    of one appointment count as one rescheduled appointment. A booking and a cancellation of the
    same appointment count once under each, because both happened;
  - `unknown`: the number of acts either settled as `unknown` in the window, or with `outcome IS
    NULL` and `created_at` in the window.
- A new index `ix_booking_acts_session_settled (session_id, settled_at)` serves the query.
  `booking_acts` has no index on `settled_at` today.

The unknown count counts **acts**, not appointments. An unknown booking may have no appointment id
at all. The count says how many changes need checking, which is what a person does with it.

**To verify first at implementation**: that every act settled `done` carries an `appointment_id`.
`COUNT(DISTINCT appointment_id)` skips NULLs, so a done booking without one would silently go
uncounted. If the settle path does not already guarantee it, the migration adds `CHECK (outcome IS
DISTINCT FROM 'done' OR appointment_id IS NOT NULL)`, after confirming no existing row violates it.

**Verified (T009, 2026-09-28): not guaranteed, so the CHECK is added.** `settle` writes
`appointment_id = COALESCE(<from the answer>, <from begin>)`. A reschedule or a cancellation is
begun with its id, so it always has one. A booking is begun without one and takes it only from the
answer: `settlement_from` reads `appointment.id` through `_text`, which turns an empty string into
`None`, and a proto3 string the scheduler left unset arrives as exactly that. So a `booked` answer
with an empty id settles `done` with a NULL id today. The dev database holds 20 done acts (8 book,
7 reschedule, 5 cancel) and none without an id, so the constraint is added without a backfill.
With it, that answer's settle fails on the CHECK, the registry logs `booking_act.settle_failed`
and the act stays NULL, which the count reads as `outcome_unknown`: a done booking whose
appointment is not known is honestly an unknown change.

The `created_at` use needs saying: `BookingAct`'s comment calls it "diagnostic only, never used for
ordering". A window is not an ordering. An unsettled act has no `settled_at`, so its creation time
is the only moment it has. The comment is amended to say so in the same change.

`refused`, `unchanged` and `not_sent` are counted nowhere, because none of them changed the
schedule. The tool description says so, so the model does not present "0 cancellations" as "no
cancellation was attempted".

**Rationale**: both are single scoped queries on the chat database. The scheduler is not called.

## R10 — The window is a whole number of minutes, 1 to 10080

**Decision**: `count_recent_booking_changes(minutes: int)`. The schema bounds it (1–10080, i.e. 7
days), and the handler rejects anything outside with an error naming the range (FR-018). The tool
description tells the model to convert hours to minutes. The result echoes the window it used.

**Rationale**: one unit removes a "minutes or hours" field pair whose combinations would need their
own rules. The model converts "last 2 hours" reliably, and the echo makes a wrong conversion
visible.

## R11 — Tool results carry structured content and a sentence ready to relay

**Decision**: each tool returns structured content (the counts as JSON) plus one sentence written
the way a person would say it. For the booking tool that is the spec's FR-017a form, e.g. "Three
appointments were booked, one cancelled and two rescheduled.":
- numbers one to ten as words, 11 and above as digits;
- the order is fixed: booked, cancelled, rescheduled;
- operations with a zero count are left out, joined with commas and a final "and";
- the subject agrees with the first count ("One appointment was booked"). Later clauses reuse its
  verb ("…, two cancelled");
- all zero gives "No appointments were booked, cancelled or rescheduled.";
- when `outcome_unknown > 0`, a second sentence: "Two changes have an unknown outcome — check the
  Staff Console."

The sentence is built by one pure function, `describe_recent_changes(RecentBookingChanges) -> str`,
tested as a table.

The tool description asks the model to relay the sentence as given. That is a request, not a
guarantee: Claude writes its own reply, and may reword it. The sentence is what makes the intended
wording the easiest one to give. The structured part is what the tests assert, and the sentence's
own tests pin its wording.

The needs-attention result is `{"needing_attention": n}`. The booking result is `{"window_minutes":
m, "booked": b, "rescheduled": r, "cancelled": c, "outcome_unknown": u}`. Neither carries a name, a
message or an id (FR-019).

## R12 — Console routes and the SPA tab

**Decision**:
- `GET /console/connected-apps` returns availability (with the reason when unavailable), the
  connector address, the current code's remaining seconds if one is live (never the code), and the
  session's grants.
- `POST /console/connected-apps/pairing-code` issues a code and returns it once, with
  `expires_in_seconds` and the connector address.
- `POST /console/connected-apps/{grant_id}/revoke` sets `revoked_at` under the session predicate.
  It is idempotent: `404` for a grant not in this session, `204` otherwise.

All three resolve the session from the cookie, like every console route. Only active grants are
listed: revoked grants and grants expired after 30 days of disuse are left out, so every row is a
pairing that still works.

The SPA gets a fourth tab, `value="connected-apps"`, as a `TabsTrigger` holding lucide's `Settings`
gear in `text-ink-muted`, `aria-label="Connected apps"`, pushed right with `ml-auto`. A new
component `ConnectedApps.tsx` renders the explanation, the code panel and the grant list. Its calls
go through `consoleApi.ts`, as the frontend guide requires.

The countdown runs from the server's `expires_in_seconds` minus elapsed time on the page, never
from a client clock reading of an absolute time, following the assistant pause countdown. The list
re-reads on the existing 2 s poll tick while the tab is open, so a pairing made on the phone
appears without a reload.

**Rationale**: `/console/*` already reaches the chat service through the Vite proxy, so no proxy
change is needed. The OAuth and MCP routes are never called by the SPA.

## R13 — Session deletion needs no code, only foreign keys

**Decision**: `mcp_pairing_codes.session_id`, `oauth_authorization_codes.session_id` and
`oauth_grants.session_id` reference `sessions` with `ON DELETE CASCADE`. `oauth_tokens.grant_id`
references `oauth_grants` with `ON DELETE CASCADE`. `chat_repository.delete_session` already
deletes the session row, so FR-022 follows.

Authorization requests and clients are not session-owned and are untouched. Requests expire in 10
minutes.

## R14 — Logging, redaction and tracing

**Decision**:
- Events:
  - `connector.pairing_code_issued`
  - `connector.client_registered` (and `connector.client_rejected`)
  - `connector.authorize_failed` (with a reason: `bad_code`, `expired_request`, `too_many_attempts`)
  - `connector.grant_created`
  - `connector.token_refused` (with a reason, for any refused code exchange or refresh other than
    reuse)
  - `connector.token_rejected` (added by T062: a bearer token this service issued, refused at `/mcp`
    because its grant is revoked or it expired, logged with that grant and session; a token never
    issued concerns no session and is not logged)
  - `connector.token_refreshed`
  - `connector.refresh_reuse_detected`
  - `connector.grant_revoked`
  - `connector.tool_called`
  - `connector.tool_refused` (added by T065: a question refused for its arguments, today only a
    window outside 1–10080 minutes, with the session, grant and tool)
- Each carries `session_id` and `grant_id` where known, and never a code, a token or a verifier
  (FR-023, FR-024).
- No new secret setting exists, so the redaction tuples are unchanged. The key-name rule already
  redacts fields named `token`, `code` is added to it (a pairing or authorization code logged by
  mistake under that key would otherwise be in the clear), and a test asserts that no event emitted
  in the OAuth test module contains a known token.
- MCP tool calls are not traced in Langfuse. `chat.observability` traces turns, and a count query
  is not a turn.

**To confirm at implementation**: that adding `code` to the key-name list does not redact an
existing field whose name contains it (e.g. an error `code`). If it does, the alternative is a
narrower key such as `pairing_code` and `authorization_code`, and the test stays.

**Confirmed (T053, 2026-09-28): it would, so the narrower keys are used.** The pattern is a
substring search, and `status_code=` is passed 67 times across the two services, some of them to
log calls. So `code` is matched only as the whole key (`^code$`), beside `pairing_code` and
`code_verifier`; `authorization_code` is already caught by the existing `authorization` substring.
`test_connector_logging.py` asserts both directions: those keys redact, and `status_code`,
`error_code`, `encoded` and `code_challenge` (public by design) do not.

## R15 — Tests

**Decision**:
- `connector_config`: a table of values, unit-tested pure.
- Repositories: against the real test database, like every chat repository, including:
  - the concurrent double-consume of a code (FR-009, SC-007);
  - refresh reuse detection;
  - the cascade on session delete.
- OAuth routes: through `httpx.AsyncClient(transport=ASGITransport(app=app))`, as the testing
  strategy requires for async tests. PKCE is checked against RFC 7636 Appendix B's vector
  (verifier `dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk` → challenge
  `E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM`).
- MCP: through the same ASGI transport with a real bearer token, because the SDK's in-memory
  `Client` skips the HTTP layer and therefore auth. The tools' queries are also tested directly.
- One full walk as a test: register → authorize GET → POST with code → token → `tools/call` →
  revoke → `401`. This is the executable form of the spec's User Stories 1–3.
- SPA: `ConnectedApps` in vitest with `consoleApi` mocked at the lib seam. The tab trigger in `App`
  tests.
- No e2e journey. Pairing requires a real Claude account and a public tunnel, so it is a manual
  quickstart scenario.

## R16 — Dependencies

**Decision**: add `mcp` (v2), `jinja2` and `python-multipart` to `services/chat`.
`python-multipart` is required by FastAPI to read `application/x-www-form-urlencoded` bodies, which
the token endpoint and the authorize form both receive. It is not in the lock today. The `mcp`
package may already pull it in, but it is declared directly because `chat` uses it directly.
