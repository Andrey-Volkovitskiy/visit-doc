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
from urllib.parse import quote

import aiohttp
import yarl

from chat.core.logging import get_logger

# The scheduler's own header for the session that authorizes a request. Declared here
# rather than imported: the two services share no code, and importing across them would
# be a dependency this boundary exists to avoid.
_SESSION_HEADER = "X-Session-Id"

# Long enough for a schedule rewrite against a warm database, short enough that a
# console form does not appear to hang. There is no second attempt behind it.
_TIMEOUT_SECONDS = 5

# The two segments a URL parser reads as a move up or sideways rather than as a name.
# Nothing else made of dots means anything to it, so nothing else needs escaping.
_RELATIVE_SEGMENTS = frozenset({".", ".."})


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


def path_segment(value: str) -> str:
    """Return `value` percent-encoded so it can only ever be one path segment.

    Every caller-supplied value interpolated into a `forward` path goes through this.
    An id taken from a request URL is chosen by whoever sent the request, and left as
    it arrived it does not stay a segment: `?` opens an attacker's query string on an
    internal write endpoint, `#` truncates the path before the id, and `..` addresses
    the collection above it - all decided by the URL parser inside the HTTP client,
    long after this code thought it had built a path.
    """
    encoded = quote(value, safe="")
    # `quote` leaves `.` alone, since it is unreserved, so a value that is exactly `.`
    # or `..` comes back unchanged and still means to a URL parser what it meant.
    # Percent-encoding those dots is what makes the segment a name again - and it stays
    # one only because `forward` sends the path as written rather than as re-parsed.
    if encoded in _RELATIVE_SEGMENTS:
        return encoded.replace(".", "%2E")
    return encoded


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
            transport at something else. Any value the caller interpolated into it
            must have come through `path_segment` first.
        session_id: Read from the request's cookie by the caller. It never reaches the
            browser, and never appears in a response.

    Raises:
        ValueError: `path` is not a path, so nothing was sent. This is a caller bug,
            not an answer about the scheduler.
        SchedulerUnreachableError: the request could not be sent, so nothing changed.
        SchedulerTimeoutError: no answer arrived within the deadline; what happened is
            unknown.

    Makes exactly one attempt. Any status the scheduler returns - including a refusal -
    is a successful round trip and comes back in the result, because a refusal is an
    answer this proxy has no business rewriting.
    """
    _reject_anything_that_is_not_a_path(path)
    # Built as a URL here, not left a string for aiohttp to parse: the parser rewrites
    # what it is handed, and `%2E%2E` decoded back to `..` and then resolved away is a
    # request to a different endpoint than the one this composed. `encoded=True` says
    # the string is already a URL and is to be sent as written - which it is, because
    # `base_url` is configuration and every caller-supplied part of `path` came through
    # `path_segment`.
    url = yarl.URL(f"{base_url.rstrip('/')}{path}", encoded=True)
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


def _reject_anything_that_is_not_a_path(path: str) -> None:
    """Refuse to send a `path` the HTTP client would read as more than a path.

    Raises:
        ValueError: `path` is not an absolute path of already-encoded segments.

    This is the transport keeping its own promise instead of trusting every present and
    future caller to remember `path_segment`. A caller that forgets fails here, with
    nothing sent and the mistake named, rather than silently addressing an endpoint and
    a query string that whoever supplied the value chose.
    """
    if not path.startswith("/"):
        raise ValueError(f"scheduler path must be absolute: {path!r}")
    if "?" in path or "#" in path:
        raise ValueError(f"scheduler path must carry no query or fragment: {path!r}")
    if any(segment in _RELATIVE_SEGMENTS for segment in path.split("/")):
        raise ValueError(f"scheduler path must have no relative segment: {path!r}")


async def _read_json(response: aiohttp.ClientResponse) -> Any | None:
    """Return the response's parsed body, or None when it has none to parse.

    A body that is not JSON is an answer this proxy cannot relay, so it is reported as
    absent rather than as text a console would render as a refusal reason.
    """
    try:
        return await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError):
        return None
