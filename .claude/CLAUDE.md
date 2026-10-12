# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@CLAUDE.local.md

## Project status

`docs/ROADMAP.md` is the authoritative design document — read it before starting any implementation
work, since it defines the architecture, phased build plan, and the reasoning behind each technology
choice. Treat its "Design principles" and "Phased build plan" sections as binding scope guidance,
not just background: don't add services/infra beyond what the current phase calls for.

The repo is a **monorepo**: `services/chat`, `services/scheduler`, and `services/frontend` are
independent services, with cross-service Python code (Pydantic schemas, the gRPC contract) factored
out into `packages/`. See "Repository layout" below.

## What this project is

VisitDoc is a conversational AI assistant for a medical clinic (a portfolio project targeting an AI
developer role). Patients book/reschedule/cancel appointments via chat and get grounded,
citation-backed answers to policy/FAQ questions; the assistant abstains and escalates to human staff
rather than confabulating when retrieval is weak. The applied-AI core (agent graph, RAG, tool use,
eval/observability) is the focus — platform/infrastructure work is explicitly scoped as optional
later phases.

## Repository layout

`evals/harness` is a tool, not shared library code, so it sits beside the data it reads rather than
under `packages/`. It depends on `chat` and `scheduler` so it parses through their own enums and seed
rather than copies of them. See `evals/harness/README.md`.

`shared-logging` and `shared-db` exist because `chat` and `scheduler` otherwise hold byte-identical
copies of their infrastructure layer, varying only in a `Settings` field name. Redaction in
particular is a security control: a copy that drifts leaves one service logging in the clear with
nothing to catch it. Each service keeps only what genuinely varies — its secret-field tuples, and
its own module-level engine bound to its own URL setting.

`chat` and `scheduler` depend on `shared-models`/`shared-proto` via `tool.uv.sources` workspace
references. All Python members share **one `uv.lock` and one `.venv`** at the repo root — they
can't pin conflicting versions of a shared dependency.

### Convention: directory-scoped CLAUDE.md files

When a subdirectory needs its own Claude Code guidance (e.g. a service-specific style guide), place
it at `<dir>/.claude/CLAUDE.md`, not `<dir>/CLAUDE.md` — matching this repo's own root convention
(this file lives at `.claude/CLAUDE.md`, not the repo root). Claude Code loads a directory's
`CLAUDE.md` only when files under that directory are being read/edited, so this keeps
service-specific rules out of context everywhere else. `services/chat/.claude/CLAUDE.md` and
`services/scheduler/.claude/CLAUDE.md` both `@`-import `docs/python-style-guide.md` rather than
duplicating it; chat's, like `evals/harness/.claude/CLAUDE.md`, also holds the design decisions
that govern only that component.

`services/frontend/.claude/CLAUDE.md` deliberately holds its rules inline instead of `@`-importing
a `docs/frontend-style-guide.md`: the `@`-import exists so two Python services can share one guide,
and there is only one frontend, so a separate file would add indirection with no second reader. If
a second Node project ever appears, extract it then.

## Commands

Common commands are in the [`Makefile`](../Makefile).

The golden harness has its own four: `make eval-run` (optionally `CASES=G-a-01,G-j-03` or
`FAMILY=<name>`) drives cases against the running stack, **spending live Claude and Voyage calls**,
then scores and prints the report. When the chat service has Langfuse keys every turn it drives is
traced, spending Langfuse units too; `TRACE=0` sends every turn untraced (`--no-trace`), and any
value but `0` or `1` stops Make. `make eval-score RUN=<run_id>` re-scores a stored run under
`.run/evals/` and needs no stack. `make eval-compare BASE=<run> NEW=<run>` (optionally
`BAND=<band>`) reports what moved between two stored runs, and `make eval-band
RUNS=<id>,<id>,<id>,<id>,<id>` measures the run-to-run noise from five full runs of one unchanged
build. Both of those are offline — no stack, no model call — and `compare` takes a run id or a
directory path, so a committed run can be named as a baseline where it sits. **The baseline is
`evals/baselines/01M3WD842TTD1Q9FX8TDRTMFAB`** - the whole set at 146 cases, taken on `009d4ff`
after Phase 4b extended the corpus to 19 entries, dropped the similarity floor, let the answerer
give a no the information states, chunked documents by heading section and fixed the
classifier's routing of what the clinic sent, billed or treated - 1 of 87 answerable requests
unserved;
`01M3VYV0RYA8N8RF2S1RQE5PSF`, the same set under the 0.25 floor, stays as the record of what the
floor cost. The earlier
v2 baseline `01M321DWRXSVSY7GW9RY3CR9YW` no longer re-scores - the corpus pin changed and six of
its labels gained a citation - and stays as the record of the 9-entry build, as the 2b record
under `specs/012-golden-set-metrics/evaluation/` stays frozen as FR-048a made it. `evals/baselines/README.md` says what a committed run is for, and why a baseline
is evidence rather than a threshold. **The noise band is
`evals/baselines/bands/01M3W93XYN42QWH1TCYXN3E5FC.json`**, five runs of the previous baseline's
build (`a171078`, which differs only in the classifier prompt): classification,
retrieval and booking did not move at all across them, and unserved answerable ranged 4-6 of 87 -
so a classifier or retrieval movement against it is real, and a ±1 in answered requests is not.
It is still a range, never a number a later run has to beat.
**Never start a full `make eval-run` to check a fix before the cases it touches have passed on
their own.** The full set spends live calls on every case; a fix is checked first offline where it
can be (replaying stored prompts from a run's case files), then with `make eval-run CASES=...` over
exactly the cases it can move — the case it is for, and every case sharing the path it changed —
and only once those behave is the full set run to compare against the baseline. A full run spent on
an unchecked FAQ-prompt change (2026-09-25) surfaced a regression in G-k-15 that the dozen affected
cases would have shown for a fraction of the cost.
`make eval-cost RUN=<run>` totals what a stored run's model calls spent - tokens, prompt-cache
hits, thinking and dollars per call site - from the `model.usage` entry the chat service logs for
every model call that returned; it is offline too, and a run recorded before that entry existed
reports none rather than zero. A sixth target, `make eval-build-set`, re-renders `evals/golden/cases.json` from its declaration in
`golden_harness.golden_set` — the JSON is an artifact, and `tests/test_golden_set.py` fails
byte-for-byte when the two disagree, so the set is changed by editing the declaration and
re-rendering, never by editing the JSON. It is offline and spends nothing.

`eval-run` requires the chat service to log JSON, so start the
stack as `LOG_FORMAT=json make services-up` (the harness reads `.run/chat.log`). Resuming a stopped
run takes an argument: `uv run --package golden-harness -- python -m golden_harness run --resume
<run_id>`.

### Running the stack by hand

`make run-chat-dev` / `make run-scheduler-dev` / `make run-frontend-dev` each hold a terminal. To
put all three in the background instead — which is what manual testing of a whole flow needs —
`make services-up`, `make services-status`, `make services-down`, with `make migrate` first if the
dev databases are behind. The background chat and scheduler run under `uvicorn --reload` watching
their own `src/`, the same as the `-dev` targets, so an edit reaches the running service. They did
not until 2026-09-20, and the cost of that was a paid `eval-run` measuring the build the service had
started with while its report read the conditions off the new source — a result that meant nothing
and looked fine. A run takes its conditions from the *latest* `service.configured` before it starts,
so a reload before a run is read correctly. The other side is a reload **during** one: the harness
checks after every case and raises `ServiceRestartedError` when the restart states different
settings, but a restart stating the same ones is passed over — and a prompt or handler edit changes
behaviour without changing any logged setting, so that run silently spans two builds with nothing to
catch it. Don't save files under a service's `src/` while `make eval-run` is going.

When `services-down` has nothing to stop but the ports are still bound —
a pid file lost, or a service started by hand with `make run-chat-dev` — `make services-free-ports`
stops whatever holds chat's and scheduler's ports, after confirming from the listener's own command
line that it is that service; anything else on the port is reported and left running. Each
service's pid and log live under `.run/` (gitignored).

**Stop them with `make services-down`, never with `pkill -f "chat.main"`.** That pattern also
matches the command line of the shell running it, so it kills the caller — and anything else whose
command line happens to quote the module name. `scripts/dev-services.sh` kills a recorded pid and
`pkill -P` for its child, which cannot match anything by accident.

**The staff connector (017) needs a public HTTPS address**, because Claude's servers call it, not
the phone. Run `ngrok http --url=<static-domain> 8000` in front of the chat service, set
`PUBLIC_BASE_URL=https://<static-domain>` in `.env`, and restart the stack: the address is read at
startup. Without it the console's gear tab says pairing is unavailable and why, and nothing else
changes. The walk against a real Claude account is `specs/017-staff-mcp-connector/quickstart.md`.

**The same stack also runs in containers** (Phase 4d's first step): `make stack-up` builds the
three images from their `Dockerfile`s and starts `docker-compose.full.yml`, which `include`s
`docker-compose.yml` and adds both migrations as one-off containers plus the three services, at the
same host ports as `services-up` - so only one of the two runs at a time, and both use the same
databases. `make stack-down` removes the services and leaves Postgres and Qdrant running; **never
`docker compose -f docker-compose.full.yml down`**, which stops the included datastores too. A
container's code is the image's, so an edit reaches it only through another `make stack-up`.

**A local Kubernetes cluster** comes from `make cluster-up` (k3d, defined in `deploy/k3d.yaml`,
Ingress at `http://localhost:8080`) and goes with `make cluster-down`. `k3d` is installed in
`~/.local/bin`; `kubectl` comes from Docker Desktop. The nodes have their own image store, so a
locally built image must be imported (`k3d image import <image> -c visitdoc`) and run with
`imagePullPolicy: Never` or `IfNotPresent` - with a `:latest` tag Kubernetes otherwise tries to
pull it from Docker Hub. The manifests are Kustomize: `deploy/k8s/base/` names the images untagged,
and `deploy/k8s/overlays/local/` adds the `:latest` tag and `Never` pull (Makefile's `K8S_OVERLAY`);
both come as three stages, `datastores`, `migrations` and `apps`, applied in that order with
`kubectl apply -k`, and `kubectl kustomize <dir>` prints what one renders to. `make k8s-up`
applies them to the cluster, in the `visitdoc` namespace,
after creating the objects that are not committed (the Postgres Secret, the init-script ConfigMap
built from `docker/postgres-init/`, and Secret `chat` - only the API keys and `ADMIN_SECRET` from
`.env`, read by `scripts/env-subset.py`, since `.env`'s addresses point at localhost). `make
k8s-images` builds the three images and imports them; `make k8s-migrate` re-creates the two
migration Jobs (the `migrations` stage, since a finished Job never runs again) and waits for them - on a failure it prints their logs, but only once its 3-minute
wait runs out. `make k8s-up` runs it between the datastores and the services (the `apps`
stage), whose Ingress sends everything at `http://localhost:8080` to the frontend's
nginx, as compose's `:5173` does - so `GET /chats` there is free, and only `POST /chats` (which the
page sends on a first arrival) mints a session and spends Voyage calls seeding it. Chat runs as exactly one pod, replaced with `strategy: Recreate`, because
its running turns live in process memory (`generation_registry`) - don't scale it or switch it to
a rolling update. `make k8s-deploy` is the whole round - images, then `k8s-up`, which stamps each
Deployment's pod template with the ID of the image the node holds (`visitdoc/image-id`), so a
rebuilt `:latest` image rolls its pods and an unchanged one rolls none. `make k8s-push` builds
the images and pushes them to `ghcr.io/andrey-volkovitskiy/visitdoc-<app>:<12-char commit>`, for a
cluster that pulls - it refuses while the tree is dirty, and needs a one-time `docker login ghcr.io`
with a token that can write packages. The images are verified to carry no `.env` value
(`.dockerignore`); pushing publishes them. `make k8s-logs` follows chat's and scheduler's logs with the probes' requests
filtered out, until their pods are replaced. `make k8s-down` deletes the namespace - **its databases'
volumes included**. The cluster's Postgres is its own, empty one, never the compose one.

`scripts/dev-chat.sh` drives a conversation against a running chat service — mint a session, post a
turn and stream the reply, read the thread or the staff console, post as staff, flip the assistant
switch, add a FAQ entry. It exists so that exercising a flow by hand doesn't start with rebuilding
a cookie jar and a `curl` invocation from memory. Run it with no arguments for the usage block.

Regenerating the gRPC stubs (after editing `packages/shared-proto/protos/scheduling/v1/scheduling.proto`)
is documented in `packages/shared-proto/README.md` — it requires a manual import fixup after
running `protoc`, don't skip that step.

Ruff lint rules are configured once, in the root `pyproject.toml`'s `[tool.ruff]`/`[tool.ruff.lint]`
tables, and apply to every Python workspace member automatically via ruff's hierarchical config
discovery (no member has its own `[tool.ruff]`, so the walk-up always lands on the root). Generated
code is excluded via `extend-exclude` rather than hand-reformatted to match style rules — the
established examples are gRPC stubs (`**/*_pb2.py`, `**/*_pb2_grpc.py`) and Alembic migrations
(`**/alembic/versions/*.py`); follow the same pattern for the next generated-code case instead of
fixing lint violations by hand in generated files.

`specs/**/*.py` is excluded from **both** ruff and mypy for the same reason, though it is not
generated: a spec directory holds documentation plus the data and one-off harnesses a manual
procedure is run from (spec 009's `evaluation/inputs/` is the first). None of it is application
code, nothing imports it, and it ships with a spec rather than with a service — so the rules written
for the app do not apply to it, and annotating a throwaway driver to satisfy `strict` would buy
nothing. Note that `make typecheck` runs `mypy .`, which overrides the `files` list in
`[tool.mypy]`, so mypy needs its own `exclude` entry rather than relying on `files` alone. **Do not
put application code under `specs/`** — it is unchecked by both gates.

Testing conventions (folder layout, naming, why `--import-mode=importlib` is required, why `tests/`
is excluded from mypy) are documented in `docs/testing-strategy.md`. In short: unit tests are
colocated per workspace member (`services/chat/tests/`, `services/scheduler/tests/`,
`packages/shared-db/tests/`, `packages/shared-models/tests/`, `packages/shared-proto/tests/`,
`evals/harness/tests/`); integration/e2e tests are
centralized at `tests/integration/`/`tests/e2e/`. Run via `make test` /
`make test-unit`, `make test-integration`, `make test-e2e`; CI runs the unit, frontend and
integration tiers (`.github/workflows/ci.yml`, alongside `pre-commit`) and never e2e.

**The e2e tier (Phase 3b) drives a real Chromium against the running stack and spends live
Claude and Voyage calls**, so it is run by hand: `make services-up`, then `make test-e2e`. It
isolates by session rather than by database - each journey mints its own session, plants its
prestate through the console API and the booking tools' gRPC client, and deletes the session
through `/admin` - and gives the browser a fixed-offset zone in which it is early morning, so
"today" always has a working day ahead. See `tests/e2e/README.md`.

## Architecture

### Core design principles
- Follow SOLID, major clean architecture principles, and best industry paractices.
- Dependency Inversion: orchestration/high-level code (e.g. a LangGraph node) should depend only on
  domain types, never on a specific provider's wire format (e.g. Anthropic's `MessageParam`).
  Translating domain data into a provider's request shape is that provider-calling function's own
  responsibility, done internally, not the caller's — keeps provider-specific knowledge in one
  place and out of orchestration code.
- **One value, one meaning.** A return value, status, or empty result must never stand for two
  different situations. An empty list that means both "this exists and has nothing" and "this does
  not exist", or a `None` that means both "already done" and "the dependency was unreachable",
  forces every caller to guess — and in an agent, a guess becomes a confident false statement to a
  patient. When two situations are genuinely different, give them different answers: a distinct
  return type, a typed failure, a `NOT_FOUND` status. If a value's docstring needs the word "or" to
  describe what it means, that is the smell.
- **Every query carries its session/tenant predicate.** Scoping is a `WHERE` clause on the read,
  never a check applied to the result afterwards, so an id from another session simply does not
  resolve. This applies especially where it feels unnecessary: a lookup by a globally-unique key
  (an idempotency key, a ULID) still needs the scope, because "unique" only means no *collision* —
  it says nothing about who is allowed to read the row. A scoped check placed *after* an unscoped
  lookup that already returned data is not a check at all.
- **A timeout never proves the server did nothing.** A deadline is the caller's, not the callee's:
  it expiring means the answer did not arrive, not that the work did not happen. The same is true
  of a server-side error status, which means the request *was* processed. Code may report "nothing
  was created" only when it actually knows that — every attempt failed to reach the server. When
  the outcome is genuinely unknown for a write, say so and stop, rather than guessing "nothing
  happened" and inviting a retry that duplicates a real, uncancellable side effect.

### Target shape (AI-core phase, per ROADMAP)

- **Core backend** — FastAPI, hosting the agent, RAG, chat, and auth. Single deployable for
  everything except Scheduling. Beyond `/chat`, `/chats` and `/faq` it publishes `/console/*` (the
  staff side: the polled conversation listing, posting as staff, the assistant switch, and a proxy
  of the scheduler's practitioner API, including since 016 a practitioner's standing appointments
  for the viewer's coming week) and `/admin/*` (the session listing and session
  deletion — guarded by one header secret, and declared `include_in_schema=False` so they
  appear in no published schema). Those two live in separate modules on purpose: a
  maintenance surface sharing a module with a published one is one refactor away from
  sharing its router.
- **Scheduling service** — a separate FastAPI service with its own PostgreSQL, the one deliberate
  service boundary in this phase. Owns doctor calendars, availability, booking, and changes to a
  booking. Talks to the core backend over gRPC (`CheckAvailability`, `BookAppointment`,
  `RescheduleAppointment`, `CancelAppointment`, `ListAppointments`). Double-booking is prevented at
  the database level via PostgreSQL exclusion constraints on interval/range types, not application
  code — **partial** ones (`WHERE status = 'standing'`) since 006, so a cancelled appointment stops
  occupying its slot at the datastore rather than by an application filter. A *change* is likewise
  one conditional `UPDATE` whose `WHERE` clause carries the staleness guard, never a check
  performed before the write.
- **Vector store** — Qdrant, for RAG over clinic policy/FAQ documents.
- **Agent framework** — LangGraph, with real branching (parallel specialist nodes + merge) for
  mixed-intent messages rather than a linear chain.
- **Frontend** — a minimal React + Vite streaming chat UI.
- **Tracing/eval** — Langfuse Cloud (free Hobby tier) for per-step latency, token cost, and decision
  traces; eval runs send Langfuse events by default, and `TRACE=0` turns that off for a run.

### Key design decisions to preserve

Decisions that govern one component live beside it and load when files there are read: the chat
service's (classification, RAG, request outcomes, escalation, booking acts, the staff connector,
tracing) in `services/chat/.claude/CLAUDE.md`, the golden harness's (scoring and comparison) in
`evals/harness/.claude/CLAUDE.md`. Read the relevant one before changing that behaviour from
anywhere else - `tests/`, `evals/` or `specs/`.

- Repository functions take the `AsyncSession` as an explicit parameter (e.g.
  `faq_repository.create(session, content)`) rather than a repository class holding session state —
  matches FastAPI's own documented pattern, keeps repository functions stateless and reusable across
  callers, and lets the API layer own the transaction boundary (one session per request via
  `Depends`). New repositories, including `scheduler`'s, should follow this shape.
- Scheduling failure handling (timeouts, retries, agent behavior when Scheduling is unreachable) is
  part of the design, not an afterthought.
- Each significant technology choice should be documented with its tradeoff in the README, so later
  additions should follow that pattern rather than going undocumented.

See `docs/ROADMAP.md` for the full phased plan (Phase 0 walking skeleton → Phase 1 agent → Phase 2
eval/observability → Phase 3 frontend → Phase 4+ optional: the staff connector, retrieval under a
realistic corpus (hybrid search planned there, measured as unneeded and not built), the
starter corpus's seeding cost (deferred until measured), then Kubernetes).
