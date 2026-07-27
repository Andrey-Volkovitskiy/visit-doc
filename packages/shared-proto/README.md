# shared-proto

The gRPC contract shared between the `chat` and `scheduler` services.

`protos/scheduling/v1/scheduling.proto` is the source of truth. Generated stubs
(`src/shared_proto/scheduling/v1/scheduling_pb2.py`,
`src/shared_proto/scheduling/v1/scheduling_pb2_grpc.py`) are checked into git — regenerate them
after editing the `.proto` file:

```bash
uv run --package shared-proto -- python -m grpc_tools.protoc \
  -I packages/shared-proto/protos \
  --python_out=packages/shared-proto/src/shared_proto \
  --grpc_python_out=packages/shared-proto/src/shared_proto \
  packages/shared-proto/protos/scheduling/v1/scheduling.proto
```

`protoc` generates the `_grpc.py` stub's import based on the `.proto` file's path relative to
`-I`, which doesn't match its actual location inside the `shared_proto` package. After
regenerating, fix the import in `scheduling_pb2_grpc.py` from
`from scheduling.v1 import scheduling_pb2 as ...` to `from . import scheduling_pb2 as ...`.
