"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from shared_logging import LogLevel


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
    # The scheduler's own practitioner REST API, which the console's proxy forwards to.
    # The browser never calls it directly: the session travels in an HttpOnly cookie the
    # page cannot read, and that surface expects it as an explicit header - so something
    # server-side has to carry it. Defaulted so a local checkout runs unconfigured, in
    # the same spirit as SCHEDULING_GRPC_TARGET above.
    SCHEDULING_HTTP_BASE_URL: str = "http://localhost:8001"
    # Guards the two session-deletion routes. Empty by default, and an empty configured
    # value refuses every request rather than admitting every one - a deployment that
    # has not set it has no admin, not an open door. Checked before the constant-time
    # comparison, because an empty secret would otherwise match an empty header.
    ADMIN_SECRET: str = ""
    # A session's FAQ corpus ceiling. Declared once because retrieval carries the
    # session's live revisions as a filter term on every FAQ turn, so corpus size sits
    # on that hot path - and a number repeated across the code is one that gets changed
    # in some of the places.
    FAQ_MAX_ENTRIES_PER_SESSION: int = 200
    # How long a staff message, or the console's assistant switch, silences the
    # assistant in one conversation. Chosen rather than derived: long enough to type a
    # follow-up sentence, short enough that a staff member who wandered off does not
    # strand the patient. Changing it touches no other rule.
    ASSISTANT_PAUSE_SECONDS: int = 120
    # The strong model writes anything a patient reads; the cheap one only routes.
    # Declared here rather than per module so the pairing stays one decision - three
    # copies of a model id is three places a change can be applied to two of.
    GENERATION_MODEL: str = "claude-sonnet-5"
    CLASSIFICATION_MODEL: str = "claude-haiku-4-5-20251001"
    # How many trailing turns of history every model call is given. One number, so the
    # specialists cannot disagree about what "recent" means within a single turn.
    CONTEXT_TURNS: int = 5
    # DEBUG additionally logs what each specialist actually sent the model. Off by
    # default: that is the whole conversation, so it belongs in a dev terminal rather
    # than in a deployment's log stream.
    LOG_LEVEL: LogLevel = LogLevel.INFO


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide `Settings` singleton, building it on first call.

    Deferred to first call (not built at import time) so tests can override env vars
    before anything reads them — see `tests/conftest.py`.
    """
    return Settings()
