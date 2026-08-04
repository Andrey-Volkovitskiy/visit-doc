from chat.core.config import Settings
from chat.main import app
from chat.repositories.qdrant_repository import COLLECTION_NAME
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient


def test_app_is_a_fastapi_instance() -> None:
    assert isinstance(app, FastAPI)


def test_lifespan_ensures_qdrant_collection_exists() -> None:
    with TestClient(app):
        pass  # entering/exiting the context runs the lifespan startup/shutdown hooks

    client = QdrantClient(url=Settings().QDRANT_URL)
    assert client.collection_exists(COLLECTION_NAME)
    client.close()
