# shared-proto

The gRPC contract shared between the `chat` and `scheduler` services.

`protos/scheduling/v1/scheduling.proto` is the source of truth. Generated stubs
(`src/shared_proto/scheduling/v1/scheduling_pb2.py`,
`src/shared_proto/scheduling/v1/scheduling_pb2.pyi`,
`src/shared_proto/scheduling/v1/scheduling_pb2_grpc.py`) are checked into git — regenerate them
after editing the `.proto` file:

```bash
uv run --package shared-proto -- python -m grpc_tools.protoc \
  -I packages/shared-proto/protos \
  --python_out=packages/shared-proto/src/shared_proto \
  --pyi_out=packages/shared-proto/src/shared_proto \
  --grpc_python_out=packages/shared-proto/src/shared_proto \
  packages/shared-proto/protos/scheduling/v1/scheduling.proto
```

`--pyi_out` is not optional: `scheduling_pb2.py` builds its message classes dynamically at
import time, so without the generated `.pyi` mypy sees a module with no attributes and every
caller referencing a message type fails with `attr-defined`. The `.pyi` is what makes
`shared_proto` usable from strict-mode code — it is generated, so it's excluded from ruff
alongside the `.py` stubs.

Changing an existing enum value's *number* is not an ordinary edit followed by a regeneration.
The wire carries the number, not the name, so a peer built from the old descriptor reads the new
number as whichever member it had there — silently, since proto3 enums are open. `Weekday` was
renumbered this way in 007 (Monday `0` → `1`, and a `WEEKDAY_UNSPECIFIED = 0` added), inside the
unchanged `scheduling.v1` package: `chat` and `scheduler` must be built and deployed together, or
a new scheduler's Monday is read as an old chat's Tuesday and every practitioner's schedule shifts
by a day. Prefer adding a new field to renumbering an existing one.

Removing an rpc is the same class of change. Patient rename was removed whole — the
`RenamePatient` rpc, the `RenameFailureReason` enum, and the `RenameFailure`,
`RenamePatientRequest` and `RenamePatientResponse` messages — again in place, inside the
unchanged `scheduling.v1` package, so again `chat` and `scheduler` must be built and deployed
together. A chat built before the removal, calling a scheduler built after it, is answered
`UNIMPLEMENTED` ("Method not found!"); `_call()` in
`services/chat/src/chat/clients/scheduling.py` retries only `UNAVAILABLE` and
`DEADLINE_EXCEEDED`, so that call is not retried — it raises `SchedulingUnavailableError`, a
`SchedulingError`, with `outcome_unknown` true after a single attempt.

proto3 offers no way to reserve what that freed. `reserved` exists only inside a message body
and an enum body — protoc rejects it at file scope and inside a `service` — and every body
those names lived in is gone; nothing else was freed, since no surviving message or enum lost
a field, so there is no field number or enum number to reserve either. The freed names are
held by a comment in the `.proto` header instead — above `syntax`, not next to
`service Scheduling`, because a comment adjacent to the service (blank line or not) becomes the
generated `SchedulingStub`/`SchedulingServicer` docstring. Do not reuse the freed names: gRPC
dispatches on the literal path `/scheduling.v1.Scheduling/RenamePatient`, so a future rpc taking
that name would be handed an old chat's `RenamePatientRequest` bytes to parse as its own request.

`protoc` generates the `_grpc.py` stub's import based on the `.proto` file's path relative to
`-I`, which doesn't match its actual location inside the `shared_proto` package. After
regenerating, fix the import in `scheduling_pb2_grpc.py` from
`from scheduling.v1 import scheduling_pb2 as ...` to `from . import scheduling_pb2 as ...`.
