import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  createPractitioner,
  deletePractitioner,
  fetchPractitioners,
  fetchSpecialties,
  updatePractitioner,
  type Practitioner,
  type PractitionerWrite,
  type WorkingRange,
} from "../lib/consoleApi";
import { useBusyLatch } from "../lib/useBusyLatch";
import { useReportDirty } from "../lib/useReportDirty";
import { PractitionerWeek } from "./PractitionerWeek";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "./ui/dialog";
import { Input } from "./ui/input";

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

/** The one message this screen writes itself; every other reason is the scheduler's. */
const NOT_A_DURATION =
  "Type the appointment length in whole minutes before saving.";

/** The class every `<select>` here wears, since the theme styles no element by tag. */
const SELECT_CLASS =
  "border-input bg-surface text-ink focus-visible:ring-ring h-9 rounded-md border px-2 text-base transition-colors focus-visible:ring-2 focus-visible:outline-none";

/**
 * The options to offer for a practitioner who is currently `specialty`.
 *
 * Their own specialty is always among them, even when the fetched list does not hold
 * it - a list that arrived short, or not at all, must not make the chooser render a
 * practitioner as somebody else. A `<select>` whose value matches no option falls back
 * to displaying the first one, so the screen would read as a confident statement about
 * a specialty nobody chose.
 */
function optionsFor(specialties: string[], specialty: string): string[] {
  if (specialties.includes(specialty)) return specialties;
  return [specialty, ...specialties];
}

function dayName(weekday: number): string {
  return WEEKDAYS[weekday] ?? `Day ${String(weekday)}`;
}

/** The working hours as one readable line, for the roster's record of a practitioner. */
function scheduleSummary(schedule: WorkingRange[]): string {
  if (schedule.length === 0) return "No working hours set";
  return schedule
    .map(
      (range) =>
        `${dayName(range.weekday)} ${range.start_time}–${range.end_time}`,
    )
    .join(" · ");
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
 * Which of the tab's three views is on screen (FR-035a, `data-model.md`).
 *
 * A discriminated union rather than two booleans and a nullable id, so "creating and
 * editing at once" and "editing with no id" are unrepresentable rather than guarded
 * against.
 */
type View =
  | { mode: "list" }
  | { mode: "edit"; id: string }
  | { mode: "create" };

/**
 * What the form holds while it is being typed into.
 *
 * `minutes` is the raw text and not a number, because the two strings a person types on
 * the way to a number are not numbers: `Number("")` is `0` and `Number("1e")` is `NaN`.
 * Keeping the number here meant the field could not be cleared to retype — it repainted
 * as the `0` it had just been told — and a Save in that moment sent a duration nobody
 * chose, or a `NaN` that `JSON.stringify` writes as an explicit `null` on a PATCH whose
 * contract is that omitted fields are left untouched. So the text is what is held, and
 * only the submit decides whether there is a number to send.
 */
interface FormState {
  full_name: string;
  specialty: string;
  minutes: string;
  schedule: WorkingRange[];
}

const BLANK_FORM: FormState = {
  full_name: "",
  specialty: "",
  minutes: "",
  schedule: [],
};

function formOf(practitioner: Practitioner): FormState {
  return {
    full_name: practitioner.full_name,
    specialty: practitioner.specialty,
    minutes: String(practitioner.appointment_duration_minutes),
    schedule: practitioner.schedule,
  };
}

/** Whether the form still holds what it was opened with (FR-035b's `dirty`, negated). */
function unchanged(a: FormState, b: FormState): boolean {
  return (
    a.full_name === b.full_name &&
    a.specialty === b.specialty &&
    a.minutes === b.minutes &&
    JSON.stringify(a.schedule) === JSON.stringify(b.schedule)
  );
}

/** What a console section owes the shell around it. */
export interface AdminSectionProps {
  /**
   * See `EditorProps.onDirtyChange`.
   *
   * Optional, and defaulted to a no-op: a section rendered on its own — which is how
   * every one of its own tests renders it — has nobody to report to, and requiring the
   * prop would make the guard's shell a precondition for using the section at all.
   */
  onDirtyChange?: (dirty: boolean) => void;
}

interface PractitionerAdminProps extends AdminSectionProps {
  /**
   * The console poll's answer count, passed through to every open week so it re-reads
   * on each advance (016 R8).
   *
   * Optional for the same reason as `onDirtyChange`: rendered on its own, the section
   * has no poll, and an open week then reads once, on opening.
   */
  pollTick?: number;
}

interface EditorProps {
  /** The practitioner being edited, or null while one is being created. */
  practitioner: Practitioner | null;
  specialties: string[];
  busy: boolean;
  onSubmit: (write: PractitionerWrite) => void;
  /** Report a reason this screen owns, for the section's one error line to render. */
  onInvalid: (message: string) => void;
  onLeave: () => void;
  /**
   * Report whether this form now holds work that leaving would lose (FR-035b).
   *
   * Lifted no further than it has to be. The *back* control is guarded in here, where
   * the form is; a **tab** switch is guarded by `App`, because only `App` knows a switch
   * was asked for — Radix destroys this panel to perform one, so by the time this
   * component could notice, the work is already gone.
   */
  onDirtyChange: (dirty: boolean) => void;
}

/**
 * The view that replaces the roster while one practitioner is being written (FR-035a).
 *
 * It is rendered in the tab's own content, not in a dialog: the page header, the console
 * header and the tab set stay on screen and operable, which is what lets a staff member
 * leave an unfinished edit by choosing another tab.
 *
 * Its own state is the form and whether the discard confirmation is open; the roster,
 * the error line and every write belong to the section around it.
 */
function PractitionerEditor({
  practitioner,
  specialties,
  busy,
  onSubmit,
  onInvalid,
  onLeave,
  onDirtyChange,
}: EditorProps) {
  const initial = practitioner === null ? BLANK_FORM : formOf(practitioner);
  const [form, setForm] = useState<FormState>(initial);
  const [confirmingDiscard, setConfirmingDiscard] = useState(false);
  const creating = practitioner === null;
  const dirty = !unchanged(form, initial);

  function patchRange(index: number, patch: Partial<WorkingRange>): void {
    setForm((prev) => ({
      ...prev,
      schedule: prev.schedule.map((range, i) =>
        i === index ? { ...range, ...patch } : range,
      ),
    }));
  }

  useReportDirty(dirty, onDirtyChange);

  function leave(): void {
    // FR-035b: a form holding work asks before losing it; a form holding none does not
    // interrupt, which is the common case of opening a practitioner to look at them.
    if (dirty) setConfirmingDiscard(true);
    else onLeave();
  }

  function submit(): void {
    // What the duration field holds *right now*, when that is not a whole number of
    // minutes - "", "1e", "1e3", "-5", something on the way to a number or something
    // that only reads as one. Refusing beats sending the last value that was one: the
    // staff member is mid-edit, and saving a number they have already typed over is a
    // change they did not ask for. "05" is 5 minutes written oddly, and saves.
    //
    // This is not one of the clinic's rules being re-implemented, which is why it says
    // nothing about how long an appointment may be. Whether 2 minutes or 600 is allowed
    // belongs to the service that owns practitioners, and this screen sends whatever
    // whole number was typed and renders that service's refusal in its own words.
    const blank = form.minutes === "";
    if (blank && !creating) {
      onInvalid(NOT_A_DURATION);
      return;
    }
    if (!blank && !isWholeMinutes(form.minutes)) {
      onInvalid(NOT_A_DURATION);
      return;
    }
    if (!creating) {
      onSubmit({
        full_name: form.full_name,
        specialty: form.specialty,
        appointment_duration_minutes: Number(form.minutes),
        schedule: form.schedule,
      });
      return;
    }
    // A create sends only what was typed. Every field this practitioner is given -
    // the pool-assigned name, the specialty, the duration and the schedule - belongs
    // to the service that owns them, so a field left alone is one this screen must not
    // answer for: an omitted field is defaulted, and an empty one would be a value.
    const write: PractitionerWrite = {};
    if (form.full_name !== "") write.full_name = form.full_name;
    if (form.specialty !== "") write.specialty = form.specialty;
    if (!blank) write.appointment_duration_minutes = Number(form.minutes);
    if (form.schedule.length > 0) write.schedule = form.schedule;
    onSubmit(write);
  }

  return (
    <div
      data-testid="practitioner-edit"
      className="flex flex-col gap-4 p-4"
    >
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={leave}
          className="text-ink-muted hover:text-ink -ml-2"
        >
          <ArrowLeft aria-hidden="true" />
          Back to the roster
        </Button>
      </div>
      <h4 className="text-md text-ink font-semibold">
        {creating ? "New practitioner" : practitioner.full_name}
      </h4>

      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink-muted">Full name</span>
          <Input
            aria-label="Full name"
            value={form.full_name}
            placeholder={creating ? "Left blank, the clinic assigns one" : ""}
            onChange={(e) =>
              setForm((prev) => ({ ...prev, full_name: e.target.value }))
            }
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink-muted">Specialty</span>
          <select
            aria-label="Specialty"
            className={SELECT_CLASS}
            value={form.specialty}
            onChange={(e) =>
              setForm((prev) => ({ ...prev, specialty: e.target.value }))
            }
          >
            {creating && <option value="">The clinic&apos;s default</option>}
            {optionsFor(specialties, form.specialty)
              // A create's blank is the option above, not an entry in the set the
              // scheduler published, so it is never offered twice.
              .filter((specialty) => specialty !== "")
              .map((specialty) => (
                <option key={specialty} value={specialty}>
                  {specialty}
                </option>
              ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink-muted">Appointment minutes</span>
          <Input
            aria-label="Appointment minutes"
            type="number"
            className="w-24"
            value={form.minutes}
            placeholder={creating ? "Default" : ""}
            onChange={(e) =>
              setForm((prev) => ({ ...prev, minutes: e.target.value }))
            }
          />
        </label>
      </div>

      <div className="flex flex-col gap-2">
        <h5 className="text-ink-muted text-sm font-medium">Working hours</h5>
        <ul className="flex flex-col gap-2">
          {form.schedule.map((range, index) => (
            <li
              key={index}
              data-testid="working-range"
              className="border-rule-soft bg-surface-sunken flex flex-wrap items-center gap-2 rounded-md border p-2"
            >
              <select
                aria-label="Weekday"
                className={SELECT_CLASS}
                value={range.weekday}
                onChange={(e) =>
                  patchRange(index, { weekday: Number(e.target.value) })
                }
              >
                {WEEKDAYS.map((name, weekday) => (
                  <option key={name} value={weekday}>
                    {name}
                  </option>
                ))}
              </select>
              <Input
                aria-label="Start time"
                className="w-24"
                value={range.start_time}
                onChange={(e) =>
                  patchRange(index, { start_time: e.target.value })
                }
              />
              <Input
                aria-label="End time"
                className="w-24"
                value={range.end_time}
                onChange={(e) => patchRange(index, { end_time: e.target.value })}
              />
              <Button
                variant="ghost"
                size="sm"
                className="text-ink-muted hover:text-ink"
                onClick={() =>
                  setForm((prev) => ({
                    ...prev,
                    schedule: prev.schedule.filter((_, i) => i !== index),
                  }))
                }
              >
                Remove
              </Button>
            </li>
          ))}
        </ul>
        <div>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              setForm((prev) => ({
                ...prev,
                schedule: [
                  ...prev.schedule,
                  { weekday: 0, start_time: "09:00", end_time: "17:00" },
                ],
              }))
            }
          >
            Add hours
          </Button>
        </div>
      </div>

      {/* No appointments here, of any kind (016 FR-007): a practitioner's week lives on
          the roster, behind that block's own Show bookings toggle. */}
      <div className="flex items-center gap-2">
        <Button onClick={submit} disabled={busy}>
          {creating ? "Add practitioner" : "Save"}
        </Button>
      </div>

      {/*
        Radix renders this into a portal on document.body, so a test reaches it with
        `screen.*` and never with `within(container)`.

        No `destructive` variant on either button: it maps onto --color-attention, which
        FR-005 reserves for "a person is needed". Losing what was typed here is not that
        claim, and a colour that means one thing must not be spent on another.
      */}
      <Dialog
        open={confirmingDiscard}
        onOpenChange={(open) => {
          if (!open) setConfirmingDiscard(false);
        }}
      >
        <DialogContent data-testid="discard-confirm">
          <DialogTitle>Leave without saving?</DialogTitle>
          <DialogDescription>
            What you typed here has not been sent to the clinic&apos;s records. Going
            back now discards it.
          </DialogDescription>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmingDiscard(false)}
            >
              Keep editing
            </Button>
            <Button
              onClick={() => {
                setConfirmingDiscard(false);
                onLeave();
              }}
            >
              Discard
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Add, edit and delete the practitioners the assistant books against.
 *
 * Three views over one tab (FR-035a): the roster, one practitioner being edited, and one
 * being created. The roster is a record a staff member reads rather than a form at rest,
 * and a write is only ever made from a view opened for the purpose — so there is no
 * half-typed change sitting in a list waiting for a Save nobody pressed.
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
export function PractitionerAdmin({
  onDirtyChange = () => undefined,
  pollTick = 0,
}: PractitionerAdminProps) {
  const [practitioners, setPractitioners] = useState<Practitioner[]>([]);
  // Which roster blocks have their bookings shown (FR-007d), keyed by practitioner id so
  // a re-read or re-render of the roster leaves each block as it was. Held here rather
  // than in the block, and cleared by `leaveRoster`: nothing about shown or hidden is
  // remembered once the roster is left, so returning finds every block closed.
  const [openWeeks, setOpenWeeks] = useState<ReadonlySet<string>>(new Set());
  // Fetched, never written out here: see `fetchSpecialties`. Empty until it arrives,
  // which `optionsFor` covers - a row still offers the specialty it already has.
  const [specialties, setSpecialties] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>({ mode: "list" });
  // One latch for every gesture on this pane, keyed by what the gesture is about, so a
  // second click on any of them is refused rather than only on Add. See `useBusyLatch`.
  const latch = useBusyLatch();

  const report = useCallback((err: unknown, fallback: string): void => {
    setError(err instanceof Error ? err.message : fallback);
  }, []);

  useEffect(() => {
    void fetchPractitioners()
      .then(setPractitioners)
      // Without this the screen sits empty with nothing explaining why.
      .catch((err: unknown) =>
        report(err, "Could not load the practitioners."),
      );
    // Its own request and its own failure: the roster is still editable without the
    // chooser's other options, so one arriving late or not at all must not empty the
    // screen or take the roster's error message away.
    void fetchSpecialties()
      .then(setSpecialties)
      .catch((err: unknown) => report(err, "Could not load the specialties."));
  }, [report]);

  /**
   * Return to the roster from `from`, the view a write was submitted from - and only
   * from it.
   *
   * A write can land after the staff member has left that view and opened another,
   * holding work of its own. Leaving *that* one would discard what they typed with no
   * prompt, which is the loss the discard confirmation exists to prevent. Compared by
   * identity: every view is a fresh object, so reopening the same practitioner is a
   * different view from the one the write came from.
   */
  function returnToRosterFrom(from: View): void {
    setView((current) => (current === from ? { mode: "list" } : current));
  }

  async function handleCreate(write: PractitionerWrite, from: View): Promise<void> {
    // A staff member who clicks again because nothing appeared to happen must not get
    // two practitioners: the second call carries the very same form, so nothing about
    // it looks different from the first - it simply creates a second row, with a
    // second pool-assigned name.
    await latch.run("create", async () => {
      setError(null);
      try {
        const created = await createPractitioner(write);
        setPractitioners((prev) => [...prev, created]);
        returnToRosterFrom(from);
      } catch (err) {
        // A refused create left the view exactly as it is, which is what lets the staff
        // member correct what they typed rather than retype it.
        report(err, "Could not add a practitioner.");
      }
    });
  }

  async function handleSave(
    id: string,
    write: PractitionerWrite,
    from: View,
  ): Promise<void> {
    await latch.run(`save:${id}`, async () => {
      setError(null);
      try {
        // The response *is* the stored practitioner, so what the roster renders is what
        // the assistant will book against — it cannot drift from what was typed.
        const saved = await updatePractitioner(id, write);
        setPractitioners((prev) =>
          prev.map((p) => (p.id === saved.id ? saved : p)),
        );
        returnToRosterFrom(from);
      } catch (err) {
        // A refused save changed nothing, so the form is left exactly as it is.
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
        setPractitioners((prev) =>
          prev.filter((p) => p.id !== practitioner.id),
        );
      } catch (err) {
        report(err, "Could not delete that practitioner.");
      }
    });
  }

  /** Replace the roster with the edit or create view, closing every block's bookings. */
  function leaveRoster(next: View): void {
    setOpenWeeks(new Set());
    setView(next);
  }

  function toggleWeek(id: string): void {
    setOpenWeeks((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // A view naming a practitioner the roster no longer holds is not a view: the record
  // it was about is gone, so the tab falls back to the list rather than editing nothing.
  const editing =
    view.mode === "edit"
      ? (practitioners.find((p) => p.id === view.id) ?? null)
      : null;
  const editorOpen = view.mode === "create" || editing !== null;

  return (
    <div
      data-testid="practitioner-admin"
      className="flex min-h-0 flex-col gap-3 p-4"
    >
      {editorOpen ? (
        <PractitionerEditor
          onDirtyChange={onDirtyChange}
          // Remounted per subject, so the form starts from the record it is about
          // rather than from whatever the last one was left holding.
          key={editing === null ? "create" : editing.id}
          practitioner={editing}
          specialties={specialties}
          busy={latch.isBusy(
            editing === null ? "create" : `save:${editing.id}`,
          )}
          onSubmit={(write) => {
            if (editing === null) void handleCreate(write, view);
            else void handleSave(editing.id, write, view);
          }}
          onInvalid={setError}
          onLeave={() => setView({ mode: "list" })}
        />
      ) : (
        <>
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-md text-ink font-semibold">Practitioners</h3>
            <Button
              size="sm"
              onClick={() => leaveRoster({ mode: "create" })}
            >
              <Plus aria-hidden="true" />
              Add practitioner
            </Button>
          </div>
          {practitioners.length === 0 ? (
            <p data-testid="no-practitioners" className="text-ink-muted text-sm">
              No practitioners yet.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {practitioners.map((practitioner) => {
                const weekOpen = openWeeks.has(practitioner.id);
                const weekId = `practitioner-week-${practitioner.id}`;
                return (
                  <li
                    key={practitioner.id}
                    data-testid="practitioner"
                    className="border-rule bg-surface flex flex-col gap-2 rounded-md border p-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h4 className="text-ink text-base font-medium">
                          {practitioner.full_name}
                        </h4>
                        <p className="text-ink-muted text-sm">
                          {practitioner.specialty} &middot;{" "}
                          {practitioner.appointment_duration_minutes} minutes
                        </p>
                        <p className="text-ink-muted text-sm">
                          {scheduleSummary(practitioner.schedule)}
                        </p>
                      </div>
                      <div className="flex flex-none items-center gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Edit ${practitioner.full_name}`}
                          className="text-ink-muted hover:text-ink"
                          onClick={() =>
                            leaveRoster({ mode: "edit", id: practitioner.id })
                          }
                        >
                          <Pencil aria-hidden="true" />
                        </Button>
                        {/* At rest this is an ordinary control: FR-005 keeps
                            --color-attention for "a person is needed", and a delete button
                            sitting in a roster is not making that claim. */}
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Delete ${practitioner.full_name}`}
                          className="text-ink-muted hover:text-ink"
                          onClick={() => void handleDelete(practitioner)}
                          disabled={latch.isBusy(`delete:${practitioner.id}`)}
                        >
                          <Trash2 aria-hidden="true" />
                        </Button>
                      </div>
                    </div>
                    {/* Mounted only while open: a closed block reads nothing, and every
                        opening is a fresh read with no list kept from the last one
                        (FR-007b). The block's key is the practitioner's id, so a roster
                        re-render keeps an open list mounted rather than starting it over. */}
                    {weekOpen && (
                      <div id={weekId}>
                        <PractitionerWeek
                          practitionerId={practitioner.id}
                          practitionerName={practitioner.full_name}
                          pollTick={pollTick}
                        />
                      </div>
                    )}
                    {/* Last in the block, below everything else (FR-007a). */}
                    <Button
                      variant="ghost"
                      size="sm"
                      data-testid="bookings-toggle"
                      aria-expanded={weekOpen}
                      aria-controls={weekOpen ? weekId : undefined}
                      className="text-ink-muted hover:text-ink -ml-2 self-start"
                      onClick={() => toggleWeek(practitioner.id)}
                    >
                      {weekOpen ? (
                        <ChevronUp aria-hidden="true" />
                      ) : (
                        <ChevronDown aria-hidden="true" />
                      )}
                      {weekOpen ? "Hide bookings" : "Show bookings"}
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      {error && (
        <p
          data-testid="practitioner-error"
          className="text-attention bg-attention-wash border-attention/30 rounded-md border px-3 py-2 text-sm"
        >
          {error}
        </p>
      )}
    </div>
  );
}

export default PractitionerAdmin;
