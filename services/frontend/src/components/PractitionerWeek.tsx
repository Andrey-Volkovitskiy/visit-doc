import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchPractitionerWeek,
  PractitionerWeekError,
  type PractitionerAppointment,
} from "../lib/consoleApi";
import { localNow } from "../lib/chatStream";
import { clockTime, dayLabel } from "../lib/localTime";
import { READ_TIMEOUT_MS } from "../lib/useThreadReads";

export interface PractitionerWeekProps {
  practitionerId: string;
  /** Named in the empty state, so "nobody is booked" says with whom. */
  practitionerName: string;
  /** The console poll's answer count; every advance re-reads this list (R8). */
  pollTick: number;
  /** A read's deadline. Defaulted to the thread panes' own; a test shortens it. */
  readTimeoutMs?: number;
}

/**
 * What the list is showing. Four states, never fewer: "still loading", "nobody booked"
 * and "could not be read" call for different things from the reader and must not look
 * alike (FR-007c).
 */
type WeekState =
  | { status: "loading" }
  | { status: "loaded"; appointments: PractitionerAppointment[] }
  | { status: "failed"; error: PractitionerWeekError };

/** One read in flight: the slot an open list holds while it waits for an answer. */
interface LiveRead {
  controller: AbortController;
  deadline: ReturnType<typeof setTimeout>;
}

/**
 * The appointments grouped by the local day they start on, days in date order and each
 * day in start order.
 *
 * The route already answers in start order; sorting here as well costs nothing and
 * means the grouping does not depend on it. ISO-8601 local times sort as strings.
 */
function byDay(
  appointments: PractitionerAppointment[],
): { date: string; appointments: PractitionerAppointment[] }[] {
  const sorted = [...appointments].sort(
    (a, b) =>
      a.starts_at.localeCompare(b.starts_at) || a.id.localeCompare(b.id),
  );
  const days: { date: string; appointments: PractitionerAppointment[] }[] = [];
  for (const appointment of sorted) {
    const date = appointment.starts_at.slice(0, 10);
    const last = days[days.length - 1];
    if (last?.date === date) last.appointments.push(appointment);
    else days.push({ date, appointments: [appointment] });
  }
  return days;
}

/**
 * One practitioner's next seven days, shown inside their roster block while it is open.
 *
 * Mounted by opening the block and unmounted by closing it, so a closed block reads
 * nothing and every opening starts from a fresh read (FR-007b) - there is no cache to be
 * stale.
 *
 * While mounted it re-reads on every console poll tick (R8), under three rules:
 *
 * - **At most one read in flight.** A tick that finds a read still out issues nothing.
 *   Unlike the console poll, which issues every tick and sequences the answers, this
 *   list can afford to skip: the deadline below guarantees the slot comes back, so a
 *   read that hangs costs one deadline's worth of ticks rather than the list for good.
 * - **A read that outlives its deadline is over.** It is aborted, its slot released, and
 *   the list says it could not be read - a hang left on screen as the last good list
 *   would present old data as current.
 * - **An answer older than the one on screen is dropped.** Each read is numbered as it is
 *   issued, and an outcome - list *or* failure - is applied only if nothing newer has
 *   been. That is what stops a read that answered after its deadline from rewinding a
 *   list a later read already replaced (the `useConsolePoll` idiom, with one difference:
 *   here a failure is shown, so it counts as applied).
 *
 * A failed refresh replaces a good list with the failure rather than keeping it (FR-007c):
 * the screen never presents a list it can no longer vouch for.
 *
 * Read-only by construction (FR-009): it renders text and nothing a person could press.
 */
export function PractitionerWeek({
  practitionerId,
  practitionerName,
  pollTick,
  readTimeoutMs = READ_TIMEOUT_MS,
}: PractitionerWeekProps) {
  const [state, setState] = useState<WeekState>({ status: "loading" });
  const live = useRef<LiveRead | null>(null);
  // `issued` numbers each read as it goes out; `applied` is the newest one whose outcome
  // reached the screen. Refs, not state: they order answers, they are never rendered.
  const issued = useRef(0);
  const applied = useRef(0);

  const read = useCallback((): void => {
    if (live.current !== null) return;
    const sequence = ++issued.current;
    const controller = new AbortController();

    const apply = (next: WeekState): void => {
      if (sequence <= applied.current) return;
      applied.current = sequence;
      setState(next);
    };
    const release = (): void => {
      if (live.current?.controller === controller) live.current = null;
    };

    const deadline = setTimeout(() => {
      controller.abort();
      release();
      apply({ status: "failed", error: new PractitionerWeekError("unreadable") });
    }, readTimeoutMs);
    live.current = { controller, deadline };

    fetchPractitionerWeek(practitionerId, localNow(), controller.signal)
      .then((appointments) => apply({ status: "loaded", appointments }))
      .catch((err: unknown) =>
        apply({
          status: "failed",
          error:
            err instanceof PractitionerWeekError
              ? err
              : new PractitionerWeekError("unreadable"),
        }),
      )
      .finally(() => {
        clearTimeout(deadline);
        release();
      });
  }, [practitionerId, readTimeoutMs]);

  // Declared before the reading effect, so under StrictMode's mount-unmount-mount this
  // cleanup runs between the two reads: the first is aborted and marked superseded, and
  // the second finds the slot free.
  useEffect(
    () => () => {
      const current = live.current;
      if (current !== null) {
        clearTimeout(current.deadline);
        current.controller.abort();
      }
      live.current = null;
      // Everything issued so far is superseded: nothing it answers may reach a list
      // that has been closed, or the one a remount starts afresh.
      applied.current = issued.current;
    },
    [],
  );

  // The first read on opening, then one per tick while open.
  useEffect(() => {
    read();
  }, [pollTick, read]);

  return (
    <section
      data-testid="practitioner-week"
      aria-label={`Bookings with ${practitionerName} over the next seven days`}
      className="border-rule-soft bg-surface-sunken rounded-md border p-3 text-sm"
    >
      {state.status === "loading" ? (
        <p
          data-testid="region-loading"
          data-region="practitioner-week"
          className="text-ink-muted"
        >
          Loading bookings…
        </p>
      ) : state.status === "failed" ? (
        <p data-testid="week-error" data-failure={state.error.kind} className="text-ink">
          {state.error.message}
        </p>
      ) : state.appointments.length === 0 ? (
        <p data-testid="week-empty" className="text-ink-muted">
          Nobody is booked with {practitionerName} in the next seven days.
        </p>
      ) : (
        <ol className="flex flex-col gap-3">
          {byDay(state.appointments).map((day) => (
            <li key={day.date} data-testid="week-day" className="flex flex-col gap-1">
              <h5 className="text-ink font-medium">{dayLabel(day.date)}</h5>
              <ul className="flex flex-col gap-0.5">
                {day.appointments.map((appointment) => (
                  <li
                    key={appointment.id}
                    data-testid="week-appointment"
                    className="text-ink flex gap-3"
                  >
                    <span className="text-ink-muted flex-none tabular-nums">
                      {clockTime(appointment.starts_at)}–{clockTime(appointment.ends_at)}
                    </span>
                    <span className="min-w-0">{appointment.patient_full_name}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default PractitionerWeek;
