from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Weekday(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WEEKDAY_MONDAY: _ClassVar[Weekday]
    WEEKDAY_TUESDAY: _ClassVar[Weekday]
    WEEKDAY_WEDNESDAY: _ClassVar[Weekday]
    WEEKDAY_THURSDAY: _ClassVar[Weekday]
    WEEKDAY_FRIDAY: _ClassVar[Weekday]
    WEEKDAY_SATURDAY: _ClassVar[Weekday]
    WEEKDAY_SUNDAY: _ClassVar[Weekday]

class AppointmentStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    APPOINTMENT_STATUS_UNSPECIFIED: _ClassVar[AppointmentStatus]
    APPOINTMENT_STATUS_STANDING: _ClassVar[AppointmentStatus]
    APPOINTMENT_STATUS_CANCELLED: _ClassVar[AppointmentStatus]

class BookingFailureReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    BOOKING_FAILURE_REASON_UNSPECIFIED: _ClassVar[BookingFailureReason]
    BOOKING_FAILURE_REASON_PRACTITIONER_BUSY: _ClassVar[BookingFailureReason]
    BOOKING_FAILURE_REASON_PATIENT_BUSY: _ClassVar[BookingFailureReason]
    BOOKING_FAILURE_REASON_OUTSIDE_SCHEDULE: _ClassVar[BookingFailureReason]
    BOOKING_FAILURE_REASON_OFF_GRID: _ClassVar[BookingFailureReason]
    BOOKING_FAILURE_REASON_IN_PAST: _ClassVar[BookingFailureReason]
    BOOKING_FAILURE_REASON_BEYOND_HORIZON: _ClassVar[BookingFailureReason]
    BOOKING_FAILURE_REASON_PRACTITIONER_NOT_FOUND: _ClassVar[BookingFailureReason]
    BOOKING_FAILURE_REASON_PATIENT_NOT_FOUND: _ClassVar[BookingFailureReason]

class RenameFailureReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RENAME_FAILURE_REASON_UNSPECIFIED: _ClassVar[RenameFailureReason]
    RENAME_FAILURE_REASON_NAME_TAKEN: _ClassVar[RenameFailureReason]
    RENAME_FAILURE_REASON_PATIENT_NOT_FOUND: _ClassVar[RenameFailureReason]

class ChangeFailureReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHANGE_FAILURE_REASON_UNSPECIFIED: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_APPOINTMENT_NOT_FOUND: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_ALREADY_CANCELLED: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_ALREADY_STARTED: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_STALE_CONFIRMATION: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_PRACTITIONER_NOT_FOUND: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_PATIENT_NOT_FOUND: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_IN_PAST: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_BEYOND_HORIZON: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_OUTSIDE_SCHEDULE: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_OFF_GRID: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_PRACTITIONER_BUSY: _ClassVar[ChangeFailureReason]
    CHANGE_FAILURE_REASON_PATIENT_BUSY: _ClassVar[ChangeFailureReason]

class TimeFilter(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TIME_FILTER_FUTURE: _ClassVar[TimeFilter]
    TIME_FILTER_PAST: _ClassVar[TimeFilter]
    TIME_FILTER_BOTH: _ClassVar[TimeFilter]

class StatusFilter(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    STATUS_FILTER_STANDING: _ClassVar[StatusFilter]
    STATUS_FILTER_CANCELLED: _ClassVar[StatusFilter]
    STATUS_FILTER_BOTH: _ClassVar[StatusFilter]
WEEKDAY_MONDAY: Weekday
WEEKDAY_TUESDAY: Weekday
WEEKDAY_WEDNESDAY: Weekday
WEEKDAY_THURSDAY: Weekday
WEEKDAY_FRIDAY: Weekday
WEEKDAY_SATURDAY: Weekday
WEEKDAY_SUNDAY: Weekday
APPOINTMENT_STATUS_UNSPECIFIED: AppointmentStatus
APPOINTMENT_STATUS_STANDING: AppointmentStatus
APPOINTMENT_STATUS_CANCELLED: AppointmentStatus
BOOKING_FAILURE_REASON_UNSPECIFIED: BookingFailureReason
BOOKING_FAILURE_REASON_PRACTITIONER_BUSY: BookingFailureReason
BOOKING_FAILURE_REASON_PATIENT_BUSY: BookingFailureReason
BOOKING_FAILURE_REASON_OUTSIDE_SCHEDULE: BookingFailureReason
BOOKING_FAILURE_REASON_OFF_GRID: BookingFailureReason
BOOKING_FAILURE_REASON_IN_PAST: BookingFailureReason
BOOKING_FAILURE_REASON_BEYOND_HORIZON: BookingFailureReason
BOOKING_FAILURE_REASON_PRACTITIONER_NOT_FOUND: BookingFailureReason
BOOKING_FAILURE_REASON_PATIENT_NOT_FOUND: BookingFailureReason
RENAME_FAILURE_REASON_UNSPECIFIED: RenameFailureReason
RENAME_FAILURE_REASON_NAME_TAKEN: RenameFailureReason
RENAME_FAILURE_REASON_PATIENT_NOT_FOUND: RenameFailureReason
CHANGE_FAILURE_REASON_UNSPECIFIED: ChangeFailureReason
CHANGE_FAILURE_REASON_APPOINTMENT_NOT_FOUND: ChangeFailureReason
CHANGE_FAILURE_REASON_ALREADY_CANCELLED: ChangeFailureReason
CHANGE_FAILURE_REASON_ALREADY_STARTED: ChangeFailureReason
CHANGE_FAILURE_REASON_STALE_CONFIRMATION: ChangeFailureReason
CHANGE_FAILURE_REASON_PRACTITIONER_NOT_FOUND: ChangeFailureReason
CHANGE_FAILURE_REASON_PATIENT_NOT_FOUND: ChangeFailureReason
CHANGE_FAILURE_REASON_IN_PAST: ChangeFailureReason
CHANGE_FAILURE_REASON_BEYOND_HORIZON: ChangeFailureReason
CHANGE_FAILURE_REASON_OUTSIDE_SCHEDULE: ChangeFailureReason
CHANGE_FAILURE_REASON_OFF_GRID: ChangeFailureReason
CHANGE_FAILURE_REASON_PRACTITIONER_BUSY: ChangeFailureReason
CHANGE_FAILURE_REASON_PATIENT_BUSY: ChangeFailureReason
TIME_FILTER_FUTURE: TimeFilter
TIME_FILTER_PAST: TimeFilter
TIME_FILTER_BOTH: TimeFilter
STATUS_FILTER_STANDING: StatusFilter
STATUS_FILTER_CANCELLED: StatusFilter
STATUS_FILTER_BOTH: StatusFilter

class WorkingRange(_message.Message):
    __slots__ = ("weekday", "start_time", "end_time")
    WEEKDAY_FIELD_NUMBER: _ClassVar[int]
    START_TIME_FIELD_NUMBER: _ClassVar[int]
    END_TIME_FIELD_NUMBER: _ClassVar[int]
    weekday: Weekday
    start_time: str
    end_time: str
    def __init__(self, weekday: _Optional[_Union[Weekday, str]] = ..., start_time: _Optional[str] = ..., end_time: _Optional[str] = ...) -> None: ...

class Patient(_message.Message):
    __slots__ = ("id", "chat_id", "full_name")
    ID_FIELD_NUMBER: _ClassVar[int]
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    id: str
    chat_id: str
    full_name: str
    def __init__(self, id: _Optional[str] = ..., chat_id: _Optional[str] = ..., full_name: _Optional[str] = ...) -> None: ...

class Practitioner(_message.Message):
    __slots__ = ("id", "full_name", "specialty", "appointment_duration_minutes", "schedule")
    ID_FIELD_NUMBER: _ClassVar[int]
    FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    SPECIALTY_FIELD_NUMBER: _ClassVar[int]
    APPOINTMENT_DURATION_MINUTES_FIELD_NUMBER: _ClassVar[int]
    SCHEDULE_FIELD_NUMBER: _ClassVar[int]
    id: str
    full_name: str
    specialty: str
    appointment_duration_minutes: int
    schedule: _containers.RepeatedCompositeFieldContainer[WorkingRange]
    def __init__(self, id: _Optional[str] = ..., full_name: _Optional[str] = ..., specialty: _Optional[str] = ..., appointment_duration_minutes: _Optional[int] = ..., schedule: _Optional[_Iterable[_Union[WorkingRange, _Mapping]]] = ...) -> None: ...

class Appointment(_message.Message):
    __slots__ = ("id", "patient_id", "patient_full_name", "practitioner_id", "practitioner_full_name", "practitioner_specialty", "starts_at", "ends_at", "status")
    ID_FIELD_NUMBER: _ClassVar[int]
    PATIENT_ID_FIELD_NUMBER: _ClassVar[int]
    PATIENT_FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    PRACTITIONER_ID_FIELD_NUMBER: _ClassVar[int]
    PRACTITIONER_FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    PRACTITIONER_SPECIALTY_FIELD_NUMBER: _ClassVar[int]
    STARTS_AT_FIELD_NUMBER: _ClassVar[int]
    ENDS_AT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    id: str
    patient_id: str
    patient_full_name: str
    practitioner_id: str
    practitioner_full_name: str
    practitioner_specialty: str
    starts_at: str
    ends_at: str
    status: AppointmentStatus
    def __init__(self, id: _Optional[str] = ..., patient_id: _Optional[str] = ..., patient_full_name: _Optional[str] = ..., practitioner_id: _Optional[str] = ..., practitioner_full_name: _Optional[str] = ..., practitioner_specialty: _Optional[str] = ..., starts_at: _Optional[str] = ..., ends_at: _Optional[str] = ..., status: _Optional[_Union[AppointmentStatus, str]] = ...) -> None: ...

class BookingFailure(_message.Message):
    __slots__ = ("reason", "detail")
    REASON_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    reason: BookingFailureReason
    detail: str
    def __init__(self, reason: _Optional[_Union[BookingFailureReason, str]] = ..., detail: _Optional[str] = ...) -> None: ...

class RenameFailure(_message.Message):
    __slots__ = ("reason", "detail")
    REASON_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    reason: RenameFailureReason
    detail: str
    def __init__(self, reason: _Optional[_Union[RenameFailureReason, str]] = ..., detail: _Optional[str] = ...) -> None: ...

class ChangeFailure(_message.Message):
    __slots__ = ("reason", "detail")
    REASON_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    reason: ChangeFailureReason
    detail: str
    def __init__(self, reason: _Optional[_Union[ChangeFailureReason, str]] = ..., detail: _Optional[str] = ...) -> None: ...

class NoChange(_message.Message):
    __slots__ = ("appointment",)
    APPOINTMENT_FIELD_NUMBER: _ClassVar[int]
    appointment: Appointment
    def __init__(self, appointment: _Optional[_Union[Appointment, _Mapping]] = ...) -> None: ...

class EnsureSessionProvisionedRequest(_message.Message):
    __slots__ = ("session_id", "chat_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    chat_id: str
    def __init__(self, session_id: _Optional[str] = ..., chat_id: _Optional[str] = ...) -> None: ...

class EnsureSessionProvisionedResponse(_message.Message):
    __slots__ = ("patient", "practitioners", "patient_created", "practitioner_created")
    PATIENT_FIELD_NUMBER: _ClassVar[int]
    PRACTITIONERS_FIELD_NUMBER: _ClassVar[int]
    PATIENT_CREATED_FIELD_NUMBER: _ClassVar[int]
    PRACTITIONER_CREATED_FIELD_NUMBER: _ClassVar[int]
    patient: Patient
    practitioners: _containers.RepeatedCompositeFieldContainer[Practitioner]
    patient_created: bool
    practitioner_created: bool
    def __init__(self, patient: _Optional[_Union[Patient, _Mapping]] = ..., practitioners: _Optional[_Iterable[_Union[Practitioner, _Mapping]]] = ..., patient_created: _Optional[bool] = ..., practitioner_created: _Optional[bool] = ...) -> None: ...

class RenamePatientRequest(_message.Message):
    __slots__ = ("session_id", "patient_id", "full_name")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PATIENT_ID_FIELD_NUMBER: _ClassVar[int]
    FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    patient_id: str
    full_name: str
    def __init__(self, session_id: _Optional[str] = ..., patient_id: _Optional[str] = ..., full_name: _Optional[str] = ...) -> None: ...

class RenamePatientResponse(_message.Message):
    __slots__ = ("patient", "failure")
    PATIENT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    patient: Patient
    failure: RenameFailure
    def __init__(self, patient: _Optional[_Union[Patient, _Mapping]] = ..., failure: _Optional[_Union[RenameFailure, _Mapping]] = ...) -> None: ...

class ListPractitionersRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class ListPractitionersResponse(_message.Message):
    __slots__ = ("practitioners",)
    PRACTITIONERS_FIELD_NUMBER: _ClassVar[int]
    practitioners: _containers.RepeatedCompositeFieldContainer[Practitioner]
    def __init__(self, practitioners: _Optional[_Iterable[_Union[Practitioner, _Mapping]]] = ...) -> None: ...

class CheckAvailabilityRequest(_message.Message):
    __slots__ = ("session_id", "practitioner_id", "from_date", "to_date", "local_now", "patient_id", "excluded_appointment_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PRACTITIONER_ID_FIELD_NUMBER: _ClassVar[int]
    FROM_DATE_FIELD_NUMBER: _ClassVar[int]
    TO_DATE_FIELD_NUMBER: _ClassVar[int]
    LOCAL_NOW_FIELD_NUMBER: _ClassVar[int]
    PATIENT_ID_FIELD_NUMBER: _ClassVar[int]
    EXCLUDED_APPOINTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    practitioner_id: str
    from_date: str
    to_date: str
    local_now: str
    patient_id: str
    excluded_appointment_id: str
    def __init__(self, session_id: _Optional[str] = ..., practitioner_id: _Optional[str] = ..., from_date: _Optional[str] = ..., to_date: _Optional[str] = ..., local_now: _Optional[str] = ..., patient_id: _Optional[str] = ..., excluded_appointment_id: _Optional[str] = ...) -> None: ...

class CheckAvailabilityResponse(_message.Message):
    __slots__ = ("available_starts", "truncated", "appointment_duration_minutes")
    AVAILABLE_STARTS_FIELD_NUMBER: _ClassVar[int]
    TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    APPOINTMENT_DURATION_MINUTES_FIELD_NUMBER: _ClassVar[int]
    available_starts: _containers.RepeatedScalarFieldContainer[str]
    truncated: bool
    appointment_duration_minutes: int
    def __init__(self, available_starts: _Optional[_Iterable[str]] = ..., truncated: _Optional[bool] = ..., appointment_duration_minutes: _Optional[int] = ...) -> None: ...

class BookAppointmentRequest(_message.Message):
    __slots__ = ("session_id", "patient_id", "practitioner_id", "starts_at", "local_now", "idempotency_key")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PATIENT_ID_FIELD_NUMBER: _ClassVar[int]
    PRACTITIONER_ID_FIELD_NUMBER: _ClassVar[int]
    STARTS_AT_FIELD_NUMBER: _ClassVar[int]
    LOCAL_NOW_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    patient_id: str
    practitioner_id: str
    starts_at: str
    local_now: str
    idempotency_key: str
    def __init__(self, session_id: _Optional[str] = ..., patient_id: _Optional[str] = ..., practitioner_id: _Optional[str] = ..., starts_at: _Optional[str] = ..., local_now: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class BookAppointmentResponse(_message.Message):
    __slots__ = ("appointment", "failure", "idempotent_replay")
    APPOINTMENT_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENT_REPLAY_FIELD_NUMBER: _ClassVar[int]
    appointment: Appointment
    failure: BookingFailure
    idempotent_replay: bool
    def __init__(self, appointment: _Optional[_Union[Appointment, _Mapping]] = ..., failure: _Optional[_Union[BookingFailure, _Mapping]] = ..., idempotent_replay: _Optional[bool] = ...) -> None: ...

class RescheduleAppointmentRequest(_message.Message):
    __slots__ = ("session_id", "patient_id", "appointment_id", "new_starts_at", "new_practitioner_id", "expected_starts_at", "expected_practitioner_id", "local_now")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PATIENT_ID_FIELD_NUMBER: _ClassVar[int]
    APPOINTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    NEW_STARTS_AT_FIELD_NUMBER: _ClassVar[int]
    NEW_PRACTITIONER_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_STARTS_AT_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_PRACTITIONER_ID_FIELD_NUMBER: _ClassVar[int]
    LOCAL_NOW_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    patient_id: str
    appointment_id: str
    new_starts_at: str
    new_practitioner_id: str
    expected_starts_at: str
    expected_practitioner_id: str
    local_now: str
    def __init__(self, session_id: _Optional[str] = ..., patient_id: _Optional[str] = ..., appointment_id: _Optional[str] = ..., new_starts_at: _Optional[str] = ..., new_practitioner_id: _Optional[str] = ..., expected_starts_at: _Optional[str] = ..., expected_practitioner_id: _Optional[str] = ..., local_now: _Optional[str] = ...) -> None: ...

class CancelAppointmentRequest(_message.Message):
    __slots__ = ("session_id", "patient_id", "appointment_id", "expected_starts_at", "expected_practitioner_id", "local_now")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PATIENT_ID_FIELD_NUMBER: _ClassVar[int]
    APPOINTMENT_ID_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_STARTS_AT_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_PRACTITIONER_ID_FIELD_NUMBER: _ClassVar[int]
    LOCAL_NOW_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    patient_id: str
    appointment_id: str
    expected_starts_at: str
    expected_practitioner_id: str
    local_now: str
    def __init__(self, session_id: _Optional[str] = ..., patient_id: _Optional[str] = ..., appointment_id: _Optional[str] = ..., expected_starts_at: _Optional[str] = ..., expected_practitioner_id: _Optional[str] = ..., local_now: _Optional[str] = ...) -> None: ...

class ChangeAppointmentResponse(_message.Message):
    __slots__ = ("appointment", "no_change", "failure", "previous_starts_at", "previous_practitioner_id", "previous_practitioner_full_name")
    APPOINTMENT_FIELD_NUMBER: _ClassVar[int]
    NO_CHANGE_FIELD_NUMBER: _ClassVar[int]
    FAILURE_FIELD_NUMBER: _ClassVar[int]
    PREVIOUS_STARTS_AT_FIELD_NUMBER: _ClassVar[int]
    PREVIOUS_PRACTITIONER_ID_FIELD_NUMBER: _ClassVar[int]
    PREVIOUS_PRACTITIONER_FULL_NAME_FIELD_NUMBER: _ClassVar[int]
    appointment: Appointment
    no_change: NoChange
    failure: ChangeFailure
    previous_starts_at: str
    previous_practitioner_id: str
    previous_practitioner_full_name: str
    def __init__(self, appointment: _Optional[_Union[Appointment, _Mapping]] = ..., no_change: _Optional[_Union[NoChange, _Mapping]] = ..., failure: _Optional[_Union[ChangeFailure, _Mapping]] = ..., previous_starts_at: _Optional[str] = ..., previous_practitioner_id: _Optional[str] = ..., previous_practitioner_full_name: _Optional[str] = ...) -> None: ...

class ListAppointmentsRequest(_message.Message):
    __slots__ = ("session_id", "patient_id", "local_now", "time_filter", "status_filter")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    PATIENT_ID_FIELD_NUMBER: _ClassVar[int]
    LOCAL_NOW_FIELD_NUMBER: _ClassVar[int]
    TIME_FILTER_FIELD_NUMBER: _ClassVar[int]
    STATUS_FILTER_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    patient_id: str
    local_now: str
    time_filter: TimeFilter
    status_filter: StatusFilter
    def __init__(self, session_id: _Optional[str] = ..., patient_id: _Optional[str] = ..., local_now: _Optional[str] = ..., time_filter: _Optional[_Union[TimeFilter, str]] = ..., status_filter: _Optional[_Union[StatusFilter, str]] = ...) -> None: ...

class ListAppointmentsResponse(_message.Message):
    __slots__ = ("future", "past", "past_truncated")
    FUTURE_FIELD_NUMBER: _ClassVar[int]
    PAST_FIELD_NUMBER: _ClassVar[int]
    PAST_TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    future: _containers.RepeatedCompositeFieldContainer[Appointment]
    past: _containers.RepeatedCompositeFieldContainer[Appointment]
    past_truncated: bool
    def __init__(self, future: _Optional[_Iterable[_Union[Appointment, _Mapping]]] = ..., past: _Optional[_Iterable[_Union[Appointment, _Mapping]]] = ..., past_truncated: _Optional[bool] = ...) -> None: ...

class DeletePatientForChatRequest(_message.Message):
    __slots__ = ("session_id", "chat_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    chat_id: str
    def __init__(self, session_id: _Optional[str] = ..., chat_id: _Optional[str] = ...) -> None: ...

class DeletePatientForChatResponse(_message.Message):
    __slots__ = ("patient_existed", "appointments_deleted")
    PATIENT_EXISTED_FIELD_NUMBER: _ClassVar[int]
    APPOINTMENTS_DELETED_FIELD_NUMBER: _ClassVar[int]
    patient_existed: bool
    appointments_deleted: int
    def __init__(self, patient_existed: _Optional[bool] = ..., appointments_deleted: _Optional[int] = ...) -> None: ...

class DeleteSessionRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class DeleteSessionResponse(_message.Message):
    __slots__ = ("patients_deleted", "practitioners_deleted", "appointments_deleted")
    PATIENTS_DELETED_FIELD_NUMBER: _ClassVar[int]
    PRACTITIONERS_DELETED_FIELD_NUMBER: _ClassVar[int]
    APPOINTMENTS_DELETED_FIELD_NUMBER: _ClassVar[int]
    patients_deleted: int
    practitioners_deleted: int
    appointments_deleted: int
    def __init__(self, patients_deleted: _Optional[int] = ..., practitioners_deleted: _Optional[int] = ..., appointments_deleted: _Optional[int] = ...) -> None: ...
