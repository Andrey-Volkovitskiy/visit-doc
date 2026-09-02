"""The HTTP half of the scheduling boundary: practitioner administration, proxied.

Everything else across this boundary goes over gRPC, and `clients/scheduling.py` is the
only module that speaks it. This is the only module that speaks HTTP, and it exists for
one reason: the console needs the human-facing practitioner CRUD that the scheduler's
REST surface already *is* - its defaults, its typed refusals, its error shapes - and
re-declaring those as three new RPCs would put a second copy of one contract across the
boundary, so every future rule change would land in two places or diverge in one.

The browser cannot call that surface itself: it expects the session as an explicit
header, and the session lives in a cookie the page is not allowed to read. So something
server-side carries it, and this is that something.

**One attempt, no retry.** A console form must not silently create two practitioners
because the first response was slow. An outcome nobody knows is reported as unknown,
which is the rule a lost write already imposes everywhere else in this system.
"""

from dataclasses import dataclass
from typing import Any

import aiohttp

from chat.core.logging import get_logger

# The scheduler's own header for the session that authorizes a request. Declared here
# rather than imported: the two services share no code, and importing across them would
# be a dependency this boundary exists to avoid.
_SESSION_HEADER = "X-Session-Id"

# Long enough for a schedule rewrite against a warm database, short enough that a
# console form does not appear to hang. There is no second attempt behind it.
_TIMEOUT_SECONDS = 5


class SchedulerUnreachableError(Exception):
    """The request never reached the scheduler, so nothing was changed.

    Distinct from `SchedulerTimeoutError` because the two are different facts, and
    only one of them can be reported to a caller as "nothing happened".
    """


class SchedulerTimeoutError(Exception):
    """The scheduler stopped answering, so what it did is not known.

    A deadline is the caller's, not the callee's: it expiring means the answer did not
    arrive, not that the work did not happen.
    """


@dataclass(frozen=True)
class ProxiedResponse:
    """One scheduler response, as it will be relayed to the console.

    `body` is the parsed JSON the scheduler returned, or None for a response with no
    body at all (its `DELETE` answers `204`). Nothing here interprets it: the whole
    point of the proxy is that the rule and its wording belong to the service that
    owns them.
    """

    status_code: int
    body: Any | None


async def forward(
    http: aiohttp.ClientSession,
    base_url: str,
    method: str,
    path: str,
    session_id: str,
    body: Any | None = None,
) -> ProxiedResponse:
    """Send one request to the scheduler's practitioner API and relay what came back.

    Args:
        path: The scheduler-side path, e.g. `/practitioners` or
            `/practitioners/{id}` - never a URL, so a caller cannot redirect this
            transport at something else.
        session_id: Read from the request's cookie by the caller. It never reaches the
            browser, and never appears in a response.

    Raises:
        SchedulerUnreachableError: the request could not be sent, so nothing changed.
        SchedulerTimeoutError: no answer arrived within the deadline; what happened is
            unknown.

    Makes exactly one attempt. Any status the scheduler returns - including a refusal -
    is a successful round trip and comes back in the result, because a refusal is an
    answer this proxy has no business rewriting.
    """
    url = f"{base_url.rstrip('/')}{path}"
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    try:
        async with http.request(
            method,
            url,
            json=body,
            headers={_SESSION_HEADER: session_id},
            timeout=timeout,
        ) as response:
            payload = None if response.status == 204 else await _read_json(response)
            return ProxiedResponse(status_code=response.status, body=payload)
    except TimeoutError as exc:
        get_logger().warning(
            "scheduling.http_timeout", method=method, path=path, error_detail=str(exc)
        )
        raise SchedulerTimeoutError(str(exc)) from exc
    except aiohttp.ClientError as exc:
        get_logger().warning(
            "scheduling.http_unavailable",
            method=method,
            path=path,
            error_detail=str(exc),
        )
        raise SchedulerUnreachableError(str(exc)) from exc


async def _read_json(response: aiohttp.ClientResponse) -> Any | None:
    """Return the response's parsed body, or None when it has none to parse.

    A body that is not JSON is an answer this proxy cannot relay, so it is reported as
    absent rather than as text a console would render as a refusal reason.
    """
    try:
        return await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError):
        return None
