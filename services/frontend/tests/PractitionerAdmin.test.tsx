import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PractitionerAdmin } from "../src/components/PractitionerAdmin";
import * as consoleApi from "../src/lib/consoleApi";
import type { Practitioner } from "../src/lib/consoleApi";
import { press } from "./press";

/** Open the roster's one practitioner for editing, and wait for the edit view. */
async function openTheEditView(): Promise<HTMLElement> {
  press(await screen.findByLabelText("Edit Dr. Ada Lovelace"));
  return await screen.findByTestId("practitioner-edit");
}

/** Open the create view from the roster, and wait for it. */
async function openTheCreateView(): Promise<HTMLElement> {
  press(await screen.findByText("Add practitioner"));
  return await screen.findByTestId("practitioner-edit");
}

function practitioner(overrides: Partial<Practitioner> = {}): Practitioner {
  return {
    id: "01PRACT0000000000000000000",
    full_name: "Dr. Ada Lovelace",
    specialty: "General Practice",
    appointment_duration_minutes: 30,
    schedule: [{ weekday: 0, start_time: "09:00", end_time: "17:00" }],
    ...overrides,
  };
}

// The closed set the scheduler publishes, as `GET /console/specialties` renders it.
const SPECIALTIES = [
  "Cardiology",
  "Dentistry",
  "Dermatology",
  "General Practice",
  "Gynecology",
  "Neurology",
  "Ophthalmology",
  "Orthopedics",
  "Pediatrics",
  "Psychiatry",
];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(consoleApi, "fetchPractitioners").mockResolvedValue([
    practitioner(),
  ]);
  vi.spyOn(consoleApi, "fetchSpecialties").mockResolvedValue(SPECIALTIES);
});

describe("PractitionerAdmin: the roster", () => {
  it("opens a practitioner on what the assistant books from", async () => {
    // The roster is a record now and the fields live behind the edit view (FR-035a),
    // so this is where the editable values are: what opens is what was stored, not a
    // blank form the staff member would have to fill in again.
    render(<PractitionerAdmin />);
    await openTheEditView();

    expect(screen.getByDisplayValue("Dr. Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByDisplayValue("30")).toBeInTheDocument();
    expect(screen.getByDisplayValue("09:00")).toBeInTheDocument();
  });

  it("says so plainly when the clinic has nobody on it", async () => {
    vi.spyOn(consoleApi, "fetchPractitioners").mockResolvedValue([]);

    render(<PractitionerAdmin />);

    await waitFor(() =>
      expect(screen.getByTestId("no-practitioners")).toBeInTheDocument(),
    );
  });

  it("says why when the roster could not be read", async () => {
    vi.spyOn(consoleApi, "fetchPractitioners").mockRejectedValue(
      new Error("scheduling is unavailable; nothing was changed"),
    );

    render(<PractitionerAdmin />);

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
        "scheduling is unavailable",
      ),
    );
  });
});

describe("PractitionerAdmin: the roster reads as a record", () => {
  // FR-032. The roster is what a staff member reads; the fields are editable behind a
  // control rather than by being a form at rest, so nothing here is a half-typed write
  // waiting for a Save nobody pressed.
  it("states each practitioner's name, specialty, appointment length and working hours", async () => {
    render(<PractitionerAdmin />);

    const row = await screen.findByTestId("practitioner");
    expect(row).toHaveTextContent("Dr. Ada Lovelace");
    expect(row).toHaveTextContent("General Practice");
    expect(row).toHaveTextContent("30 minutes");
    expect(row).toHaveTextContent("Monday");
    expect(row).toHaveTextContent("09:00");
    expect(row).toHaveTextContent("17:00");
  });

  it("offers create, edit and delete from the roster itself", async () => {
    render(<PractitionerAdmin />);

    await screen.findByTestId("practitioner");
    expect(screen.getByText("Add practitioner")).toBeInTheDocument();
    const row = screen.getByTestId("practitioner");
    expect(
      within(row).getByLabelText("Edit Dr. Ada Lovelace"),
    ).toBeInTheDocument();
    expect(
      within(row).getByLabelText("Delete Dr. Ada Lovelace"),
    ).toBeInTheDocument();
  });

  it("shows no edit view until one is asked for", async () => {
    render(<PractitionerAdmin />);

    await screen.findByTestId("practitioner");
    expect(screen.queryByTestId("practitioner-edit")).toBeNull();
  });

  it("names what a refused save failed at, while the edit view is still open", async () => {
    // FR-035: the reason is the scheduler's own, and it has to be readable from where
    // the staff member is standing - which, after a refused save, is the edit view.
    vi.spyOn(consoleApi, "updatePractitioner").mockRejectedValue(
      new Error("working ranges on one weekday must not overlap"),
    );

    render(<PractitionerAdmin />);
    await openTheEditView();
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
        "must not overlap",
      ),
    );
    expect(screen.getByTestId("practitioner-edit")).toBeInTheDocument();
  });
});

describe("PractitionerAdmin: the edit view replaces the tab's content", () => {
  // FR-035a. Not an overlay and not an expansion in place: the list is gone while the
  // edit view is up, and a control leads back to it.
  it("replaces the roster when a practitioner is opened", async () => {
    render(<PractitionerAdmin />);
    await openTheEditView();

    expect(screen.queryByTestId("practitioner")).toBeNull();
    expect(screen.getByDisplayValue("Dr. Ada Lovelace")).toBeInTheDocument();
    // An overlay would leave the list underneath and the dialog role above it.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("replaces the roster when a practitioner is being created", async () => {
    render(<PractitionerAdmin />);
    await openTheCreateView();

    expect(screen.queryByTestId("practitioner")).toBeNull();
    expect(screen.getByLabelText("Full name")).toHaveValue("");
  });

  it("returns to the roster from an untouched edit view, asking nothing", async () => {
    // FR-035b gates the confirmation on there being work to lose: opening a
    // practitioner to look at them and closing again is the common case.
    render(<PractitionerAdmin />);
    await openTheEditView();

    press(screen.getByText("Back to the roster"));

    expect(screen.queryByTestId("discard-confirm")).toBeNull();
    expect(await screen.findByTestId("practitioner")).toBeInTheDocument();
    expect(screen.queryByTestId("practitioner-edit")).toBeNull();
  });

  it("asks before abandoning typed changes", async () => {
    render(<PractitionerAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Dr. Grace Hopper" },
    });

    press(screen.getByText("Back to the roster"));

    // Radix renders the confirmation into a portal on document.body, so it is reached
    // with `screen.*` and never with `within(container)`.
    expect(await screen.findByTestId("discard-confirm")).toBeInTheDocument();
    // Nothing has been abandoned yet: the edit view is still there behind it.
    expect(screen.getByTestId("practitioner-edit")).toBeInTheDocument();
  });

  it("keeps the edit view and the typed text when the discard is refused", async () => {
    render(<PractitionerAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Dr. Grace Hopper" },
    });
    press(screen.getByText("Back to the roster"));

    const confirm = await screen.findByTestId("discard-confirm");
    press(within(confirm).getByRole("button", { name: "Keep editing" }));

    await waitFor(() => expect(screen.queryByTestId("discard-confirm")).toBeNull());
    expect(screen.getByTestId("practitioner-edit")).toBeInTheDocument();
    expect(screen.getByLabelText("Full name")).toHaveValue("Dr. Grace Hopper");
  });

  it("returns to the roster, saving nothing, when the discard is confirmed", async () => {
    const update = vi.spyOn(consoleApi, "updatePractitioner");

    render(<PractitionerAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Dr. Grace Hopper" },
    });
    press(screen.getByText("Back to the roster"));
    press(
      within(await screen.findByTestId("discard-confirm")).getByRole("button", {
        name: "Discard",
      }),
    );

    expect(await screen.findByTestId("practitioner")).toBeInTheDocument();
    // FR-035b: leaving never writes. The roster still reads what the scheduler stored.
    expect(update).not.toHaveBeenCalled();
    expect(screen.getByTestId("practitioner")).toHaveTextContent(
      "Dr. Ada Lovelace",
    );
  });

  it("asks before abandoning a half-typed create", async () => {
    render(<PractitionerAdmin />);
    await openTheCreateView();
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Dr. Grace Hopper" },
    });

    press(screen.getByText("Back to the roster"));

    expect(await screen.findByTestId("discard-confirm")).toBeInTheDocument();
  });

  it("returns from an untouched create view, asking nothing", async () => {
    render(<PractitionerAdmin />);
    await openTheCreateView();

    press(screen.getByText("Back to the roster"));

    expect(screen.queryByTestId("discard-confirm")).toBeNull();
    expect(await screen.findByTestId("practitioner")).toBeInTheDocument();
  });
});

describe("PractitionerAdmin: the appointments that cannot be read yet", () => {
  // FR-033 and SC-008: a gap the backend cannot fill reads as deliberately absent -
  // never as an error, and never as an empty result that would assert there are none.
  it("says the next seven days are not available yet, and what will appear there", async () => {
    render(<PractitionerAdmin />);
    const view = await openTheEditView();

    const stub = within(view).getByTestId("appointments-stub");
    expect(stub).toHaveTextContent(/not yet available/i);
    expect(stub).toHaveTextContent(/seven days/i);
    // Naming what will appear there, rather than only that something is missing.
    expect(stub).toHaveTextContent(/appointment/i);
  });

  it("renders no appointment list, empty or otherwise", async () => {
    render(<PractitionerAdmin />);
    await openTheEditView();

    const stub = screen.getByTestId("appointments-stub");
    expect(stub.querySelector("ul")).toBeNull();
    expect(stub.querySelector("ol")).toBeNull();
    expect(stub.querySelector("table")).toBeNull();
    expect(stub).not.toHaveTextContent(/no appointments|none booked|nothing/i);
  });

  it("is not reported as an error", async () => {
    render(<PractitionerAdmin />);
    await openTheEditView();

    expect(screen.getByTestId("appointments-stub")).toBeInTheDocument();
    expect(screen.queryByTestId("practitioner-error")).toBeNull();
  });

  it("makes no claim about a practitioner who does not exist yet", async () => {
    // A practitioner being created has no id, so there is nothing whose appointments
    // this panel could be about.
    render(<PractitionerAdmin />);
    await openTheCreateView();

    expect(screen.queryByTestId("appointments-stub")).toBeNull();
  });
});

describe("PractitionerAdmin: the specialty chooser", () => {
  it("offers the set the scheduler publishes, rather than a list of its own", async () => {
    // A list written out on this side is one the enum can be extended without, and a
    // value it does not recognise is refused at the write with nothing on screen to
    // explain why.
    render(<PractitionerAdmin />);
    await openTheEditView();

    const chooser = screen.getByLabelText("Specialty");
    await waitFor(() =>
      expect(
        [...chooser.querySelectorAll("option")].map((o) => o.value),
      ).toEqual(SPECIALTIES),
    );
  });

  it("shows a practitioner as what they are, not as the first option", async () => {
    // A `<select>` whose value matches no option renders the first one instead, so a
    // chooser missing a stored specialty states, confidently, that a dentist is a
    // general practitioner.
    vi.spyOn(consoleApi, "fetchPractitioners").mockResolvedValue([
      practitioner({ specialty: "Dentistry" }),
    ]);
    vi.spyOn(consoleApi, "fetchSpecialties").mockResolvedValue([
      "Cardiology",
      "General Practice",
    ]);

    render(<PractitionerAdmin />);
    await openTheEditView();

    const chooser = screen.getByLabelText("Specialty");
    await waitFor(() =>
      expect((chooser as HTMLSelectElement).value).toBe("Dentistry"),
    );
  });

  it("still shows the stored specialty when the set could not be read", async () => {
    vi.spyOn(consoleApi, "fetchPractitioners").mockResolvedValue([
      practitioner({ specialty: "Dentistry" }),
    ]);
    vi.spyOn(consoleApi, "fetchSpecialties").mockRejectedValue(
      new Error("scheduling is unavailable; nothing was changed"),
    );

    render(<PractitionerAdmin />);
    await openTheEditView();

    const chooser = screen.getByLabelText("Specialty");
    await waitFor(() =>
      expect((chooser as HTMLSelectElement).value).toBe("Dentistry"),
    );
    expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
      "scheduling is unavailable",
    );
  });

  it("sends the chosen specialty exactly as the scheduler named it", async () => {
    const update = vi
      .spyOn(consoleApi, "updatePractitioner")
      .mockResolvedValue(practitioner({ specialty: "Dentistry" }));

    render(<PractitionerAdmin />);
    await openTheEditView();
    const chooser = screen.getByLabelText("Specialty");
    await waitFor(() =>
      expect(chooser.querySelectorAll("option")).toHaveLength(
        SPECIALTIES.length,
      ),
    );
    fireEvent.change(chooser, { target: { value: "Dentistry" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        "01PRACT0000000000000000000",
        expect.objectContaining({ specialty: "Dentistry" }),
      ),
    );
  });
});

describe("PractitionerAdmin: creating", () => {
  it("creates with every field blank and shows back the name it was given", async () => {
    // The defaults, including the pool-assigned name, belong to the service that owns
    // practitioners. This screen supplies none of them, so an empty create is valid
    // and the name that comes back is the answer rather than an echo.
    const create = vi
      .spyOn(consoleApi, "createPractitioner")
      .mockResolvedValue(
        practitioner({ id: "01NEW", full_name: "Dr. Grace Hopper" }),
      );

    render(<PractitionerAdmin />);
    await openTheCreateView();
    fireEvent.click(screen.getByText("Add practitioner"));

    await waitFor(() => expect(create).toHaveBeenCalledWith({}));
    // The roster is what shows the answer back, since a landed create returns to it.
    expect(await screen.findByText("Dr. Grace Hopper")).toBeInTheDocument();
  });

  it("renders a refusal's own reason, and changes nothing", async () => {
    // FR-035: the reason is the scheduler's, in its own words. Restating it here would
    // be this screen inventing a rule it does not own.
    vi.spyOn(consoleApi, "createPractitioner").mockRejectedValue(
      new Error("another practitioner in this session already has that name"),
    );

    render(<PractitionerAdmin />);
    await openTheCreateView();
    fireEvent.click(screen.getByText("Add practitioner"));

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
        "already has that name",
      ),
    );
    // Nothing was added, which is read off the roster - the refusal left the create
    // view up rather than returning to it, and an untouched form leaves without asking.
    press(screen.getByText("Back to the roster"));
    expect(await screen.findAllByTestId("practitioner")).toHaveLength(1);
  });
});

describe("PractitionerAdmin: editing", () => {
  it("saves the fields and the working hours together", async () => {
    const update = vi.spyOn(consoleApi, "updatePractitioner").mockResolvedValue(
      practitioner({
        full_name: "Dr. Grace Hopper",
        schedule: [{ weekday: 0, start_time: "10:00", end_time: "17:00" }],
      }),
    );

    render(<PractitionerAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Dr. Grace Hopper" },
    });
    fireEvent.change(screen.getByLabelText("Start time"), {
      target: { value: "10:00" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("01PRACT0000000000000000000", {
        full_name: "Dr. Grace Hopper",
        specialty: "General Practice",
        appointment_duration_minutes: 30,
        schedule: [{ weekday: 0, start_time: "10:00", end_time: "17:00" }],
      }),
    );
  });

  it("renders the row the server stored, not the one that was typed", async () => {
    vi.spyOn(consoleApi, "updatePractitioner").mockResolvedValue(
      practitioner({ full_name: "Dr. Grace B. Hopper" }),
    );

    render(<PractitionerAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Dr. Grace Hopper" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByTestId("practitioner")).toHaveTextContent(
        "Dr. Grace B. Hopper",
      ),
    );
  });

  it("shows an overlapping-hours refusal beside the practitioner it was about", async () => {
    vi.spyOn(consoleApi, "updatePractitioner").mockRejectedValue(
      new Error("working ranges on one weekday must not overlap"),
    );

    render(<PractitionerAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Start time"), {
      target: { value: "10:00" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
        "must not overlap",
      ),
    );
    // Nothing was changed by a refused request: leaving shows the roster still reading
    // what the scheduler stored, and the form still holds what was typed to correct.
    expect(screen.getByLabelText("Start time")).toHaveValue("10:00");
    press(screen.getByText("Back to the roster"));
    press(
      within(await screen.findByTestId("discard-confirm")).getByRole("button", {
        name: "Discard",
      }),
    );
    expect(await screen.findByTestId("practitioner")).toHaveTextContent(
      "09:00",
    );
  });

  it("adds and removes working ranges", async () => {
    render(<PractitionerAdmin />);
    await openTheEditView();

    fireEvent.click(screen.getByText("Add hours"));
    expect(screen.getAllByTestId("working-range")).toHaveLength(2);

    fireEvent.click(screen.getAllByText("Remove")[1]!);
    expect(screen.getAllByTestId("working-range")).toHaveLength(1);
  });
});

describe("PractitionerAdmin: deleting", () => {
  it("removes the practitioner and their appointments", async () => {
    const remove = vi
      .spyOn(consoleApi, "deletePractitioner")
      .mockResolvedValue(undefined);

    render(<PractitionerAdmin />);
    fireEvent.click(await screen.findByLabelText("Delete Dr. Ada Lovelace"));

    await waitFor(() =>
      expect(remove).toHaveBeenCalledWith("01PRACT0000000000000000000"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("no-practitioners")).toBeInTheDocument(),
    );
  });

  it("leaves the practitioner in place when the delete is refused", async () => {
    vi.spyOn(consoleApi, "deletePractitioner").mockRejectedValue(
      new Error(
        "scheduling did not answer; the change may not have been applied",
      ),
    );

    render(<PractitionerAdmin />);
    fireEvent.click(await screen.findByLabelText("Delete Dr. Ada Lovelace"));

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
        "may not have been applied",
      ),
    );
    expect(screen.getByTestId("practitioner")).toHaveTextContent(
      "Dr. Ada Lovelace",
    );
  });
});

describe("PractitionerAdmin: the credential the page never holds", () => {
  // SC-012: the session lives in an `HttpOnly` cookie, so the browser cannot address
  // the scheduler itself - and must not, or the credential would have to be readable
  // for it to try. These two exercise the real fetch layer rather than a spy over it.
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("[]", {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  });

  it("sends every practitioner request to this app's own origin", async () => {
    render(<PractitionerAdmin />);

    await waitFor(() =>
      expect(vi.mocked(globalThis.fetch).mock.calls.length).toBeGreaterThan(0),
    );
    const urls = vi
      .mocked(globalThis.fetch)
      .mock.calls.map((call) => String(call[0]));
    for (const url of urls) {
      expect(url.startsWith("/console/")).toBe(true);
    }
  });

  it("carries no session header of its own", async () => {
    render(<PractitionerAdmin />);

    await waitFor(() =>
      expect(vi.mocked(globalThis.fetch).mock.calls.length).toBeGreaterThan(0),
    );
    for (const [, init] of vi.mocked(globalThis.fetch).mock.calls) {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain(
        "x-session-id",
      );
    }
  });
});

describe("PractitionerAdmin: writes that must happen once", () => {
  it("adds one practitioner, not two, when the button is clicked again before it lands", async () => {
    // A second click carries the very same form, so nothing about it looks different
    // from the first - it simply creates a second row, with a second pool-assigned name.
    let landCreate: (created: Practitioner) => void = () => undefined;
    const create = vi.spyOn(consoleApi, "createPractitioner").mockReturnValue(
      new Promise<Practitioner>((resolve) => {
        landCreate = resolve;
      }),
    );

    render(<PractitionerAdmin />);
    await openTheCreateView();
    fireEvent.click(screen.getByText("Add practitioner"));
    fireEvent.click(screen.getByText("Add practitioner"));

    expect(create).toHaveBeenCalledTimes(1);

    await act(async () => {
      landCreate(practitioner({ id: "01PRACT0000000000000000002" }));
    });
  });

  it("lets the duration be cleared and retyped", async () => {
    // `Number("")` is 0, so writing it into the row repainted the field as a "0" the
    // staff member had to clear before they could type anything.
    render(<PractitionerAdmin />);
    await openTheEditView();
    const minutes = screen.getByLabelText("Appointment minutes");

    fireEvent.change(minutes, { target: { value: "" } });

    expect(minutes).toHaveValue(null);
    fireEvent.change(minutes, { target: { value: "45" } });
    expect(minutes).toHaveValue(45);
  });

  it("refuses to save a duration that is not a number yet", async () => {
    // Rather than sending the last one that was: the staff member is mid-edit, and
    // saving a value they have already typed over is a change they did not ask for.
    // `Number("")` would have sent 0; a half-typed "1e" is NaN, which `JSON.stringify`
    // writes as an explicit null on a PATCH whose contract leaves omitted fields alone.
    const save = vi.spyOn(consoleApi, "updatePractitioner");

    render(<PractitionerAdmin />);
    await openTheEditView();
    const minutes = screen.getByLabelText("Appointment minutes");
    fireEvent.change(minutes, { target: { value: "" } });
    fireEvent.click(screen.getByText("Save"));

    expect(save).not.toHaveBeenCalled();
    expect(screen.getByTestId("practitioner-error")).toBeInTheDocument();
  });

  it("saves the number once the duration is one again", async () => {
    const save = vi
      .spyOn(consoleApi, "updatePractitioner")
      .mockResolvedValue(practitioner({ appointment_duration_minutes: 45 }));

    render(<PractitionerAdmin />);
    await openTheEditView();
    const minutes = screen.getByLabelText("Appointment minutes");
    fireEvent.change(minutes, { target: { value: "" } });
    fireEvent.change(minutes, { target: { value: "45" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "01PRACT0000000000000000000",
        expect.objectContaining({ appointment_duration_minutes: 45 }),
      ),
    );
  });

  // `" 30"`, `"+30"` and `"0x10"` belong on this list and are absent from it: jsdom's
  // number input refuses them outright, firing no change at all, so a test here would
  // pass without the guard ever running. A real browser hands them over, which is why
  // the guard tests the text rather than what `Number` makes of it.
  it.each(["1e3", "1.0", "-5"])(
    "does not read %s as a whole number of minutes",
    async (typed) => {
      // `Number("1e3")` is 1000 and `Number.isInteger(1000)` is true, so the check this
      // replaced let a 1000-minute appointment into the row - one the assistant then
      // books against - while the field repainted as "1000" under the cursor.
      const save = vi.spyOn(consoleApi, "updatePractitioner");

      render(<PractitionerAdmin />);
      await openTheEditView();
      const minutes = screen.getByLabelText("Appointment minutes");
      fireEvent.change(minutes, { target: { value: typed } });

      // What was typed is still on screen, unrewritten, and there is nothing to send.
      expect(minutes).toHaveDisplayValue(typed);
      fireEvent.click(screen.getByText("Save"));
      expect(save).not.toHaveBeenCalled();
      expect(screen.getByTestId("practitioner-error")).toBeInTheDocument();
    },
  );

  it("saves a leading zero as the number it is, without rewriting the field", async () => {
    // "05" is 5 minutes written oddly, not a field with nothing in it: repainting it as
    // "5" moves the text under the cursor, and refusing to save it would make a staff
    // member retype a number they had already typed.
    const save = vi
      .spyOn(consoleApi, "updatePractitioner")
      .mockResolvedValue(practitioner({ appointment_duration_minutes: 5 }));

    render(<PractitionerAdmin />);
    await openTheEditView();
    const minutes = screen.getByLabelText("Appointment minutes");
    fireEvent.change(minutes, { target: { value: "05" } });

    expect(minutes).toHaveDisplayValue("05");
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "01PRACT0000000000000000000",
        expect.objectContaining({ appointment_duration_minutes: 5 }),
      ),
    );
  });

  it("leaves how long an appointment may be to the service that owns the rule", async () => {
    // The screen refuses a field that is not a number; it carries no bound of its own.
    // A 2-minute appointment is the scheduler's to refuse, in the scheduler's words -
    // a client-side bound would be a second copy of that rule, free to disagree.
    const save = vi
      .spyOn(consoleApi, "updatePractitioner")
      .mockRejectedValue(
        new Error("appointment_duration_minutes must be 5 to 480"),
      );

    render(<PractitionerAdmin />);
    await openTheEditView();
    const minutes = screen.getByLabelText("Appointment minutes");
    fireEvent.change(minutes, { target: { value: "2" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(
        "01PRACT0000000000000000000",
        expect.objectContaining({ appointment_duration_minutes: 2 }),
      ),
    );
    expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
      "appointment_duration_minutes must be 5 to 480",
    );
  });

  it("does not save or delete a practitioner twice on a double click", async () => {
    // The latch was on Add alone. A double-clicked Save ran the write twice; a
    // double-clicked Delete made the second call a 404, reported to the staff member as
    // a failure for a delete that worked.
    let landSave!: (saved: Practitioner) => void;
    const save = vi.spyOn(consoleApi, "updatePractitioner").mockReturnValue(
      new Promise<Practitioner>((resolve) => {
        landSave = resolve;
      }),
    );
    let landDelete!: () => void;
    const remove = vi.spyOn(consoleApi, "deletePractitioner").mockReturnValue(
      new Promise<void>((resolve) => {
        landDelete = resolve;
      }),
    );

    render(<PractitionerAdmin />);
    await openTheEditView();

    fireEvent.click(screen.getByText("Save"));
    fireEvent.click(screen.getByText("Save"));
    expect(save).toHaveBeenCalledTimes(1);
    await act(async () => {
      landSave(practitioner());
    });

    // The save landed, so the view is back on the roster the delete is reached from.
    await screen.findByLabelText("Delete Dr. Ada Lovelace");
    fireEvent.click(screen.getByLabelText("Delete Dr. Ada Lovelace"));
    fireEvent.click(screen.getByLabelText("Delete Dr. Ada Lovelace"));
    expect(remove).toHaveBeenCalledTimes(1);
    await act(async () => {
      landDelete();
    });
  });
});
