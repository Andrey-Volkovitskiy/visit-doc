# Contract: A practitioner's week

Two HTTP surfaces: the console's, which the browser calls, and the scheduler's, which only the
chat service calls. See research R1–R4.

## Console: `GET /console/practitioners/{practitioner_id}/appointments`

**Query**

- `local_now` (required): an offset-free local datetime, `YYYY-MM-DDTHH:MM:SS`, as sent by
  `localNow()` in `src/lib/chatStream.ts`.

**Session**: the `visitdoc_session_id` cookie. It is never echoed back.

**Window** (FR-003), computed by the route from `local_now`:

- `ends_after = local_now`
- `starts_before = local_now.date() + 7 days, 00:00`

| Status | Body | When |
|---|---|---|
| 200 | `{"appointments": [PractitionerAppointment, …]}` in start order, possibly `[]` | The practitioner is in this session. `[]` means nobody is booked in the window. |
| 401 | `{"detail": "no session"}` | No cookie. Nothing is sent to the scheduler. |
| 404 | the scheduler's body, relayed | The practitioner does not exist in this session: deleted, or another session's id. |
| 422 | a validation error | `local_now` is missing, malformed, or carries an offset. Nothing is sent. |
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
  never appears, and neither does one with `starts_at >= starts_before`.
- An appointment already under way at `local_now` does appear.
- Every returned row belongs to this session and this practitioner, enforced by the scheduler's
  `WHERE`, not by filtering here.
- The route makes one attempt and no retry. The browser's next poll tick is the retry.

## Scheduler: `GET /practitioners/{practitioner_id}/appointments`

**Header**: `X-Session-Id` (required; 401 when missing, per `api/dependencies.py`).

**Query**: `ends_after` and `starts_before` (both required, naive local datetimes; 422 when either
is malformed or carries an offset, or when `starts_before <= ends_after`).

| Status | Body |
|---|---|
| 200 | `{"appointments": [PractitionerAppointment, …]}`, ordered by `starts_at`, then `id` |
| 404 | `{"detail": "practitioner not found"}`, the same wording `PATCH`/`DELETE` use for a miss |
| 422 | a validation error |

The query is a single statement with all five predicates in its `WHERE`: session, practitioner,
standing status, and both bounds (data-model.md). There is no paging and no cap (spec
Assumptions: bounded by working hours).

## Transport: `scheduler_rest.forward`

The signature gains `query: Mapping[str, str] | None = None`. The query is attached with
`yarl.URL.with_query` after the path guard has run. The guard's behaviour is unchanged, and `?`
in `path` is still refused.
