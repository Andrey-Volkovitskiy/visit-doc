"""Helpers for pointing a suite at an isolated copy of a service's database.

Every suite in this repo hits real Postgres, so each must run against a `<db>_test`
database rather than the one a locally-running service uses. The rule for deriving that
name lives here, once: two suites deriving it separately is two chances for one of them
to be fixed and the other left pointed at the developer's own data.
"""

from urllib.parse import urlsplit, urlunsplit

TEST_SUFFIX = "_test"


def with_test_suffix(value: str) -> str:
    """Append the test suffix to `value`, unless it already carries one.

    Idempotent, so re-deriving from an already-isolated value is harmless - which is
    what makes it safe to call on a `Settings` field an earlier run may have overridden.
    """
    return value if value.endswith(TEST_SUFFIX) else value + TEST_SUFFIX


def isolated_database_url(url: str) -> str:
    """Return `url` pointed at its `<db>_test` sibling, leaving the rest untouched."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=with_test_suffix(parts.path)))
