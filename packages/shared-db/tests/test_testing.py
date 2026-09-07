"""How a suite's isolated store names are derived.

The rule these cover is load-bearing in a way that is easy to miss: a suite overrides
`DATABASE_URL` in the environment at conftest import time, and under pytest-xdist each
worker inherits an environment the controller has already overridden - so every one of
these functions runs a second time on its own output. A derivation that is not
idempotent does not fail loudly there; it quietly points a worker at
`visitdoc_chat_test_ccabc_test_ccabc_gw0`, a database nothing provisioned.
"""

import pytest
from shared_db.testing import (
    base_name,
    current_test_namespace,
    ensure_database_exists,
    isolated_database_url,
    isolated_name,
)

_AGENT_ENV = "CLAUDE_CODE_SESSION_ID"
_EXPLICIT_ENV = "VISITDOC_TEST_NAMESPACE"
_WORKER_ENV = "PYTEST_XDIST_WORKER"


@pytest.fixture(autouse=True)
def _no_ambient_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test from a plain shell's environment.

    This suite runs inside the very things it derives names from - a Claude Code
    session, an xdist worker - so without this every expectation below would depend on
    who happened to run it.
    """
    for variable in (_AGENT_ENV, _EXPLICIT_ENV, _WORKER_ENV):
        monkeypatch.delenv(variable, raising=False)


def test_a_plain_shell_gets_the_unsuffixed_test_stores() -> None:
    assert current_test_namespace() == ""
    assert isolated_name("visitdoc_chat") == "visitdoc_chat_test"


def test_an_agent_session_gets_stores_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_AGENT_ENV, "7cdb7d2a-2a4d-4351-aba5-8c480c227a9c")
    assert isolated_name("visitdoc_chat") == "visitdoc_chat_test_cc7cdb7d2a"


def test_two_agent_sessions_do_not_share_stores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_AGENT_ENV, "11111111-aaaa-bbbb-cccc-dddddddddddd")
    first = isolated_name("visitdoc_chat")
    monkeypatch.setenv(_AGENT_ENV, "22222222-aaaa-bbbb-cccc-dddddddddddd")
    assert isolated_name("visitdoc_chat") != first


def test_an_explicit_namespace_beats_the_session_it_runs_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_AGENT_ENV, "7cdb7d2a-2a4d-4351-aba5-8c480c227a9c")
    monkeypatch.setenv(_EXPLICIT_ENV, "mine")
    assert isolated_name("visitdoc_chat") == "visitdoc_chat_test_mine"


def test_a_namespace_is_reduced_to_what_a_store_name_may_carry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Lowercased in particular: Postgres folds an unquoted identifier, so a mixed-case
    # database survives only as a quoted one and later reads report it missing.
    monkeypatch.setenv(_EXPLICIT_ENV, "My Session!")
    assert isolated_name("visitdoc_chat") == "visitdoc_chat_test_mysession"


def test_a_worker_gets_its_own_stores_within_a_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_AGENT_ENV, "7cdb7d2a-2a4d-4351-aba5-8c480c227a9c")
    monkeypatch.setenv(_WORKER_ENV, "gw1")
    assert isolated_name("visitdoc_chat") == "visitdoc_chat_test_cc7cdb7d2a_gw1"


@pytest.mark.parametrize(
    "namespace",
    [
        pytest.param({}, id="plain shell"),
        pytest.param({_AGENT_ENV: "7cdb7d2a-2a4d"}, id="agent session"),
        pytest.param({_EXPLICIT_ENV: "mine", _WORKER_ENV: "gw3"}, id="explicit worker"),
    ],
)
def test_deriving_a_name_twice_is_deriving_it_once(
    namespace: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The invariant xdist's inherited environment depends on."""
    for variable, value in namespace.items():
        monkeypatch.setenv(variable, value)

    once = isolated_name("visitdoc_chat")
    assert isolated_name(once) == once
    assert isolated_name(isolated_name(once)) == once


def test_deriving_a_url_twice_is_deriving_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_AGENT_ENV, "7cdb7d2a-2a4d")
    monkeypatch.setenv(_WORKER_ENV, "gw0")
    url = "postgresql+asyncpg://u:p@localhost:5432/visitdoc_chat"

    once = isolated_database_url(url)
    assert once.endswith("/visitdoc_chat_test_cc7cdb7d2a_gw0")
    assert isolated_database_url(once) == once


def test_a_name_carrying_no_suffix_is_left_alone() -> None:
    assert base_name("visitdoc_chat") == "visitdoc_chat"


def test_a_database_name_that_is_not_an_identifier_is_refused() -> None:
    """Rejected rather than interpolated: `CREATE DATABASE` cannot bind a parameter."""
    with pytest.raises(ValueError, match="refusing to create"):
        ensure_database_exists("postgresql+asyncpg://u:p@localhost:5432/bad-name;DROP")
