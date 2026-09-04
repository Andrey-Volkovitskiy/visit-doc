"""The scheduling vocabulary both services must agree on, declared once.

Kept here rather than in either service because neither owns these sets alone: the
scheduler validates against them on ingress and the chat service maps them onto
patient-facing text, so a second declaration on either side could silently drift.
"""

from enum import IntEnum, StrEnum


class Specialty(StrEnum):
    """The closed set of practitioner specialties.

    The values *are* the display names - there is no separate key-to-label mapping, so
    sorting by name and sorting by value are the same operation, and a database row or
    log line reads without a lookup. Carried across both the gRPC and HTTP boundaries
    as a plain string validated against this enum, and stored in a plain `VARCHAR`
    column rather than a SQL enum, so adding an eleventh value needs no migration and
    no stub regeneration.
    """

    CARDIOLOGY = "Cardiology"
    DENTISTRY = "Dentistry"
    DERMATOLOGY = "Dermatology"
    GENERAL_PRACTICE = "General Practice"
    GYNECOLOGY = "Gynecology"
    NEUROLOGY = "Neurology"
    OPHTHALMOLOGY = "Ophthalmology"
    ORTHOPEDICS = "Orthopedics"
    PEDIATRICS = "Pediatrics"
    PSYCHIATRY = "Psychiatry"


class Weekday(IntEnum):
    """A day of the week, Monday-based.

    Matches Python's own `date.weekday()` numbering, so a working range's `weekday`
    column can be compared to a calendar date's weekday with no conversion.
    """

    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


class BookingFailureReason(StrEnum):
    """Why an evaluated booking attempt was refused.

    The whole set - every refusal maps to exactly one member, and one member is
    reported per attempt even when several rules were broken. Mirrors the
    `BookingFailureReason` enum on the gRPC contract 1:1.

    A refusal is a normal outcome carried as data; it is distinct from the service
    being unreachable, which is a transport failure and has no member here.
    """

    PRACTITIONER_BUSY = "practitioner_busy"
    PATIENT_BUSY = "patient_busy"
    OUTSIDE_SCHEDULE = "outside_schedule"
    OFF_GRID = "off_grid"
    IN_PAST = "in_past"
    BEYOND_HORIZON = "beyond_horizon"
    PRACTITIONER_NOT_FOUND = "practitioner_not_found"
    PATIENT_NOT_FOUND = "patient_not_found"


class NotFoundEntity(StrEnum):
    """Which id in a request failed to resolve, when the answer is gRPC `NOT_FOUND`.

    `NOT_FOUND` carries no typed payload, so the status *detail* is the contract: the
    scheduler aborts with exactly one of these values and the chat service reads it
    back. Without it a single status would stand for several situations at once, and a
    caller could only guess which id was at fault - the guess that becomes the
    assistant telling a patient a real practitioner does not exist.

    "Belongs to another session" is reported identically to "never existed", per the
    contract's scoping rule; the entity is still named, since that much is true either
    way.
    """

    PRACTITIONER = "practitioner_not_found"
    PATIENT = "patient_not_found"
    CHAT = "chat_not_found"


class AppointmentStatus(StrEnum):
    """Whether an appointment still counts, or was called off but kept.

    The single fact separating "this is happening" from "this was called off". A
    cancelled appointment keeps its identifier, its practitioner and its times - the
    record survives - but blocks no slot, holds no booking key, and is absent from a
    listing that did not ask for it.

    Stored as a plain `VARCHAR` guarded by a `CHECK`, not a SQL enum, matching how
    `Specialty` is stored: a third value would then need no migration.
    """

    STANDING = "standing"
    CANCELLED = "cancelled"


class ChangeFailureReason(StrEnum):
    """Why an evaluated reschedule or cancellation was refused.

    The whole set of twelve - this feature's own four, then booking's eight, which
    reuse `BookingFailureReason`'s exact string values so one explanation table can be
    keyed by string for both flows. A unit test pins that overlap member by member;
    without it the two vocabularies drift until one reason means different things
    depending on which flow reported it.

    Declaration order *is* the evaluation precedence, and the four come first because
    each settles whether the appointment can be changed at all, before any question of
    where it may go is worth asking. Exactly one is reported per refusal, even when an
    attempt breaks several rules.

    `ALREADY_STARTED` is not `IN_PAST`: `IN_PAST` is about the new start being asked
    for, `ALREADY_STARTED` about the appointment's current one, and an appointment that
    has begun is refused even when the time it would move to is perfectly valid.

    `ALREADY_CANCELLED` is reachable as a *failure* only for a reschedule. For a
    cancellation that state is the target state, so reaching it is `no_change` - not a
    failed cancellation.
    """

    APPOINTMENT_NOT_FOUND = "appointment_not_found"
    ALREADY_CANCELLED = "already_cancelled"
    ALREADY_STARTED = "already_started"
    STALE_CONFIRMATION = "stale_confirmation"
    PRACTITIONER_NOT_FOUND = "practitioner_not_found"
    PATIENT_NOT_FOUND = "patient_not_found"
    IN_PAST = "in_past"
    BEYOND_HORIZON = "beyond_horizon"
    OUTSIDE_SCHEDULE = "outside_schedule"
    OFF_GRID = "off_grid"
    PRACTITIONER_BUSY = "practitioner_busy"
    PATIENT_BUSY = "patient_busy"


class TimeFilter(StrEnum):
    """Which side of the patient's clock a listing asks about.

    Independent of `StatusFilter`: whether an appointment has started is a comparison
    against the client's clock, while whether it stands is stored, so every combination
    of the two is a question someone can ask.
    """

    FUTURE = "future"
    PAST = "past"
    BOTH = "both"


class StatusFilter(StrEnum):
    """Which statuses a listing asks about.

    Mirrors `AppointmentStatus` plus a "both", rather than reusing it: a filter is not a
    status, and letting one type mean both would make "the appointments I asked for"
    and "the state one of them is in" the same value.
    """

    STANDING = "standing"
    CANCELLED = "cancelled"
    BOTH = "both"
