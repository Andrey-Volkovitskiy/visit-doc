"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from shared_logging import LogLevel


class Settings(BaseSettings):
    """Runtime configuration for the scheduling service."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SCHEDULER_DATABASE_URL: str
    SCHEDULER_GRPC_PORT: int = 50051
    SCHEDULER_HTTP_PORT: int = 8001
    # How far ahead of the caller's `local_now` an appointment may start, boundary
    # inclusive. Read by the availability walk and the booking validator alike.
    BOOKING_HORIZON_DAYS: int = 90
    # The two server-side caps on one availability answer, so a vague request
    # ("sometime next month") cannot flood a model's context. Declared beside their
    # sibling horizon rather than as module constants, since all three shape the same
    # answer and are tuned together.
    AVAILABILITY_MAX_WINDOW_DAYS: int = 14
    AVAILABILITY_MAX_SLOTS: int = 50
    LOG_LEVEL: LogLevel = LogLevel.INFO


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide `Settings` singleton, building it on first call.

    Deferred to first call (not built at import time) so tests can override env vars
    before anything reads them — see `tests/conftest.py`.
    """
    return Settings()
