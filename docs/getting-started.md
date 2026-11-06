# Getting started

How to go from a fresh clone to the whole app running on your machine, with the tests passing and
the commit hooks installed. Every command is run from the repo root unless it says otherwise.
`make` with no arguments lists every target, grouped by section.

## 1. Repository layout

A `uv`-workspace monorepo (see Install below):

```
services/
├── chat/            # FastAPI core backend: agent, RAG, chat, staff console, admin
├── scheduler/       # FastAPI + own Postgres, gRPC server
└── frontend/        # React + Vite SPA
packages/
├── shared-models/   # cross-service Pydantic schemas
├── shared-proto/    # chat<->scheduler gRPC contract
├── shared-db/       # engine construction and migration URL handling
└── shared-logging/  # the structlog processor chain (and its redaction) both services log through
evals/
├── golden/          # the labelled golden set and its pinned corpus
├── harness/         # drives the golden set against the running stack, scores and compares runs
└── baselines/       # committed runs to compare against, and noise bands
tests/               # cross-service integration and end-to-end (browser) tiers
deploy/              # k3d cluster config and Kustomize manifests (local and public clusters)
docker/              # Postgres init scripts
scripts/             # dev helpers: background services, driving a conversation from the shell
specs/               # one directory per feature: spec, plan, research, quickstart
docs/                # roadmap, getting started, testing strategy, style guide
```

## 2. Prerequisites

| Tool | Why | Notes |
|---|---|---|
| [`uv`](https://docs.astral.sh/uv/) | Installs Python and every Python workspace member | Installs Python 3.12 (`.python-version`) by itself if you don't have it |
| Node.js 22+ and `npm` | The frontend (React + Vite) | CI uses 22, the frontend image uses 24 |
| Docker, with `docker compose` | Postgres and Qdrant run in containers | Docker Desktop is fine |
| `make`, `git` | The `Makefile` is the entry point for everything below | |

You also need two API keys for the app to answer anything:

- **`ANTHROPIC_API_KEY`**: Claude, for classification and replies.
- **`VOYAGE_API_KEY`**: Voyage AI, for embeddings and reranking. A new visitor's session gets the
  clinic's starter FAQ corpus embedded on arrival, so even opening the page spends Voyage calls.

The unit and frontend tests need neither key; they run against fakes.

## 3. Install

```bash
make sync                                # uv sync: every service and package into one .venv
make install-hooks                       # pre-commit: uv lock/sync checks, ruff, mypy before each commit
(cd services/frontend && npm ci)         # the frontend's node_modules
```

The repo is a `uv` workspace: all Python members share one `uv.lock` and one `.venv` at the root, so
there is nothing to install per service. Prefix Python tools with `uv run` (`uv run pytest`,
`uv run alembic ...`), the way the `Makefile` does, unless your shell has `.venv` activated.

## 4. Configure

```bash
cp .env.example .env
```

Then fill in `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`. The other defaults already point at the
local containers and ports, so nothing else needs changing to run the app. `.env` is gitignored, and
both services read the same file. `.env.example` explains every field; these are optional:

- `ADMIN_SECRET`: guards the `/admin` session listing and deletion. If it's blank, every request to
  them is refused. The e2e tier uses it to delete the sessions it creates.
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`: per-turn tracing in Langfuse Cloud. Leaving them
  blank turns tracing off.
- `PUBLIC_BASE_URL`: the public HTTPS address the staff connector for the Claude app needs (an ngrok
  static domain in development). If it's blank, the connector is unavailable and nothing else
  changes. See `specs/017-staff-mcp-connector/quickstart.md`.

## 5. Start the datastores

```bash
make db-up      # Postgres :5432 and Qdrant :6333 (docker-compose.yml)
make migrate    # both services' Alembic migrations, scheduler then chat
```

The one Postgres container holds four databases, created automatically on a fresh volume
(`docker/postgres-init/`):

| Database | Used by |
|---|---|
| `visitdoc_chat` | the chat service (`DATABASE_URL`) |
| `visitdoc_scheduler` | the scheduler (`SCHEDULER_DATABASE_URL`) |
| `visitdoc_chat_test`, `visitdoc_scheduler_test` | the test suites |

Each service has its own Alembic history, and neither has a foreign key into the other; they refer
to each other by opaque id only. Tests use their own databases and their own Qdrant collection
(`faq_chunks_test`, created on demand), so manual work and test runs never clean up each other's
data.

The init scripts run only on a **fresh** `postgres_data` volume. If you have an older volume that
predates a database, create it by hand (`docker exec visitdoc-postgres psql -U visitdoc -c "CREATE
DATABASE <name>;"`), or wipe both datastores with `make db-reset` (this **deletes their data**).

`make db-down` stops the containers and keeps their data.

## 6. Run the app

To start all three services in the background, which is what manual testing of a whole flow
needs:

```bash
make services-up       # chat :8000, scheduler :8001 (gRPC :50051), frontend :5173
make services-status   # which are running
make services-down     # stop them
```

Logs and pid files go to `.run/` (gitignored). Chat and the scheduler run under `uvicorn --reload`
watching their own `src/`, so an edit reaches the running service. Stop them with
`make services-down`, never with `pkill -f`. If a port is still held after that, run
`make services-free-ports`.

Or run each service in its own terminal, in the foreground:

```bash
make run-scheduler-dev   # migrate, then serve the scheduler
make run-chat-dev        # migrate, then serve chat
make run-frontend-dev    # Vite dev server
```

Then open **http://localhost:5173**. The patient chat is on the left and the staff console is on
the right. Vite proxies `/chat`, `/chats`, `/faq` and `/console` to chat on `:8000`. A first
visit mints a session and seeds it with practitioners and the starter FAQ corpus. Everything you
see is scoped to that session.

Things worth knowing:

- **Build `chat` and `scheduler` from the same commit.** The gRPC contract's `Weekday` numbering
  once changed in place, inside the same `scheduling.v1` package. A mismatched pair doesn't fail; it
  just shows every schedule a day out.
- **Chat does not need the scheduler to start.** If the scheduler is down, a visitor still gets a
  working (unnamed) chat and FAQ answers.
- **`scripts/dev-chat.sh`** drives a conversation from the shell against a running chat service:
  start a session, post a turn, read the thread or the console, post as staff. Run it with no
  arguments for usage. A patient turn spends live Claude and Voyage calls; a turn into a paused
  conversation spends none.

### The same app in containers or Kubernetes

Neither is needed for day-to-day development, and each uses the same host ports as `services-up`,
so run only one at a time.

- **Docker:** `make stack-up` builds the three images, migrates and serves the app at the same
  addresses (`docker-compose.full.yml`). `make stack-down` removes the services and leaves the
  datastores running. A container runs the image's code, so an edit reaches it only through another
  `make stack-up`.
- **Kubernetes:** `make cluster-up` (needs [`k3d`](https://k3d.io/) and `kubectl`), then
  `make k8s-deploy`, serves the app at `http://localhost:8080` from its own, empty Postgres.
  `make k8s-down` deletes it, data included.

The tradeoffs of both are under Phase 4d in [`roadmap-details.md`](roadmap-details.md).

## 7. Test and check

```bash
make test               # unit tests (every Python member) + frontend tests (vitest)
make test-integration   # tests/integration
make lint               # ruff
make format             # ruff format
make typecheck          # mypy (strict) + tsc for the frontend
make precommit          # every pre-commit hook over every file
```

The Python unit and integration tiers run against the real Postgres and Qdrant, so `make db-up`
first. Each test worker gets its own database and collection. Those per-worker copies are never
cleaned up automatically; `make test-db-prune` drops them. CI (`.github/workflows/ci.yml`) runs
pre-commit, the unit, frontend and integration tiers, and the frontend typecheck on every push.

Two more tiers **spend live Claude and Voyage calls**, so they are run by hand and never in CI.
Both need the app running (`make services-up`):

```bash
make test-e2e                                # eight browser journeys; ARGS="-k booking --headed" goes to pytest
make eval-run CASES=G-a-01,G-j-03            # chosen golden cases, driven and scored
```

- **`make test-e2e`** drives a real Chromium through the patient pane and checks each journey from
  the staff console too. Install its browser once with `uv run playwright install chromium`, or
  point `PLAYWRIGHT_CHROMIUM_EXECUTABLE` at a Chromium you already have. Put either setting in
  `.run/e2e.env` (gitignored), which the target reads before every run. Set `ADMIN_SECRET` so each
  journey can delete the session it creates. A failing journey leaves a Playwright trace under
  `.run/e2e/`. See `tests/e2e/README.md`.
- **`make eval-run`** drives the golden set against the running app and scores it. It reads chat's
  JSON log, so start the app with `LOG_FORMAT=json make services-up`. Run only the cases a change
  touches (`CASES=...` or `FAMILY=...`) before the full set. The other `eval-*` targets are
  offline. See `evals/harness/README.md`.

## 8. Where to read next

- [`ROADMAP.md`](ROADMAP.md): what was built in which phase, on one page.
- [`roadmap-details.md`](roadmap-details.md): the architecture, the design principles, and every
  technology choice with its tradeoff, under the phase that made it. Read the phase you are about
  to change.
- [`testing-strategy.md`](testing-strategy.md): where tests go, how they are named, and why
  `--import-mode=importlib`.
- [`python-style-guide.md`](python-style-guide.md): the Python conventions both services follow.
- `.claude/CLAUDE.md`, and the `.claude/CLAUDE.md` inside each service: the rules and design
  decisions the code holds to. They are written for Claude Code but read just as well by a person.
- `packages/shared-proto/README.md`: regenerating the gRPC stubs after editing the `.proto`, which
  includes a manual import fixup.
- `specs/<number>-<feature>/`: each feature's spec, plan, research and quickstart. The
  quickstarts are manual walkthroughs of a feature against the running app.
