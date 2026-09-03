import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PractitionerAdmin } from "../src/components/PractitionerAdmin";
import * as consoleApi from "../src/lib/consoleApi";
import type { Practitioner } from "../src/lib/consoleApi";

function practitioner(overrides: Partial<Practitioner> = {}): Practitioner {
  return {
    id: "01PRACT0000000000000000000",
    full_name: "Dr. Ada Lovelace",
    specialty: "general_practice",
    appointment_duration_minutes: 30,
    schedule: [{ weekday: 0, start_time: "09:00", end_time: "17:00" }],
    ...overrides,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(consoleApi, "fetchPractitioners").mockResolvedValue([practitioner()]);
});

describe("PractitionerAdmin: the roster", () => {
  it("lists the clinic's practitioners with what the assistant books from", async () => {
    render(<PractitionerAdmin />);

    expect(await screen.findByDisplayValue("Dr. Ada Lovelace")).toBeInTheDocument();
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

describe("PractitionerAdmin: creating", () => {
  it("creates with every field blank and shows back the name it was given", async () => {
    // The defaults, including the pool-assigned name, belong to the service that owns
    // practitioners. This screen supplies none of them, so an empty create is valid
    // and the name that comes back is the answer rather than an echo.
    const create = vi
      .spyOn(consoleApi, "createPractitioner")
      .mockResolvedValue(practitioner({ id: "01NEW", full_name: "Dr. Grace Hopper" }));

    render(<PractitionerAdmin />);
    fireEvent.click(await screen.findByText("Add practitioner"));

    await waitFor(() => expect(create).toHaveBeenCalledWith({}));
    expect(
      await screen.findByDisplayValue("Dr. Grace Hopper"),
    ).toBeInTheDocument();
  });

  it("renders a refusal's own reason, and changes nothing", async () => {
    // FR-035: the reason is the scheduler's, in its own words. Restating it here would
    // be this screen inventing a rule it does not own.
    vi.spyOn(consoleApi, "createPractitioner").mockRejectedValue(
      new Error("another practitioner in this session already has that name"),
    );

    render(<PractitionerAdmin />);
    await screen.findByDisplayValue("Dr. Ada Lovelace");
    fireEvent.click(screen.getByText("Add practitioner"));

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
        "already has that name",
      ),
    );
    expect(screen.getAllByTestId("practitioner")).toHaveLength(1);
  });
});

describe("PractitionerAdmin: editing", () => {
  it("saves the fields and the working hours together", async () => {
    const update = vi
      .spyOn(consoleApi, "updatePractitioner")
      .mockResolvedValue(
        practitioner({
          full_name: "Dr. Grace Hopper",
          schedule: [
            { weekday: 0, start_time: "10:00", end_time: "17:00" },
          ],
        }),
      );

    render(<PractitionerAdmin />);
    fireEvent.change(await screen.findByDisplayValue("Dr. Ada Lovelace"), {
      target: { value: "Dr. Grace Hopper" },
    });
    fireEvent.change(screen.getByDisplayValue("09:00"), {
      target: { value: "10:00" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("01PRACT0000000000000000000", {
        full_name: "Dr. Grace Hopper",
        specialty: "general_practice",
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
    fireEvent.change(await screen.findByDisplayValue("Dr. Ada Lovelace"), {
      target: { value: "Dr. Grace Hopper" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByDisplayValue("Dr. Grace B. Hopper")).toBeInTheDocument(),
    );
  });

  it("shows an overlapping-hours refusal beside the practitioner it was about", async () => {
    vi.spyOn(consoleApi, "updatePractitioner").mockRejectedValue(
      new Error("working ranges on one weekday must not overlap"),
    );

    render(<PractitionerAdmin />);
    await screen.findByDisplayValue("Dr. Ada Lovelace");
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
        "must not overlap",
      ),
    );
    // Nothing was changed by a refused request: the row still shows what it had.
    expect(screen.getByDisplayValue("Dr. Ada Lovelace")).toBeInTheDocument();
  });

  it("adds and removes working ranges", async () => {
    render(<PractitionerAdmin />);
    await screen.findByDisplayValue("Dr. Ada Lovelace");

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
      new Error("scheduling did not answer; the change may not have been applied"),
    );

    render(<PractitionerAdmin />);
    fireEvent.click(await screen.findByLabelText("Delete Dr. Ada Lovelace"));

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-error")).toHaveTextContent(
        "may not have been applied",
      ),
    );
    expect(screen.getByDisplayValue("Dr. Ada Lovelace")).toBeInTheDocument();
  });
});

describe("PractitionerAdmin: the credential the page never holds", () => {
  // SC-012: the session lives in an `HttpOnly` cookie, so the browser cannot address
  // the scheduler itself - and must not, or the credential would have to be readable
  // for it to try. These two exercise the real fetch layer rather than a spy over it.
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("[]", { status: 200, headers: { "content-type": "application/json" } }),
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
    // There is no form to read here, so nothing about a second click looks different
    // from the first - it simply creates a second row, with a second pool-assigned name.
    let landCreate: (created: Practitioner) => void = () => undefined;
    const create = vi.spyOn(consoleApi, "createPractitioner").mockReturnValue(
      new Promise<Practitioner>((resolve) => {
        landCreate = resolve;
      }),
    );

    render(<PractitionerAdmin />);
    fireEvent.click(await screen.findByText("Add practitioner"));
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
    const minutes = await screen.findByLabelText("Appointment minutes");

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
    const minutes = await screen.findByLabelText("Appointment minutes");
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
    const minutes = await screen.findByLabelText("Appointment minutes");
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
});
