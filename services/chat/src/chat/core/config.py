"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared_logging import LogFormat, LogLevel


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
    # How many candidates the vector search fetches. Deliberately wider than the cap
    # below: the cap can only be tuned against candidates something recorded, and one
    # that discards only what it never fetched cannot be argued up or down. The extra
    # candidates are logged and otherwise ignored - they reach no gate, no prompt and
    # no reranker, so widening this changes what a turn records and never what it says.
    #
    # Bounded, as are the four gate numbers below it. An out-of-range value here does
    # not fail loudly - a cap of zero or less silently empties a gate, and a floor above
    # every score silently abstains on every turn - so the range is enforced at startup
    # rather than discovered from a week of abstentions.
    RETRIEVAL_POOL_SIZE: int = Field(default=25, gt=0)
    # Per-chunk, not per-turn: a chunk below this is not admitted because a better one
    # cleared it. Lower than the 0.5 whole-turn gate it replaces, and stricter in
    # effect, because a chunk admitted here is still only a candidate - the reranker
    # decides whether it survives. 0.25 rather than 0.3 since G081: "what cards do you
    # take?" ranks the payment entry first at 0.257, and a short, colloquial question
    # scoring low against a long entry in the clinic's wording is what this floor
    # should let through to the reranker, not decide on its own.
    SIMILARITY_FLOOR: float = Field(default=0.25, ge=-1.0, le=1.0)
    SIMILARITY_CAP: int = Field(default=5, gt=0)
    # The midpoint of the band that scores best on the calibration set. Every floor
    # from 0.520 to 0.636 scores identically there (10/10 answerable, 9/10 not), so
    # there is no optimum to find - only a widest gap from the nearest mistake on
    # either side, which is what the midpoint is. Below it sits an unanswerable
    # question at 0.5195, above it the weakest correct answer at 0.6367.
    #
    # Those two numbers overlap in the other direction as well: the worst unanswerable
    # scores 0.6953, *above* the weakest correct answer, so no floor separates the two
    # classes and every value here is a choice of which mistake to make.
    # See specs/008-*/calibration/questions.md.
    RERANK_FLOOR: float = Field(default=0.58, ge=0.0, le=1.0)
    RERANK_CAP: int = Field(default=3, gt=0)
    # The reranker sits between retrieval and the first generated token, so every
    # second it hangs is silence the patient watches. Exceeding this is handled as a
    # failure: the turn answers from the retrieval survivors instead. Tolerant rather
    # than tight because falling back costs the turn its precision stage, so a call
    # that would have returned at 3s is worth waiting for.
    RERANK_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0.0)
    RERANK_MODEL: str = "rerank-3"
    # DEBUG additionally logs what each specialist actually sent the model. Off by
    # default: that is the whole conversation, so it belongs in a dev terminal rather
    # than in a deployment's log stream.
    LOG_LEVEL: LogLevel = LogLevel.INFO
    # `json` renders one object per line for the golden harness, which reads a turn's
    # events back out of the log as data. The console format is for people, and carries
    # no guarantee a program could parse it, so it stays the default.
    LOG_FORMAT: LogFormat = LogFormat.CONSOLE
    # Langfuse tracing: one trace per turn, exported to the project these keys belong
    # to. Blank by default, and blank means off - both keys have to be set for anything
    # to leave the process. Read here and handed to the SDK explicitly, never left to
    # its own environment lookup, which would treat an empty key as a real one and
    # build an exporter that fails to authenticate on every batch.
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = "https://cloud.langfuse.com"
    # The environment a non-eval turn's trace is filed under. Langfuse drops a value it
    # does not accept with only a warning, and the trace then files under its default -
    # so the same rule it applies is enforced here, where a wrong value fails startup.
    LANGFUSE_ENVIRONMENT: str = Field(
        default="development", max_length=40, pattern=r"^[a-z0-9_-]+$"
    )

    @field_validator("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    @classmethod
    def _key_without_surrounding_whitespace(cls, value: str) -> str:
        """Strip a Langfuse key, so a blank one reads as unset.

        Stripped once, here, so the key the exporter authenticates with and the value
        redaction matches are the same string: a key redacted with a stray space or
        carriage return attached is not a substring of any text carrying the key.
        """
        return value.strip()

    @field_validator("LANGFUSE_ENVIRONMENT")
    @classmethod
    def _environment_is_not_reserved(cls, value: str) -> str:
        """Refuse an environment in the prefix Langfuse reserves for its own.

        Raises: ValueError when `value` starts with `langfuse`.
        """
        if value.startswith("langfuse"):
            raise ValueError("must not start with 'langfuse', which Langfuse reserves")
        return value

    @property
    def tracing_enabled(self) -> bool:
        """Return whether this process exports traces: both Langfuse keys are set."""
        return bool(self.LANGFUSE_PUBLIC_KEY and self.LANGFUSE_SECRET_KEY)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide `Settings` singleton, building it on first call.

    Deferred to first call (not built at import time) so tests can override env vars
    before anything reads them — see `tests/conftest.py`.
    """
    return Settings()
