# Data Model: Staff in the loop from an AI assistant app (Phase 4a)

Five new tables in `visitdoc_chat`, one new index on an existing table, one new setting. The
scheduler's database is unchanged.

Conventions carried over from the existing schema:
- Timestamps that record when something happened are `timestamptz` defaulting to `now()`, and every
  expiry is compared against the database's clock, never the application's.
- Secrets (codes, tokens, the authorization-request id) are stored only as their SHA-256 hex
  digest, never in plain form.
- Every table holding session data carries `session_id` so every read scopes itself with a plain
  predicate.

## Relationships

```text
sessions ─┬─< mcp_pairing_codes          (1:0..1, PK = session_id)        ON DELETE CASCADE
          ├─< oauth_authorization_codes  (1:n)                            ON DELETE CASCADE
          └─< oauth_grants ──< oauth_tokens (1:n)                         ON DELETE CASCADE (both)

oauth_clients ─┬─< oauth_authorization_requests                           ON DELETE CASCADE
               ├─< oauth_authorization_codes                              ON DELETE CASCADE
               └─< oauth_grants                                           ON DELETE CASCADE
```

`oauth_clients` and `oauth_authorization_requests` are not session data. A client can read nothing
until a grant ties it to a session. A request expires in 10 minutes and holds nothing about any
session.

## `mcp_pairing_codes`

One row per session at most: the session's current code.

| Column | Type | Rules |
|---|---|---|
| `session_id` | `varchar(26)` PK, FK → `sessions.id` CASCADE | The session the code pairs to. Being the PK is what makes "one usable code per session" structural (FR-007). |
| `code_hash` | `char(64)` UNIQUE | SHA-256 of the normalized code (uppercased, hyphen removed). |
| `expires_at` | `timestamptz` not null | `now() + 10 minutes` at issue. |
| `used_at` | `timestamptz` null | Set once, by the consuming `UPDATE`. |
| `created_at` | `timestamptz` not null default `now()` | |

**Transitions**:
- *issue*: `INSERT … ON CONFLICT (session_id) DO UPDATE SET code_hash, expires_at, used_at = NULL,
  created_at = now()`. The previous code's hash is overwritten, so it can no longer match.
- *consume*: `UPDATE … SET used_at = now() WHERE code_hash = :h AND expires_at > now() AND used_at
  IS NULL RETURNING session_id`. No row back means wrong, expired or used, and one value covers
  those deliberately: the page must not tell a guesser which.

**Read for the console**: `expires_in_seconds = GREATEST(0, EXTRACT(EPOCH FROM expires_at - now()))`
when `used_at IS NULL AND expires_at > now()`, else "no live code". The code itself is never read
back.

## `oauth_clients`

| Column | Type | Rules |
|---|---|---|
| `client_id` | `varchar(43)` PK | 32 random bytes, URL-safe. Public: sent in URLs. |
| `client_name` | `varchar(100)` not null | As registered, trimmed. `"Unnamed client"` when absent. |
| `redirect_uri` | `text` not null | Always `https://claude.ai/api/mcp/auth_callback` (FR-012). Stored so a future allowlist change does not rewrite history. |
| `created_at` | `timestamptz` not null default `now()` | |

## `oauth_authorization_requests`

A sign-in in progress, between the `GET` and the `POST` of `/oauth/authorize`.

| Column | Type | Rules |
|---|---|---|
| `id_hash` | `char(64)` PK | SHA-256 of the 32-byte request id carried in the form. |
| `client_id` | FK → `oauth_clients` CASCADE | |
| `redirect_uri` | `text` not null | Equal to the client's, checked at `GET`. |
| `code_challenge` | `varchar(128)` not null | S256 only. |
| `state` | `text` null | Echoed back unchanged. Opaque to us. |
| `scope` | `text` not null | Normalized: always contains `console:read`. |
| `resource` | `text` not null | Must equal the connector address when sent. |
| `failed_attempts` | `smallint` not null default 0 | CHECK 0–5. |
| `expires_at` | `timestamptz` not null | `now() + 10 minutes`. |
| `completed_at` | `timestamptz` null | Set when a code was accepted. A completed request accepts nothing more. |

**Transitions**:
- *wrong code*: `UPDATE … SET failed_attempts = failed_attempts + 1 WHERE id_hash = :h AND
  completed_at IS NULL AND failed_attempts < 5 AND expires_at > now() RETURNING failed_attempts`.
  At 5, the request is dead (FR-013).
- *right code*: in one transaction, the request's `completed_at` is set under the same guards, the
  pairing code is consumed (see above), and an authorization code is inserted. If either
  conditional update returns no row, the transaction rolls back and the page shows the failure.

## `oauth_authorization_codes`

| Column | Type | Rules |
|---|---|---|
| `code_hash` | `char(64)` PK | |
| `session_id` | FK → `sessions` CASCADE | From the consumed pairing code. |
| `client_id` | FK → `oauth_clients` CASCADE | |
| `redirect_uri` | `text` not null | Must equal the one sent to `/oauth/token`. |
| `code_challenge` | `varchar(128)` not null | Checked against the verifier at exchange. |
| `scope` | `text` not null | |
| `expires_at` | `timestamptz` not null | `now() + 60 seconds`. |
| `used_at` | `timestamptz` null | |

**Transition** *exchange*: `UPDATE … SET used_at = now() WHERE code_hash = :h AND used_at IS NULL
AND expires_at > now() RETURNING …`. Then client id, redirect URI and PKCE are checked. On any
mismatch the transaction rolls back and the answer is `invalid_grant`, so a failed exchange leaves
the code unused. That does not help an attacker: the code is valid for 60 seconds and only with the
verifier.

## `oauth_grants`

What the console lists as a connected app.

| Column | Type | Rules |
|---|---|---|
| `id` | `varchar(26)` PK | ULID. Appears in the console's revoke URL. |
| `session_id` | FK → `sessions` CASCADE, indexed | |
| `client_id` | FK → `oauth_clients` CASCADE | |
| `client_name` | `varchar(100)` not null | Snapshot of the client's name at grant time. |
| `scope` | `text` not null | |
| `created_at` | `timestamptz` not null default `now()` | Shown as "paired at". |
| `last_used_at` | `timestamptz` null | Updated by token verification, at most once a minute. Null shows as "never". |
| `revoked_at` | `timestamptz` null | Set by the console's revoke or by refresh-token reuse. Never cleared. |

**State** (derived, never stored):
- *active*: `revoked_at IS NULL` and the grant holds an unused, unexpired refresh token;
- *expired*: `revoked_at IS NULL` and no such refresh token exists (30 days of inactivity). Not
  listed;
- *revoked*: `revoked_at IS NOT NULL`. Not listed.

The console lists **active grants only**, newest first. An expired grant can do nothing, so
listing it would offer a Revoke that protects nothing. Until it expires, an idle grant stays
listed with its last use, so it can still be recognized and revoked (spec edge case "removes the
connector inside the assistant app"). Expired rows stay in the table. Pruning them is housekeeping
and is deferred, like pruning unused clients (research R8).

## `oauth_tokens`

| Column | Type | Rules |
|---|---|---|
| `token_hash` | `char(64)` PK | |
| `grant_id` | FK → `oauth_grants` CASCADE, indexed | |
| `kind` | `varchar(8)` not null | CHECK `kind IN ('access', 'refresh')`. |
| `expires_at` | `timestamptz` not null | Access: `now() + 1 hour`. Refresh: `now() + 30 days`. |
| `used_at` | `timestamptz` null | Refresh only. CHECK `kind = 'refresh' OR used_at IS NULL`. |
| `created_at` | `timestamptz` not null default `now()` | |

**Transitions**:
- *verify access*: `SELECT g.session_id, g.id, t.expires_at FROM oauth_tokens t JOIN oauth_grants g
  … WHERE t.token_hash = :h AND t.kind = 'access' AND t.expires_at > now() AND g.revoked_at IS
  NULL`. No row means `401`.
- *refresh*: `UPDATE … SET used_at = now() WHERE token_hash = :h AND kind = 'refresh' AND used_at IS
  NULL AND expires_at > now() RETURNING grant_id`, joined to a grant that is not revoked. Then a new
  access and refresh pair is inserted in the same transaction.
- *reuse detected*: when that `UPDATE` returns nothing but a row with that hash exists with
  `used_at IS NOT NULL`, the grant's `revoked_at` is set. The answer is `invalid_grant` either way.

## Changes to existing tables

- `booking_acts`: new index `ix_booking_acts_session_settled (session_id, settled_at)`. The comment
  on `created_at` is amended: it is also the window position of an act that never settled (R9).
- `chat_repository`: new `count_needing_attention(session, session_id) -> int`, built on the
  existing `_EMPHASIZED` expression.
- `booking_act_repository`: new `count_recent_changes(session, session_id, minutes) ->
  RecentBookingChanges`.

## `RecentBookingChanges` (a value, not a table)

| Field | Meaning |
|---|---|
| `window_minutes` | The window the counts cover, as asked. |
| `booked`, `cancelled`, `rescheduled` | Distinct appointments with at least one act of that operation that settled `done` inside the window. One appointment rescheduled twice counts once; one booked then cancelled counts once under each. |
| `outcome_unknown` | Acts inside the window whose outcome is not known: settled as `unknown`, or never settled. Counted as acts, since an unknown booking may have no appointment id. |

Its reading as a sentence is `describe_recent_changes()` (research R11), e.g. "Three appointments
were booked, one cancelled and two rescheduled."

If the settle path does not already guarantee it, `booking_acts` gains `CHECK (outcome IS DISTINCT
FROM 'done' OR appointment_id IS NOT NULL)`, so no done act can drop out of a distinct count
(research R9).

## Setting

| Setting | Type | Default | Rules |
|---|---|---|---|
| `PUBLIC_BASE_URL` | `str` | `""` | Must be `https://host[:port]` with no path, query or fragment for the connector to be available. Anything else makes it unavailable with a stated reason (R4). Not a secret. Deliberately **not** added to `service.configured`: that event's fields are the eval harness's data contract, and this setting changes nothing a run measures. |
