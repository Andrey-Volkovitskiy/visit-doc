.PHONY: sync lint format typecheck typecheck-python typecheck-frontend \
        test test-unit test-frontend test-integration test-e2e test-db-prune \
        precommit install-hooks run-chat run-chat-dev run-scheduler run-scheduler-dev run-frontend-dev \
        services-up services-down services-status services-free-ports migrate \
        db-up db-down db-reset stack-up stack-down stack-logs cluster-up cluster-down k8s-up k8s-down \
        alembic-chat-history alembic-scheduler-history \
        eval-run eval-score eval-compare eval-band eval-cost eval-build-set

sync:
	uv sync

lint:
	uv run ruff check .

format:
	uv run ruff format .

# Both languages: `tsc` is the only thing that typechecks the frontend - vitest
# transpiles without checking, so a type error is invisible to `make test`.
typecheck: typecheck-python typecheck-frontend

typecheck-python:
	uv run mypy .

typecheck-frontend:
	cd services/frontend && npm run typecheck

test: test-unit test-frontend

# Distributed across processes, since every test in these suites talks to real Postgres
# and real Qdrant and spends most of its time waiting on one of them. Each worker gets
# its own database and its own Qdrant collection (shared_db.testing), so they cannot
# clear each other's rows mid-test. Two rather than one-per-core: the speedup plateaus
# early, the Postgres and Qdrant containers need cores of their own, and a second run
# alongside this one (another terminal, another Claude Code session) has to fit on the
# same machine. `--dist loadfile` keeps a file's tests together, so a suite's
# within-file ordering survives.
test-unit:
	uv run pytest -n 2 --dist loadfile

test-frontend:
	cd services/frontend && npm test

test-integration:
	uv run pytest tests/integration

# Drop the per-session test databases/collections the suites create automatically
# (one set per Claude Code session, never reaped). Leaves the plain `_test` ones alone.
test-db-prune:
	@./scripts/prune-test-stores.sh

# A real browser against the stack `make services-up` runs, spending live Claude and Voyage
# calls - so it is never part of `make test` or CI's per-push gate. A failing journey
# leaves its Playwright trace under .run/e2e (`uv run playwright show-trace <zip>`).
# `.run/e2e.env`, if present, is sourced first: the place for one machine's browser setup
# (PLAYWRIGHT_CHROMIUM_EXECUTABLE, LD_LIBRARY_PATH), gitignored with the rest of .run/, and
# a sibling of .run/e2e/ rather than inside it, since pytest-playwright empties that folder
# at the start of every run. ARGS passes through to pytest, e.g. ARGS="-k booking --headed".
test-e2e:
	set -a; if [ -f .run/e2e.env ]; then . ./.run/e2e.env; fi; set +a; \
	uv run pytest tests/e2e --tracing retain-on-failure --output .run/e2e $(ARGS)

precommit:
	uv run pre-commit run --all-files

install-hooks:
	uv run pre-commit install

run-chat:
	uv run --package chat -- python -m chat.main

run-chat-dev:
	uv run --directory services/chat alembic upgrade head
	uv run --package chat -- uvicorn chat.main:app --reload --reload-dir services/chat/src --host 0.0.0.0 --port 8000

run-scheduler:
	uv run --package scheduler -- python -m scheduler.main

run-scheduler-dev:
	uv run --directory services/scheduler alembic upgrade head
	uv run --package scheduler -- uvicorn scheduler.main:app --reload --reload-dir services/scheduler/src --host 0.0.0.0 --port 8001

run-frontend-dev:
	cd services/frontend && npm run dev

# All three services in the background at once, for manual testing. Each records its pid under
# .run/ and is stopped by that pid - never with `pkill -f "chat.main"`, whose pattern also matches
# the shell running that very command and kills the caller with it. chat and scheduler run under
# `uvicorn --reload`, as `run-chat-dev` does, so an edit under their `src/` reaches the running
# service: a background service that held the code it started with made `eval-run` measure the old
# build off the new source. The other side is a reload mid-run: the harness stops the run when the
# restart states different settings, but cannot see one stating the same - a prompt edit changes no
# logged setting - so don't save files under a service's `src/` while `make eval-run` is going.
services-up:
	@./scripts/dev-services.sh up all

services-down:
	@./scripts/dev-services.sh down all

services-status:
	@./scripts/dev-services.sh status all

# Stop chat and scheduler by what holds their ports, not by a recorded pid - for when
# `services-down` cannot help because .run/*.pid is gone or the service was started by hand with
# `run-chat-dev`. A port whose listener is some other program is reported, never killed.
services-free-ports:
	@./scripts/dev-services.sh free-ports all

# Bring both dev databases to head. `run-chat-dev`/`run-scheduler-dev` each do their own half;
# this is for the background services above, which deliberately don't migrate on start.
migrate:
	uv run --directory services/scheduler alembic upgrade head
	uv run --directory services/chat alembic upgrade head

db-up:
	docker compose up -d

db-down:
	docker compose down

# Destructive: wipes Postgres + Qdrant data volumes. Confirm before running.
db-reset:
	docker compose down -v

# The whole stack in containers (docker-compose.full.yml): both migrations, then scheduler, chat
# and the frontend, at the same ports as `services-up` - so stop one before starting the other.
STACK := docker compose -f docker-compose.full.yml
# The services the full file adds to docker-compose.yml, read from the two files rather than
# listed here, so `stack-down` leaves Postgres and Qdrant running as `services-down` does.
STACK_APPS = $$($(STACK) config --services | grep -vxF "$$(docker compose config --services)")

stack-up:
	$(STACK) up -d --build

stack-down:
	$(STACK) rm --stop --force $(STACK_APPS)

stack-logs:
	$(STACK) logs -f $(STACK_APPS)

# The local Kubernetes cluster (deploy/k3d.yaml): k3s in Docker through k3d, Ingress on
# http://localhost:8080. Disposable - `cluster-down` deletes it and everything running in it.
cluster-up:
	k3d cluster create --config deploy/k3d.yaml

cluster-down:
	k3d cluster delete visitdoc

# The app in that cluster (deploy/k8s/). Pinned to its context, so these never act on
# another cluster kubectl happens to point at. The Secrets and the init ConfigMap are made
# here, not committed: the Secrets hold a password and the API keys, the ConfigMap is read
# from docker/postgres-init/.
# `create --dry-run | apply` makes each safe to re-run.
K8S := kubectl --context k3d-visitdoc
POSTGRES_USER ?= visitdoc
POSTGRES_PASSWORD ?= visitdoc
# The only keys of `.env` the cluster receives: its addresses point at localhost.
CHAT_SECRET_KEYS := ANTHROPIC_API_KEY VOYAGE_API_KEY ADMIN_SECRET LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

k8s-up:
	$(K8S) apply -f deploy/k8s/namespace.yaml
	@echo "creating Secret postgres (not echoed: it holds the password)"
	@$(K8S) -n visitdoc create secret generic postgres \
		--from-literal=POSTGRES_USER=$(POSTGRES_USER) --from-literal=POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
		--dry-run=client -o yaml | $(K8S) apply -f -
	$(K8S) -n visitdoc create configmap postgres-init --from-file=docker/postgres-init \
		--dry-run=client -o yaml | $(K8S) apply -f -
	@echo "creating Secret chat from .env: $(CHAT_SECRET_KEYS)"
	@# Read into a variable first: in a plain pipe a failed read would still be applied.
	@keys="$$(uv run python scripts/env-subset.py $(CHAT_SECRET_KEYS))" && printf '%s\n' "$$keys" \
		| $(K8S) -n visitdoc create secret generic chat --from-env-file=/dev/stdin \
		--dry-run=client -o yaml | $(K8S) apply -f -
	$(K8S) apply -f deploy/k8s/

# Deletes the namespace and everything in it - the databases' volumes too.
k8s-down:
	$(K8S) delete namespace visitdoc --ignore-not-found

alembic-chat-history:
	uv run --directory services/chat alembic history

alembic-scheduler-history:
	uv run --directory services/scheduler alembic history

# The golden harness (spec 012; see its quickstart.md). `eval-run` drives the golden set against the
# running stack, spending live model calls; `CASES=G-a-01,G-j-03` or `FAMILY=<name>` narrows it,
# and each flag is passed only when set. Every turn is traced to Langfuse when the chat service has
# its keys (spec 014); `TRACE=0` passes `--no-trace`, which sends every turn with
# `X-VisitDoc-Trace: off` instead, and run.json's `tracing` records which it was. `TRACE=1`, or no
# TRACE, passes nothing and is traced; any other value stops Make rather than guess, so a typo
# cannot trace a run that asked not to be. `eval-score RUN=<run_id>` re-scores a stored run offline.
eval_trace_flag = $(if $(filter-out 0 1,$(TRACE))$(word 2,$(TRACE)),$(error TRACE must be 0 (untraced) or 1 (traced), not '$(TRACE)'),$(if $(filter 0,$(TRACE)),--no-trace))

eval-run:
	uv run --package golden-harness -- python -m golden_harness run \
		$(if $(CASES),--cases $(CASES)) $(if $(FAMILY),--family $(FAMILY)) $(eval_trace_flag)

eval-score:
	uv run --package golden-harness -- python -m golden_harness score --run $(RUN)

# Comparing runs (spec 013; see its quickstart.md). Both are offline: they read stored runs and the
# labels, spend no model call, and need nothing running. `eval-compare BASE=<run> NEW=<run>` reports
# what moved between two stored runs, with an optional `BAND=<band id or file>` marking each
# movement against measured noise; `eval-band RUNS=<id>,<id>,<id>,<id>,<id>` builds that band from
# five full runs of one unchanged build.
eval-compare:
	uv run --package golden-harness -- python -m golden_harness compare \
		--base $(BASE) --new $(NEW) $(if $(BAND),--band $(BAND))

eval-band:
	uv run --package golden-harness -- python -m golden_harness band --runs $(RUNS)

# What a stored run's model calls spent, by call site: tokens, cache hits, thinking and dollars,
# read from the `model.usage` entries each case stored. Offline, it spends nothing, and a run
# recorded before the chat service logged those entries reports no spend.
eval-cost:
	uv run --package golden-harness -- python -m golden_harness cost --run $(RUN)

# Re-render `evals/golden/cases.json` from its declaration in `golden_harness.golden_set`, which is
# where the set is actually written - the JSON is an artifact, and `tests/test_golden_set.py` fails
# when the two disagree. Offline, and it spends nothing.
eval-build-set:
	uv run --package golden-harness -- python -m golden_harness build-set

ngrok:
	ngrok http 8000 --url https://ship-pellet-unranked.ngrok-free.dev