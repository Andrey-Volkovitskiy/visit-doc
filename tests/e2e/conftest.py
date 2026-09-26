"""The e2e tier: a real browser against the running stack.

Unlike the unit and integration tiers, nothing here starts a service or isolates a
database. The tier drives the stack `make services-up` runs - frontend on :5173,
chat on :8000, the scheduler behind it - exactly as a person at the keyboard would,
and it spends live Claude and Voyage calls doing so (see `docs/testing-strategy.md`).
Isolation comes from the product's own scoping instead: every test mints a brand-new
session, every read the app makes is scoped to a session, and the session is deleted
through `/admin` when the test ends. Nothing a test plants is visible to another test
or to a person using the same stack.

Two fixtures here are machine-shaped rather than product-shaped, and both are env-driven
so the tier itself pins nothing to one machine:

- `PLAYWRIGHT_CHROMIUM_EXECUTABLE` points the launcher at an already-downloaded Chromium
  when it is not the build this `playwright` release expects.
- The browser's timezone is chosen per run so that its local clock reads early morning.
  This system has no timezone anywhere - every time is the visitor's own wall clock,
  sent as `local_now` - so a zone is only a way of choosing which wall clock the
  visitor has. A morning one is what lets a case ask about "today" and be answered from
  a working day that has not yet started, whatever hour the suite is run at.
"""

import asyncio
import os
import re
from collections.abc import Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

import grpc
import httpx
import pytest
from chat.api.session_cookie import COOKIE_NAME
from chat.clients import scheduling
from chat.core.config import Settings, get_settings
from playwright.sync_api import BrowserContext, Locator, Page, expect
from shared_models.scheduling import Specialty, Weekday
from ulid import ULID

FRONTEND_URL = os.environ.get("E2E_FRONTEND_URL", "http://localhost:5173")
CHAT_URL = os.environ.get("E2E_CHAT_URL", "http://localhost:8000")

# The browser's local hour when the run starts. Early enough that a working day
# starting at 10:00 is still wholly ahead after a slow suite, late enough that nothing
# rolls back across midnight into yesterday.
_MORNING_HOUR = 7

# How long a live turn may take to be answered and to reach the staff thread's poll.
# Generous on purpose: a classifier call, a tool loop and a composed reply are several
# sequential model calls, and a slow minute is not a defect this tier is looking for.
REPLY_TIMEOUT_MS = 120_000

T = TypeVar("T")


def _zone_where_it_is_morning(now_utc: datetime) -> str:
    """Return a fixed-offset IANA zone whose local hour is `_MORNING_HOUR` right now.

    The `Etc/GMT` zones cover every whole-hour offset from -12 to +14, a 27-hour span,
    so some offset always puts the local hour at any chosen value. Their sign is POSIX's
    and reads backwards: `Etc/GMT-3` is three hours *ahead* of UTC.
    """
    offset = (_MORNING_HOUR - now_utc.hour) % 24
    if offset > 14:
        offset -= 24
    return "Etc/GMT" if offset == 0 else f"Etc/GMT{-offset:+d}"


@pytest.fixture(scope="session")
def clinic_zone() -> str:
    return _zone_where_it_is_morning(datetime.now(UTC))


@pytest.fixture(scope="session", autouse=True)
def _stack_is_ready() -> None:
    """Fail the run before any browser opens when it could not mean anything.

    Fails, never skips: a skipped e2e run reads exactly like a passing one. The keys are
    read from the settings the running chat service reads too, so a blank one here is a
    service that cannot answer a turn.
    """
    settings = get_settings()
    missing = [
        name
        for name in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY")
        if not getattr(settings, name).strip()
    ]
    if missing:
        pytest.fail(f"the e2e tier spends live calls and needs {', '.join(missing)}")
    for url in (f"{FRONTEND_URL}/", f"{CHAT_URL}/chats"):
        try:
            httpx.get(url, timeout=5).raise_for_status()
        except httpx.HTTPError as exc:
            pytest.fail(
                f"{url} is not answering ({exc!r}) - start the stack with "
                "`make services-up` (and `make migrate` if the databases are behind)"
            )


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if executable:
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args


@pytest.fixture(scope="session")
def browser_context_args(
    browser_context_args: dict[str, Any], clinic_zone: str
) -> dict[str, Any]:
    return {
        **browser_context_args,
        "base_url": FRONTEND_URL,
        "timezone_id": clinic_zone,
        # Both panes side by side, as the product lays them out on a desktop.
        "viewport": {"width": 1600, "height": 1000},
    }


def _run(call: Callable[[grpc.aio.Channel], Awaitable[T]]) -> T:
    """Run one scheduling call on a channel of its own, on a thread of its own.

    Playwright's sync API keeps an event loop running on this thread, so `asyncio.run`
    here would refuse to start. A worker thread has no loop, and the channel is opened
    inside the coroutine so it binds to the loop that thread creates.
    """

    async def with_channel() -> T:
        channel = scheduling.create_channel(get_settings())
        try:
            return await call(channel)
        finally:
            await channel.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, with_channel()).result()


@dataclass
class Practitioner:
    id: str
    full_name: str


@dataclass
class Patient:
    chat_id: str
    patient_id: str
    full_name: str


@dataclass
class Clinic:
    """One fresh session on the running stack, and the prestate a case plants in it.

    Planted through the product's own surfaces, never by writing rows: practitioners
    through the console's proxy of the scheduler's REST API, appointments through the
    same gRPC client the booking tools use. A prestate the product could not have
    produced is not one worth testing against.
    """

    zone: ZoneInfo
    http: httpx.Client
    settings: Settings
    session_id: str
    patient: Patient
    other_patient: Patient
    _practitioners: list[Practitioner] = field(default_factory=list)

    def local_now(self) -> datetime:
        """The visitor's wall clock, which is the only clock this system has."""
        return datetime.now(self.zone).replace(tzinfo=None, microsecond=0)

    def today(self) -> date:
        return self.local_now().date()

    def at(self, clock: str) -> datetime:
        """Today, at `HH:MM` on the visitor's own clock."""
        return datetime.combine(self.today(), time.fromisoformat(clock))

    def add_practitioner(
        self,
        full_name: str,
        specialty: Specialty,
        *,
        opens: str,
        closes: str,
        minutes: int = 60,
    ) -> Practitioner:
        """Add a practitioner working `opens`-`closes` on every day of the week.

        Every day rather than today's weekday alone, so the case does not depend on
        which weekday it is run on.
        """
        response = self.http.post(
            "/console/practitioners",
            json={
                "full_name": full_name,
                "specialty": specialty.value,
                "appointment_duration_minutes": minutes,
                "schedule": [
                    {"weekday": day.value, "start_time": opens, "end_time": closes}
                    for day in Weekday
                ],
            },
        )
        response.raise_for_status()
        practitioner = Practitioner(response.json()["id"], full_name)
        self._practitioners.append(practitioner)
        return practitioner

    def book(self, patient: Patient, practitioner: Practitioner, clock: str) -> str:
        """Book `patient` with `practitioner` today at `clock`; return the new id."""
        result = _run(
            lambda channel: scheduling.book_appointment(
                channel,
                self.settings,
                session_id=self.session_id,
                patient_id=patient.patient_id,
                practitioner_id=practitioner.id,
                starts_at=self.at(clock),
                local_now=self.local_now(),
                idempotency_key=str(ULID()),
            )
        )
        assert isinstance(result, scheduling.BookingSuccess), result
        return result.appointment.id

    def cancel(
        self,
        patient: Patient,
        practitioner: Practitioner,
        appointment_id: str,
        clock: str,
    ) -> None:
        result = _run(
            lambda channel: scheduling.cancel_appointment(
                channel,
                self.settings,
                session_id=self.session_id,
                patient_id=patient.patient_id,
                appointment_id=appointment_id,
                expected_starts_at=self.at(clock),
                expected_practitioner_id=practitioner.id,
                local_now=self.local_now(),
            )
        )
        assert isinstance(result, scheduling.ChangeApplied), result

    def open(self, context: BrowserContext) -> "ClinicPage":
        """Open the app as this session's visitor, on the patient's chat in both panes.

        The chat is chosen explicitly in each pane rather than left to the app's own
        default, which picks by recency.
        """
        context.add_cookies(
            [{"name": COOKIE_NAME, "value": self.session_id, "url": FRONTEND_URL}]
        )
        page = context.new_page()
        page.goto("/")
        view = ClinicPage(page, self)
        view.patient_pane.locator(
            f'[data-testid="chat-list-item"][data-chat-id="{self.patient.chat_id}"]'
        ).get_by_role("button").first.click()
        view.conversation().click()
        expect(view.staff_thread).to_be_visible()
        return view


def _mint_chat(http: httpx.Client, settings: Settings) -> Patient:
    """Create one chat - and so one patient - in the client's session."""
    response = http.post("/chats")
    response.raise_for_status()
    chat_id = response.json()["id"]
    session_id = http.cookies[COOKIE_NAME]
    # Idempotent: answers with the patient `POST /chats` already provisioned, and
    # creates it only if that provisioning failed - the same repair a turn makes.
    provisioned = _run(
        lambda channel: scheduling.ensure_session_provisioned(
            channel, settings, session_id=session_id, chat_id=chat_id
        )
    )
    return Patient(chat_id, provisioned.patient.id, provisioned.patient.full_name)


@pytest.fixture
def clinic(clinic_zone: str) -> Iterator[Clinic]:
    """A new session holding two patients and no practitioners.

    The session's first `POST /chats` mints it (and plants the starter FAQ corpus); the
    second chat is the patient under test. It is created last so it is also what the
    app opens first, although `Clinic.open` selects it explicitly rather than relying
    on that. The default practitioner a new session is given is deleted, so the roster
    holds exactly what the case adds - a stray dentist would turn "a dentist" into a
    question about which one.
    """
    settings = get_settings()
    with httpx.Client(base_url=CHAT_URL, timeout=30) as http:
        other_patient = _mint_chat(http, settings)
        patient = _mint_chat(http, settings)
        session_id = http.cookies[COOKIE_NAME]
        roster = http.get("/console/practitioners")
        roster.raise_for_status()
        for practitioner in roster.json():
            http.delete(
                f"/console/practitioners/{practitioner['id']}"
            ).raise_for_status()
        try:
            yield Clinic(
                zone=ZoneInfo(clinic_zone),
                http=http,
                settings=settings,
                session_id=session_id,
                patient=patient,
                other_patient=other_patient,
            )
        finally:
            # Housekeeping only: a session nobody holds a cookie for is unreachable
            # whether or not it is deleted, so a stack with no admin secret keeps it.
            secret = settings.ADMIN_SECRET.strip()
            if secret:
                httpx.delete(
                    f"{CHAT_URL}/admin/sessions/{session_id}",
                    headers={"x-admin-secret": secret},
                    timeout=30,
                )


@dataclass
class ClinicPage:
    """The two panes of one open page, and the moves every case makes in them."""

    page: Page
    clinic: Clinic

    @property
    def patient_pane(self) -> Locator:
        return self.page.get_by_test_id("patient-pane")

    @property
    def staff_pane(self) -> Locator:
        return self.page.get_by_test_id("staff-pane")

    @property
    def staff_thread(self) -> Locator:
        return self.staff_pane.get_by_test_id("staff-thread")

    def conversation(self) -> Locator:
        """The patient's row in the staff console's conversation list, by name."""
        return self.staff_pane.get_by_test_id("staff-conversation").filter(
            has_text=self.clinic.patient.full_name
        )

    def ask(self, message: str) -> Locator:
        """Send one patient message and return the reply as the staff thread shows it.

        The staff thread is read rather than the patient pane because it renders the
        stored reply whole, while the patient pane types it out at a reading pace; the
        patient pane is then required to end up showing the same text, so the reply
        the patient sees is checked too.
        """
        self.patient_pane.get_by_label("question").fill(message)
        self.patient_pane.get_by_label("question").press("Enter")
        reply = self.staff_thread.locator(
            '[data-testid="message"][data-sender="assistant"]'
        )
        expect(reply).to_have_count(1, timeout=REPLY_TIMEOUT_MS)
        text = reply.get_by_test_id("message-content").inner_text()
        expect(
            self.patient_pane.locator(
                '[data-testid="message"][data-sender="assistant"] '
                '[data-testid="message-content"]'
            )
        ).to_have_text(text, timeout=REPLY_TIMEOUT_MS)
        return reply

    def open_evidence(self, reply: Locator) -> Locator:
        """Open the reply's evidence marker; return the marker."""
        marker = reply.get_by_test_id("outcome-marker")
        marker.click()
        expect(marker).to_have_attribute("aria-expanded", "true")
        return marker

    def week_of(self, practitioner: Practitioner) -> Locator:
        """Open Practitioners and show `practitioner`'s coming bookings."""
        self.staff_pane.get_by_role("tab", name="Practitioners").click()
        block = self.staff_pane.get_by_test_id("practitioner").filter(
            has_text=practitioner.full_name
        )
        block.get_by_test_id("bookings-toggle").click()
        week = block.get_by_test_id("practitioner-week")
        expect(week.get_by_test_id("region-loading")).to_have_count(0)
        return week


def booked_starts(week: Locator) -> Locator:
    return week.get_by_test_id("week-appointment")


def starting_at(*clocks: str) -> list[re.Pattern[str]]:
    """Expected `week-appointment` texts, one per start, in order.

    A line reads "<start>-<end> <patient>" (joined by an en dash), so each is matched
    by its opening clock alone.
    """
    return [re.compile(rf"^{re.escape(clock)}\b") for clock in clocks]


_CLOCK = re.compile(
    r"\b(?P<h12>\d{1,2})(?::(?P<m12>\d{2}))?\s*(?P<half>[ap])\.?\s?m\b\.?"
    r"|\b(?P<h24>\d{1,2}):(?P<m24>\d{2})\b"
    r"|\b(?P<noon>noon|midday)\b",
    re.IGNORECASE,
)


def clocks_named(text: str) -> set[time]:
    """Every time of day `text` names, however it is written.

    "10:00", "10am", "10 a.m.", "2:30 PM" and "noon" are all read, so a reply is held
    to which times it names rather than to how the model chose to write them.
    """
    found: set[time] = set()
    for match in _CLOCK.finditer(text):
        if match["noon"]:
            found.add(time(12))
        elif match["h24"] is not None:
            found.add(time(int(match["h24"]), int(match["m24"])))
        else:
            hour = int(match["h12"]) % 12 + (12 if match["half"].lower() == "p" else 0)
            found.add(time(hour, int(match["m12"] or 0)))
    return found


def clock(value: str) -> time:
    return time.fromisoformat(value)


def plus_minutes(value: str, minutes: int) -> str:
    moved = datetime.combine(date.min, clock(value)) + timedelta(minutes=minutes)
    return moved.strftime("%H:%M")
