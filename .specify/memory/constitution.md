<!--
Sync Impact Report
- Version change: 1.0.0 → 2.0.0
- Rationale: MAJOR bump. Prior principles III, VI, and VII, plus the "Repository & Workspace
  Structure" and "Development Workflow & Quality Gates" sections, were rewritten to remove
  implementation-level detail (specific hook names, exact tool config keys, literal gRPC method
  and MCP tool names, uv/venv mechanics) that belongs in `.claude/CLAUDE.md` / plan.md, not a
  constitution — a backward-incompatible redefinition of what those sections mandate. Principle
  VIII (Test-Driven Development) is new and non-negotiable, per explicit user direction.
- Modified principles:
  - III. "One Deliberate Service Boundary" → "Deliberate, Minimal Service Boundaries" (generalized:
    dropped literal gRPC/constraint specifics, kept the underlying rule)
  - VI. "Documented Technology Tradeoffs" → "Documentation as a First-Class Deliverable" (broadened
    per user direction to cover architecture and every subsystem, not just tech-choice tradeoffs)
  - VII. "Clean Architecture & SOLID" → "Clean Architecture, SOLID & Design Patterns" (broadened
    per user direction to explicitly include design patterns and industry best practice)
- Added principles: VIII. Test-Driven Development (NON-NEGOTIABLE)
- Added sections: none new (prior "Repository & Workspace Structure" and "Development Workflow &
  Quality Gates" sections were merged/generalized into a single "Technology Foundations" section)
- Removed sections: "Repository & Workspace Structure", "Development Workflow & Quality Gates"
  (replaced by "Technology Foundations", see above)
- Templates requiring updates:
  - ✅ .specify/templates/tasks-template.md — updated: removed "Tests are OPTIONAL" framing and the
    "(OPTIONAL - only if tests requested)" story-section headers, which contradicted the new
    non-negotiable TDD principle.
  - ✅ .specify/templates/plan-template.md — generic "Constitution Check" gate already defers to
    this file; no hardcoded contradictions found, no edit needed.
  - ✅ .specify/templates/spec-template.md — generic, no technology-specific assumptions that
    conflict with these principles; no edit needed.
  - ✅ .claude/skills/speckit-*/SKILL.md — no CLAUDE-only or other agent-specific hardcoded
    references found.
  - ✅ README.md, .claude/CLAUDE.md — remain the detailed reference this file summarizes; the
    Commands/testing-strategy detail they carry is exactly the kind of implementation detail this
    revision moved *out* of the constitution, so no change needed there.
- Follow-up TODOs: none.
-->

# VisitDoc Constitution

## Core Principles

### I. Phase-Gated Scope Discipline (NON-NEGOTIABLE)
`docs/ROADMAP.md` is the authoritative, binding build plan, not background reading. Work MUST
proceed in phase order — walking skeleton, then the real agent, then evaluation/observability —
before any optional platform/infrastructure layer is introduced, and only as deliberate,
individually-justified evolution. Do not add services, infrastructure, or platform layers beyond
what the current phase calls for, even if a later target architecture describes them.
**Rationale**: This is a portfolio project with fixed effort; scope creep into infrastructure
directly trades away time from the applied-AI work the project exists to demonstrate.

### II. AI Core Is the Centerpiece
The agent graph, retrieval-augmented generation, tool use, and the evaluation/observability harness
receive the bulk of engineering effort. Platform and infrastructure work is explicitly secondary and
optional. When effort must be traded off, the AI core wins.
**Rationale**: The project targets an AI developer role — the applied-AI work is what the project is
being judged on.

### III. Deliberate, Minimal Service Boundaries
Every service split MUST be a deliberate, justified seam — its own datastore and its own API
contract — not an incidental one. Data-integrity invariants that a datastore can enforce (e.g.
preventing conflicting bookings) MUST be enforced there, not solely in application code. Failure
handling across a service boundary (timeouts, retries, degraded behavior when a dependency is
unreachable) is part of the design for that boundary, not an afterthought added later.
**Rationale**: A small number of real, well-justified boundaries demonstrates distributed-systems
judgment without paying the operational cost of splitting everything up front.

### IV. Structured Outputs & Decoupled Tool Interfaces
Any step that routes or classifies (e.g. intent detection) MUST use structured output rather than
free-text parsing, and MUST use the cheapest model capable of the task — reserving stronger models
for generation. Every capability the agent can invoke MUST be exposed behind a tool-call interface,
so agent reasoning stays decoupled from how each capability is implemented.
**Rationale**: Structured outputs are reliable and cheap to route on; a decoupled tool boundary lets
the agent's reasoning be tested and evolved independently of backend implementation details.

### V. Grounded Retrieval with Mandatory Abstention
Any answer derived from retrieval MUST be traceable to its source and pass an explicit groundedness
check before it is returned to the user. When retrieval is weak or inconclusive, the system MUST
abstain and escalate to a human rather than confabulate an answer.
**Rationale**: A medical-clinic assistant that guesses at policy or clinical logistics is worse than
one that admits uncertainty — abstention is a correctness requirement, not a nicety.

### VI. Documentation as a First-Class Deliverable
The system's overall architecture and every subsystem within it MUST be documented well enough for a
reader to understand what it does and why without reading its source. Every significant technology
choice MUST be recorded with its tradeoff. Documentation is updated in the same change that makes it
stale — it is a deliverable of the work, not follow-up work.
**Rationale**: For a portfolio project, its documentation is itself an artifact under review —
undocumented architecture and undocumented choices read as accidental rather than intentional.

### VII. Clean Architecture, SOLID & Design Patterns
Code MUST follow SOLID, established clean-architecture separation of concerns, appropriate design
patterns, and general industry best practice. Complexity introduced beyond what a requirement
actually needs MUST be justified, not assumed.
**Rationale**: Clean boundaries keep subsystems independently testable and keep the codebase legible
to a reviewer evaluating engineering judgment, not just AI output quality.

### VIII. Test-Driven Development (NON-NEGOTIABLE)
Work on any feature or contract MUST follow this order: define the contract, derive test cases from
it, write the tests, confirm they fail, then implement, then run the tests to confirm they pass.
Implementation MUST NOT be written before its corresponding tests exist and have been observed to
fail. No step in this order may be skipped or reordered.
**Rationale**: TDD in this order keeps the contract as the source of truth, catches
under-specification before implementation, and gives every feature an executable definition of done.

## Technology Foundations

The stack is fixed at this phase: Python/FastAPI/Pydantic and PostgreSQL on the backend, Qdrant for
vector retrieval, LangGraph for agent orchestration, Langfuse for tracing/eval, and a React/Vite SPA
for the frontend. The repository is organized as a monorepo with clear module/service boundaries;
code shared across services is factored into a common location rather than duplicated. Automated
linting, type-checking, and the test suite MUST pass before any change is merged. Concrete tooling,
commands, and conventions that implement these constraints live in `.claude/CLAUDE.md` and the
per-service style guides it references, and MUST stay consistent with the principles above rather
than reintroducing conflicting rules.

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

**Version**: 2.0.0 | **Ratified**: 2026-07-28 | **Last Amended**: 2026-07-29
