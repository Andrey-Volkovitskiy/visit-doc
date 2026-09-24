# tests/integration

Cross-service integration tests: chat's real clients against the scheduler's real servers, backed
by the real (isolated) `visitdoc_scheduler_test` and `visitdoc_chat_test` databases and the chat
test Qdrant collection. `conftest.py` provides the two scheduler surfaces on loopback ports - the
gRPC servicer (`scheduling_channel`) and the practitioner REST API (`scheduler_http`) - plus this
tier's own fakes for the paid APIs, which it blocks outright. Run via `make test-integration`; CI
runs it as its own `integration` job.

See `docs/testing-strategy.md` for the full testing convention.
