# tests/integration

Reserved for cross-service integration tests (`chat` <-> `scheduler` over gRPC, or a service
against a real Postgres/Qdrant instance). Not yet populated — no integration surface exists yet to
test (see `docs/ROADMAP.md`). Run via `make test-integration` once tests exist; until then this
target exits with pytest's "no tests ran" status, which is expected.

See `docs/testing-strategy.md` for the full testing convention.
