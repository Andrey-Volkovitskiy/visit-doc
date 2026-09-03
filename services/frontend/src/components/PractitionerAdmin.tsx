import { useCallback, useEffect, useRef, useState } from "react";
import {
  createPractitioner,
  deletePractitioner,
  fetchPractitioners,
  updatePractitioner,
  type Practitioner,
  type WorkingRange,
} from "../lib/consoleApi";

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

/**
 * Add, edit and delete the practitioners the assistant books against.
 *
 * Every rule shown here — the defaults a blank create gets, a duplicate name, working
 * ranges that overlap — belongs to the service that owns practitioners, and this screen
 * re-implements none of them. It sends what was typed, and renders back what that
 * service stored or the reason it refused, in that service's own words.
 */
export function PractitionerAdmin() {
  const [practitioners, setPractitioners] = useState<Practitioner[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Whether a create is in flight, for the button to show. `creating` repaints it;
  // `creatingRef` is what actually stops a second call, and the two are not
  // alternatives - see `handleCreate`.
  const [creating, setCreating] = useState(false);
  // The same latch as the handler can see it. A ref rather than reading `creating`,
  // because two clicks dispatched in one React batch both run against the render that
  // preceded them: the state the first set has not committed, so a guard reading it lets
  // the second through - and the button is still painted enabled for the same reason.
  const creatingRef = useRef(false);
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
    if (creatingRef.current) return;
    setError(null);
    creatingRef.current = true;
    setCreating(true);
    try {
      // Empty on purpose: the name, the specialty, the duration and the schedule are
      // all defaulted by the service that owns them.
      const created = await createPractitioner({});
      setPractitioners((prev) => [...prev, created]);
    } catch (err) {
      report(err, "Could not add a practitioner.");
    } finally {
      creatingRef.current = false;
      setCreating(false);
    }
  }

  /** Record what was typed into a duration field, and the number if it is one yet. */
  function editDuration(id: string, raw: string): void {
    setDurationDrafts((prev) => ({ ...prev, [id]: raw }));
    const parsed = Number(raw);
    if (raw.trim() !== "" && Number.isInteger(parsed) && parsed > 0) {
      edit(id, (p) => ({ ...p, appointment_duration_minutes: parsed }));
      setDurationDrafts((prev) => {
        const { [id]: _settled, ...rest } = prev;
        return rest;
      });
    }
  }

  async function handleSave(practitioner: Practitioner): Promise<void> {
    // A draft still held for this row means its duration field is not a number right
    // now. Refusing beats sending the last one that was: the staff member is mid-edit,
    // and saving a value they have already typed over is a change they did not ask for.
    if (durationDrafts[practitioner.id] !== undefined) {
      setError("Appointment minutes must be a whole number of minutes.");
      return;
    }
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
      // A refused save changed nothing, so the row is left exactly as it is — which is
      // also what lets the staff member correct what they typed rather than retype it.
      report(err, "Could not save that practitioner.");
    }
  }

  async function handleDelete(practitioner: Practitioner): Promise<void> {
    setError(null);
    try {
      await deletePractitioner(practitioner.id);
      setPractitioners((prev) => prev.filter((p) => p.id !== practitioner.id));
    } catch (err) {
      report(err, "Could not delete that practitioner.");
    }
  }

  return (
    <div data-testid="practitioner-admin">
      <h3>Practitioners</h3>
      {/* Disabled while the create is out so the wait is visible; the handler's own
          latch is what makes a second click harmless either way. */}
      <button onClick={() => void handleCreate()} disabled={creating}>
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
              <button onClick={() => void handleSave(practitioner)}>Save</button>
              <button
                aria-label={`Delete ${practitioner.full_name}`}
                onClick={() => void handleDelete(practitioner)}
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
