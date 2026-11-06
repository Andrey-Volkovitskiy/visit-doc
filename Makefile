# VisitDoc's commands. `make` (or `make help`) lists every target, by section.
#
# The app is three services - chat (:8000), the scheduler (:8001) and the frontend (:5173) -
# over two datastores, Postgres and Qdrant. They run in one of three ways, each a section below:
#
#   1. Locally, from source:   make db-up && make migrate && make services-up
#   2. In Docker:              make stack-up
#   3. In Kubernetes:          make cluster-up && make k8s-deploy                 (local k3d)
#                              make k8s-tunnel, then make k8s-deploy CLUSTER=public
#
# Run one at a time: the first two use the same ports and the same databases (the Kubernetes
# clusters have their own). First time: `make sync`, `npm install` in services/frontend,
# `make install-hooks`, and a `.env` copied from `.env.example`.

.DEFAULT_GOAL := help

##@ Help

.PHONY: help
help: ## List every target, by section
	@awk 'BEGIN { FS = ":.*## " } \
		/^##@ / { printf "\n%s\n", substr($$0, 5) } \
		/^[a-zA-Z0-9_-]+:.*## / { printf "  %-26s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

##@ Setup and code quality

.PHONY: sync install-hooks lint format typecheck typecheck-python typecheck-frontend precommit
sync: ## Install the Python workspace into .venv (one uv.lock for every member)
	uv sync

install-hooks: ## Install the pre-commit hooks into .git
	uv run pre-commit install

lint: ## Lint the Python code (ruff)
	uv run ruff check .

format: ## Format the Python code (ruff)
	uv run ruff format .

# Both languages: `tsc` is the only thing that typechecks the frontend - vitest
# transpiles without checking, so a type error is invisible to `make test`.
typecheck: typecheck-python typecheck-frontend ## Typecheck Python (mypy) and the frontend (tsc)

typecheck-python: ## Typecheck the Python code (mypy)
	uv run mypy .

typecheck-frontend: ## Typecheck the frontend (tsc)
	cd services/frontend && npm run typecheck

precommit: ## Run every pre-commit hook over every file
	uv run pre-commit run --all-files

##@ Tests (the database-backed tiers need `make db-up`)

.PHONY: test test-unit test-frontend test-integration test-e2e test-db-prune
test: test-unit test-frontend ## The unit and frontend tiers

# Distributed across processes, since every test in these suites talks to real Postgres
# and real Qdrant and spends most of its time waiting on one of them. Each worker gets
# its own database and its own Qdrant collection (shared_db.testing), so they cannot
# clear each other's rows mid-test. Two rather than one-per-core: the speedup plateaus
# early, the Postgres and Qdrant containers need cores of their own, and a second run
# alongside this one (another terminal, another Claude Code session) has to fit on the
# same machine. `--dist loadfile` keeps a file's tests together, so a suite's
# within-file ordering survives.
test-unit: ## Every member's unit tests, on two workers
	uv run pytest -n 2 --dist loadfile

test-frontend: ## The frontend's tests (vitest)
	cd services/frontend && npm test

test-integration: ## The integration tier (tests/integration)
	uv run pytest tests/integration

# A real browser against the stack `make services-up` runs, spending live Claude and Voyage
# calls - so it is never part of `make test` or CI's per-push gate. A failing journey
# leaves its Playwright trace under .run/e2e (`uv run playwright show-trace <zip>`).
# `.run/e2e.env`, if present, is sourced first: the place for one machine's browser setup
# (PLAYWRIGHT_CHROMIUM_EXECUTABLE, LD_LIBRARY_PATH), gitignored with the rest of .run/, and
# a sibling of .run/e2e/ rather than inside it, since pytest-playwright empties that folder
# at the start of every run. ARGS passes through to pytest, e.g. ARGS="-k booking --headed".
test-e2e: ## A real browser against `make services-up` - spends live calls; ARGS=... to pytest
	set -a; if [ -f .run/e2e.env ]; then . ./.run/e2e.env; fi; set +a; \
	uv run pytest tests/e2e --tracing retain-on-failure --output .run/e2e $(ARGS)

# Drop the per-session test databases/collections the suites create automatically
# (one set per Claude Code session, never reaped). Leaves the plain `_test` ones alone.
test-db-prune: ## Drop the per-session test databases and collections
	@./scripts/prune-test-stores.sh

##@ Datastores: Postgres and Qdrant in Docker, for running locally and in Docker

.PHONY: db-up db-down db-reset migrate alembic-chat-history alembic-scheduler-history
db-up: ## Start Postgres (:5432) and Qdrant (:6333)
	docker compose up -d

db-down: ## Stop them, keeping their data
	docker compose down

# Destructive: wipes Postgres + Qdrant data volumes. Confirm before running.
db-reset: ## Stop them and DELETE their data
	docker compose down -v

# Bring both dev databases to head. `run-chat-dev`/`run-scheduler-dev` each do their own half;
# this is for the background services below, which deliberately don't migrate on start.
migrate: ## Bring both databases to the latest migration
	uv run --directory services/scheduler alembic upgrade head
	uv run --directory services/chat alembic upgrade head

alembic-chat-history: ## List chat's migrations
	uv run --directory services/chat alembic history

alembic-scheduler-history: ## List the scheduler's migrations
	uv run --directory services/scheduler alembic history

##@ 1. Run locally: the services from source, on this machine (needs `make db-up`)

.PHONY: services-up services-down services-status services-free-ports \
        run-chat-dev run-scheduler-dev run-frontend-dev run-chat run-scheduler ngrok

# All three services in the background at once, for manual testing. Each records its pid under
# .run/ and is stopped by that pid - never with `pkill -f "chat.main"`, whose pattern also matches
# the shell running that very command and kills the caller with it. chat and scheduler run under
# `uvicorn --reload`, as `run-chat-dev` does, so an edit under their `src/` reaches the running
# service: a background service that held the code it started with made `eval-run` measure the old
# build off the new source. The other side is a reload mid-run: the harness stops the run when the
# restart states different settings, but cannot see one stating the same - a prompt edit changes no
# logged setting - so don't save files under a service's `src/` while `make eval-run` is going.
services-up: ## Start all three in the background, reloading on edits (logs in .run/)
	@./scripts/dev-services.sh up all

services-down: ## Stop them
	@./scripts/dev-services.sh down all

services-status: ## Show which are running
	@./scripts/dev-services.sh status all

# Stop chat and scheduler by what holds their ports, not by a recorded pid - for when
# `services-down` cannot help because .run/*.pid is gone or the service was started by hand with
# `run-chat-dev`. A port whose listener is some other program is reported, never killed.
services-free-ports: ## Stop whatever chat or the scheduler left holding their ports
	@./scripts/dev-services.sh free-ports all

run-chat-dev: ## Migrate, then run chat in this terminal, reloading on edits
	uv run --directory services/chat alembic upgrade head
	uv run --package chat -- uvicorn chat.main:app --reload --reload-dir services/chat/src --host 0.0.0.0 --port 8000

run-scheduler-dev: ## Migrate, then run the scheduler in this terminal, reloading on edits
	uv run --directory services/scheduler alembic upgrade head
	uv run --package scheduler -- uvicorn scheduler.main:app --reload --reload-dir services/scheduler/src --host 0.0.0.0 --port 8001

run-frontend-dev: ## Run the frontend's Vite dev server in this terminal
	cd services/frontend && npm run dev

run-chat: ## Run chat in this terminal, without reload or migrations
	uv run --package chat -- python -m chat.main

run-scheduler: ## Run the scheduler in this terminal, without reload or migrations
	uv run --package scheduler -- python -m scheduler.main

# The staff connector needs a public HTTPS address, because Claude's servers call it: this one
# goes with PUBLIC_BASE_URL in .env (see .env.example).
ngrok: ## Expose the local chat at its public ngrok address, for the staff connector
	ngrok http 8000 --url https://ship-pellet-unranked.ngrok-free.dev

##@ 2. Run in Docker: the services as containers (docker-compose.full.yml)

.PHONY: stack-up stack-down stack-logs

# The whole stack in containers (docker-compose.full.yml): both migrations, then scheduler, chat
# and the frontend, at the same ports as `services-up` - so stop one before starting the other.
STACK := docker compose -f docker-compose.full.yml
# The services the full file adds to docker-compose.yml, read from the two files rather than
# listed here, so `stack-down` leaves Postgres and Qdrant running as `services-down` does.
STACK_APPS = $$($(STACK) config --services | grep -vxF "$$(docker compose config --services)")

stack-up: ## Build the images, start the datastores, migrate, start the services
	$(STACK) up -d --build

stack-down: ## Remove the services' containers, leaving the datastores running
	$(STACK) rm --stop --force $(STACK_APPS)

stack-logs: ## Follow the services' logs
	$(STACK) logs -f $(STACK_APPS)

##@ 3. Run in Kubernetes: every k8s-* target takes CLUSTER=local (default) or CLUSTER=public

# The app in a cluster, in three Kustomize stages (deploy/k8s/overlays/<cluster>/): the
# datastores and settings, then the migrations, then the services - each waited for before
# the next. `CLUSTER` says which cluster, and every k8s-* target acts on it alone:
#   CLUSTER=local   (the default) the k3d cluster `make cluster-up` creates; images built
#                   here and imported. Ingress at http://localhost:8080.
#   CLUSTER=public  k3s on the VPS behind https://visitdoc.app; images pulled from ghcr.io.
#                   Its API is reached only through `make k8s-tunnel`, open in another terminal.
# Pinned to that cluster's context, so these never act on another cluster kubectl happens to
# point at; any other value stops Make rather than guess. The Secrets and the init ConfigMap
# are made here, not committed: the Secrets hold a password and the API keys, the ConfigMap
# is read from docker/postgres-init/. `create --dry-run | apply` makes each safe to re-run.
CLUSTER ?= local
k8s_context = $(if $(word 2,$(CLUSTER)),,$(if $(filter local,$(CLUSTER)),k3d-visitdoc,$(if $(filter public,$(CLUSTER)),visitdoc-public)))
K8S = kubectl --context $(or $(k8s_context),$(error CLUSTER must be local or public, not '$(CLUSTER)'))
# Which variant of deploy/k8s/base/ to apply: the overlay for this cluster.
K8S_OVERLAY = deploy/k8s/overlays/$(CLUSTER)
POSTGRES_USER ?= visitdoc
# The local cluster's only. The public one's is in .env.public (`make k8s-public-env`).
POSTGRES_PASSWORD ?= visitdoc
# The only keys of `.env` the cluster receives: its addresses point at localhost. The public
# cluster reads `.env.public` after it, which replaces ADMIN_SECRET.
CHAT_SECRET_KEYS := ANTHROPIC_API_KEY VOYAGE_API_KEY ADMIN_SECRET LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY
K8S_ENV_FILES = --from .env $(if $(filter public,$(CLUSTER)),--from .env.public)
K8S_APPS := chat scheduler frontend
# `k8s-images` imports every image into each node, so either node's copy is the one pods run.
K8S_NODE := k3d-visitdoc-server-0
# Where `k8s-push` publishes the images, each tagged with the commit it was built from - HEAD
# unless IMAGE_TAG names another, so an earlier commit's images can be deployed again.
REGISTRY := ghcr.io/andrey-volkovitskiy
IMAGE_TAG ?= $(shell git rev-parse --short=12 HEAD)
# The public server. Its API (6443) is closed to the internet and reached through an SSH
# tunnel to it (`make k8s-tunnel`), which is where the `visitdoc-public` context points.
PUBLIC_HOST := 2.29.132.205

# Renders stage $(1) of this cluster's overlay and applies it, with the public overlay's tag
# placeholder replaced by the commit deployed - IMAGE_TAG, HEAD unless named, so an earlier
# commit's images can be deployed again (the local overlay has no placeholder). Rendered into
# a variable first: in a plain pipe a failed render would still be applied, as nothing.
k8s_apply = rendered="$$($(K8S) kustomize $(K8S_OVERLAY)/$(1))" \
	&& printf '%s\n' "$$rendered" | sed 's/unset-deploy-tag/$(IMAGE_TAG)/g' | $(K8S) apply -f -

# The first line of every target that talks to the cluster: for the public one, stop with
# what to do when the tunnel is not open, rather than let kubectl time out against nothing.
k8s_reachable = $(if $(filter public,$(CLUSTER)),@nc -z -w 2 127.0.0.1 6443 \
	|| { echo "no tunnel to the public cluster's API on localhost:6443: run make k8s-tunnel in another terminal"; exit 1; })

##@ 3a. Kubernetes, the local cluster: k3d, k3s in Docker

.PHONY: cluster-up cluster-down k8s-images

# The local Kubernetes cluster (deploy/k3d.yaml): k3s in Docker through k3d, Ingress on
# http://localhost:8080. Disposable - `cluster-down` deletes it and everything running in it.
cluster-up: ## Create the local cluster (Ingress at http://localhost:8080)
	k3d cluster create --config deploy/k3d.yaml

cluster-down: ## Delete the local cluster and everything in it
	k3d cluster delete visitdoc

# The three images, built as `make stack-up` builds them, then copied into the nodes' own
# image store: the local cluster pulls from no registry, and cannot see this machine's images.
k8s-images: ## Build the images and import them into the local cluster's nodes
	@test "$(CLUSTER)" = local || { echo "k8s-images is the local cluster's; the public one pulls what make k8s-push pushed"; exit 1; }
	$(STACK) build chat scheduler frontend
	k3d image import visitdoc-chat visitdoc-scheduler visitdoc-frontend -c visitdoc

##@ 3b. Kubernetes, the public cluster: k3s on the VPS at https://visitdoc.app

.PHONY: k8s-tunnel k8s-public-env k8s-public-context k8s-push

# The tunnel the public cluster's API is reached through: this machine's port 6443 to the
# server's, over SSH, since 6443 is closed to the internet. Holds the terminal; Ctrl+C closes
# it. Fails at once if 6443 is taken here, rather than forwarding nothing.
k8s-tunnel: ## Open the SSH tunnel to the public cluster's API; keep it open in its own terminal
	ssh -N -o ExitOnForwardFailure=yes -L 6443:127.0.0.1:6443 root@$(PUBLIC_HOST)

# The public cluster's own secrets - its Postgres password and ADMIN_SECRET - written once to
# .env.public (gitignored, dockerignored), never overwritten: Postgres keeps the password it
# was initialised with. See scripts/public-env.py.
k8s-public-env: ## Once: write .env.public, the public cluster's own secrets - never regenerate
	uv run python scripts/public-env.py create

# Adds the public cluster to ~/.kube/config as context `visitdoc-public`, from the kubeconfig
# k3s wrote on the server (scripts/public-kubeconfig.sh). Re-run after k3s is reinstalled.
k8s-public-context: ## Once: add the public cluster to ~/.kube/config as context visitdoc-public
	./scripts/public-kubeconfig.sh $(PUBLIC_HOST)

# The images for a cluster that pulls from a registry - the public one - pushed to GitHub's
# (ghcr.io; lower case, which registries require). Each is tagged with the commit it was built
# from, so a deploy names exactly what it runs and a new tag is what rolls the pods. Hence
# the clean-tree check: an image built from uncommitted edits would carry a commit's name
# without its content. Needs `docker login ghcr.io` once, with a token that can write
# packages. Independent of CLUSTER: it touches no cluster.
k8s-push: ## Build the images and push them to ghcr.io at HEAD's commit (publishes them)
	@test -z "$$(git status --porcelain)" \
		|| { echo "commit first - the images are tagged with HEAD, and would not be what it holds:"; \
			git status --short; exit 1; }
	$(STACK) build chat scheduler frontend
	@for app in $(K8S_APPS); do \
		docker tag visitdoc-$$app $(REGISTRY)/visitdoc-$$app:$(IMAGE_TAG) \
			&& docker push $(REGISTRY)/visitdoc-$$app:$(IMAGE_TAG) || exit 1; \
	done
	@echo "pushed $(K8S_APPS) as $(REGISTRY)/visitdoc-<app>:$(IMAGE_TAG)"

##@ 3c. Kubernetes, either cluster: deploy, inspect, delete

.PHONY: k8s-deploy k8s-up k8s-migrate k8s-logs k8s-admin k8s-db k8s-down

# The whole round after a code change, or the first time on a fresh cluster: build the images
# and get them to the cluster - imported into the local one, pushed for the public one - then
# apply everything, migrating the databases and replacing exactly the pods whose image changed.
k8s-deploy: ## The whole round: build the images, get them to the cluster, then k8s-up
ifeq ($(CLUSTER),public)
	$(MAKE) k8s-push
else
	$(MAKE) k8s-images
endif
	$(MAKE) k8s-up

k8s-up: ## Apply everything (secrets, datastores, migrations, services) and wait for it
	$(k8s_reachable)
ifeq ($(CLUSTER),public)
	uv run python scripts/public-env.py check
	@# A tag the registry lacks would leave every pod pulling forever; say so before applying.
	@for app in $(K8S_APPS); do \
		docker manifest inspect $(REGISTRY)/visitdoc-$$app:$(IMAGE_TAG) >/dev/null 2>&1 \
			|| { echo "$(REGISTRY)/visitdoc-$$app:$(IMAGE_TAG) is not in the registry: run make k8s-push"; exit 1; }; \
	done
	@# k3s's own Traefik, in kube-system: TLS, the redirect to HTTPS, the visitor's address.
	$(K8S) apply -k $(K8S_OVERLAY)/traefik
endif
	$(K8S) apply -f deploy/k8s/base/namespace.yaml
	@echo "creating Secret postgres (not echoed: it holds the password)"
ifeq ($(CLUSTER),public)
	@pw="$$(uv run python scripts/env-subset.py --from .env.public POSTGRES_PASSWORD | cut -d= -f2-)" \
		&& test -n "$$pw" && $(K8S) -n visitdoc create secret generic postgres \
		--from-literal=POSTGRES_USER=$(POSTGRES_USER) --from-literal=POSTGRES_PASSWORD="$$pw" \
		--dry-run=client -o yaml | $(K8S) apply -f -
else
	@$(K8S) -n visitdoc create secret generic postgres \
		--from-literal=POSTGRES_USER=$(POSTGRES_USER) --from-literal=POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
		--dry-run=client -o yaml | $(K8S) apply -f -
endif
	$(K8S) -n visitdoc create configmap postgres-init --from-file=docker/postgres-init \
		--dry-run=client -o yaml | $(K8S) apply -f -
	@echo "creating Secret chat from $(strip $(subst --from ,,$(K8S_ENV_FILES))): $(CHAT_SECRET_KEYS)"
	@# Read into a variable first: in a plain pipe a failed read would still be applied.
	@keys="$$(uv run python scripts/env-subset.py $(K8S_ENV_FILES) $(CHAT_SECRET_KEYS))" && printf '%s\n' "$$keys" \
		| $(K8S) -n visitdoc create secret generic chat --from-env-file=/dev/stdin \
		--dry-run=client -o yaml | $(K8S) apply -f -
	@$(call k8s_apply,datastores)
	$(MAKE) k8s-migrate
	@$(call k8s_apply,apps)
ifeq ($(CLUSTER),local)
	@# Every image is tagged `:latest`, so a re-imported one leaves the pod template
	@# unchanged and starts no rollout. Stamping the template with the ID of the image the
	@# node holds changes it exactly when the image did: a new image replaces the pods, an
	@# unchanged one is a no-op ("patched (no change)"). The public cluster needs none of
	@# this: its images are tagged by commit, and a new tag is itself what rolls the pods.
	@for app in $(K8S_APPS); do \
		id="$$(docker exec $(K8S_NODE) crictl inspecti -o go-template --template '{{.status.id}}' visitdoc-$$app:latest)" \
			|| { echo "visitdoc-$$app is not on the nodes: run make k8s-images"; exit 1; }; \
		$(K8S) -n visitdoc patch deployment/$$app \
			-p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"visitdoc/image-id\":\"$$id\"}}}}}" || exit 1; \
	done
endif
	$(K8S) -n visitdoc rollout status deployment/scheduler deployment/chat deployment/frontend --timeout=180s

# Both migrations, as Jobs (the overlay's migrations stage). A finished Job does not run again, so
# the previous ones are deleted first; on failure, their pods' logs are printed.
k8s-migrate: ## Re-run both migrations as Jobs and wait for them (k8s-up runs this)
	$(k8s_reachable)
	$(K8S) -n visitdoc rollout status statefulset/postgres --timeout=180s
	$(K8S) -n visitdoc delete job chat-migrate scheduler-migrate --ignore-not-found
	@$(call k8s_apply,migrations)
	@$(K8S) -n visitdoc wait --for=condition=complete job/chat-migrate job/scheduler-migrate --timeout=180s \
		|| { $(K8S) -n visitdoc logs --prefix --tail=30 \
			-l 'batch.kubernetes.io/job-name in (chat-migrate,scheduler-migrate)'; exit 1; }

# Follows chat's and scheduler's logs, each line prefixed with its pod, as `stack-logs` does
# for the containers. It follows the pods running when it starts: a replaced pod (a restart, a
# rollout) ends its stream, so run it again after one. The probes' requests are left out:
# they would otherwise be most of what it shows.
k8s-logs: ## Follow chat's and the scheduler's logs, without the probes' requests
	$(k8s_reachable)
	$(K8S) -n visitdoc logs -f --prefix --tail=50 -l 'app in (chat,scheduler)' \
		| grep --line-buffered -vE '"GET /(openapi\.json|health) HTTP'

# `/admin` is in no route into the cluster, so it is reached from here, by forwarding a local
# port to chat's Service through the cluster's API. Holds the terminal; Ctrl+C closes it. Port
# 8090, clear of every port the dev services use. Each call needs the cluster's ADMIN_SECRET
# (.env's for the local cluster, .env.public's for the public one) as `X-Admin-Secret`.
k8s-admin: ## Forward chat to localhost:8090, the only way to /admin
	$(k8s_reachable)
	@echo "chat at http://localhost:8090 - e.g. curl -H 'X-Admin-Secret: <secret>' http://localhost:8090/admin/sessions"
	$(K8S) -n visitdoc port-forward svc/chat 8090:8000

# The cluster's Postgres and Qdrant on this machine, for a database client such as DBeaver:
# neither is in any route into the cluster, so they are forwarded through its API as /admin
# is. Ports 15432 and 16333, clear of compose's 5432 and 6333, so the dev datastores can run
# beside them. Postgres's user is `visitdoc`, its password the local cluster's default or
# .env.public's; its databases are visitdoc_chat and visitdoc_scheduler. Qdrant's dashboard is
# at /dashboard. Holds the terminal; Ctrl+C closes both, and either one failing closes the other.
k8s-db: ## Forward Postgres to localhost:15432 and Qdrant to :16333, for DBeaver
	$(k8s_reachable)
	@echo "Postgres at localhost:15432, Qdrant at http://localhost:16333 - Ctrl+C closes both"
	@$(K8S) -n visitdoc port-forward pod/postgres-0 15432:5432 & pg=$$!; \
		$(K8S) -n visitdoc port-forward pod/qdrant-0 16333:6333 & qd=$$!; \
		trap 'kill $$pg $$qd 2>/dev/null' EXIT INT TERM; \
		while kill -0 $$pg 2>/dev/null && kill -0 $$qd 2>/dev/null; do sleep 1; done; \
		echo "a forward stopped (see above), so both are closed"; exit 1

# Deletes the namespace and everything in it - the databases' volumes too. On the public
# cluster that is every visitor's data, so it asks first.
k8s-down: ## Delete the app and its DATA from the cluster (asks first on the public one)
	$(k8s_reachable)
ifeq ($(CLUSTER),public)
	@printf 'This deletes the PUBLIC deployment, its databases included. Type "public" to go on: '; \
		read answer; test "$$answer" = public || { echo "nothing deleted"; exit 1; }
endif
	$(K8S) delete namespace visitdoc --ignore-not-found

##@ Evals: the golden harness (live calls only in eval-run; the rest are offline)

.PHONY: eval-run eval-score eval-compare eval-band eval-cost eval-build-set

# The golden harness (spec 012; see its quickstart.md). `eval-run` drives the golden set against the
# running stack, spending live model calls; `CASES=G-a-01,G-j-03` or `FAMILY=<name>` narrows it,
# and each flag is passed only when set. Every turn is traced to Langfuse when the chat service has
# its keys (spec 014); `TRACE=0` passes `--no-trace`, which sends every turn with
# `X-VisitDoc-Trace: off` instead, and run.json's `tracing` records which it was. `TRACE=1`, or no
# TRACE, passes nothing and is traced; any other value stops Make rather than guess, so a typo
# cannot trace a run that asked not to be. `eval-score RUN=<run_id>` re-scores a stored run offline.
eval_trace_flag = $(if $(filter-out 0 1,$(TRACE))$(word 2,$(TRACE)),$(error TRACE must be 0 (untraced) or 1 (traced), not '$(TRACE)'),$(if $(filter 0,$(TRACE)),--no-trace))

eval-run: ## Drive the golden set against the running stack - spends live calls; CASES= FAMILY= TRACE=0
	uv run --package golden-harness -- python -m golden_harness run \
		$(if $(CASES),--cases $(CASES)) $(if $(FAMILY),--family $(FAMILY)) $(eval_trace_flag)

eval-score: ## Re-score a stored run: RUN=<run_id>
	uv run --package golden-harness -- python -m golden_harness score --run $(RUN)

# Comparing runs (spec 013; see its quickstart.md). Both are offline: they read stored runs and the
# labels, spend no model call, and need nothing running. `eval-compare BASE=<run> NEW=<run>` reports
# what moved between two stored runs, with an optional `BAND=<band id or file>` marking each
# movement against measured noise; `eval-band RUNS=<id>,<id>,<id>,<id>,<id>` builds that band from
# five full runs of one unchanged build.
eval-compare: ## What moved between two runs: BASE=<run> NEW=<run> [BAND=<band>]
	uv run --package golden-harness -- python -m golden_harness compare \
		--base $(BASE) --new $(NEW) $(if $(BAND),--band $(BAND))

eval-band: ## Measure run-to-run noise from five runs: RUNS=<id>,<id>,<id>,<id>,<id>
	uv run --package golden-harness -- python -m golden_harness band --runs $(RUNS)

# What a stored run's model calls spent, by call site: tokens, cache hits, thinking and dollars,
# read from the `model.usage` entries each case stored. Offline, it spends nothing, and a run
# recorded before the chat service logged those entries reports no spend.
eval-cost: ## What a stored run's model calls cost: RUN=<run_id>
	uv run --package golden-harness -- python -m golden_harness cost --run $(RUN)

# Re-render `evals/golden/cases.json` from its declaration in `golden_harness.golden_set`, which is
# where the set is actually written - the JSON is an artifact, and `tests/test_golden_set.py` fails
# when the two disagree. Offline, and it spends nothing.
eval-build-set: ## Re-render evals/golden/cases.json from its declaration
	uv run --package golden-harness -- python -m golden_harness build-set
