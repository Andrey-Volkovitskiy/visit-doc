# visit-doc
A conversational AI-assistant for a for medical clinics that automates appointment booking via chat and get grounded answers to policy/FAQ questions.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for what was built in which phase, and
[`docs/roadmap_details.md`](docs/roadmap_details.md) for the full design and phased build plan,
including every significant technology choice and its tradeoff, under the phase that made it.

## Repository layout

A `uv`-workspace monorepo:

```
services/
├── chat/          # FastAPI core backend: agent, RAG, chat, auth
├── scheduler/      # FastAPI + own Postgres, gRPC server
└── frontend/       # React + Vite SPA
packages/
├── shared-models/  # cross-service Pydantic schemas
└── shared-proto/   # chat<->scheduler gRPC contract
```

## Installation

Prerequisites: [`uv`](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically by `uv`
if you don't have it).

```bash
uv sync                    # install every service/package into one shared venv
uv run pre-commit install  # install git hooks (lint/type-check before each commit)
```

## Getting started

The `chat` service needs a local Postgres and Qdrant (`docker-compose.yml`, repo root), and a
`.env` file at the repo root with `DATABASE_URL`, `QDRANT_URL`, `ANTHROPIC_API_KEY`,
`VOYAGE_API_KEY` (see `specs/001-grounded-faq-chat/quickstart.md` for the full walkthrough).

```bash
make db-up             # start Postgres + Qdrant
make run-scheduler-dev # migrate, then serve the scheduler (HTTP :8001, gRPC :50051)
make run-chat-dev      # migrate, then serve the chat service (:8000)
make run-frontend-dev  # Vite (:5173)
```

`chat` and `scheduler` must be built from the same commit. The gRPC contract's `Weekday`
numbering changed in 007 (Monday `0` → `1`) in place, inside the unchanged `scheduling.v1`
package, so a mismatched pair does not fail — a new scheduler's Monday reads as an old chat's
Tuesday and every practitioner's schedule is presented a day out, with no error anywhere.

Each service owns its own database, named for it: `visitdoc_chat` and `visitdoc_scheduler`. Each
has its own Alembic history, and neither has a foreign key into the other — they reference each
other by opaque id only. Both live in the one local Postgres container (see the tradeoff under Phase 1c in
[`docs/roadmap_details.md`](docs/roadmap_details.md));
`SCHEDULER_DATABASE_URL` points at the scheduler's, `DATABASE_URL` at chat's.

Each service's test suite runs against its own `_test`-suffixed database (`visitdoc_chat_test`,
`visitdoc_scheduler_test`), with the schema kept at head automatically by a session-scoped fixture
in each `conftest.py` — so manual `curl`/dev work and automated tests can't pollute each other.
`docker-compose.yml` creates all four automatically on a fresh volume; on a pre-existing one,
create the missing ones by hand (see
`specs/005-scheduling-and-booking/quickstart.md` for the exact commands). CI
(`.github/workflows/ci.yml`) uses its own fully ephemeral Postgres/Qdrant service containers, so it
needs no such step.

Qdrant gets the same treatment: tests use a `faq_chunks_test` collection (derived from
`QDRANT_COLLECTION_NAME`, default `faq_chunks`) in the same Qdrant instance, created on demand by
the existing idempotent `ensure_collection` — no volume/init-script step needed, since a collection
is just an API-created resource, not something that has to pre-exist like a Postgres database.

The same five pieces also run entirely in containers, from images built out of each service's
`Dockerfile`: `make stack-up` builds them, runs both migrations and serves the app at the same
addresses as above (`docker-compose.full.yml`); `make stack-down` stops the three services and
leaves the datastores running. It uses the same ports as the hand-run services, so stop those
first. They also run in a local Kubernetes cluster: `make cluster-up`, then `make k8s-deploy`, serves
the app at `http://localhost:8080`. The tradeoffs of both are under Phase 4d in
[`docs/roadmap_details.md`](docs/roadmap_details.md).

See the [`Makefile`](Makefile) for shortcuts (`make sync`, `make lint`, `make format`,
`make typecheck`, `make precommit`, `make install-hooks`, `make run-chat`, `make run-chat-dev`,
`make run-scheduler`, `make run-scheduler-dev`, `make run-frontend-dev`, `make db-up`,
`make db-down`).
