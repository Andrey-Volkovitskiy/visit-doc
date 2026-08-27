"""Tests for deterministic name allocation and the pools it walks."""

import pytest
from scheduler.domain.name_pools import PHYSICIAN_POOL, WRITER_POOL
from scheduler.domain.naming import NamedEntity, allocate_name
from structlog.testing import capture_logs


def test_the_writer_pool_holds_a_hundred_distinct_names() -> None:
    assert len(WRITER_POOL) == 100
    assert len(set(WRITER_POOL)) == 100


def test_the_physician_pool_holds_twenty_distinct_names() -> None:
    assert len(PHYSICIAN_POOL) == 20
    assert len(set(PHYSICIAN_POOL)) == 20


def test_names_are_drawn_in_strict_pool_order() -> None:
    taken: set[str] = set()
    drawn = []
    for _ in range(5):
        name = allocate_name(WRITER_POOL, taken, entity=NamedEntity.PATIENT)
        taken.add(name)
        drawn.append(name)

    assert drawn == list(WRITER_POOL[:5])


def test_the_same_creation_sequence_yields_the_identical_sequence() -> None:
    def draw(count: int) -> list[str]:
        taken: set[str] = set()
        names = []
        for _ in range(count):
            name = allocate_name(WRITER_POOL, taken, entity=NamedEntity.PATIENT)
            taken.add(name)
            names.append(name)
        return names

    assert draw(20) == draw(20)


def test_a_gap_left_by_a_rename_is_filled_before_the_pool_advances() -> None:
    """A count-based shortcut would skip past the freed name; the walk reuses it."""
    taken = set(WRITER_POOL[:10]) - {WRITER_POOL[3]}

    assert (
        allocate_name(WRITER_POOL, taken, entity=NamedEntity.PATIENT)
        == (WRITER_POOL[3])
    )


def test_the_name_after_the_pool_is_exhausted_is_the_first_plus_a_suffix() -> None:
    taken = set(WRITER_POOL)

    assert allocate_name(WRITER_POOL, taken, entity=NamedEntity.PATIENT) == (
        f"{WRITER_POOL[0]} 2"
    )


def test_a_third_pass_appends_three() -> None:
    taken = set(WRITER_POOL) | {f"{name} 2" for name in WRITER_POOL}

    assert allocate_name(WRITER_POOL, taken, entity=NamedEntity.PATIENT) == (
        f"{WRITER_POOL[0]} 3"
    )


def test_an_allocation_logs_its_pass_number() -> None:
    with capture_logs() as logs:
        allocate_name(WRITER_POOL, set(WRITER_POOL), entity=NamedEntity.PATIENT)

    allocated = next(e for e in logs if e["event"] == "name.allocated")
    assert allocated["entity"] == NamedEntity.PATIENT
    assert allocated["pass_number"] == 2


def test_a_first_pass_allocation_reports_pass_one() -> None:
    with capture_logs() as logs:
        allocate_name(PHYSICIAN_POOL, set(), entity=NamedEntity.PRACTITIONER)

    allocated = next(e for e in logs if e["event"] == "name.allocated")
    assert allocated["pass_number"] == 1
    assert allocated["full_name"] == PHYSICIAN_POOL[0]


def test_an_empty_pool_is_rejected_rather_than_looping_forever() -> None:
    with pytest.raises(ValueError):
        allocate_name((), set(), entity=NamedEntity.PATIENT)


def test_the_two_pools_are_allocated_independently() -> None:
    """A session's patients and practitioners never compete for one name space."""
    patient = allocate_name(WRITER_POOL, set(), entity=NamedEntity.PATIENT)
    practitioner = allocate_name(PHYSICIAN_POOL, set(), entity=NamedEntity.PRACTITIONER)

    assert patient != practitioner
