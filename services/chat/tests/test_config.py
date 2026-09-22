"""This service's settings: the fields 007 adds, and why each default is what it is.

Defaults are behaviour here, not convenience. `ADMIN_SECRET` defaulting to empty is
what makes an unconfigured deployment refuse every deletion request rather than admit
every one, and the two numeric caps exist so a value that several call sites depend on
is declared once instead of repeated.
"""

import pytest
from chat.core.config import Settings
from pydantic import ValidationError
from shared_logging import LogFormat

# Every field 007 adds, so an unconfigured build can be constructed deliberately.
_NEW_FIELDS = (
    "ADMIN_SECRET",
    "FAQ_MAX_ENTRIES_PER_SESSION",
    "ASSISTANT_PAUSE_SECONDS",
    "SCHEDULING_HTTP_BASE_URL",
    "RETRIEVAL_POOL_SIZE",
    "SIMILARITY_FLOOR",
    "SIMILARITY_CAP",
    "RERANK_FLOOR",
    "RERANK_CAP",
    "RERANK_TIMEOUT_SECONDS",
    "RERANK_MODEL",
    "LOG_FORMAT",
)


def _settings(**overrides: object) -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost/db",
        QDRANT_URL="http://localhost:6333",
        ANTHROPIC_API_KEY="sk-ant-test-key",
        VOYAGE_API_KEY="voyage-test-key",
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings as a deployment that configured none of 007's fields would see them.

    Both sources have to be shut off, not just one: the repo's own `.env` sets some of
    these for local development, and an exported variable would override that in turn.
    A default asserted against either source still present is not a default - it is
    whichever value this machine happens to carry.
    """
    for field in _NEW_FIELDS:
        monkeypatch.delenv(field, raising=False)
    return _settings(_env_file=None)


def test_admin_secret_defaults_to_empty(unconfigured: Settings) -> None:
    # Fail closed. An unset secret means there is no admin, not that anyone may act as
    # one - and the empty default is what the route's own guard tests against before it
    # ever reaches a comparison.
    assert unconfigured.ADMIN_SECRET == ""


def test_faq_max_entries_per_session_defaults_to_the_documented_cap(
    unconfigured: Settings,
) -> None:
    assert unconfigured.FAQ_MAX_ENTRIES_PER_SESSION == 200


def test_assistant_pause_seconds_defaults_to_two_minutes(
    unconfigured: Settings,
) -> None:
    assert unconfigured.ASSISTANT_PAUSE_SECONDS == 120


def test_scheduling_http_base_url_defaults_to_the_local_scheduler(
    unconfigured: Settings,
) -> None:
    # The console's practitioner proxy forwards here. It has a working default for the
    # same reason SCHEDULING_GRPC_TARGET does: a local checkout should run unconfigured.
    assert unconfigured.SCHEDULING_HTTP_BASE_URL == "http://localhost:8001"


def test_every_new_setting_is_overridable_from_the_environment() -> None:
    # Pinned because a field typed as a plain literal rather than read through
    # BaseSettings would silently ignore its env var and keep the default forever.
    overridden = _settings(
        ADMIN_SECRET="from-env",
        FAQ_MAX_ENTRIES_PER_SESSION=2,
        ASSISTANT_PAUSE_SECONDS=5,
        SCHEDULING_HTTP_BASE_URL="http://scheduler:9001",
    )

    assert overridden.ADMIN_SECRET == "from-env"
    assert overridden.FAQ_MAX_ENTRIES_PER_SESSION == 2
    assert overridden.ASSISTANT_PAUSE_SECONDS == 5
    assert overridden.SCHEDULING_HTTP_BASE_URL == "http://scheduler:9001"


def test_retrieval_pool_is_wider_than_the_similarity_cap(
    unconfigured: Settings,
) -> None:
    # The whole point of the pool: a cap that discards only candidates it never
    # fetched cannot be calibrated, so the pool has to exceed it.
    assert unconfigured.RETRIEVAL_POOL_SIZE == 25
    assert unconfigured.SIMILARITY_CAP == 5
    assert unconfigured.RETRIEVAL_POOL_SIZE > unconfigured.SIMILARITY_CAP


def test_pipeline_gate_defaults(unconfigured: Settings) -> None:
    assert unconfigured.SIMILARITY_FLOOR == 0.25
    assert unconfigured.RERANK_FLOOR == 0.58
    assert unconfigured.RERANK_CAP == 3


def test_rerank_gate_is_narrower_than_the_similarity_gate(
    unconfigured: Settings,
) -> None:
    # Retrieval widens for recall, reranking narrows for precision. A rerank cap at or
    # above the similarity cap would make the second gate incapable of pruning.
    assert unconfigured.RERANK_CAP < unconfigured.SIMILARITY_CAP


def test_reranking_call_defaults(unconfigured: Settings) -> None:
    assert unconfigured.RERANK_TIMEOUT_SECONDS == 5.0
    assert unconfigured.RERANK_MODEL == "rerank-3"


def test_log_format_defaults_to_the_console(unconfigured: Settings) -> None:
    # `json` is for the golden harness, which reads the log as data; a person running
    # the service in a terminal should not have to ask for the readable one.
    assert unconfigured.LOG_FORMAT is LogFormat.CONSOLE


def test_log_format_accepts_json_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")

    assert _settings(_env_file=None).LOG_FORMAT is LogFormat.JSON


def test_every_pipeline_setting_is_overridable_from_the_environment() -> None:
    overridden = _settings(
        RETRIEVAL_POOL_SIZE=40,
        SIMILARITY_FLOOR=0.25,
        SIMILARITY_CAP=8,
        RERANK_FLOOR=0.55,
        RERANK_CAP=2,
        RERANK_TIMEOUT_SECONDS=1.5,
        RERANK_MODEL="rerank-other",
    )

    assert overridden.RETRIEVAL_POOL_SIZE == 40
    assert overridden.SIMILARITY_FLOOR == 0.25
    assert overridden.SIMILARITY_CAP == 8
    assert overridden.RERANK_FLOOR == 0.55
    assert overridden.RERANK_CAP == 2
    assert overridden.RERANK_TIMEOUT_SECONDS == 1.5
    assert overridden.RERANK_MODEL == "rerank-other"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("RETRIEVAL_POOL_SIZE", 0),
        ("SIMILARITY_CAP", 0),
        ("RERANK_CAP", -1),
        ("RERANK_FLOOR", 1.5),
        ("SIMILARITY_FLOOR", -2.0),
        ("RERANK_TIMEOUT_SECONDS", 0.0),
    ],
)
def test_a_gate_number_outside_its_range_fails_at_startup(
    field: str, value: object
) -> None:
    # None of these fails loudly on its own: a cap of zero empties a gate, a floor above
    # every score abstains on every turn, and a deadline of zero abandons the call
    # before it is made. Each reads in the log as a corpus problem, so the range is
    # enforced where the value enters rather than inferred from a week of abstentions.
    with pytest.raises(ValidationError):
        _settings(**{field: value})


def test_the_test_environment_runs_untraced() -> None:
    # Pinned because the repo's `.env` holds a developer's real Langfuse keys: every
    # test that builds the app would otherwise export its turns to their project. A test
    # about tracing builds its own tracer over an in-memory exporter instead.
    assert Settings().tracing_enabled is False


_LANGFUSE_FIELDS = (
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_ENVIRONMENT",
)


@pytest.fixture
def untraced(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings as a deployment that configured no Langfuse field would see them.

    Both sources shut off, for the reason `unconfigured` gives: the repo's `.env` holds
    real keys, and the conftest blanks two of them in the environment.
    """
    for field in _LANGFUSE_FIELDS:
        monkeypatch.delenv(field, raising=False)
    return _settings(_env_file=None)


def test_tracing_is_off_when_no_langfuse_key_is_configured(untraced: Settings) -> None:
    assert untraced.LANGFUSE_PUBLIC_KEY == ""
    assert untraced.LANGFUSE_SECRET_KEY == ""
    assert untraced.tracing_enabled is False


def test_tracing_is_on_when_both_keys_are_set() -> None:
    settings = _settings(LANGFUSE_PUBLIC_KEY="pk-lf-1", LANGFUSE_SECRET_KEY="sk-lf-1")

    assert settings.tracing_enabled is True


@pytest.mark.parametrize(
    ("public_key", "secret_key"),
    [
        ("pk-lf-1", ""),
        ("", "sk-lf-1"),
        ("  ", "sk-lf-1"),
        ("pk-lf-1", "\t "),
        ("  ", "  "),
    ],
)
def test_a_blank_key_means_tracing_is_off(public_key: str, secret_key: str) -> None:
    # A blank key is not a missing one to the SDK: handed "", it would build a live
    # exporter that fails to authenticate on every batch. So blank means off, decided
    # here and never left to the SDK.
    settings = _settings(LANGFUSE_PUBLIC_KEY=public_key, LANGFUSE_SECRET_KEY=secret_key)

    assert settings.tracing_enabled is False


def test_langfuse_destination_and_environment_defaults(untraced: Settings) -> None:
    assert untraced.LANGFUSE_BASE_URL == "https://cloud.langfuse.com"
    assert untraced.LANGFUSE_ENVIRONMENT == "development"


def test_langfuse_environment_is_overridable_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGFUSE_ENVIRONMENT", "staging-2")

    assert _settings(_env_file=None).LANGFUSE_ENVIRONMENT == "staging-2"


@pytest.mark.parametrize(
    "environment", ["Development", "PROD", "langfuse-dev", "langfuse", "has space", ""]
)
def test_a_langfuse_environment_langfuse_would_drop_fails_at_startup(
    environment: str,
) -> None:
    # Langfuse drops an environment it does not accept with a warning on its own logger,
    # and the trace then files under the default - a misconfiguration that reads as
    # traces going missing rather than as a setting being wrong.
    with pytest.raises(ValidationError):
        _settings(LANGFUSE_ENVIRONMENT=environment)
