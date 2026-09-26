# tests/e2e

Full-stack end-to-end tests: a real Chromium, driven by `pytest-playwright`, against
`services/frontend` talking to a running `chat` and `scheduler`. Filled in Phase 3b (see
`docs/ROADMAP.md`), after 1e and 1h had settled the verdict/citation contract these tests assert on
and 3a's design pass had settled the DOM they select by.

## The journeys

`test_journeys.py` holds eight, one per feature a demo is judged on. Each is driven from the
patient pane and checked from the staff console as well as from the reply:

| Journey | Prestate | Checked |
|---|---|---|
| Free slots today | a dentist, two of today's slots booked by another patient | the reply names every free start and neither booked one |
| My appointments | one standing and one cancelled appointment of the patient's, one of another patient's | the reply names the standing one alone |
| Booking | a GP | a blue marker holding a `book`/`done` act; the GP's week shows the booking |
| Rescheduling | the patient's appointment with a GP | a blue marker holding a `reschedule`/`done` act; the week shows the new time only |
| Cancelling | two of the patient's appointments with a GP | a blue marker holding a `cancel`/`done` act; the week shows the other one only |
| FAQ answer | the starter corpus | a blue marker whose citation is the entry that answers it |
| FAQ gap | the starter corpus | the abstention reply, a red marker, the conversation emphasized, the `corpus_could_not_answer` mark |
| Asking for a person | — | the hand-off reply, a red marker, the conversation emphasized, the `patient_asked_for_person` mark |

## Running it

```bash
make services-up      # LOG_FORMAT is irrelevant here; `make migrate` first if the dev DBs are behind
make test-e2e
```

The tier checks the stack is answering and that the live keys are set before any browser opens, and
**fails** when either is missing — a skipped e2e run reads exactly like a passing one. A failing
journey leaves its Playwright trace under `.run/e2e/` (`uv run playwright show-trace <zip>`).

`make test-e2e ARGS="-k booking --headed"` passes arguments through to pytest. Before running,
the target sources `.run/e2e.env` if it exists (gitignored, shell syntax) — the place for one
machine's browser setup, so it is set once rather than typed on every run.

Environment knobs, all optional:

- `E2E_FRONTEND_URL` / `E2E_CHAT_URL` — default `http://localhost:5173` / `http://localhost:8000`.
- `PLAYWRIGHT_CHROMIUM_EXECUTABLE` — a Chromium to launch instead of the build this `playwright`
  release expects (`uv run playwright install chromium` fetches that one).
- Every `pytest-playwright` option works as usual: `--headed`, `--slowmo 300`, `-k booking`.

## How it stays honest

- **It is the one tier allowed to reach the live Claude and Voyage APIs**, which is why it never
  joins the per-push gate (see `docs/testing-strategy.md`). A full run is eight live turns and eight
  starter-corpus plantings, a couple of minutes end to end.
- **It asserts on structure, not wording**: the `data-testid` hooks and data attributes listed in
  `services/frontend/.claude/CLAUDE.md` (`data-outcome-state`, `data-emphasized`, `booking-act`'s
  `data-operation`/`data-outcome`, `data-mark`), the constant a handed-off or fully abstained turn
  replies with (imported from `chat`, never restated), and the corpus entry a citation must come
  from. The two journeys whose claim lives only in a model-written reply — free slots and the
  patient's own appointments — are held to *which times of day* the reply names, read however it
  writes them ("13:00", "1pm", "noon"). Each plants its prestate so no time it must leave out can
  appear as the end of one it must name.
- **It isolates by session, not by database.** Nothing here starts a service or truncates a table:
  each journey mints a fresh session through `POST /chats`, plants its prestate through the
  product's own surfaces (the console's practitioner API, and the gRPC client the booking tools
  use), and deletes the session through `/admin` afterwards when `ADMIN_SECRET` is set. Every read
  the app makes is scoped to a session, so a journey can see nothing another one planted — or
  anything a person using the same stack has.
- **It chooses the visitor's clock.** This system has no timezone; every time is the visitor's own
  wall clock. The browser context is given a fixed-offset `Etc/GMT` zone in which it is 07:xx when
  the run starts, so "today" always has a whole working day ahead of it whatever hour the suite is
  run at, and the prestate is planted on that same clock.
- **It is not a port of a feature's `quickstart.md`.** Those stay manual: their value is that a
  person walks them before a demo.
