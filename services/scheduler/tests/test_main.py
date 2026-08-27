import grpc
from fastapi.testclient import TestClient
from scheduler.core.config import Settings
from scheduler.grpc.server import create_server
from scheduler.main import app


def test_health_reports_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_the_grpc_server_starts_and_stops_with_the_app() -> None:
    # Port 0 lets the OS pick a free one, so this never collides with a locally
    # running scheduler.
    settings = Settings(SCHEDULER_GRPC_PORT=0)
    server = create_server(settings)
    await server.start()
    try:
        assert isinstance(server, grpc.aio.Server)
    finally:
        await server.stop(0)
