# Contract: A practitioner's upcoming bookings

> **Amended after shipping.** This was a *week*: the console asked for seven local days and the
> list simply ended there, so a booking made for next spring was invisible with nothing on screen
> saying anything had been left out. There is no far end now. The console reads forward from the
> viewer's clock, capped at a page of **20**, and `has_more` says when the page was cut. The file
> keeps its name so the spec's own cross-references still resolve.

Two HTTP surfaces: the console's, which the browser calls, and the scheduler's, which only the
chat service calls. See research R1–R4.

## Console: `GET /console/practitioners/{practitioner_id}/appointments`

**Query**

- `local_now` (required): an offset-free local datetime, `YYYY-MM-DDTHH:MM:SS`, as sent by
  `localNow()` in `src/lib/chatStream.ts`.

**Session**: the `visitdoc_session_id` cookie. It is never echoed back.

**Window**, set by the route from `local_now`:

- `ends_after = local_now`
- no far end: every standing appointment from `local_now` onward, however far ahead
- `limit = 20`, the console's page

| Status | Body | When |
|---|---|---|
| 200 | `{"appointments": [PractitionerAppointment, …], "has_more": bool}` in start order, possibly `[]` | The practitioner is in this session. `[]` means nobody is booked. `has_more` is true when the page was cut short. |
| 401 | `{"detail": "no session"}` | No cookie. Nothing is sent to the scheduler. |
| 404 | the scheduler's body, relayed | The practitioner does not exist in this session: deleted, or another session's id. |
| 422 | a validation error | `local_now` is missing, malformed, or carries an offset. Nothing is sent. A clock near the end of the calendar is no longer refused: no window is computed from it. |
| 503 | `{"detail": "scheduling is unavailable; the appointments could not be read"}` | The scheduler could not be reached. |
| 504 | `{"detail": "scheduling did not answer; the appointments could not be read"}` | No answer within the deadline. |

`PractitionerAppointment` is:

```json
{"id": "01K…", "patient_full_name": "Leo Tolstoy",
 "starts_at": "2026-09-24T14:00:00", "ends_at": "2026-09-24T15:00:00"}
```

The times are naive local times, in the same format as every other scheduling time.

**Guarantees**

- Only `status = standing` appointments are returned. An appointment with `ends_at <= local_now`
  never appears. Nothing is excluded for starting too far ahead.
- At most 20 are returned, the soonest first, and `has_more` is read from a row beyond the page
  rather than from its length — a page of exactly 20 that is complete and one that was cut short
  are otherwise indistinguishable.
- An appointment already under way at `local_now` does appear.
- Every returned row belongs to this session and this practitioner, enforced by the scheduler's
  `WHERE`, not by filtering here.
- The route makes one attempt and no retry. The browser's next poll tick is the retry.

## Scheduler: `GET /practitioners/{practitioner_id}/appointments`

**Header**: `X-Session-Id` (required; 401 when missing, per `api/dependencies.py`).

**Query**: `ends_after` (required, naive local datetime), `starts_before` (optional; omitted means
no far end), and `limit` (required, 1–500). 422 when a datetime is malformed or carries an offset,
when `starts_before <= ends_after`, or when `limit` is missing or out of range.

| Status | Body |
|---|---|
| 200 | `{"appointments": [PractitionerAppointment, …], "has_more": bool}`, ordered by `starts_at`, then `id` |
| 404 | `{"detail": "practitioner not found"}`, the same wording `PATCH`/`DELETE` use for a miss |
| 422 | a validation error |

The query is a single statement with every given predicate in its `WHERE`: session, practitioner,
standing status, and each bound that was supplied (data-model.md). It reads `limit + 1` rows and
returns `limit`, which is what `has_more` is read from. The old "no paging and no cap" assumption —
that working hours bounded the result — died with the window: an unbounded calendar needs the cap.

## Transport: `scheduler_rest.forward`

The signature gains `query: Mapping[str, str] | None = None`. The query is attached with
`yarl.URL.with_query` after the path guard has run. The guard's behaviour is unchanged, and `?`
in `path` is still refused.
