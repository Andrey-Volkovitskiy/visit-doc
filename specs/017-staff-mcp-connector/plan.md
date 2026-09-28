# Implementation Plan: Staff in the loop from an AI assistant app (Phase 4a)

**Branch**: `017-staff-mcp-connector` | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/017-staff-mcp-connector/spec.md`

## Summary

A staff member pairs the Claude app with their session from a new gear tab in the staff console,
then asks Claude two questions from their phone and gets counts back.

1. **A remote MCP server inside the chat service.** The official `mcp` SDK serves `/mcp` over
   streamable HTTP and checks bearer tokens against Postgres. It has two read-only tools: how many
   conversations need attention (the console's own `_EMPHASIZED` rule), and how many distinct
   appointments were booked, cancelled and rescheduled in the last N minutes, plus a count of
   changes with an unknown outcome (from `booking_acts`). The second answer carries a ready-made
   sentence, e.g. "Three appointments were booked, one cancelled and two rescheduled."
2. **A small OAuth 2.1 authorization server, written in `chat`.** Discovery metadata, dynamic
   client registration restricted to Claude's callback, a server-rendered authorize page that asks
   for a pairing code, and a token endpoint with PKCE S256 and rotating refresh tokens. Tokens are
   opaque and hashed, grouped under a grant bound to the session.
3. **Pairing codes issued by the console.** One row per session keyed by the session, so a new code
   replaces the old one structurally. Consumed by one conditional `UPDATE`.
4. **The console tab.** The gear trigger at the far right, an explanation, *Get pairing code* with
   the connector address and a countdown, and the list of paired apps with *Revoke*.

Everything public derives from one setting, `PUBLIC_BASE_URL` (an ngrok static domain locally). An
unusable value makes the connector unavailable, with the reason shown.

## Technical Context

**Language/Version**: Python 3.12 (chat); TypeScript / React 19 (frontend)

**Primary Dependencies**:
- Existing: FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic; Vite, vitest, Testing Library,
  lucide-react.
- **New** in `services/chat`:
  - `mcp` (v2): transport and resource-server auth (R1);
  - `jinja2`: the authorize page (R3);
  - `python-multipart`: form bodies (R16).

**Storage**: PostgreSQL `visitdoc_chat`: five new tables and one new index on `booking_acts`
(data-model.md). Scheduler database unchanged.

**Testing**:
- pytest against the real test database;
- `httpx.AsyncClient` over `ASGITransport` for the OAuth and MCP routes, since the SDK's in-memory
  client skips auth (R15);
- vitest for the tab.
- No e2e: pairing needs a real Claude account and a public tunnel, so it is walked by hand in
  quickstart.md.

**Target Platform**: Local Linux (WSL2) dev stack, exposed through ngrok for the manual walk.

**Project Type**: Web application: two FastAPI services, one React SPA, shared-packages monorepo.

**Performance Goals**:
- The token endpoint answers well under Claude's 10 s limit; each request is a few indexed
  statements.
- A tool call is one scoped count query.
- A pairing appears in the console within one 2 s poll tick.

**Constraints**:
- Claude requires HTTPS and calls from Anthropic's cloud, so the connector exists only when
  `PUBLIC_BASE_URL` is a valid `https://` origin.
- Every expiry is compared on the database's clock.
- No change to any model input, so the golden-set baseline stays comparable, and no change to the
  gRPC contract.

**Scale/Scope**:
- A session has a handful of grants at most.
- Roughly 10 backend modules new or touched, 3 frontend files.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I. Phase-gated scope | **Pass.** Exactly ROADMAP Phase 4a. No new service, broker or deployment. The ngrok tunnel is run by hand and is not part of the repo. |
| II. AI core is the centerpiece | **Pass.** MCP is the tool-use protocol the AI role is judged on, and this is its first real consumer. No model behaviour changes. |
| III. Deliberate, minimal service boundaries | **Pass.** Nothing crosses to the scheduler. Invariants are enforced in the datastore: one code per session by primary key, single use by conditional `UPDATE`, cascades on session delete. Failure handling is designed: `503` when unavailable, `invalid_grant` on every bad exchange. |
| IV. Structured outputs and decoupled tools | **Pass.** Tools with closed schemas and structured results. The agent's in-process registry is untouched, and the MCP tools are a separate, staff-scoped surface. |
| V. Grounded retrieval and abstention | **N/A.** No retrieval path changes. |
| VI. Documentation | **Pass, with planned deliverables:** ROADMAP 4a shipped note, a README tradeoff entry (hand-written authorization server vs a framework or IdP; opaque tokens vs JWT), a CLAUDE.md key-decision entry, `.env.example`, the frontend hook table. |
| VII. Clean architecture | **Pass, with one justified mechanism** (Complexity Tracking). Routes are thin; logic lives in `chat/connectors/`; repositories take the `AsyncSession` explicitly. The SDK is behind one adapter (`TokenVerifier`) and one module (`mcp_server.py`). |
| VIII. TDD | **Pass, and binding on tasks.** Each contract file maps to failing tests written first (see Invariants below). |

**Post-design re-check**: still passing. The design added three dependencies, each justified in
research (R1, R3, R16), and one mechanism, justified below.

## Invariants of the new mechanism

Each is written down here and gets its own test, because a sign-in server is the kind of new
mechanism whose failures are silent.

| # | Invariant | Where it is enforced | Test |
|---|---|---|---|
| I1 | A session has at most one usable pairing code | PK `mcp_pairing_codes.session_id` | Issue twice; the first code no longer consumes |
| I2 | A pairing code is consumed at most once | Conditional `UPDATE … used_at IS NULL` | Two concurrent consumes; exactly one returns a session |
| I3 | An authorization request accepts at most 5 wrong codes and then nothing | Conditional `UPDATE` on `failed_attempts` / `completed_at` | Five wrong, then the right one, refused |
| I4 | Only Claude's callback can be registered or redirected to | `/oauth/register` check; `redirect_uri` equality at authorize and token | Registration with another URI refused; authorize with a mismatched URI renders the error page and does not redirect |
| I5 | An authorization code is exchanged at most once, only with its verifier, client and redirect URI | Conditional `UPDATE`, then PKCE and equality checks in one transaction | RFC 7636 vector passes; a wrong verifier, client or URI fails; a second exchange fails |
| I6 | A refresh token is used at most once; a second use revokes the grant | Conditional `UPDATE`, reuse branch | Refresh twice with the same token; the grant is revoked and the new access token stops working |
| I7 | A tool answers only for the grant's session | Session read from the verified token; no session argument exists | Two sessions, two tokens; each sees only its own counts |
| I8 | A revoked or expired grant's token is refused on its next call | `revoked_at IS NULL` and expiry in `verify_token`'s `WHERE` | Revoke, then call: `401` |
| I9 | Deleting a session removes its codes, grants and tokens | FK cascades | Delete through `/admin`; rows gone, token refused |
| I10 | No code, token or verifier is stored in plain form or logged | Hash-only columns; key-name redaction; no header logging | Tables hold 64-char digests only; captured logs contain none of the test's secrets |
| I11 | Tool results carry no names, text or ids | The result types have only count fields | Schema-level test on both results |

## Project Structure

### Documentation (this feature)

```text
specs/017-staff-mcp-connector/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── oauth.md
│   ├── mcp-tools.md
│   ├── console-connected-apps.md
│   └── console-ui.md
├── checklists/requirements.md
├── evaluation/manual-walk.md   # T061: the quickstart walked with real Claude
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
services/chat/
├── pyproject.toml                                  # + mcp, jinja2, python-multipart
├── src/chat/core/config.py                         # + PUBLIC_BASE_URL
├── src/chat/connectors/                            # new package
│   ├── public_address.py                           # connector_config() -> ConnectorConfig | ConnectorUnavailable
│   ├── secrets.py                                  # new_secret(), digest(), pairing-code format/normalize
│   ├── pkce.py                                     # s256_matches(verifier, challenge)
│   ├── authorization.py                            # register, start/complete authorization, exchange, refresh
│   ├── token_verifier.py                           # the SDK TokenVerifier adapter
│   ├── mcp_server.py                               # MCPServer + the two tools
│   ├── recent_changes_text.py                      # describe_recent_changes(): the relayable sentence
│   └── templates/authorize.html, error.html
├── src/chat/repositories/
│   ├── pairing_code_repository.py                  # issue, consume, live_expiry
│   ├── oauth_repository.py                         # clients, authorization requests, authorization codes
│   ├── grant_repository.py                         # grants and tokens: create, verify, refresh, revoke, list_for_session
│   ├── chat_repository.py                          # + count_needing_attention
│   └── booking_act_repository.py                   # + count_recent_changes
├── src/chat/domain/models.py                       # + 5 models, + ix_booking_acts_session_settled
├── src/chat/domain/schemas.py                      # + ConnectedAppsOut, PairingCodeOut, GrantOut
├── src/chat/api/oauth.py                           # new: metadata, register, authorize GET/POST, token
├── src/chat/api/console.py                         # + /console/connected-apps routes
├── src/chat/main.py                                # + oauth router, MCP mount, session manager in lifespan
├── alembic/versions/<new>_connector_pairing.py
└── tests/
    ├── test_connector_public_address.py
    ├── test_connector_secrets.py                   # secrets, pairing-code format, PKCE vector
    ├── test_connector_recent_changes_text.py       # describe_recent_changes, the contract's table
    ├── test_pairing_code_repository.py             # I1, I2
    ├── test_oauth_metadata.py                      # the metadata document, 503 when unavailable
    ├── test_oauth_register.py                      # I4
    ├── test_oauth_authorize.py                     # I3, I4
    ├── test_oauth_token.py                         # I5, I6
    ├── test_connector_mcp.py                       # I7, I8, I11, the mount's three paths
    ├── test_connector_console_api.py               # console routes, I9
    ├── test_connector_walkthrough.py               # register → authorize → token → tools/call → revoke → 401
    ├── test_connector_logging.py                   # I10
    ├── test_chat_repository.py                     # + count_needing_attention agrees with the listing
    ├── test_booking_act_repository.py              # + count_recent_changes
    └── test_migrations.py                          # + the five tables and the new index

packages/shared-logging/src/shared_logging/logging.py   # + redaction key for pairing/authorization codes (R14)

services/frontend/
├── src/App.tsx                                     # + gear TabsTrigger and TabsContent
├── src/components/ConnectedApps.tsx                # new
├── src/lib/consoleApi.ts                           # + fetchConnectedApps, issuePairingCode, revokeConnectedApp
├── tests/ConnectedApps.test.tsx                    # new
└── tests/App.test.tsx                              # + the gear tab trigger

docs/ROADMAP.md, README.md, .claude/CLAUDE.md, services/frontend/.claude/CLAUDE.md, .env.example
```

**Structure Decision**: everything lands in the existing `chat` service and SPA. The new
`chat/connectors/` package holds the connector's domain logic, keeping it out of `agent/`, which
the connector must not touch, and out of `api/`, which stays thin.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| A hand-written OAuth authorization server (a new security mechanism) | Claude connects only through OAuth, and the spec's identity is a pairing code tied to an anonymous session, which no off-the-shelf server models. | *The SDK's embedded server*: its own docs advise new servers against it. *Authlib*: a general framework configured down to one client type and one grant pair. *An external IdP*: needs staff accounts, which the spec and Phase 1d rule out. The risk is contained by the invariant table above, one test each. |
| Three new dependencies | `mcp` carries the protocol (R1); `jinja2` gives the secret-entry page autoescaping (R3); `python-multipart` is FastAPI's requirement for form bodies (R16). | Hand-written JSON-RPC tracks a moving protocol; manual escaping fails open; there is no other way to read a form body in FastAPI. |
