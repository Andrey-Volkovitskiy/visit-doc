import { act, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PractitionerWeek } from "../src/components/PractitionerWeek";
import * as consoleApi from "../src/lib/consoleApi";
import {
  PractitionerWeekError,
  type PractitionerAppointment,
  type PractitionerAppointmentPage,
} from "../src/lib/consoleApi";

const ID = "01PRACT0000000000000000000";
const NAME = "Dr. Ada Lovelace";

function appointment(
  patient: string,
  startsAt: string,
  endsAt: string,
): PractitionerAppointment {
  return {
    id: `01APPT-${patient}-${startsAt}`,
    patient_full_name: patient,
    starts_at: startsAt,
    ends_at: endsAt,
  };
}

/** One page as the route answers it: these appointments, and nothing beyond them. */
function page(
  appointments: PractitionerAppointment[],
  hasMore = false,
): PractitionerAppointmentPage {
  return { appointments, hasMore };
}

/** A read the test settles by hand, so the component's in-between states are visible. */
interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function reads() {
  return vi.mocked(consoleApi.fetchPractitionerWeek);
}

function renderWeek(pollTick = 0, readTimeoutMs?: number) {
  const result = render(
    <PractitionerWeek
      practitionerId={ID}
      practitionerName={NAME}
      pollTick={pollTick}
      readTimeoutMs={readTimeoutMs}
    />,
  );
  return {
    ...result,
    tick: (next: number) =>
      result.rerender(
        <PractitionerWeek
          practitionerId={ID}
          practitionerName={NAME}
          pollTick={next}
          readTimeoutMs={readTimeoutMs}
        />,
      ),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("PractitionerWeek: what it shows", () => {
  it("says it is loading until the first answer arrives, and nothing else", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockReturnValue(
      deferred<PractitionerAppointmentPage>().promise,
    );

    renderWeek();

    const week = screen.getByTestId("practitioner-week");
    const loading = within(week).getByTestId("region-loading");
    expect(loading).toHaveAttribute("data-region", "practitioner-week");
    // Loading is its own state: not an empty week, and not a failure.
    expect(within(week).queryByTestId("week-empty")).toBeNull();
    expect(within(week).queryByTestId("week-error")).toBeNull();
    expect(within(week).queryByTestId("week-day")).toBeNull();
  });

  it("reads this practitioner's week, sending the browser's local now", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockResolvedValue(page([]));

    renderWeek();

    await waitFor(() => expect(reads()).toHaveBeenCalledTimes(1));
    const [id, now] = reads().mock.calls[0];
    expect(id).toBe(ID);
    // Offset-free local wall-clock time, as `localNow()` builds it.
    expect(now).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/);
  });

  it("groups appointments under their local day, days in date order, each day in start order", async () => {
    // Handed over out of order, so the grouping is shown to come from the times and not
    // from the order the list happened to arrive in.
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockResolvedValue(
      page([
        appointment("Anna Karenina", "2026-09-27T10:00:00", "2026-09-27T10:30:00"),
        appointment("Leo Tolstoy", "2026-09-24T14:00:00", "2026-09-24T15:00:00"),
        appointment("Ivan Ilyich", "2026-09-24T09:30:00", "2026-09-24T10:00:00"),
      ]),
    );

    renderWeek();

    const days = await screen.findAllByTestId("week-day");
    expect(days).toHaveLength(2);
    expect(days[0]).toHaveTextContent("Thursday");
    expect(days[0]).toHaveTextContent("24 September");
    expect(days[1]).toHaveTextContent("Sunday");
    expect(days[1]).toHaveTextContent("27 September");

    const thursday = within(days[0]).getAllByTestId("week-appointment");
    expect(thursday.map((a) => a.textContent)).toEqual([
      expect.stringContaining("Ivan Ilyich"),
      expect.stringContaining("Leo Tolstoy"),
    ]);
    // Start and end, as local wall-clock times read straight off the wire - no
    // timezone shift, since there is no timezone anywhere in this system.
    expect(thursday[0]).toHaveTextContent("09:30");
    expect(thursday[0]).toHaveTextContent("10:00");
    expect(thursday[1]).toHaveTextContent("14:00");
    expect(thursday[1]).toHaveTextContent("15:00");

    const sunday = within(days[1]).getAllByTestId("week-appointment");
    expect(sunday).toHaveLength(1);
    expect(sunday[0]).toHaveTextContent("Anna Karenina");
  });

  it("states plainly that nobody is booked, naming the practitioner, rather than showing an empty list", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockResolvedValue(page([]));

    renderWeek();

    const empty = await screen.findByTestId("week-empty");
    expect(empty).toHaveTextContent(`Nobody is booked with ${NAME}.`);
    expect(screen.queryByTestId("week-day")).toBeNull();
    expect(screen.queryByTestId("week-error")).toBeNull();
  });

  it("says so when the route stopped at its page with more still booked", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockResolvedValue(
      page(
        [appointment("Leo Tolstoy", "2026-09-24T14:00:00", "2026-09-24T15:00:00")],
        true,
      ),
    );

    renderWeek();

    const more = await screen.findByTestId("week-more");
    // Words as well as the glyph, and no count: the route stopped reading at its page,
    // so how many more there are is a thing nothing here has counted.
    expect(more).toHaveTextContent("more are booked");
    expect(more.textContent).not.toMatch(/\d/);
  });

  it("says nothing of the kind when the page held all of them", async () => {
    // The ellipsis is a claim about the clinic's schedule, and it is the route's claim
    // to make: a full-looking list is not evidence that anything was left out.
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockResolvedValue(
      page([appointment("Leo Tolstoy", "2026-09-24T14:00:00", "2026-09-24T15:00:00")]),
    );

    renderWeek();

    await screen.findByTestId("week-appointment");
    expect(screen.queryByTestId("week-more")).toBeNull();
  });

  it("says the appointments could not be read when the scheduler could not answer", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockRejectedValue(
      new PractitionerWeekError("unreadable"),
    );

    renderWeek();

    const error = await screen.findByTestId("week-error");
    expect(error).toHaveTextContent("The appointments could not be read.");
    // A failure must never read as "nobody is booked".
    expect(screen.queryByTestId("week-empty")).toBeNull();
  });

  it("says the practitioner no longer exists on a not-found, not that the read failed", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockRejectedValue(
      new PractitionerWeekError("not_found"),
    );

    renderWeek();

    const error = await screen.findByTestId("week-error");
    expect(error).toHaveTextContent("This practitioner no longer exists.");
    expect(error).not.toHaveTextContent("could not be read");
    expect(screen.queryByTestId("week-empty")).toBeNull();
  });

  it("treats a failure of any other shape as unreadable", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockRejectedValue(
      new Error("something nobody planned for"),
    );

    renderWeek();

    expect(await screen.findByTestId("week-error")).toHaveTextContent(
      "The appointments could not be read.",
    );
  });

  it("offers no control of any kind, and the patient's name is plain text", async () => {
    // FR-009: the list is read-only, and a name is not a way into a conversation.
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockResolvedValue(
      page([
        appointment("Leo Tolstoy", "2026-09-24T14:00:00", "2026-09-24T15:00:00"),
      ]),
    );

    renderWeek();

    const week = screen.getByTestId("practitioner-week");
    await within(week).findByTestId("week-appointment");
    expect(within(week).queryAllByRole("button")).toHaveLength(0);
    expect(within(week).queryAllByRole("link")).toHaveLength(0);
    expect(week.querySelector("a, button, input, select, textarea")).toBeNull();
  });
});

describe("PractitionerWeek: keeping an open list current", () => {
  it("re-reads on every poll tick", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockResolvedValue(page([]));

    const { tick } = renderWeek(5);
    await screen.findByTestId("week-empty");
    expect(reads()).toHaveBeenCalledTimes(1);

    tick(6);
    await waitFor(() => expect(reads()).toHaveBeenCalledTimes(2));
    tick(7);
    await waitFor(() => expect(reads()).toHaveBeenCalledTimes(3));
  });

  it("shows what a refresh found, replacing what was there", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek")
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce(
          page([
            appointment("Leo Tolstoy", "2026-09-24T14:00:00", "2026-09-24T15:00:00"),
          ]),
        );

    const { tick } = renderWeek();
    await screen.findByTestId("week-empty");

    tick(1);

    expect(await screen.findByTestId("week-appointment")).toHaveTextContent(
      "Leo Tolstoy",
    );
    expect(screen.queryByTestId("week-empty")).toBeNull();
  });

  it("does not stack a second read on one still in flight", async () => {
    const first = deferred<PractitionerAppointmentPage>();
    vi.spyOn(consoleApi, "fetchPractitionerWeek")
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue(page([]));

    const { tick } = renderWeek();
    await waitFor(() => expect(reads()).toHaveBeenCalledTimes(1));

    tick(1);
    tick(2);
    // Give any read the ticks might have started a chance to be issued.
    await act(async () => {
      await Promise.resolve();
    });
    expect(reads()).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve(page([]));
      await first.promise;
    });
    await screen.findByTestId("week-empty");

    // Once the slot is free, the next tick reads again.
    tick(3);
    await waitFor(() => expect(reads()).toHaveBeenCalledTimes(2));
  });

  it("drops an answer that arrives after a newer one, rather than rewinding the list", async () => {
    // The first read never settles within its deadline, so the list says it could not
    // be read and the next tick is free to ask again. When that first answer finally
    // turns up, it describes a week older than the one on screen.
    const late = deferred<PractitionerAppointmentPage>();
    vi.spyOn(consoleApi, "fetchPractitionerWeek")
      .mockReturnValueOnce(late.promise)
      .mockResolvedValueOnce(
          page([
            appointment("Leo Tolstoy", "2026-09-24T14:00:00", "2026-09-24T15:00:00"),
          ]),
        );

    const { tick } = renderWeek(0, 20);
    expect(await screen.findByTestId("week-error")).toHaveTextContent(
      "The appointments could not be read.",
    );

    tick(1);
    expect(await screen.findByTestId("week-appointment")).toHaveTextContent(
      "Leo Tolstoy",
    );

    await act(async () => {
      late.resolve(
          page([
            appointment("Anna Karenina", "2026-09-25T10:00:00", "2026-09-25T10:30:00"),
          ]),
        );
      await late.promise;
    });

    const shown = screen.getAllByTestId("week-appointment");
    expect(shown).toHaveLength(1);
    expect(shown[0]).toHaveTextContent("Leo Tolstoy");
    expect(screen.queryByText(/Anna Karenina/)).toBeNull();
  });

  it("ends a read that outlives its deadline, so the next tick can ask again", async () => {
    let signal: AbortSignal | undefined;
    vi.spyOn(consoleApi, "fetchPractitionerWeek")
      .mockImplementationOnce((_id, _now, s) => {
        signal = s;
        return deferred<PractitionerAppointmentPage>().promise;
      })
      .mockResolvedValue(page([]));

    const { tick } = renderWeek(0, 20);
    await screen.findByTestId("week-error");
    expect(signal?.aborted).toBe(true);

    tick(1);
    await waitFor(() => expect(reads()).toHaveBeenCalledTimes(2));
    await screen.findByTestId("week-empty");
  });

  it("shows the failure when a refresh fails after a good read, not the list it can no longer vouch for", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek")
      .mockResolvedValueOnce(
          page([
            appointment("Leo Tolstoy", "2026-09-24T14:00:00", "2026-09-24T15:00:00"),
          ]),
        )
      .mockRejectedValueOnce(new PractitionerWeekError("unreadable"));

    const { tick } = renderWeek();
    await screen.findByTestId("week-appointment");

    tick(1);

    expect(await screen.findByTestId("week-error")).toHaveTextContent(
      "The appointments could not be read.",
    );
    expect(screen.queryByTestId("week-appointment")).toBeNull();
    expect(screen.queryByText(/Leo Tolstoy/)).toBeNull();
  });

  it("recovers on the next tick after a failure", async () => {
    vi.spyOn(consoleApi, "fetchPractitionerWeek")
      .mockRejectedValueOnce(new PractitionerWeekError("unreadable"))
      .mockResolvedValueOnce(page([]));

    const { tick } = renderWeek();
    await screen.findByTestId("week-error");

    tick(1);

    await screen.findByTestId("week-empty");
    expect(screen.queryByTestId("week-error")).toBeNull();
  });

  it("under StrictMode's double mount, shows the live read's answer and not the aborted one's failure", async () => {
    // The first mount's read is aborted by the dev-only unmount, and a real fetch then
    // rejects. That rejection belongs to a mount that no longer exists and must not be
    // shown as "could not be read" over the second mount's good answer.
    const live = deferred<PractitionerAppointmentPage>();
    let aborted = 0;
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockImplementation(
      (_id, _now, signal) =>
        new Promise((resolve, reject) => {
          signal?.addEventListener("abort", () => {
            aborted += 1;
            reject(new PractitionerWeekError("unreadable"));
          });
          // Only the read that was not aborted ever answers, and only when told to.
          void live.promise.then(resolve);
        }),
    );

    render(
      <StrictMode>
        <PractitionerWeek practitionerId={ID} practitionerName={NAME} pollTick={0} />
      </StrictMode>,
    );

    // The first mount's read was aborted and has rejected; the live one is pending.
    await waitFor(() => expect(aborted).toBe(1));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByTestId("week-error")).toBeNull();
    expect(screen.getByTestId("region-loading")).toBeInTheDocument();

    await act(async () => {
      live.resolve(page([]));
      await live.promise;
    });
    await screen.findByTestId("week-empty");
  });

  it("gives its read back when it goes away", async () => {
    let signal: AbortSignal | undefined;
    vi.spyOn(consoleApi, "fetchPractitionerWeek").mockImplementation(
      (_id, _now, s) => {
        signal = s;
        return deferred<PractitionerAppointmentPage>().promise;
      },
    );

    const { unmount } = renderWeek();
    await waitFor(() => expect(signal).toBeDefined());

    unmount();

    expect(signal?.aborted).toBe(true);
  });
});
