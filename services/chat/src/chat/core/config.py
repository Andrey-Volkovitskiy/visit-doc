"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the chat service."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    QDRANT_URL: str
    QDRANT_COLLECTION_NAME: str = "faq_chunks"
    ANTHROPIC_API_KEY: str
    VOYAGE_API_KEY: str
    SCHEDULING_GRPC_TARGET: str = "localhost:50051"
    # Per-call deadline and attempt budget for the scheduling service. Two attempts at
    # two seconds is a ~4s worst case, inside the 5-second promise a patient gets when
    # scheduling is unreachable.
    SCHEDULING_TIMEOUT_SECONDS: float = 2.0
    SCHEDULING_MAX_ATTEMPTS: int = 2
    # Paused between attempts so the budget spans real time rather than being spent in
    # microseconds against a socket that is still refusing - a restarting scheduler is
    # exactly what the retry exists for. Kept well inside the ~5s worst case above.
    SCHEDULING_RETRY_BACKOFF_SECONDS: float = 0.25
    # The strong model writes anything a patient reads; the cheap one only routes.
    # Declared here rather than per module so the pairing stays one decision - three
    # copies of a model id is three places a change can be applied to two of.
    GENERATION_MODEL: str = "claude-sonnet-5"
    CLASSIFICATION_MODEL: str = "claude-haiku-4-5-20251001"
    # How many trailing turns of history every model call is given. One number, so the
    # specialists cannot disagree about what "recent" means within a single turn.
    CONTEXT_TURNS: int = 5


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide `Settings` singleton, building it on first call.

    Deferred to first call (not built at import time) so tests can override env vars
    before anything reads them — see `tests/conftest.py`.
    """
    return Settings()
