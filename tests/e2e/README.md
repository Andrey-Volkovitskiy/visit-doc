# tests/e2e

Reserved for full-stack end-to-end tests: a browser against `services/frontend`, talking to a
running `chat` and `scheduler`. Not yet populated — the tier is scheduled for Phase 3b, after 1e and
1h have settled the verdict/citation contract those tests would assert on and 3a's design pass has
settled the DOM they would select by (see `docs/ROADMAP.md`). Until then `make test-e2e` exits with
pytest's "no tests ran" status, which is expected.

Two things about this tier that differ from the others, both in `docs/testing-strategy.md`:

- **It is the one tier allowed to reach the live Claude and Voyage APIs**, which is why it never
  joins the per-push gate, and why its assertions are on structure — a citation arrived, a stream
  was cancelled, a booking reached the scheduler's database — never on the model's wording.
- **It is not a port of a feature's `quickstart.md`.** Those stay manual: their value is that a
  person walks them before a demo. This tier holds eight journeys over the features a demo is
  judged on — free slots, the patient's own appointments, booking, rescheduling, cancelling, an FAQ
  answer, an FAQ gap and a request for a person — each checked from the patient's reply and from
  the staff console (the list is in `docs/ROADMAP.md`, Phase 3b).

See `docs/testing-strategy.md` for the full testing convention.
