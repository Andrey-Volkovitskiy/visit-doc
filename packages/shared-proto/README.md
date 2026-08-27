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

`protoc` generates the `_grpc.py` stub's import based on the `.proto` file's path relative to
`-I`, which doesn't match its actual location inside the `shared_proto` package. After
regenerating, fix the import in `scheduling_pb2_grpc.py` from
`from scheduling.v1 import scheduling_pb2 as ...` to `from . import scheduling_pb2 as ...`.
