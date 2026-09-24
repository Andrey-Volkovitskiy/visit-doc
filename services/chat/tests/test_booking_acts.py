"""Tests for `chat.agent.booking_acts`: the port the tool registry records acts through.

Covers what an act is planned as from a write's arguments, what a tool's answer settles
it as, the port's shape - write-only, so the agent can never read its own record back -
and the database adapter against the real store.
"""

import ast
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from chat.agent import handle_booking
from chat.agent.booking_acts import (
    ActHandle,
    BookingActNotRecordedError,
    BookingActRecorder,
    DatabaseBookingActRecorder,
    DiscardingBookingActRecorder,
    PlannedAct,
    Settlement,
    settlement_from,
)
from chat.db.session import session_factory
from chat.domain.models import (
    BookingAct,
    BookingActOperation,
    BookingActOutcome,
    MessageSender,
)
from chat.repositories import booking_act_repository, chat_repository
from ulid import ULID

_OSLER = "01PRACT0000000000000000000"
_VESALIUS = "01PRACT0000000000000000001"
_APPOINTMENT = "01APPT00000000000000000000"
_NAMES = {_OSLER: "William Osler", _VESALIUS: "Andreas Vesalius"}
_AT_NINE = datetime(2027, 1, 12, 9, 0)
_AT_TEN = datetime(2027, 1, 12, 10, 0)


def _name_of(practitioner_id: str) -> str | None:
    return _NAMES.get(practitioner_id)


# --- planning an act from a write's arguments ---------------------------------------


def test_a_booking_is_planned_at_the_practitioner_and_time_asked_for() -> None:
    planned = PlannedAct.book(
        practitioner_id=_OSLER, starts_at=_AT_TEN, name_of=_name_of
    )

    assert planned == PlannedAct(
        operation=BookingActOperation.BOOK,
        practitioner_id=_OSLER,
        practitioner_full_name="William Osler",
        starts_at=_AT_TEN,
        appointment_id=None,
        previous_practitioner_id=None,
        previous_practitioner_full_name=None,
        previous_starts_at=None,
    )


def test_a_reschedule_to_another_practitioner_names_both_ends() -> None:
    planned = PlannedAct.reschedule(
        appointment_id=_APPOINTMENT,
        new_starts_at=_AT_TEN,
        new_practitioner_id=_VESALIUS,
        expected_starts_at=_AT_NINE,
        expected_practitioner_id=_OSLER,
        name_of=_name_of,
    )

    assert planned == PlannedAct(
        operation=BookingActOperation.RESCHEDULE,
        practitioner_id=_VESALIUS,
        practitioner_full_name="Andreas Vesalius",
        starts_at=_AT_TEN,
        appointment_id=_APPOINTMENT,
        previous_practitioner_id=_OSLER,
        previous_practitioner_full_name="William Osler",
        previous_starts_at=_AT_NINE,
    )


def test_a_reschedule_keeping_its_practitioner_stays_with_the_expected_one() -> None:
    # Absent means "keep the practitioner it has" - the guard value names that one.
    planned = PlannedAct.reschedule(
        appointment_id=_APPOINTMENT,
        new_starts_at=_AT_TEN,
        new_practitioner_id=None,
        expected_starts_at=_AT_NINE,
        expected_practitioner_id=_OSLER,
        name_of=_name_of,
    )

    assert planned.practitioner_id == _OSLER
    assert planned.practitioner_full_name == "William Osler"
    assert planned.previous_practitioner_id == _OSLER
    assert planned.starts_at == _AT_TEN
    assert planned.previous_starts_at == _AT_NINE


def test_a_cancellation_is_planned_at_the_appointment_as_described() -> None:
    planned = PlannedAct.cancel(
        appointment_id=_APPOINTMENT,
        expected_starts_at=_AT_NINE,
        expected_practitioner_id=_OSLER,
        name_of=_name_of,
    )

    assert planned == PlannedAct(
        operation=BookingActOperation.CANCEL,
        practitioner_id=_OSLER,
        practitioner_full_name="William Osler",
        starts_at=_AT_NINE,
        appointment_id=_APPOINTMENT,
        previous_practitioner_id=None,
        previous_practitioner_full_name=None,
        previous_starts_at=None,
    )


def test_a_practitioner_the_roster_does_not_name_is_planned_nameless() -> None:
    planned = PlannedAct.book(
        practitioner_id="01PRACT000000000000000000Z",
        starts_at=_AT_TEN,
        name_of=_name_of,
    )

    assert planned.practitioner_full_name is None


# --- settling an act from a tool's answer -------------------------------------------


def _booked() -> dict[str, Any]:
    return {
        "status": "booked",
        "appointment": {
            "id": _APPOINTMENT,
            "practitioner_full_name": "Sir William Osler",
            "starts_at": "2027-01-12T10:00:00",
            "ends_at": "2027-01-12T11:00:00",
        },
    }


def _changed(change: str) -> dict[str, Any]:
    return {
        "status": "changed",
        "change": change,
        "appointment": {
            "id": _APPOINTMENT,
            "practitioner_full_name": "Andreas Vesalius",
            "specialty": "Anatomy",
            "starts_at": "2027-01-12T10:00:00",
            "ends_at": "2027-01-12T10:30:00",
            "status": "standing",
        },
        "previous_starts_at": "2027-01-12T09:00:00",
        "previous_practitioner_full_name": "William Osler",
    }


def test_a_booking_that_was_made_settles_done_with_what_the_scheduler_reported() -> (
    None
):
    settlement = settlement_from(BookingActOperation.BOOK, _booked())

    assert settlement == Settlement(
        outcome=BookingActOutcome.DONE,
        refusal_reason=None,
        appointment_id=_APPOINTMENT,
        practitioner_full_name="Sir William Osler",
        starts_at=_AT_TEN,
        ends_at=datetime(2027, 1, 12, 11, 0),
        previous_practitioner_full_name=None,
        previous_starts_at=None,
    )


def test_a_reschedule_that_was_made_settles_done_with_both_ends() -> None:
    settlement = settlement_from(
        BookingActOperation.RESCHEDULE, _changed("rescheduled")
    )

    assert settlement == Settlement(
        outcome=BookingActOutcome.DONE,
        refusal_reason=None,
        appointment_id=_APPOINTMENT,
        practitioner_full_name="Andreas Vesalius",
        starts_at=_AT_TEN,
        ends_at=datetime(2027, 1, 12, 10, 30),
        previous_practitioner_full_name="William Osler",
        previous_starts_at=_AT_NINE,
    )


def test_a_cancellation_that_was_made_takes_no_previous_end() -> None:
    # A cancellation's answer reports where the appointment "was", which is where it
    # still is: it moved nowhere, and only a reschedule's record has a previous end.
    result = _changed("cancelled")
    result["appointment"]["starts_at"] = "2027-01-12T09:00:00"
    settlement = settlement_from(BookingActOperation.CANCEL, result)

    assert settlement.outcome is BookingActOutcome.DONE
    assert settlement.starts_at == _AT_NINE
    assert settlement.previous_practitioner_full_name is None
    assert settlement.previous_starts_at is None


def test_an_unchanged_answer_settles_unchanged_with_the_appointment_as_it_stands() -> (
    None
):
    result = {
        "status": "unchanged",
        "appointment": _changed("rescheduled")["appointment"],
        "explanation": "already so",
    }

    settlement = settlement_from(BookingActOperation.RESCHEDULE, result)

    assert settlement.outcome is BookingActOutcome.UNCHANGED
    assert settlement.practitioner_full_name == "Andreas Vesalius"
    assert settlement.ends_at == datetime(2027, 1, 12, 10, 30)
    assert settlement.previous_practitioner_full_name is None
    assert settlement.previous_starts_at is None


def test_a_refusal_settles_refused_with_its_reason() -> None:
    result = {
        "status": "refused",
        "reason": "practitioner_busy",
        "explanation": "taken",
    }

    settlement = settlement_from(BookingActOperation.BOOK, result)

    assert settlement == Settlement(
        outcome=BookingActOutcome.REFUSED, refusal_reason="practitioner_busy"
    )


def test_a_refusal_naming_no_reason_settles_unknown_rather_than_inventing_one() -> None:
    settlement = settlement_from(BookingActOperation.BOOK, {"status": "refused"})

    assert settlement == Settlement(outcome=BookingActOutcome.UNKNOWN)


def test_an_unavailable_answer_settles_not_sent() -> None:
    settlement = settlement_from(
        BookingActOperation.CANCEL, {"status": "unavailable", "explanation": "down"}
    )

    assert settlement == Settlement(outcome=BookingActOutcome.NOT_SENT)


def test_an_unknown_answer_settles_unknown() -> None:
    settlement = settlement_from(
        BookingActOperation.BOOK, {"status": "unknown", "explanation": "no answer"}
    )

    assert settlement == Settlement(outcome=BookingActOutcome.UNKNOWN)


@pytest.mark.parametrize("result", [{}, {"status": "confirmed"}, {"status": None}])
def test_an_answer_this_build_cannot_name_settles_unknown(
    result: dict[str, Any],
) -> None:
    assert settlement_from(BookingActOperation.BOOK, result) == Settlement(
        outcome=BookingActOutcome.UNKNOWN
    )


def test_an_unreadable_appointment_leaves_the_planned_values_standing() -> None:
    # What the answer did not report readably is left unreported, so the settle keeps
    # what the act was begun with rather than writing a guess over it.
    result = _booked()
    result["appointment"]["starts_at"] = "next Tuesday"
    result["appointment"]["practitioner_full_name"] = 42

    settlement = settlement_from(BookingActOperation.BOOK, result)

    assert settlement.outcome is BookingActOutcome.DONE
    assert settlement.starts_at is None
    assert settlement.practitioner_full_name is None
    assert settlement.ends_at == datetime(2027, 1, 12, 11, 0)


# --- the port is write-only (plan invariant 7, FR-021a) ----------------------------

_PORT = {"learn_practitioners", "name_of", "begin", "record_not_sent", "settle"}


def _public(cls: type) -> set[str]:
    return {name for name in dir(cls) if not name.startswith("_")}


def test_the_port_exposes_exactly_the_five_write_side_calls() -> None:
    assert _public(BookingActRecorder) == _PORT


@pytest.mark.parametrize(
    "adapter", [DiscardingBookingActRecorder, DatabaseBookingActRecorder]
)
def test_no_adapter_offers_the_agent_a_way_to_read_the_record(adapter: type) -> None:
    assert _public(adapter) == _PORT


async def test_the_discarding_recorder_accepts_every_call() -> None:
    recorder = DiscardingBookingActRecorder()
    planned = PlannedAct.book(
        practitioner_id=_OSLER, starts_at=_AT_TEN, name_of=_name_of
    )

    recorder.learn_practitioners([{"id": _OSLER, "full_name": "William Osler"}])
    handle = await recorder.begin(planned)
    await recorder.record_not_sent(planned)
    await recorder.settle(handle, Settlement(outcome=BookingActOutcome.DONE))

    assert isinstance(handle, ActHandle)


def test_a_recorder_names_the_practitioners_its_roster_named() -> None:
    recorder = DiscardingBookingActRecorder()

    recorder.learn_practitioners(
        [
            {"id": _OSLER, "full_name": "William Osler"},
            # What a roster read cannot vouch for is skipped, never guessed at.
            {"id": _VESALIUS},
            {"full_name": "Nobody"},
            "not an entry",
        ]
    )

    assert recorder.name_of(_OSLER) == "William Osler"
    assert recorder.name_of(_VESALIUS) is None
    assert recorder.name_of("01PRACT000000000000000000Z") is None


def test_a_recorder_that_learned_no_roster_names_nobody() -> None:
    assert DiscardingBookingActRecorder().name_of(_OSLER) is None


# --- the agent never reads the record (FR-021a) ------------------------------------

_AGENT = Path(__file__).resolve().parents[1] / "src" / "chat" / "agent"
_MAY_IMPORT = {
    "booking_acts.py",
    "tools/registry.py",
    "tools/scheduling_tools.py",
    "handle_booking.py",
}
_PORT_MODULE = "chat.agent.booking_acts"


def _imports_the_port(path: Path) -> bool:
    """Whether `path` imports `chat.agent.booking_acts`, at any depth of the file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == _PORT_MODULE for alias in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module == _PORT_MODULE:
                return True
            if node.module == "chat.agent" and any(
                alias.name == "booking_acts" for alias in node.names
            ):
                return True
    return False


def test_only_the_recording_path_imports_the_port() -> None:
    importers = {
        path.relative_to(_AGENT).as_posix()
        for path in _AGENT.rglob("*.py")
        if _imports_the_port(path)
    }

    assert importers <= _MAY_IMPORT


def test_the_walk_sees_the_registry_importing_the_port() -> None:
    # Guards the walk itself: a moved module would leave the test above passing over a
    # tree that no longer contains the importer it exists to allow.
    assert _imports_the_port(_AGENT / "tools" / "registry.py")


# What stores or renders the record. The port's rule above is about who may *write* an
# act; this one is about who may *hold* one: an agent module that imports the
# repository could read acts back, and one that imports the row or its staff-facing
# shape could hand one to a prompt. Each value is the name its key's module exposes the
# record through.
_RECORD_REPOSITORY = "chat.repositories.booking_act_repository"
_RECORD_NAMES = {
    "chat.domain.models": "BookingAct",
    "chat.domain.schemas": "BookingActOut",
}
# `booking_acts.py` is the port's own database adapter: `DatabaseBookingActRecorder`
# writes through the repository, which is the one thing it exists to do. It is the only
# agent module that may import it, and even it imports neither the row nor the staff
# shape - it writes from a `PlannedAct` and reads nothing back.
_MAY_REACH_THE_REPOSITORY = {"booking_acts.py"}


def _record_reads(path: Path) -> set[str]:
    """What `path` reaches the stored record through, at any depth of the file.

    Names each reach as the dotted path it arrived at - `chat.repositories.
    booking_act_repository`, `chat.domain.models.BookingAct` - covering `import x`,
    `from x import y`, and a module imported whole whose forbidden attribute is then
    used (`from chat.domain import models` then `models.BookingAct`, or the fully dotted
    `chat.domain.models.BookingAct`).
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    # Each local expression that stands for a module holding a forbidden name.
    holders: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _RECORD_REPOSITORY:
                    found.add(_RECORD_REPOSITORY)
                if alias.name in _RECORD_NAMES:
                    holders[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            # A name taken *out of* the repository is a reach of it as much as the
            # module itself: `from ...booking_act_repository import list_for_chat`
            # reads acts back without ever naming the module in a way the loop below
            # would compare.
            if node.module == _RECORD_REPOSITORY:
                found.add(_RECORD_REPOSITORY)
                continue
            for alias in node.names:
                reached = f"{node.module}.{alias.name}"
                if reached == _RECORD_REPOSITORY:
                    found.add(_RECORD_REPOSITORY)
                elif _RECORD_NAMES.get(node.module) == alias.name:
                    found.add(reached)
                elif reached in _RECORD_NAMES:
                    holders[alias.asname or alias.name] = reached
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            module = holders.get(ast.unparse(node.value))
            if module is not None and _RECORD_NAMES[module] == node.attr:
                found.add(f"{module}.{node.attr}")
    return found


def test_the_agent_never_imports_the_record() -> None:
    reads = {
        f"{path.relative_to(_AGENT).as_posix()}: {reached}"
        for path in _AGENT.rglob("*.py")
        for reached in _record_reads(path)
        if not (
            reached == _RECORD_REPOSITORY
            and path.relative_to(_AGENT).as_posix() in _MAY_REACH_THE_REPOSITORY
        )
    }

    assert reads == set()


def test_the_walk_sees_the_adapter_reaching_the_repository() -> None:
    # Guards the walk the same way: an exception that no longer matches anything is one
    # the test above would go on granting to whatever takes the adapter's place.
    assert _record_reads(_AGENT / "booking_acts.py") == {_RECORD_REPOSITORY}


@pytest.mark.parametrize(
    "source",
    [
        "import chat.repositories.booking_act_repository",
        "from chat.repositories import booking_act_repository",
        "from chat.repositories.booking_act_repository import list_for_chat",
        "from chat.domain.models import BookingAct",
        "from chat.domain.schemas import BookingActOut as Out",
        "from chat.domain import models\nmodels.BookingAct",
        "from chat.domain import schemas as s\ns.BookingActOut",
        "import chat.domain.models\nchat.domain.models.BookingAct",
    ],
)
def test_the_walk_catches_every_import_form(source: str, tmp_path: Path) -> None:
    module = tmp_path / "module.py"
    module.write_text(source)

    assert _record_reads(module)


def test_the_walk_passes_over_the_record_enums(tmp_path: Path) -> None:
    module = tmp_path / "module.py"
    module.write_text(
        "from chat.domain.models import BookingActOperation, BookingActOutcome\n"
        "from chat.domain import models\n"
        "models.BookingActOutcome.DONE\n"
    )

    assert _record_reads(module) == set()


@pytest.mark.parametrize(
    "module", ["history.py", "compose_answer.py", "classify_intent.py"]
)
def test_no_prompt_builder_mentions_booking_acts(module: str) -> None:
    assert "booking_act" not in (_AGENT / module).read_text()


def test_the_booking_prompt_says_nothing_about_the_record() -> None:
    prompt = handle_booking._SYSTEM_PROMPT.lower()

    assert "booking act" not in prompt
    assert "booking_act" not in prompt


# --- the database adapter -----------------------------------------------------------


async def _patient_message() -> tuple[str, str, str]:
    """Returns: a new session's id, a chat of it, and a patient message in that chat."""
    async with session_factory() as session:
        owner = await chat_repository.create_session(session)
        chat = await chat_repository.create_chat(session, owner.id)
        message = await chat_repository.create_message(
            session,
            id=str(ULID()),
            chat_id=chat.id,
            session_id=owner.id,
            sender=MessageSender.PATIENT,
            content="book me in",
        )
    assert message is not None
    return owner.id, chat.id, message.id


def _recorder(
    session_id: str, chat_id: str, message_id: str
) -> DatabaseBookingActRecorder:
    return DatabaseBookingActRecorder(
        session_factory, session_id=session_id, chat_id=chat_id, message_id=message_id
    )


async def _stored(act_id: str) -> BookingAct:
    # A fresh session: what another connection sees is what was committed.
    async with session_factory() as session:
        found = await session.get(BookingAct, act_id)
    assert found is not None
    return found


async def test_begin_commits_the_unsettled_act_before_it_returns() -> None:
    recorder = _recorder(*await _patient_message())
    recorder.learn_practitioners([{"id": _OSLER, "full_name": "William Osler"}])
    planned = PlannedAct.book(
        practitioner_id=_OSLER, starts_at=_AT_TEN, name_of=recorder.name_of
    )

    handle = await recorder.begin(planned)

    row = await _stored(handle.act_id)
    assert row.outcome is None
    assert row.practitioner_full_name == "William Osler"


async def test_begin_raises_when_the_act_could_not_be_written() -> None:
    _, chat_id, message_id = await _patient_message()
    other_session_id, _, _ = await _patient_message()
    recorder = _recorder(other_session_id, chat_id, message_id)
    planned = PlannedAct.book(
        practitioner_id=_OSLER, starts_at=_AT_TEN, name_of=_name_of
    )

    with pytest.raises(BookingActNotRecordedError):
        await recorder.begin(planned)


async def test_record_not_sent_raises_when_the_act_could_not_be_written() -> None:
    _, chat_id, message_id = await _patient_message()
    other_session_id, _, _ = await _patient_message()
    recorder = _recorder(other_session_id, chat_id, message_id)
    planned = PlannedAct.book(
        practitioner_id=_OSLER, starts_at=_AT_TEN, name_of=_name_of
    )

    with pytest.raises(BookingActNotRecordedError):
        await recorder.record_not_sent(planned)


async def test_record_not_sent_writes_a_settled_act() -> None:
    session_id, chat_id, message_id = await _patient_message()
    recorder = _recorder(session_id, chat_id, message_id)
    planned = PlannedAct.cancel(
        appointment_id=_APPOINTMENT,
        expected_starts_at=_AT_NINE,
        expected_practitioner_id=_OSLER,
        name_of=_name_of,
    )

    await recorder.record_not_sent(planned)

    async with session_factory() as session:
        (act,) = await booking_act_repository.list_for_chat(
            session, chat_id, session_id
        )
    assert act.outcome == BookingActOutcome.NOT_SENT
    assert act.operation == BookingActOperation.CANCEL


async def test_settle_writes_what_the_answer_reported() -> None:
    recorder = _recorder(*await _patient_message())
    planned = PlannedAct.reschedule(
        appointment_id=_APPOINTMENT,
        new_starts_at=_AT_TEN,
        new_practitioner_id=_VESALIUS,
        expected_starts_at=_AT_NINE,
        expected_practitioner_id=_OSLER,
        name_of=lambda _id: None,
    )
    handle = await recorder.begin(planned)

    await recorder.settle(
        handle, settlement_from(BookingActOperation.RESCHEDULE, _changed("rescheduled"))
    )

    row = await _stored(handle.act_id)
    assert row.outcome == BookingActOutcome.DONE
    assert row.practitioner_full_name == "Andreas Vesalius"
    assert row.previous_practitioner_full_name == "William Osler"
    assert row.ends_at == datetime(2027, 1, 12, 10, 30)
