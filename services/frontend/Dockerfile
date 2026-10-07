# syntax=docker/dockerfile:1
#
# Built from the repo root, like the Python services, so the root .dockerignore applies:
#   docker build -f services/frontend/Dockerfile -t visitdoc-frontend .

# ---------- Stage 1: build the static bundle ----------
FROM node:24-slim AS builder

WORKDIR /app

# Dependencies first, so `npm ci` is cached until the lockfile changes.
COPY services/frontend/package.json services/frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY services/frontend/ ./
# Typechecks, then writes the bundle to dist/.
RUN npm run build

# ---------- Stage 2: serve it ----------
# The unprivileged nginx build runs as a non-root user and listens on 8080, for the same
# reason the Python images do not run as root.
FROM nginxinc/nginx-unprivileged:1.30-alpine

COPY services/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080
