# syntax=docker/dockerfile:1
#
# Build from the repo root - chat needs `packages/` and the workspace's `uv.lock`:
#   docker build -f services/chat/Dockerfile -t visitdoc-chat .

# ---------- Stage 1: build the virtualenv ----------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

# Compile .pyc at build time; copy out of the cache mount rather than hard-link
# across it; use this image's Python, never a downloaded one.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Only what decides the dependency set, so the slow install below is cached until
# one of these changes. Scheduler's and the harness's manifests are never installed:
# `uv.lock` describes the whole workspace, and `--locked` can check it only against
# the workspace it was made from.
COPY pyproject.toml uv.lock ./
COPY services/chat/pyproject.toml           services/chat/
COPY services/scheduler/pyproject.toml      services/scheduler/
COPY evals/harness/pyproject.toml           evals/harness/
COPY packages/shared-db/pyproject.toml      packages/shared-db/
COPY packages/shared-logging/pyproject.toml packages/shared-logging/
COPY packages/shared-models/pyproject.toml  packages/shared-models/
COPY packages/shared-proto/pyproject.toml   packages/shared-proto/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package chat --no-dev --no-install-workspace

COPY packages/ packages/
COPY services/chat/ services/chat/

# `--no-editable`: the final stage copies the venv without `src/`, so our own
# packages must be installed into it rather than pointed back at the source tree.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package chat --no-dev --no-editable

# ---------- Stage 2: the image that runs ----------
FROM python:3.12-slim

RUN useradd --create-home --uid 1000 app

# Same path as in the builder: the venv's scripts carry it in their shebangs.
WORKDIR /app
# Left owned by root: the app only reads its code, so a compromised process cannot
# rewrite it. Bytecode was compiled in the builder, so nothing here needs writing.
COPY --from=builder /app/.venv /app/.venv
# Migrations run from this same image, as a one-off command instead of the server.
COPY services/chat/alembic.ini services/chat/alembic.ini
COPY services/chat/alembic/    services/chat/alembic/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# By number, not name: Kubernetes' `runAsNonRoot` checks the image's USER without
# reading its /etc/passwd, and refuses a name it cannot verify.
USER 1000
EXPOSE 8000
CMD ["uvicorn", "chat.main:app", "--host", "0.0.0.0", "--port", "8000"]
