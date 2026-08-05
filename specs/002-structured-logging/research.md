# Research: Structured Logging for App/AI Behavior

## 1. Structured logging library

**Decision**: `structlog`, added as a new dependency of `services/chat`.

**Rationale**: The spec's core constraint (FR-009, FR-011, FR-014) is that log entries are captured
as structured data once, then rendered two different ways from a single centralized place — a
human-readable terminal view today, and (later, out of scope here) a machine/Langfuse-ready shape —
without touching every call site when that switch happens. `structlog`'s processor-chain
architecture is built exactly for this: every log call produces a plain structured event dict that
flows through a shared, ordered list of processors (add timestamp, merge correlation ID, truncate,
redact secrets, ...), and only the *last* processor — the renderer — decides the final output shape.
Swapping `ConsoleRenderer` for a JSON renderer later is a one-line change to that last processor,
directly satisfying FR-014/SC-008 with no change anywhere logs are actually emitted from.

**Alternatives considered**:
- **stdlib `logging` + a custom `Formatter`**: works, but arbitrary structured key-value data per
  event is bolted on via `extra=` rather than being the native call shape, and there's no equivalent
  of a shared processor pipeline — truncation/redaction would need to live inside the `Formatter`
  itself or be duplicated at each call site, which is exactly what FR-014 rules out.
- **`loguru`**: excellent ergonomics and built-in coloring, but its model is string-formatting/sink
  oriented rather than structured key-value events — a worse fit for FR-009's "consistent,
  identifiable fields per entry" requirement, and it has no equivalent processor-chain concept to
  centralize truncation/redaction/rendering.

## 2. Turn/request correlation

**Decision**: `structlog.contextvars` (`bind_contextvars`/`clear_contextvars`), bound once per
request in FastAPI middleware, generating a ULID turn ID (new dependency: `python-ulid`).

**Rationale**: FR-006 requires every log entry from one chat turn to share an identifier without
threading it explicitly through `answer_faq` → `search_faq` → `is_grounded` → the Anthropic call.
`structlog.contextvars` is backed by Python's `contextvars.ContextVar`, which is scoped to the
current `asyncio` task — matching FastAPI's per-request task model exactly: bind once in middleware,
every log call inside that request automatically includes it, and concurrent requests (User Story 3)
never see each other's bound value, with no explicit passing or per-function plumbing.

A ULID (26 chars, Crockford base32, e.g. `01J8Z3K9QAF7VXP9T6E9T3RZ9B`) is used as the ID value
itself, rather than a hyphenated UUID4. It carries the same collision resistance as UUID4 (128 bits
of entropy, versus UUID4's 122), so nothing is given up on uniqueness even if traffic ever grows
beyond this project's current portfolio-demo scale. Unlike UUID4's canonical hyphenated form, a ULID
has no separator characters, so double-clicking it in a terminal selects the whole ID as one token —
useful when a developer is grepping/copying a `turn_id` out of a wall of interleaved log lines
(User Story 2/3). It's also shorter (26 vs. 36 chars) and, as a bonus not required by any FR,
lexicographically sorts by creation time, so turn IDs printed together already sort chronologically.

**Alternatives considered**:
- **UUID4, hyphenated (`uuid.uuid4()`)**: the obvious stdlib default and originally chosen here, but
  its hyphens break single-click/double-click selection in most terminals, and its canonical
  36-character form is the longest of the options considered for something a developer reads
  constantly while debugging.
- **UUID4 hex, truncated (`uuid.uuid4().hex[:8]`)**: shortest option and needs no new dependency, but
  discards enough entropy that the birthday-bound collision risk becomes noticeable at a much lower
  event count than this project is likely to ever produce in one run — not worth the risk for a
  correlation ID whose entire job is uniqueness.
- **`shortuuid` (re-encoded UUID4, base57, ~22 chars)**: keeps full UUID4 entropy and drops the
  hyphens, a reasonable alternative — but ULID does the same (no separators, full-strength
  uniqueness) while also being shorter and chronologically sortable, so it was preferred.
- **Explicit `turn_id: str` parameter threaded through every function**: correct, but pollutes
  `answer_faq`/`search_faq`/`is_grounded` signatures purely for observability — a needless coupling
  between business logic and logging concerns.
- **A request-scoped logger object** (e.g. built once per request, passed down): more machinery than
  a single contextvar bind needs; `structlog.contextvars` already gives task-local scoping for free.

## 3. Severity tiers (routine / error / critical)

**Decision**: Three standard log levels — `info` for routine turn outcomes (grounded answers and
abstentions alike, FR-012) and FAQ operations that succeed; `error` for turn-scoped errors (FR-005)
and failed FAQ operations (FR-007); `critical` for non-turn-scoped critical events (FR-015). The
terminal `ConsoleRenderer` is configured with `critical` styled more prominently than `error`
(FR-019), and `info` left unstyled/neutral so abstentions never read as a problem (per the
2026-08-05 clarification).

**Rationale**: Mapping directly onto Python's already-standard logging-level vocabulary (`info` <
`error` < `critical`) avoids inventing a bespoke severity field that would need its own renderer
logic from scratch. `structlog`'s `ConsoleRenderer` already styles by level out of the box; only
`critical`'s extra prominence over the built-in `error` styling needs a small custom style override,
not a new mechanism.

**Alternatives considered**: a custom `severity` key on the event dict, independent of the logging
level — strictly more flexible, but duplicates what the level already expresses and forfeits
`structlog`'s built-in level-aware rendering for no benefit at this project's scale.

## 4. Truncation (FR-013) and secret redaction (FR-017)

**Decision**: Two `structlog` processors placed early in the shared chain (before the renderer).
Truncation operates generically over every string value in the event dict (not named fields),
clipping anything over 2,000 characters to `2000` chars + `"..."`. Redaction combines two
complementary checks over every key/value pair in the event dict:

1. **Known-value matching**: constructed once at process startup from the process's own live secret
   values (`Settings.ANTHROPIC_API_KEY`, `Settings.VOYAGE_API_KEY`, and the credential portion of
   `Settings.DATABASE_URL`/`Settings.QDRANT_URL`, if either embeds one) — replaces any exact
   occurrence of those literal values inside a string, anywhere, with a fixed placeholder.
2. **Key-name matching**: independent of value, any key whose name case-insensitively contains
   `password`, `token`, `secret`, `api_key`, `apikey`, `credential`, or `authorization` has its
   value replaced outright with the same placeholder, regardless of what that value actually is.

**Rationale**: FR-013/FR-017 apply to "any text value" (message, retrieved content, final answer,
exception detail), not a fixed named field — a generic value-scanning processor makes the rule hold
for every field, including ones added later, without relying on every call site remembering to
truncate/redact its own arguments. Placing both ahead of the renderer in the same shared chain that
gives FR-014 its "one centralized place" property means the rule is enforced identically regardless
of which renderer is active, now or after a future Langfuse-shaped renderer replaces it.

The two redaction checks catch different failure modes, so neither alone is sufficient: known-value
matching only protects a value already on the startup list — a secret introduced later (a new
`Settings` field, or a third-party client's exception embedding its own credential under an
unrelated key) would leak until that list is updated. Key-name matching closes exactly that gap as a
defense-in-depth backstop — it redacts by *looking* like a secret, so a `token=` or `password=`
field is scrubbed on sight even if its value was never registered anywhere. The converse gap is why
known-value matching is still needed too: a real secret logged under an innocuous-looking key
(e.g. a raw `DATABASE_URL` string logged as `connection_info`) wouldn't match the name pattern, but
is still caught because its literal value is on the known list.

**Alternatives considered**:
- Truncating/redacting at each call site (e.g., inside `answer_faq.py` before logging chunk text) —
  rejected, since it is exactly the per-call-site duplication FR-014's rationale warns against, and
  is easy to miss for a field added later.
- Key-name matching alone, without known-value matching — rejected: it only catches secrets logged
  under a recognizably-named key, and would miss a real secret value logged under any other key
  name (the `connection_info` case above).

## 5. Critical events outside a chat turn (FR-015, FR-018)

**Decision**: Critical events are logged from two places that already exist in the code today: (a)
`main.py`'s `lifespan` context manager, around the existing `ensure_collection` Qdrant startup
check; and (b) the existing exception-handling boundary around a dependency call inside a chat turn
or FAQ operation (`search_faq`, the Anthropic call in `answer_faq`, `faq_repository`/
`qdrant_repository` calls) — logged at `critical` level *in addition to* that call site's existing
turn-scoped `error`/failed-FAQ-operation record (FR-018), correlated via the bound turn ID where one
exists.

**Rationale**: This matches what the service can actually fail at today — there is no background
health-check loop, so "critical event" resolves to "a dependency call that was already being made
failed." Reusing the exact call sites that already exist (rather than adding new proactive
monitoring) keeps the feature reactive-only, consistent with Constitution Principle I (no
infrastructure beyond what the current phase needs).

**Alternatives considered**: a dedicated background task periodically polling Qdrant/Postgres
health — rejected as scope creep beyond FR-015's actual requirement (log failures that occur, not
proactively detect ones that haven't yet) and against Constitution I.

## 6. FAQ operation correlation (FR-021) and sub-step logging granularity (FR-020, FR-022)

**Decision**: FAQ content management operations get their own correlation ID (`operation_id`),
generated and bound the same way as a chat turn's `turn_id` (research.md #2) — a ULID, bound via
`structlog.contextvars` in FastAPI middleware for the FAQ CRUD routes, distinct from (never equal
to) any `turn_id` bound elsewhere. Sub-step logging for both retrieval's embedding step (FR-020) and
FAQ content's chunking/embedding steps (FR-022) is *summarized*, not per-item: one log entry per
sub-step describing its outcome (e.g., how many chunks were produced, that all of them were
embedded), not one entry per individual chunk.

**Rationale**: `operation_id` reuses the exact mechanism `turn_id` already established (research.md
#2) — same generator, same contextvars-binding approach, just bound in the FAQ route's middleware
instead of the chat route's — so there's no new correlation *mechanism* to design, only a second
place it's applied. Summarizing chunking/embedding as one entry each (rather than one per chunk)
mirrors how FR-002 already logs retrieval's candidate list as a single entry containing a list,
rather than one entry per candidate: the debugging question a developer actually asks is "did
chunking/embedding succeed, and roughly how much work did it do," not "show me chunk #7
specifically" — a per-chunk log would multiply entry volume for FAQ content that can run to 20,000
characters (dozens of chunks) without adding debugging value FR-016's "enough detail to identify
what failed and why" doesn't already need. A failure during chunking/embedding is still fully
attributable via the operation's `faq.operation_failed` entry (FR-007), which identifies the failing
sub-step by name.

**Alternatives considered**:
- Reusing `turn_id` for FAQ operations instead of a separate `operation_id` — rejected: a FAQ
  operation is conceptually distinct from a chat turn (data-model.md), and naming its correlation
  field `turn_id` would misleadingly suggest FAQ management is itself a "turn."
- One log entry per chunk for chunking/embedding — rejected per the rationale above: it scales log
  volume with content length rather than with genuinely distinct debugging steps, for no gain over a
  single summarized entry plus the existing failure-attribution path.
