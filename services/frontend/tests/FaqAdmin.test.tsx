import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FaqAdmin } from "../src/components/FaqAdmin";
import * as consoleApi from "../src/lib/consoleApi";
import type { FaqEntry } from "../src/lib/consoleApi";

function entry(overrides: Partial<FaqEntry> = {}): FaqEntry {
  return {
    id: 1,
    content: "Visiting hours are 8am to 5pm.",
    created_at: "2026-09-01T12:00:00",
    updated_at: "2026-09-01T12:00:00",
    ...overrides,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(consoleApi, "fetchFaqEntries").mockResolvedValue([entry()]);
});

describe("FaqAdmin: what the corpus holds", () => {
  it("shows every entry with its text", async () => {
    // The text is what the assistant answers from, so it is what a staff member has to
    // be able to read and correct.
    vi.spyOn(consoleApi, "fetchFaqEntries").mockResolvedValue([
      entry({ id: 1, content: "Visiting hours are 8am to 5pm." }),
      entry({ id: 2, content: "Parking is free for the first hour." }),
    ]);

    render(<FaqAdmin />);

    expect(
      await screen.findByDisplayValue("Visiting hours are 8am to 5pm."),
    ).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("Parking is free for the first hour."),
    ).toBeInTheDocument();
  });

  it("renders an empty corpus as plainly empty", async () => {
    // FR-039d: the ordinary starting state of every session, not a problem to report.
    vi.spyOn(consoleApi, "fetchFaqEntries").mockResolvedValue([]);

    render(<FaqAdmin />);

    await waitFor(() => expect(screen.getByTestId("no-faq-entries")).toBeInTheDocument());
    expect(screen.queryByTestId("faq-error")).toBeNull();
  });

  it("renders no retrievability state for any entry", async () => {
    // FR-040: an entry owns a live revision or it cannot be stored, so every listed
    // entry is one the assistant can answer from. An indicator here could only ever
    // read "yes" — and a signal that can never fire teaches a staff member to rely on
    // one that would not warn them.
    const { container } = render(<FaqAdmin />);

    await screen.findByDisplayValue("Visiting hours are 8am to 5pm.");
    expect(
      screen.queryByText(/indexed|indexing|retrievable|searchable|ready|pending/i),
    ).toBeNull();
    expect(container.querySelector("[data-retrievable]")).toBeNull();
    expect(container.querySelector("[data-indexed]")).toBeNull();
  });

  it("says why when the corpus could not be read", async () => {
    vi.spyOn(consoleApi, "fetchFaqEntries").mockRejectedValue(
      new Error("the clinic's documents could not be read"),
    );

    render(<FaqAdmin />);

    await waitFor(() =>
      expect(screen.getByTestId("faq-error")).toHaveTextContent(
        "could not be read",
      ),
    );
  });
});

describe("FaqAdmin: writing", () => {
  it("adds an entry and shows what was stored", async () => {
    const create = vi
      .spyOn(consoleApi, "createFaqEntry")
      .mockResolvedValue(entry({ id: 2, content: "Parking is free." }));

    render(<FaqAdmin />);
    fireEvent.change(await screen.findByLabelText("New entry"), {
      target: { value: "Parking is free." },
    });
    fireEvent.click(screen.getByText("Add entry"));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("Parking is free."),
    );
    expect(await screen.findByDisplayValue("Parking is free.")).toBeInTheDocument();
    expect(screen.getByLabelText("New entry")).toHaveValue("");
  });

  it("renders a refused create's reason and adds nothing", async () => {
    vi.spyOn(consoleApi, "createFaqEntry").mockRejectedValue(
      new Error("this session's corpus is full (200 entries) - delete one first"),
    );

    render(<FaqAdmin />);
    fireEvent.change(await screen.findByLabelText("New entry"), {
      target: { value: "One too many." },
    });
    fireEvent.click(screen.getByText("Add entry"));

    await waitFor(() =>
      expect(screen.getByTestId("faq-error")).toHaveTextContent("corpus is full"),
    );
    expect(screen.getAllByTestId("faq-entry")).toHaveLength(1);
    // What was typed is kept: the entry was not saved, and retyping it is the one
    // thing a failed save must not ask for.
    expect(screen.getByLabelText("New entry")).toHaveValue("One too many.");
  });

  it("saves an edit and shows the text the server stored", async () => {
    const update = vi
      .spyOn(consoleApi, "updateFaqEntry")
      .mockResolvedValue(entry({ id: 1, content: "Visiting hours are 9am to 6pm." }));

    render(<FaqAdmin />);
    fireEvent.change(
      await screen.findByDisplayValue("Visiting hours are 8am to 5pm."),
      { target: { value: "Visiting hours are 9am to 6pm." } },
    );
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(1, "Visiting hours are 9am to 6pm."),
    );
    expect(
      await screen.findByDisplayValue("Visiting hours are 9am to 6pm."),
    ).toBeInTheDocument();
  });

  it("leaves the entry answering its old text when a save is refused", async () => {
    // A save that lost a race, or hit an unreachable dependency, changed nothing —
    // and the screen must not imply otherwise.
    vi.spyOn(consoleApi, "updateFaqEntry").mockRejectedValue(
      new Error("That entry was changed by another save. Please try again."),
    );

    render(<FaqAdmin />);
    await screen.findByDisplayValue("Visiting hours are 8am to 5pm.");
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByTestId("faq-error")).toHaveTextContent(
        "changed by another save",
      ),
    );
  });

  it("deletes an entry", async () => {
    const remove = vi
      .spyOn(consoleApi, "deleteFaqEntry")
      .mockResolvedValue(undefined);

    render(<FaqAdmin />);
    fireEvent.click(await screen.findByLabelText("Delete entry 1"));

    await waitFor(() => expect(remove).toHaveBeenCalledWith(1));
    await waitFor(() =>
      expect(screen.getByTestId("no-faq-entries")).toBeInTheDocument(),
    );
  });

  it("sends nothing for whitespace alone", async () => {
    const create = vi.spyOn(consoleApi, "createFaqEntry");

    render(<FaqAdmin />);
    fireEvent.change(await screen.findByLabelText("New entry"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByText("Add entry"));

    expect(create).not.toHaveBeenCalled();
  });
});
