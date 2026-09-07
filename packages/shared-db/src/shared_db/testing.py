"""Helpers for pointing a suite at an isolated copy of a service's database.

Every suite in this repo hits real Postgres, so each must run against a `<db>_test`
database rather than the one a locally-running service uses. The rule for deriving that
name lives here, once: two suites deriving it separately is two chances for one of them
to be fixed and the other left pointed at the developer's own data.
"""

import os
import re
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa

TEST_SUFFIX = "_test"
# What a derived database name is allowed to contain, checked before it is interpolated
# into `CREATE DATABASE`. Every name reaching that statement is built by this module out
# of a settings value, so this guards a mistake rather than an attack - but an
# identifier cannot be passed as a bound parameter, and the check costs nothing.
_SAFE_DATABASE_NAME = re.compile(r"\A[A-Za-z0-9_]+\Z")
# Where an automatic namespace comes from: Claude Code exports this per session, so it
# is stable across every run of one session and different between two of them. Named
# here rather than read inline so `scripts/prune-test-stores.sh` and this module agree
# on what an automatic namespace looks like.
_AGENT_SESSION_ENV_VAR = "CLAUDE_CODE_SESSION_ID"
# Kept short so `<db>_test_<namespace>_gw0` stays inside Postgres' 63-byte identifier
# limit, whose longest base here is `visitdoc_scheduler_test`.
_MAX_NAMESPACE_LENGTH = 24
# A `_test` tail and everything a namespace/worker id may have appended after it. Used
# to reduce an already-isolated name back to its base, so deriving twice is harmless.
_ISOLATION_SUFFIX = re.compile(rf"{TEST_SUFFIX}(?:_[a-z0-9]+)*\Z")


def with_test_suffix(value: str) -> str:
    """Append the test suffix to `value`, unless it already carries one.

    Idempotent, so re-deriving from an already-isolated value is harmless - which is
    what makes it safe to call on a `Settings` field an earlier run may have overridden.
    """
    return value if value.endswith(TEST_SUFFIX) else value + TEST_SUFFIX


def _sanitized(value: str) -> str:
    """Reduce `value` to the lowercase alphanumerics a store name may carry.

    Lowercased rather than passed through: Postgres folds an unquoted identifier, so a
    mixed-case name survives only as a quoted one, and every later `psql` that names it
    without quotes reports it missing.
    """
    return re.sub(r"[^a-z0-9]", "", value.lower())[:_MAX_NAMESPACE_LENGTH]


def _run_namespace() -> str:
    """Return the part of the namespace that separates one whole test run from another.

    `VISITDOC_TEST_NAMESPACE` set by hand wins, so a run can always be given stores of
    its own deliberately.

    Failing that, a Claude Code session supplies its own id, and gets its own stores
    without anyone having to remember to ask. That is the case worth automating: a
    developer running a suite in their terminal and an agent running one on their behalf
    are two runs on one machine that know nothing about each other, and every suite here
    clears every table before each test - so without this they delete each other's rows
    mid-test and the failures land wherever the timing put them rather than on anything
    either run is testing. A plain shell has no such id and gets the unsuffixed stores,
    which keeps `visitdoc_chat_test` meaning what it has always meant.
    """
    explicit = _sanitized(os.environ.get("VISITDOC_TEST_NAMESPACE", ""))
    if explicit:
        return explicit
    session = _sanitized(os.environ.get(_AGENT_SESSION_ENV_VAR, ""))[:8]
    return f"cc{session}" if session else ""


def current_test_namespace() -> str:
    """Return the suffix separating this test process's stores from another's.

    Two parts, either of which may be absent: the run's own namespace (see
    `_run_namespace`), then this pytest-xdist worker's id.

    `PYTEST_XDIST_WORKER` (`gw0`, `gw1`, ...) is set by pytest-xdist in each worker.
    Workers are separate processes sharing nothing but the datastores, and every suite
    here clears every table before each test - so on one shared database each worker
    deletes the others' rows mid-test. Advisory locks make it a correctness requirement
    rather than a matter of speed: they are scoped per database, and
    `test_chat_repository.py` asserts on a database-wide count of them, which a sibling
    worker's lock would join.

    The stores this names are created on demand and never reaped, so the set of them
    grows by one per namespace ever used - `make test-db-prune` drops the automatic
    ones.

    Read from the environment rather than through a pytest fixture because the value is
    needed at conftest import time, which is before any fixture can run and before any
    service module has read its settings.
    """
    parts = [_run_namespace(), os.environ.get("PYTEST_XDIST_WORKER", "")]
    return "".join(f"_{part}" for part in parts if part)


def with_namespace_suffix(value: str) -> str:
    """Append `current_test_namespace()` to `value`. Idempotent, like the above."""
    namespace = current_test_namespace()
    if not namespace or value.endswith(namespace):
        return value
    return value + namespace


def base_name(value: str) -> str:
    """Strip any isolation suffix `isolated_name` has already applied to `value`.

    What makes re-deriving safe. A suite overrides `DATABASE_URL` in the environment at
    conftest import time, and under pytest-xdist each worker inherits the environment
    the controller already overrode - so the second derivation runs on the first one's
    output. Appending was idempotent only while `_test` was the last thing in the name;
    once a namespace follows it, `<db>_test_ccabc` no longer ends in `_test` and a
    second pass produced `<db>_test_ccabc_test_ccabc_gw0`. Reducing to the base first
    makes the derivation idempotent whatever has been appended.
    """
    return _ISOLATION_SUFFIX.sub("", value)


def isolated_name(value: str) -> str:
    """Return the isolated store name for `value`: `<base>_test`, plus this process's
    namespace when it has one.

    The one derivation both a database name and a Qdrant collection name go through, so
    a suite cannot end up with its Postgres side split per worker and its Qdrant side
    shared. Idempotent: `isolated_name(isolated_name(x))` is `isolated_name(x)`.
    """
    return base_name(value) + TEST_SUFFIX + current_test_namespace()


def isolated_database_url(url: str) -> str:
    """Return `url` pointed at its isolated sibling, leaving the rest untouched."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=isolated_name(parts.path)))


def ensure_database_exists(url: str) -> None:
    """Create the database `url` names, if it is not there already.

    Raises: ValueError if the derived database name is not a plain identifier.

    The shared `<db>_test` databases are provisioned before any suite runs - by
    `docker-compose.yml`'s init scripts locally, by the service container's
    `POSTGRES_DB` in CI - but a namespaced one cannot be, because its name is not known
    until the process that needs it starts. So each suite asks for its own on the way
    in, against the `postgres` maintenance database that every cluster has.

    Created empty rather than `TEMPLATE`d from the shared test database: a template must
    have no other connection open to it, which several workers starting at once cannot
    promise, and the caller runs `alembic upgrade head` against the result anyway.

    `CREATE DATABASE` cannot run inside a transaction, hence `AUTOCOMMIT`; and it races
    against another process asking for the same name, which is not an error worth
    raising - both wanted the database to exist, and it does.
    """
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    if not _SAFE_DATABASE_NAME.match(name):
        raise ValueError(f"refusing to create a database named {name!r}")
    admin_url = urlunsplit(parts._replace(path="/postgres")).replace(
        "postgresql+asyncpg://", "postgresql+psycopg://"
    )
    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            already_there = connection.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": name},
            ).scalar_one_or_none()
            if already_there is not None:
                return
            try:
                connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
            except sa.exc.ProgrammingError:
                # Another process created it between the read above and this write.
                pass
    finally:
        engine.dispose()
