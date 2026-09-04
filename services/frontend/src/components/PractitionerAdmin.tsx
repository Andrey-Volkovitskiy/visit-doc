import { useCallback, useEffect, useState } from "react";
import {
  createPractitioner,
  deletePractitioner,
  fetchPractitioners,
  updatePractitioner,
  type Practitioner,
  type WorkingRange,
} from "../lib/consoleApi";
import { useBusyLatch } from "../lib/useBusyLatch";

// Monday-based and numeric, matching the scheduler's own enum: the wire carries the
// number, and these are only the labels this screen puts on it.
const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

const SPECIALTIES = [
  "general_practice",
  "cardiology",
  "dermatology",
  "paediatrics",
  "physiotherapy",
];

function withRange(
  practitioner: Practitioner,
  index: number,
  patch: Partial<WorkingRange>,
): Practitioner {
  return {
    ...practitioner,
    schedule: practitioner.schedule.map((range, i) =>
      i === index ? { ...range, ...patch } : range,
    ),
  };
}

// A whole number of minutes, and nothing that merely reads as one. `Number.isInteger`
// is not this test: a `type="number"` input accepts exponent form, so `1e3` arrives as
// a string `Number` reads as 1000 and `Number.isInteger` calls whole — and the row
// silently became a 1000-minute appointment the assistant then books against. `"1.0"`,
// `"-5"`, `" 30"`, `"+30"` and `"0x10"` are the same trick in other clothes.
const WHOLE_MINUTES = /^\d+$/;

function isWholeMinutes(raw: string): boolean {
  return WHOLE_MINUTES.test(raw);
}

/**
 * Add, edit and delete the practitioners the assistant books against.
 *
 * Every rule shown here — the defaults a blank create gets, a duplicate name, working
 * ranges that overlap, how long an appointment may be — belongs to the service that
 * owns practitioners, and this screen re-implements none of them. It sends what was
 * typed, and renders back what that service stored or the reason it refused, in that
 * service's own words.
 *
 * The one thing it does decide for itself is whether a field holds a value to send at
 * all: a `type="number"` input hands over a string, and "" and "1e" are not numbers.
 * That is not a rule about practitioners, and it deliberately carries no bound.
 */
export function PractitionerAdmin() {
  const [practitioners, setPractitioners] = useState<Practitioner[]>([]);
  const [error, setError] = useState<string | null>(null);
  // One latch for every gesture on this pane, keyed by what the gesture is about, so a
  // second click on any of them is refused rather than only on Add. See `useBusyLatch`.
  const latch = useBusyLatch();
  // The raw text of a duration field mid-edit, keyed by practitioner, for the ones that
  // are not currently a number at all.
  //
  // A `type="number"` input still hands over a string, and the two strings a person
  // types on the way to a number are not numbers: `Number("")` is `0` and `Number("1e")`
  // is `NaN`. Writing either into the row meant the field could not be cleared to
  // retype - it repainted as the `0` it had just been told - and a Save in that moment
  // sent a duration nobody chose, or a `NaN` that `JSON.stringify` writes as an explicit
  // `null` on a PATCH whose contract is that omitted fields are left untouched. So the
  // row keeps the last real number and this keeps what is on screen, until it is one.
  const [durationDrafts, setDurationDrafts] = useState<Record<string, string>>({});

  const report = useCallback((err: unknown, fallback: string): void => {
    setError(err instanceof Error ? err.message : fallback);
  }, []);

  useEffect(() => {
    void fetchPractitioners()
      .then(setPractitioners)
      // Without this the screen sits empty with nothing explaining why.
      .catch((err: unknown) => report(err, "Could not load the practitioners."));
  }, [report]);

  function replace(saved: Practitioner): void {
    // The response *is* the stored practitioner, so what is rendered is what the
    // assistant will book against — it cannot drift from what was typed.
    setPractitioners((prev) =>
      prev.map((p) => (p.id === saved.id ? saved : p)),
    );
  }

  function edit(id: string, change: (p: Practitioner) => Practitioner): void {
    setPractitioners((prev) => prev.map((p) => (p.id === id ? change(p) : p)));
  }

  async function handleCreate(): Promise<void> {
    // A staff member who clicks again because nothing appeared to happen must not get
    // two practitioners. There is no form to read here, so nothing about the second
    // call looks different from the first - it simply creates a second row, with a
    // second pool-assigned name.
    await latch.run("create", async () => {
      setError(null);
      try {
        // Empty on purpose: the name, the specialty, the duration and the schedule are
        // all defaulted by the service that owns them.
        const created = await createPractitioner({});
        setPractitioners((prev) => [...prev, created]);
      } catch (err) {
        report(err, "Could not add a practitioner.");
      }
    });
  }

  function dropDraft(id: string): void {
    setDurationDrafts((prev) => {
      if (prev[id] === undefined) return prev;
      const { [id]: _dropped, ...rest } = prev;
      return rest;
    });
  }

  /** Record what was typed into a duration field, and the number if it is one yet. */
  function editDuration(id: string, raw: string): void {
    setDurationDrafts((prev) => ({ ...prev, [id]: raw }));
    if (!isWholeMinutes(raw)) return;
    const parsed = Number(raw);
    edit(id, (p) => ({ ...p, appointment_duration_minutes: parsed }));
    // The draft is kept while what was typed is not how the row would render the
    // number, so "05" stays "05" instead of repainting as "5" under the cursor. It is
    // still a number, so it still saves — the draft records what is on screen, and
    // only `handleSave` decides whether there is a number to send.
    if (String(parsed) === raw) dropDraft(id);
  }

  async function handleSave(practitioner: Practitioner): Promise<void> {
    // What the duration field holds *right now*, when that is not a whole number of
    // minutes - "", "1e", "1e3", "-5", something on the way to a number or something
    // that only reads as one. Refusing beats sending the last value that was one: the
    // staff member is mid-edit, and saving a number they have already typed over is a
    // change they did not ask for. A draft that *is* a number saves: "05" is 5 minutes
    // written oddly, not a field with nothing in it.
    //
    // This is not one of the clinic's rules being re-implemented, which is why it says
    // nothing about how long an appointment may be. Whether 2 minutes or 600 is allowed
    // belongs to the service that owns practitioners, and this screen sends whatever
    // whole number was typed and renders that service's refusal in its own words.
    const draft = durationDrafts[practitioner.id];
    if (draft !== undefined && !isWholeMinutes(draft)) {
      setError("Type the appointment length in whole minutes before saving.");
      return;
    }
    await latch.run(`save:${practitioner.id}`, async () => {
      setError(null);
      try {
        replace(
          await updatePractitioner(practitioner.id, {
            full_name: practitioner.full_name,
            specialty: practitioner.specialty,
            appointment_duration_minutes: practitioner.appointment_duration_minutes,
            schedule: practitioner.schedule,
          }),
        );
      } catch (err) {
        // A refused save changed nothing, so the row is left exactly as it is — which
        // is also what lets the staff member correct what they typed rather than
        // retype it.
        report(err, "Could not save that practitioner.");
      }
    });
  }

  async function handleDelete(practitioner: Practitioner): Promise<void> {
    // The second delete of a practitioner the first one removed is a 404, reported as a
    // failure the staff member cannot act on - for a delete that worked.
    await latch.run(`delete:${practitioner.id}`, async () => {
      setError(null);
      try {
        await deletePractitioner(practitioner.id);
        setPractitioners((prev) => prev.filter((p) => p.id !== practitioner.id));
        // The row is gone, so its half-typed duration is too. Left behind, it would be
        // handed to the next practitioner to be created under the same id - which
        // cannot happen with ULIDs, but a draft nothing can ever clear is a leak in a
        // map that grows for the life of the page either way.
        dropDraft(practitioner.id);
      } catch (err) {
        report(err, "Could not delete that practitioner.");
      }
    });
  }

  return (
    <div data-testid="practitioner-admin">
      <h3>Practitioners</h3>
      {/* Disabled while the create is out so the wait is visible; the handler's own
          latch is what makes a second click harmless either way. */}
      <button onClick={() => void handleCreate()} disabled={latch.isBusy("create")}>
        Add practitioner
      </button>
      {practitioners.length === 0 ? (
        <p data-testid="no-practitioners">No practitioners yet.</p>
      ) : (
        <ul>
          {practitioners.map((practitioner) => (
            <li key={practitioner.id} data-testid="practitioner">
              <input
                aria-label="Full name"
                value={practitioner.full_name}
                onChange={(e) =>
                  edit(practitioner.id, (p) => ({
                    ...p,
                    full_name: e.target.value,
                  }))
                }
              />
              <select
                aria-label="Specialty"
                value={practitioner.specialty}
                onChange={(e) =>
                  edit(practitioner.id, (p) => ({
                    ...p,
                    specialty: e.target.value,
                  }))
                }
              >
                {SPECIALTIES.map((specialty) => (
                  <option key={specialty} value={specialty}>
                    {specialty.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
              <input
                aria-label="Appointment minutes"
                type="number"
                value={
                  durationDrafts[practitioner.id] ??
                  String(practitioner.appointment_duration_minutes)
                }
                onChange={(e) => editDuration(practitioner.id, e.target.value)}
              />
              <ul>
                {practitioner.schedule.map((range, index) => (
                  <li key={index} data-testid="working-range">
                    <select
                      aria-label="Weekday"
                      value={range.weekday}
                      onChange={(e) =>
                        edit(practitioner.id, (p) =>
                          withRange(p, index, { weekday: Number(e.target.value) }),
                        )
                      }
                    >
                      {WEEKDAYS.map((name, weekday) => (
                        <option key={name} value={weekday}>
                          {name}
                        </option>
                      ))}
                    </select>
                    <input
                      aria-label="Start time"
                      value={range.start_time}
                      onChange={(e) =>
                        edit(practitioner.id, (p) =>
                          withRange(p, index, { start_time: e.target.value }),
                        )
                      }
                    />
                    <input
                      aria-label="End time"
                      value={range.end_time}
                      onChange={(e) =>
                        edit(practitioner.id, (p) =>
                          withRange(p, index, { end_time: e.target.value }),
                        )
                      }
                    />
                    <button
                      onClick={() =>
                        edit(practitioner.id, (p) => ({
                          ...p,
                          schedule: p.schedule.filter((_, i) => i !== index),
                        }))
                      }
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
              <button
                onClick={() =>
                  edit(practitioner.id, (p) => ({
                    ...p,
                    schedule: [
                      ...p.schedule,
                      { weekday: 0, start_time: "09:00", end_time: "17:00" },
                    ],
                  }))
                }
              >
                Add hours
              </button>
              <button
                onClick={() => void handleSave(practitioner)}
                disabled={latch.isBusy(`save:${practitioner.id}`)}
              >
                Save
              </button>
              <button
                aria-label={`Delete ${practitioner.full_name}`}
                onClick={() => void handleDelete(practitioner)}
                disabled={latch.isBusy(`delete:${practitioner.id}`)}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p data-testid="practitioner-error">{error}</p>}
    </div>
  );
}

export default PractitionerAdmin;
