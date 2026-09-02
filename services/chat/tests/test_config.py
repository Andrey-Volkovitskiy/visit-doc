"""This service's settings: the fields 007 adds, and why each default is what it is.

Defaults are behaviour here, not convenience. `ADMIN_SECRET` defaulting to empty is
what makes an unconfigured deployment refuse every deletion request rather than admit
every one, and the two numeric caps exist so a value that several call sites depend on
is declared once instead of repeated.
"""

import pytest
from chat.core.config import Settings

# Every field 007 adds, so an unconfigured build can be constructed deliberately.
_NEW_FIELDS = (
    "ADMIN_SECRET",
    "FAQ_MAX_ENTRIES_PER_SESSION",
    "ASSISTANT_PAUSE_SECONDS",
    "SCHEDULING_HTTP_BASE_URL",
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
