"""The eight journeys a demo is judged on, each driven from the patient pane and checked
from the staff pane as well as from the reply (`docs/ROADMAP.md`, Phase 3b).

Assertions read structure: the hooks and data attributes the frontend exposes for
exactly this, the constants a handed-off turn replies with, the corpus text a citation
is drawn from. The two journeys whose whole claim is in a model-written reply - free
slots and the patient's own appointments - are held to which times of day the reply
names, never to its phrasing, and each plants its prestate so that no time it must
leave out can appear as the end of one it must name.
"""

import re

from chat.agent.answer_faq import _ABSTENTION_MESSAGE
from chat.agent.escalation import HANDOFF_TEXT
from chat.domain.models import EscalationReason
from chat.rag.default_corpus import DEFAULT_FAQ_ENTRIES
from playwright.sync_api import BrowserContext, Locator, expect
from shared_models.scheduling import Specialty

from .conftest import (
    Clinic,
    ClinicPage,
    booked_starts,
    clock,
    clocks_named,
    starting_at,
)

DENTIST = "Nadia Petrova"
GP = "Samuel Okafor"


def _reply_text(reply: Locator) -> str:
    return reply.get_by_test_id("message-content").inner_text()


def _only_act(view: ClinicPage, reply: Locator, operation: str) -> Locator:
    """The reply's marker is blue and holds one settled act of `operation`."""
    marker = view.open_evidence(reply)
    expect(marker).to_have_attribute("data-outcome-state", "served")
    act = reply.get_by_test_id("booking-act")
    expect(act).to_have_count(1)
    expect(act).to_have_attribute("data-operation", operation)
    expect(act).to_have_attribute("data-outcome", "done")
    return act


def _called_staff(view: ClinicPage, reply: Locator, reason: EscalationReason) -> None:
    """The console shows this conversation as needing a person, for `reason`."""
    expect(view.open_evidence(reply)).to_have_attribute(
        "data-outcome-state", "needs-person"
    )
    expect(view.conversation()).to_have_attribute("data-emphasized", "true")
    patient_message = view.staff_thread.locator(
        '[data-testid="message"][data-sender="patient"]'
    )
    expect(patient_message.get_by_test_id("attention-mark")).to_have_attribute(
        "data-mark", reason.value
    )


def test_free_slots_today_name_every_free_slot_and_no_booked_one(
    clinic: Clinic, context: BrowserContext
) -> None:
    dentist = clinic.add_practitioner(
        DENTIST, Specialty.DENTISTRY, opens="10:00", closes="15:00"
    )
    # The first two slots of the day, so neither can surface as the end of a free
    # slot the reply names ("12:00-13:00" names 13:00, which is free).
    busy = ("10:00", "11:00")
    free = ("12:00", "13:00", "14:00")
    for start in busy:
        clinic.book(clinic.other_patient, dentist, start)
    view = clinic.open(context)

    reply = view.ask("Which times are available with a dentist today?")

    named = clocks_named(_reply_text(reply))
    assert {clock(start) for start in free} <= named
    assert named.isdisjoint(clock(start) for start in busy)


def test_my_appointments_lists_the_standing_one_alone(
    clinic: Clinic, context: BrowserContext
) -> None:
    gp = clinic.add_practitioner(
        GP, Specialty.GENERAL_PRACTICE, opens="09:00", closes="17:00"
    )
    clinic.book(clinic.patient, gp, "10:00")
    clinic.book(clinic.other_patient, gp, "13:00")
    cancelled = clinic.book(clinic.patient, gp, "15:00")
    clinic.cancel(clinic.patient, gp, cancelled, "15:00")
    view = clinic.open(context)

    reply = view.ask("What appointments do I have?")

    named = clocks_named(_reply_text(reply))
    assert clock("10:00") in named
    assert clock("13:00") not in named, "another patient's appointment was listed"
    assert clock("15:00") not in named, "a cancelled appointment was listed"


def test_booking_a_gp_lands_on_the_practitioners_week(
    clinic: Clinic, context: BrowserContext
) -> None:
    gp = clinic.add_practitioner(
        GP, Specialty.GENERAL_PRACTICE, opens="09:00", closes="17:00"
    )
    view = clinic.open(context)

    reply = view.ask("Please book me an appointment with a GP today at 11:00.")

    act = _only_act(view, reply, "book")
    expect(act).to_contain_text(GP)
    expect(booked_starts(view.week_of(gp))).to_have_text(starting_at("11:00"))


def test_rescheduling_moves_the_appointment_on_the_practitioners_week(
    clinic: Clinic, context: BrowserContext
) -> None:
    gp = clinic.add_practitioner(
        GP, Specialty.GENERAL_PRACTICE, opens="09:00", closes="17:00"
    )
    clinic.book(clinic.patient, gp, "10:00")
    view = clinic.open(context)

    reply = view.ask("Please move my 10:00 appointment today to 14:00.")

    _only_act(view, reply, "reschedule")
    expect(booked_starts(view.week_of(gp))).to_have_text(starting_at("14:00"))


def test_cancelling_removes_the_appointment_from_the_practitioners_week(
    clinic: Clinic, context: BrowserContext
) -> None:
    gp = clinic.add_practitioner(
        GP, Specialty.GENERAL_PRACTICE, opens="09:00", closes="17:00"
    )
    clinic.book(clinic.patient, gp, "10:00")
    clinic.book(clinic.patient, gp, "13:00")
    view = clinic.open(context)

    reply = view.ask("Please cancel my 13:00 appointment today.")

    _only_act(view, reply, "cancel")
    expect(booked_starts(view.week_of(gp))).to_have_text(starting_at("10:00"))


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_an_faq_answer_cites_the_entry_it_came_from(
    clinic: Clinic, context: BrowserContext
) -> None:
    question = "What should I bring to my first appointment?"
    entry = next(e for e in DEFAULT_FAQ_ENTRIES if question in e)
    view = clinic.open(context)

    reply = view.ask(question)

    marker = view.open_evidence(reply)
    expect(marker).to_have_attribute("data-outcome-state", "served")
    citations = reply.get_by_test_id("citations").locator("li").all_inner_texts()
    assert any(
        _normalized(c) and _normalized(c) in _normalized(entry) for c in citations
    )
    expect(view.conversation()).to_have_attribute("data-emphasized", "false")


def test_a_question_the_faq_does_not_cover_calls_staff(
    clinic: Clinic, context: BrowserContext
) -> None:
    view = clinic.open(context)

    reply = view.ask("Is the entrance wheelchair accessible?")

    expect(reply.get_by_test_id("message-content")).to_have_text(_ABSTENTION_MESSAGE)
    _called_staff(view, reply, EscalationReason.CORPUS_COULD_NOT_ANSWER)


def test_asking_for_a_person_calls_staff(
    clinic: Clinic, context: BrowserContext
) -> None:
    view = clinic.open(context)

    reply = view.ask("I'd rather speak to a person.")

    expect(reply.get_by_test_id("message-content")).to_have_text(
        HANDOFF_TEXT[EscalationReason.PATIENT_ASKED_FOR_PERSON]
    )
    _called_staff(view, reply, EscalationReason.PATIENT_ASKED_FOR_PERSON)
