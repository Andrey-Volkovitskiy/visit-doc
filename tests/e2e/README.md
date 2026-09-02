# tests/e2e

Reserved for full-stack end-to-end tests: a browser against `services/frontend`, talking to a
running `chat` and `scheduler`. Not yet populated — the tier is scheduled for Phase 2, once 1e has
settled the verdict/citation contract those tests would assert on (see `docs/ROADMAP.md`). Until
then `make test-e2e` exits with pytest's "no tests ran" status, which is expected.

Two things about this tier that differ from the others, both in `docs/testing-strategy.md`:

- **It is the one tier allowed to reach the live Claude and Voyage APIs**, which is why it never
  joins the per-push gate, and why its assertions are on structure — a citation arrived, a stream
  was cancelled, a booking reached the scheduler's database — never on the model's wording.
- **It is not a port of a feature's `quickstart.md`.** Those stay manual: their value is that a
  person walks them before a demo. This tier aims at the one class of defect no other tier can
  reach — frontend state across time, where a pane, the poll and a reload disagree.

See `docs/testing-strategy.md` for the full testing convention.
