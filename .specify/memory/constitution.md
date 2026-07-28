<!--
Sync Impact Report
- Version change: (unratified template) → 1.0.0
- Rationale: Initial concrete ratification. The file previously contained only
  unfilled [PLACEHOLDER] tokens; this is the first version with binding content,
  so it is treated as the initial adoption (MAJOR = 1.0.0), not an incremental bump.
- Modified principles: n/a (first ratified version)
- Added sections: Core Principles (I-VII), Repository & Workspace Structure,
  Development Workflow & Quality Gates, Governance
- Removed sections: none
- Templates requiring updates:
  - ✅ .specify/templates/plan-template.md — generic "Constitution Check" gate
    already defers to this file; no hardcoded contradictions found.
  - ✅ .specify/templates/spec-template.md — generic, no technology-specific
    assumptions that conflict with these principles.
  - ✅ .specify/templates/tasks-template.md — generic single/web-app scaffolding;
    already compatible with the monorepo layout described below.
  - ✅ .claude/skills/speckit-*/SKILL.md — no CLAUDE-only or other agent-specific
    hardcoded references found (the phrase appears only in this command's own
    generic instructions, not as an actual stale reference).
  - ✅ README.md, .claude/CLAUDE.md — source documents for this constitution;
    no changes needed, they remain the detailed reference this file summarizes.
- Follow-up TODOs: none.
-->

# VisitDoc Constitution

## Core Principles

### I. Phase-Gated Scope Discipline (NON-NEGOTIABLE)
`docs/ROADMAP.md` is the authoritative, binding build plan, not background reading. Work MUST
proceed in phase order: Phase 0 (walking skeleton) before Phase 1 (the real agent) before Phase 2
(evaluation/observability), with Phase 3+ platform layers (further service extraction, message
broker, ClickHouse/analytics, staff console, Kubernetes) built only if time allows and only as
deliberate evolution. Do not add services, infrastructure, or platform layers beyond what the
current phase calls for, even if the target architecture describes them.
**Rationale**: This is a portfolio project with fixed effort; scope creep into infrastructure
directly trades away time from the applied-AI work the project exists to demonstrate.

### II. AI Core Is the Centerpiece
The agent graph, retrieval-augmented generation, tool use, and the evaluation/observability harness
receive the bulk of engineering effort. Platform and infrastructure work is explicitly secondary and
optional. When effort must be traded off, the AI core wins.
**Rationale**: The project targets an AI developer role — the applied-AI work is what the project is
being judged on.

### III. One Deliberate Service Boundary
Scheduling is the single intentionally separated service in the AI-core phase: its own FastAPI app,
its own PostgreSQL database, and a synchronous gRPC API (`CheckAvailability`, `BookAppointment`)
consumed by the core backend. Double-booking MUST be prevented at the database level via a
PostgreSQL exclusion constraint on interval/range types in Scheduling's own database — not caught by
application code. Scheduling failure handling (timeouts, retries, and agent behavior when Scheduling
is unreachable) is part of the design and MUST be addressed alongside the happy path, not deferred.
No further service extraction happens before Phase 3+.
**Rationale**: One real, well-justified service boundary demonstrates distributed-systems judgment
without paying the operational cost of splitting everything up front.

### IV. Structured Outputs & Decoupled Tool Interfaces
Intent classification (FAQ / booking / escalation) MUST use structured output from a cheap, fast
model — never free-text parsing — reserving the stronger model for generation. Every capability the
agent can invoke (`search_faq`, `check_availability`, `book_appointment`, `escalate_to_staff`) MUST
be exposed as an MCP tool, so agent logic stays decoupled from how each capability is implemented.
**Rationale**: Structured outputs are reliable and cheap to route on; MCP tool boundaries let the
agent's reasoning be tested and evolved independently of backend implementation details.

### V. Grounded Retrieval with Mandatory Abstention
RAG answers MUST go through defensible chunking and a reranking step, cite the source document(s),
and pass an explicit groundedness check before being returned to the user. When retrieval is weak,
the assistant MUST abstain and escalate to human staff rather than confabulate an answer.
**Rationale**: A medical-clinic assistant that guesses at policy or clinical logistics is worse than
one that admits uncertainty — abstention is a correctness requirement, not a nicety.

### VI. Documented Technology Tradeoffs
Every significant technology choice (backend framework, datastore, agent framework, tracing/eval
tool, inter-service protocol, etc.) MUST be recorded in the README with its tradeoff, following the
existing table format in `docs/ROADMAP.md`. New additions follow the same pattern rather than going
undocumented.
**Rationale**: For a portfolio project, the README is the artifact a reviewer reads — undocumented
choices read as accidental rather than intentional.

### VII. Clean Architecture & SOLID
Code follows SOLID, established clean-architecture separation of concerns, and general industry best
practice. Service-specific style guides (see `services/chat/.claude/CLAUDE.md`,
`services/scheduler/.claude/CLAUDE.md`, both importing `docs/python-style-guide.md`) govern the
concrete Python conventions; this principle is the non-negotiable umbrella they specialize.
**Rationale**: Clean boundaries keep the agent, RAG, and scheduling logic independently testable and
keep the codebase legible to a reviewer evaluating engineering judgment, not just AI output quality.

## Repository & Workspace Structure

The repository is a single `uv`-workspace monorepo, not independent repositories: `services/chat`,
`services/scheduler`, and `services/frontend` are independent services; cross-service Python code
(Pydantic schemas in `packages/shared-models`, the gRPC contract in `packages/shared-proto`) is
factored out rather than duplicated. All Python workspace members share one `uv.lock` and one
`.venv` at the repository root and MUST NOT pin conflicting versions of a shared dependency.
`services/frontend` is a plain Node project and is not a `uv` workspace member. Directory-scoped
guidance (a service-specific style guide, for example) belongs at `<dir>/.claude/CLAUDE.md`, matching
the root's own `.claude/CLAUDE.md` convention, so it loads only when that directory is in scope.

## Development Workflow & Quality Gates

Ruff and mypy are each configured exactly once, in the root `pyproject.toml`, and apply to every
workspace member via ruff's hierarchical config discovery and mypy's explicit per-member `files`
entries (`strict` mode); no member may add its own conflicting `[tool.ruff]` or `[tool.mypy]` table.
Pre-commit runs four local hooks — `uv-lock-check`, `uv-sync-check`, `ruff-check`, `mypy-check` — all
shelling out to the exact tool versions pinned in `uv.lock` so there is one source of truth for
versions. Tests are tiered: unit tests are colocated per workspace member; integration and e2e tests
are centralized under `tests/integration/` and `tests/e2e/`. Only the unit tier runs in CI so far,
alongside pre-commit. New workspace members and new test suites MUST follow these same conventions
rather than introducing a parallel lint/type/test setup.

## Governance

This constitution supersedes ad hoc practice for any conflict between "how we've been doing it" and
what is written here; `docs/ROADMAP.md` and `.claude/CLAUDE.md` remain the detailed reference this
file summarizes into binding rules, and this file governs when the two diverge. Amendments are made
by editing this file directly, updating the Sync Impact Report at its top, and propagating any
consequential changes to `.specify/templates/*.md` and to `README.md` / `.claude/CLAUDE.md` in the
same change. Versioning follows semantic versioning: MAJOR for backward-incompatible principle
removals or redefinitions, MINOR for a new principle or materially expanded guidance, PATCH for
wording/clarification fixes. Every plan produced by `/speckit-plan` MUST pass the Constitution Check
gate against the principles above before Phase 0 research and again after Phase 1 design; deviations
require an entry in that plan's Complexity Tracking table explaining why a simpler, compliant
alternative was rejected.

**Version**: 1.0.0 | **Ratified**: 2026-07-28 | **Last Amended**: 2026-07-28
