---

description: "Task list for 017 — Staff in the loop from an AI assistant app (Phase 4a)"
---

# Tasks: Staff in the loop from an AI assistant app (Phase 4a)

**Input**: Design documents from `specs/017-staff-mcp-connector/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Per the constitution's Test-Driven Development principle, test tasks are mandatory and
precede their implementation: contract → test cases → tests (observed failing) → implementation →
tests run (observed passing). Invariants I1–I11 are the ones in plan.md's table; each has at least
one test task naming it.

**Organization**: grouped by user story. US1 (pair) and US2 (ask) are both P1 and together are the
MVP. US3 (see and revoke) is P2.

Scoped test runs only while working. The full `make test-unit` and `make test-frontend` run once,
in the Polish phase (`docs/testing-strategy.md`).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1, US2, US3 from spec.md

Paths: backend `services/chat/src/chat/…`, backend tests `services/chat/tests/…`, frontend
`services/frontend/src/…`, frontend tests `services/frontend/tests/…`.

---

## Phase 1: Setup

**Purpose**: dependencies and configuration every story needs.

- [X] T001 Add `mcp` (v2 line), `jinja2` and `python-multipart` to `services/chat/pyproject.toml` with `uv add --package chat`, then `uv sync`; confirm `uv lock --check` passes (research R1, R3, R16)
- [X] T002 Record the installed `mcp` version's actual names in `specs/017-staff-mcp-connector/research.md` R1: the server class (`MCPServer` or otherwise), the stateless-HTTP option, `streamable_http_app()`, `TokenVerifier`, `AuthSettings`, `get_access_token()`, `TransportSecuritySettings`. Correct any name the rest of this file uses that differs.
- [X] T003 [P] Add `PUBLIC_BASE_URL: str = ""` to `Settings` in `services/chat/src/chat/core/config.py`, and to `.env.example` with a comment explaining that it is the chat service's public HTTPS origin (an ngrok static domain locally); do not add it to the `service.configured` event (data-model.md → Setting)

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: schema, pure helpers and the public-address rule that all three stories build on.

**⚠️ No user story work begins until this phase is complete.**

### Tests first

- [X] T004 [P] Write `services/chat/tests/test_connector_public_address.py`: a table over `connector_config()` covering empty, `http://`, `https://host`, `https://host:8443`, a trailing slash, a path, a query and a fragment; available results carry issuer, `…/mcp` address and hostname; unavailable results carry `not_configured` / `not_https` / `has_path` (research R4). Observe it failing.
- [X] T005 [P] Write `services/chat/tests/test_connector_secrets.py`: `new_secret()` length and alphabet; `digest()` is 64 hex chars; pairing codes are 8 Crockford base32 characters rendered `XXXX-XXXX`; `normalize_pairing_code()` accepts lower case and a missing hyphen; `s256_matches()` passes RFC 7636 Appendix B's vector and fails a wrong verifier (research R5, R15). Observe it failing.
- [X] T006 [P] Extend `services/chat/tests/test_migrations.py` (or the chat migration test that exists): upgrade to head creates `mcp_pairing_codes`, `oauth_clients`, `oauth_authorization_requests`, `oauth_authorization_codes`, `oauth_grants`, `oauth_tokens` and `ix_booking_acts_session_settled`; downgrade removes them. Observe it failing.

### Implementation

- [X] T007 [P] Implement `services/chat/src/chat/connectors/public_address.py`: `ConnectorConfig`, `ConnectorUnavailable` (with a `StrEnum` reason) and `connector_config(settings)`; add `services/chat/src/chat/connectors/__init__.py`. T004 passes.
- [X] T008 [P] Implement `services/chat/src/chat/connectors/secrets.py` (`new_secret`, `digest`, `new_pairing_code`, `normalize_pairing_code`) and `services/chat/src/chat/connectors/pkce.py` (`s256_matches`, constant-time comparison). T005 passes.
- [X] T009 Verify research R9's open question: read the booking-act settle path in `services/chat/src/chat/repositories/booking_act_repository.py` and confirm every act settled `done` carries `appointment_id`; query the dev database for violating rows. Record the finding in research.md R9.
- [X] T010 Add the five models from data-model.md to `services/chat/src/chat/domain/models.py` (columns, CHECKs, FKs with `ON DELETE CASCADE`, indexes), plus `ix_booking_acts_session_settled` on `BookingAct`, plus the `appointment_id` CHECK if T009 found it not already guaranteed; amend `BookingAct.created_at`'s comment to say it is also the window position of an act that never settled (research R9)
- [X] T011 Write the Alembic migration `services/chat/alembic/versions/<rev>_connector_pairing.py` (down_revision `d4f7a2c93e16`) matching T010. T006 passes.

**Checkpoint**: schema and helpers exist; stories can start.

---

## Phase 3: User Story 1 — Pair the Claude app with the session (Priority: P1) 🎯 MVP

**Goal**: from the gear tab, get a code and the connector address; complete Claude's connect flow
with that code; the console lists the new pairing.

**Independent test**: `test_connector_walkthrough.py`'s pairing half (register → authorize → token
→ grant listed); by hand, quickstart.md §3.

### Tests for User Story 1 (write first, observe failing)

- [X] T012 [P] [US1] Write `services/chat/tests/test_pairing_code_repository.py`: issue then consume returns the session (I2 happy path); issue twice, the first code no longer consumes (I1); an expired code does not consume; two concurrent consumes of one code, exactly one returns a session (I2, SC-007); `live_expiry` returns remaining seconds and never the code
- [X] T013 [P] [US1] Write `services/chat/tests/test_oauth_register.py` per `contracts/oauth.md` → register: Claude's request gets `201` with a `client_id` and no secret; another redirect URI gets `invalid_redirect_uri` (I4); a non-`none` auth method or unsupported grant gets `invalid_client_metadata`; an over-long name is trimmed to 100
- [X] T014 [P] [US1] Write `services/chat/tests/test_oauth_metadata.py`: `/.well-known/oauth-authorization-server` equals the contract's document for a configured base URL, omits `client_id_metadata_document_supported`; every OAuth route answers `503` when the connector is unavailable
- [X] T015 [P] [US1] Write `services/chat/tests/test_oauth_authorize.py` per `contracts/oauth.md` → authorize: unknown client or mismatched redirect URI renders the error page with no redirect (I4); other invalid parameters redirect with `error=invalid_request` and the original `state`; a valid request renders the page naming the client and `claude.ai` and stores a request; a right code (any case, with or without the hyphen) redirects with `code` and `state`; a wrong, expired or used code re-renders with the single "not valid" message; five wrong codes, then the right one, is refused (I3); a completed request accepts nothing more; every interpolated value is escaped (a client name containing `<script>` renders inert)
- [X] T016 [P] [US1] Write `services/chat/tests/test_oauth_token.py`, authorization-code half, per `contracts/oauth.md` → token: a valid exchange returns the success document with `Cache-Control: no-store` and creates a grant for the pairing's session; a wrong verifier, client or redirect URI gives `invalid_grant` and leaves no grant; a second exchange of the same code gives `invalid_grant` (I5); a JSON body is refused, the form body is accepted
- [X] T017 [P] [US1] Write `services/chat/tests/test_connector_mcp.py`, auth half: `POST /mcp` without a token is `401` with the contract's `WWW-Authenticate` header; `/.well-known/oauth-protected-resource/mcp` returns the contract's document; an unknown token is `401`; the mount serves only those two paths and does not shadow an existing route's `404` (research R1's verification); with the connector unavailable, both paths answer `503`
- [X] T018 [P] [US1] Write `services/chat/tests/test_connector_console_api.py`, pairing half, per `contracts/console-connected-apps.md`: `GET` with no cookie is `404`; `GET` reports availability, the address, `pairing_code: null` before issuing, and remaining seconds (never the code) after; `POST …/pairing-code` returns `201` with a code and `expires_in_seconds: 600` when available and `409` with the reason when not (FR-004); a grant created for the session appears in `grants` with `paired_seconds_ago` and `last_used_seconds_ago: null`; another session's grant does not
- [X] T019 [P] [US1] Write `services/chat/tests/test_connector_walkthrough.py`, pairing half: issue a code through the console route, register, authorize GET, POST with the code, exchange with the RFC vector's verifier, and see the grant in `GET /console/connected-apps`
- [X] T020 [P] [US1] Write `services/frontend/tests/ConnectedApps.test.tsx`, pairing half, mocking `consoleApi` at the lib seam: the explanation text; unavailable shows the reason and no button; available shows *Get pairing code*; after issuing, the code, the address, copy buttons and a countdown that reaches "Code expired" and brings the button back; a live code after reload shows "A code is active…" without a code; the grant list renders rows and the empty message; a changed `pollTick` re-reads `fetchConnectedApps` and a newly returned grant appears (spec edge case "tab open while an app is paired")
- [X] T021 [P] [US1] Extend `services/frontend/tests/App.test.tsx`: the staff tab bar has a fourth trigger named "Connected apps", placed last, with no visible text; selecting it shows the panel

### Implementation for User Story 1

- [X] T022 [P] [US1] Implement `services/chat/src/chat/repositories/pairing_code_repository.py`: `issue(session, session_id) -> str` (upsert, returns the plain code once), `consume(session, code) -> str | None`, `live_expiry(session, session_id) -> int | None`. T012 passes.
- [X] T023 [P] [US1] Implement `services/chat/src/chat/repositories/oauth_repository.py`: clients (create, get), authorization requests (create, get live, record failure with the conditional `UPDATE`, complete), authorization codes (create, consume). Each consuming step is one conditional `UPDATE … RETURNING`.
- [X] T024 [P] [US1] Implement `services/chat/src/chat/repositories/grant_repository.py`, pairing half: `create_grant_with_tokens(session, …) -> IssuedTokens`, `verify_access(session, token_hash) -> VerifiedGrant | None` (with the once-a-minute `last_used_at` update), `list_for_session(session, session_id)` returning active grants only (not revoked, holding an unused unexpired refresh token), with ages computed on the database's clock
- [X] T025 [US1] Implement `services/chat/src/chat/connectors/authorization.py`: `register_client`, `start_authorization`, `complete_authorization` (request completion, pairing-code consume and authorization-code insert in one transaction, rolled back if either conditional update misses), `exchange_code` (consume, then client, redirect and PKCE checks in the same transaction), each returning a typed outcome rather than raising for expected refusals; log `connector.client_registered`, `connector.client_rejected`, `connector.authorize_failed` (with reason), `connector.token_refused` (with reason, for every refused exchange) and `connector.grant_created`, never a secret (research R14, FR-024)
- [X] T026 [P] [US1] Add `services/chat/src/chat/connectors/templates/authorize.html` and `error.html` per `contracts/oauth.md` (client name, redirect host, the read-only statement, a code field with `autocomplete="one-time-code"`, a hidden `request` field; one inline style block using the SPA's colour values)
- [X] T027 [US1] Implement `services/chat/src/chat/api/oauth.py`: the metadata document, `POST /oauth/register`, `GET`/`POST /oauth/authorize` (Jinja2 with autoescape), `POST /oauth/token` for `grant_type=authorization_code` (form body, RFC 6749 error bodies, `Cache-Control: no-store`), and the `503` when unavailable. T013–T016 pass.
- [X] T028 [US1] Implement `services/chat/src/chat/connectors/token_verifier.py`: the SDK `TokenVerifier` adapter over `grant_repository.verify_access`, putting the grant's session in `AccessToken.subject` and its id in the claims
- [X] T029 [US1] Implement `services/chat/src/chat/connectors/mcp_server.py`, transport half: the server with `AuthSettings` from `connector_config` (issuer, resource `…/mcp`, required scope `console:read`), the verifier from T028, stateless streamable HTTP, and `TransportSecuritySettings` allowing the configured hostname; no tools yet
- [X] T030 [US1] Wire `services/chat/src/chat/main.py`: include the oauth router; mount the MCP app after every other route; enter `session_manager.run()` in the lifespan's `AsyncExitStack`; when the connector is unavailable, mount no MCP app and instead serve the fixed `503` body of `contracts/oauth.md` at `/mcp` and `/.well-known/oauth-protected-resource/mcp`, as the oauth router does for its own routes (research R4). T017 passes.
- [X] T031 [US1] Add `GET /console/connected-apps` and `POST /console/connected-apps/pairing-code` to `services/chat/src/chat/api/console.py`, with `ConnectedAppsOut`, `ConnectorStatusOut`, `PairingCodeOut` and `GrantOut` in `services/chat/src/chat/domain/schemas.py`; log `connector.pairing_code_issued`. T018 and T019 pass.
- [X] T032 [P] [US1] Add `fetchConnectedApps()` and `issuePairingCode()` to `services/frontend/src/lib/consoleApi.ts`, each through `ensureOk()`
- [X] T033 [US1] Implement `services/frontend/src/components/ConnectedApps.tsx` per `contracts/console-ui.md`: explanation, pairing block (unavailable reason, button, code, address, copy buttons, countdown from `expires_in_seconds`, three steps, expired state, "A code is active" state), and the read-only grant list; re-read on `pollTick`. T020 passes.
- [X] T034 [US1] Add the gear `TabsTrigger` (lucide `Settings`, `text-ink-muted`, `aria-label` and `title` "Connected apps", `ml-auto`, `value="connected-apps"`) and its `TabsContent` to `services/frontend/src/App.tsx`, inside the existing dirty-tab guard. T021 passes.

**Checkpoint**: Claude can be paired and the pairing is listed. The connector answers nothing yet.

---

## Phase 4: User Story 2 — Ask the two questions from the phone (Priority: P1)

**Goal**: a paired Claude asks both tools and gets counts for its own session only.

**Independent test**: `test_connector_mcp.py`'s tools half and the walkthrough's `tools/call` step;
by hand, quickstart.md §4.

### Tests for User Story 2 (write first, observe failing)

- [X] T035 [P] [US2] Extend `services/chat/tests/test_chat_repository.py`: `count_needing_attention` equals the number of `emphasized` rows `list_conversations_for_console` returns, across at least 10 states built from escalated, marked, both and neither chats in varying numbers, including zero chats (SC-002); another session's chats are not counted (FR-016, SC-002)
- [X] T036 [P] [US2] Extend `services/chat/tests/test_booking_act_repository.py`: `count_recent_changes` counts distinct appointments per operation for `done` acts settled inside the window; one appointment rescheduled twice counts once; booked-then-cancelled counts once under each; `refused`, `unchanged` and `not_sent` are not counted; `unknown` settled in the window and `NULL` created in the window are counted as acts in `outcome_unknown`; acts outside the window and another session's acts are not counted (FR-017, SC-003)
- [X] T037 [P] [US2] Write `services/chat/tests/test_connector_recent_changes_text.py`: `describe_recent_changes` produces every row of the table in `contracts/mcp-tools.md`, plus the one/eleven boundary of word numbers (FR-017a)
- [X] T038 [P] [US2] Extend `services/chat/tests/test_connector_mcp.py`, tools half, over HTTP with a real bearer token: the tool list is exactly the two tools, both read-only (FR-015); each returns the contract's structured content and text; `minutes` of 0, 10081 and a non-integer is a tool error naming the range and nothing is counted (FR-018); two sessions' tokens each see only their own counts (I7, SC-006); neither structured result has any field beyond its counts (I11, FR-019); after a tool call, `GET /console/connected-apps` shows the grant's `last_used_seconds_ago` set (FR-005, research R7)
- [X] T039 [US2] Extend `services/chat/tests/test_connector_walkthrough.py`: after pairing, `tools/call` for both tools returns the state the test planted

### Implementation for User Story 2

- [X] T040 [P] [US2] Add `count_needing_attention(session, session_id) -> int` to `services/chat/src/chat/repositories/chat_repository.py`, built on the existing `_EMPHASIZED`. T035 passes.
- [X] T041 [P] [US2] Add `RecentBookingChanges` and `count_recent_changes(session, session_id, minutes)` to `services/chat/src/chat/repositories/booking_act_repository.py`, windowed on the database's clock. T036 passes.
- [X] T042 [P] [US2] Implement `services/chat/src/chat/connectors/recent_changes_text.py`: `describe_recent_changes(changes) -> str`. T037 passes.
- [X] T043 [US2] Add the two tools to `services/chat/src/chat/connectors/mcp_server.py` per `contracts/mcp-tools.md`: descriptions verbatim, closed input schemas, read-only annotations, the session from `get_access_token().subject` only, structured content plus text; log `connector.tool_called` with session, grant and tool name. T038 and T039 pass.

**Checkpoint**: US1 + US2 are the MVP: pair, then ask.

---

## Phase 5: User Story 3 — See paired apps and revoke one (Priority: P2)

**Goal**: grants stay usable through refresh, drop out of the list after 30 days idle, and stop working
the moment staff revoke them or the session is deleted.

**Independent test**: the walkthrough's revoke step; by hand, quickstart.md §5.

### Tests for User Story 3 (write first, observe failing)

- [X] T044 [P] [US3] Extend `services/chat/tests/test_oauth_token.py`, refresh half: refresh returns a new pair and the old refresh token no longer works; reusing a refresh token revokes the grant, gives `invalid_grant`, and the newest access token is then refused (I6); a refresh for another `client_id` or a revoked grant gives `invalid_grant`; an expired refresh token gives `invalid_grant`
- [X] T045 [P] [US3] Extend `services/chat/tests/test_connector_console_api.py`, revoke half: revoking this session's grant is `204` and removes it from `GET`; revoking again is `204`; another session's grant id is `404`; a grant with no live refresh token (expired after 30 days idle) is not listed; deleting the session through `/admin` removes its codes, grants and tokens (I9, FR-022)
- [X] T046 [P] [US3] Extend `services/chat/tests/test_connector_mcp.py`: a revoked grant's access token is `401` on its next call (I8, SC-004); an expired access token is `401`
- [X] T047 [P] [US3] Write `services/chat/tests/test_connector_logging.py`: capture every event emitted while running a full pair → ask → refresh → revoke flow and assert none contains the pairing code, an authorization code, a token or the verifier; assert every connector table's secret column holds only 64-character hex digests (I10, FR-023, SC-005); assert adding the new redacted key did not change an existing event's fields (research R14's open question)
- [X] T048 [P] [US3] Extend `services/frontend/tests/ConnectedApps.test.tsx`, revoke half: *Revoke* opens a confirmation dialog (driven with `press()`), confirming calls `revokeConnectedApp` with the grant id and the row leaves; cancelling calls nothing
- [X] T049 [US3] Extend `services/chat/tests/test_connector_walkthrough.py`: revoke through the console route, then `tools/call` is `401`

### Implementation for User Story 3

- [X] T050 [US3] Add refresh to `services/chat/src/chat/repositories/grant_repository.py` (`rotate_refresh` with the conditional `UPDATE`, reuse detection that revokes the grant) and `revoke(session, session_id, grant_id) -> bool` with the session in the `WHERE`
- [X] T051 [US3] Add `refresh` to `services/chat/src/chat/connectors/authorization.py` and `grant_type=refresh_token` to `POST /oauth/token` in `services/chat/src/chat/api/oauth.py`; log `connector.token_refreshed`, `connector.refresh_reuse_detected`, and `connector.token_refused` (with reason) for every other refused refresh. T044 and T046 pass.
- [X] T052 [US3] Add `POST /console/connected-apps/{grant_id}/revoke` to `services/chat/src/chat/api/console.py`; log `connector.grant_revoked`. T045 and T049 pass.
- [X] T053 [US3] Add the redaction key for pairing and authorization codes to the shared key-name rule in `packages/shared-logging/src/shared_logging/logging.py` (as research R14 settles it), then make T047 pass
- [X] T054 [P] [US3] Add `revokeConnectedApp(grantId)` to `services/frontend/src/lib/consoleApi.ts`
- [X] T055 [US3] Add the *Revoke* confirmation dialog to `services/frontend/src/components/ConnectedApps.tsx`. T048 passes.

**Checkpoint**: all three stories work independently.

---

## Phase 6: Polish & cross-cutting concerns

- [X] T056 [P] Add the four new test hooks (`pairing-code`, `connector-address`, `pairing-countdown`, `connected-app`) to the hook table in `services/frontend/.claude/CLAUDE.md`
- [X] T057 [P] Add a README tradeoff entry in `README.md`: a hand-written authorization server over the SDK's embedded one, Authlib or an external IdP; opaque tokens over JWT; the pairing code as the identity bridge; counts only (research R2, R7, and the spec's "deliberately is not")
- [X] T058 [P] Add a key-decision entry to `.claude/CLAUDE.md` (the connector is a staff-scoped surface separate from the agent's registry; the session comes only from the token; `PUBLIC_BASE_URL` is the single source of every public address), and mention the tunnel prerequisite under "Running the stack by hand"
- [X] T059 [P] Mark Phase 4a shipped in `docs/ROADMAP.md` with a one-paragraph note of what differed from the plan, if anything
- [X] T060 Run the full tiers once: `make lint`, `make typecheck`, `make test-unit`, `make test-frontend`, `./node_modules/.bin/tsc -b --noEmit` and `npm run build` in `services/frontend`
- [ ] T061 Walk `specs/017-staff-mcp-connector/quickstart.md` §1–§5 by hand with ngrok and a real Claude account, timing §3 from opening the tab to "connected" against SC-001's 3 minutes, noting §3 step 6's `Origin` header (research R4), and record each section's result in `specs/017-staff-mcp-connector/evaluation/manual-walk.md`

---

## Dependencies & execution order

- **Setup (T001–T003)** → **Foundational (T004–T011)** → stories.
- **US1 (T012–T034)** depends only on Foundational.
- **US2 (T035–T043)** needs a token to call its tools, so its HTTP tests (T038, T039) depend on US1's
  T027–T030. Its repository and text tasks (T035–T037, T040–T042) depend only on Foundational and
  can run beside US1.
- **US3 (T044–T055)** depends on US1 (grants exist) and extends US1's files.
- **Polish** after all stories.

Within each story: tests are written and observed failing before the implementation task that makes
them pass. Tasks editing the same file are sequential: `grant_repository.py` T024 → T050;
`authorization.py` T025 → T051; `api/oauth.py` T027 → T051; `mcp_server.py` T029 → T043;
`console.py` T031 → T052; `ConnectedApps.tsx` T033 → T055; `consoleApi.ts` T032 → T054.

## Parallel examples

- **Foundational tests**: T004, T005, T006 together; then T007 and T008 together.
- **US1 tests**: T012–T021 are all different files and can be written together.
- **US1 repositories**: T022, T023, T024 together.
- **US2 beside US1**: T035–T037 and T040–T042 can run while US1's routes are built.
- **US3 tests**: T044–T048 together.
- **Polish docs**: T056–T059 together.

## Implementation strategy

1. **MVP = US1 + US2.** Pairing alone answers nothing, and asking alone cannot authenticate. Stop
   after Phase 4 and walk quickstart.md §3–§4 against real Claude.
2. **Then US3.** Refresh, revoke, and expired grants leaving the list. Until it lands, a paired app stops working
   after its first access token expires (1 hour), which is acceptable for an MVP demo and not for a
   finished feature.
3. **Polish** last: documentation in the same change as the code it describes, then the full tiers
   once.

## Phase 7: Convergence

- [X] T062 Log every refused connector credential with the session and grant it concerned where they are known: a bearer token refused because its grant is revoked or it expired (in `services/chat/src/chat/connectors/token_verifier.py`, via a repository read that names the grant), and a refresh refused for `client_mismatch` (in `services/chat/src/chat/connectors/authorization.py`); extend `services/chat/tests/test_connector_logging.py` to assert both events carry `session_id` and `grant_id` and no credential per FR-024 (partial)
- [X] T063 Name the specific unavailability reason (`not_configured` / `not_https` / `has_path`) on the error page `GET /oauth/authorize` renders while the connector is unavailable, in `services/chat/src/chat/api/oauth.py`, with a test per reason in `services/chat/tests/test_oauth_metadata.py` per contracts/oauth.md (partial)
- [X] T064 Review the `https://claude.ai` entry in the MCP transport's `allowed_origins` (`services/chat/src/chat/connectors/mcp_server.py`): justify and document it in research.md R4, or remove it and allow only the configured origin, per plan: TransportSecuritySettings allowing the configured hostname (unrequested)

## Phase 8: Convergence

- [X] T065 Log a tool call refused for its arguments (a `minutes` outside 1–10080 in `count_recent_booking_changes`, `services/chat/src/chat/connectors/mcp_server.py`) as `connector.tool_refused` with the session, grant, tool and reason, and assert it in `services/chat/tests/test_connector_logging.py` per FR-024 (partial)
- [X] T066 Review the `https://claude.ai` entry in the MCP transport's `allowed_origins` (`services/chat/src/chat/connectors/mcp_server.py`): justify and document it, or remove it per plan: TransportSecuritySettings allowing the configured hostname (unrequested)

## Phase 9: Convergence

- [X] T067 Log a code exchange refused after its authorization code matched (client, redirect URI or PKCE mismatch, `exchange_code` in `services/chat/src/chat/connectors/authorization.py`) with the session that code belongs to, read before the rollback expires it, and assert it in `services/chat/tests/test_connector_logging.py` per FR-024 (partial)
- [X] T068 Review the `https://claude.ai` entry in the MCP transport's `allowed_origins` (`services/chat/src/chat/connectors/mcp_server.py`): justify and document it, or remove it per plan: TransportSecuritySettings allowing the configured hostname (unrequested)
