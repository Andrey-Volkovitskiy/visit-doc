"""Deterministic name allocation: walk the pool in order, then suffix and walk again.

Fully deterministic by requirement - the same creation sequence in a fresh session must
produce the same names - which rules out picking a random unused name, and rules out any
count-based shortcut too, since a session that deleted someone would then collide with a
name still held.
"""

from enum import StrEnum

from scheduler.core.logging import get_logger

# How many times a pool-name collision is retried before giving up. A retry only
# happens when a concurrent creation took the chosen name between this caller reading
# the taken set and inserting, so more than a couple of rounds would mean something
# other than ordinary contention.
MAX_NAME_ATTEMPTS = 5


class NamedEntity(StrEnum):
    """Which pool a name is being drawn for."""

    PATIENT = "patient"
    PRACTITIONER = "practitioner"


def allocate_name(
    pool: tuple[str, ...], taken: set[str], *, entity: NamedEntity
) -> str:
    """Return the first pool name not already used in this session.

    Once every entry is taken, the walk repeats with `" 2"` appended, then `" 3"`, and
    so on - so the pool never runs out, and the hundred-and-first name is the first
    name plus a suffix rather than an error.

    Raises: ValueError if `pool` is empty, which would make the walk unbounded.
    """
    if not pool:
        raise ValueError("cannot allocate a name from an empty pool")

    pass_number = 1
    while True:
        suffix = "" if pass_number == 1 else f" {pass_number}"
        for name in pool:
            candidate = f"{name}{suffix}"
            if candidate not in taken:
                get_logger().info(
                    "name.allocated",
                    entity=entity,
                    full_name=candidate,
                    pass_number=pass_number,
                )
                return candidate
        pass_number += 1
