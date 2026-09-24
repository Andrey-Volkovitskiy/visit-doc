import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FaqAdmin } from "../src/components/FaqAdmin";
import * as consoleApi from "../src/lib/consoleApi";
import type { FaqEntry } from "../src/lib/consoleApi";
import { press } from "./press";

/** Open entry 1 for editing, and wait for the edit view. */
async function openTheEditView(): Promise<HTMLElement> {
  press(await screen.findByLabelText("Edit entry 1"));
  return await screen.findByTestId("faq-edit");
}

/**
 * Ask to delete the named entry, and answer the confirmation with Delete.
 *
 * By name, not by position: the vendored dialog renders its own close control first, so
 * "the first button in the dialog" is the X, which would dismiss the prompt and report
 * nothing — and the test would fail for a reason unrelated to what it protects.
 */
async function deleteAndConfirm(label: string): Promise<void> {
  fireEvent.click(await screen.findByLabelText(label));
  fireEvent.click(
    within(await screen.findByTestId("delete-confirm")).getByRole("button", {
      name: "Delete",
    }),
  );
}

/** Open the create view from the list, and wait for it. */
async function openTheCreateView(): Promise<HTMLElement> {
  press(await screen.findByText("Add entry"));
  return await screen.findByTestId("faq-edit");
}

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

    // The list is a record now, not a column of boxes (FR-035a), so the text is read
    // off the entry rather than out of an input.
    expect(
      await screen.findByText("Visiting hours are 8am to 5pm."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Parking is free for the first hour."),
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

    await screen.findByText("Visiting hours are 8am to 5pm.");
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

describe("FaqAdmin: an entry reads as a question and its answer", () => {
  // FR-034. The seeded corpus labels both halves, and a labelled entry is rendered as
  // the two things it is - the question a patient's phrasing is matched against, and
  // the text the assistant is allowed to say.
  it("distinguishes the question from the answer", async () => {
    vi.spyOn(consoleApi, "fetchFaqEntries").mockResolvedValue([
      entry({
        id: 1,
        content:
          "Question: Do I need a referral?\nAnswer: You can book without one.",
      }),
    ]);

    render(<FaqAdmin />);

    const row = await screen.findByTestId("faq-entry");
    const question = within(row).getByRole("heading");
    expect(question).toHaveTextContent("Do I need a referral?");
    expect(question).not.toHaveTextContent("You can book without one.");
    expect(row).toHaveTextContent("You can book without one.");
    // The labels are what the split reads, not what it shows: an entry rendered with
    // them still on it is one where nothing was distinguished.
    expect(row).not.toHaveTextContent("Question:");
    expect(row).not.toHaveTextContent("Answer:");
  });

  it("invents no question for an entry that carries none", async () => {
    // Entry text is free text a staff member typed, so an entry may be a plain
    // statement. Promoting its first line to a question would be this screen
    // asserting a shape the entry does not have.
    render(<FaqAdmin />);

    const row = await screen.findByTestId("faq-entry");
    expect(within(row).queryByRole("heading")).toBeNull();
    expect(row).toHaveTextContent("Visiting hours are 8am to 5pm.");
  });

  it("offers create, edit and delete from the entry each acts on", async () => {
    render(<FaqAdmin />);

    const row = await screen.findByTestId("faq-entry");
    expect(screen.getByText("Add entry")).toBeInTheDocument();
    expect(within(row).getByLabelText("Edit entry 1")).toBeInTheDocument();
    expect(within(row).getByLabelText("Delete entry 1")).toBeInTheDocument();
  });

  it("names what a refused save failed at, while the edit view is still open", async () => {
    // FR-035: the reason is the server's own, and it has to be readable from where the
    // staff member is standing - which, after a refused save, is the edit view.
    vi.spyOn(consoleApi, "updateFaqEntry").mockRejectedValue(
      new Error("That entry was changed by another save. Please try again."),
    );

    render(<FaqAdmin />);
    await openTheEditView();
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByTestId("faq-error")).toHaveTextContent(
        "changed by another save",
      ),
    );
    expect(screen.getByTestId("faq-edit")).toBeInTheDocument();
  });
});

describe("FaqAdmin: the edit view replaces the tab's content", () => {
  // FR-035a. Not an overlay and not an expansion in place.
  it("replaces the list when an entry is opened", async () => {
    render(<FaqAdmin />);
    await openTheEditView();

    expect(screen.queryByTestId("faq-entry")).toBeNull();
    expect(
      screen.getByDisplayValue("Visiting hours are 8am to 5pm."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("replaces the list when an entry is being written", async () => {
    render(<FaqAdmin />);
    await openTheCreateView();

    expect(screen.queryByTestId("faq-entry")).toBeNull();
    expect(screen.getByLabelText("New entry")).toHaveValue("");
  });

  it("returns to the list from an untouched edit view, asking nothing", async () => {
    render(<FaqAdmin />);
    await openTheEditView();

    press(screen.getByText("Back to the documents"));

    expect(screen.queryByTestId("discard-confirm")).toBeNull();
    expect(await screen.findByTestId("faq-entry")).toBeInTheDocument();
    expect(screen.queryByTestId("faq-edit")).toBeNull();
  });

  it("asks before abandoning typed changes", async () => {
    render(<FaqAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Entry 1"), {
      target: { value: "Visiting hours are 9am to 6pm." },
    });

    press(screen.getByText("Back to the documents"));

    // Portalled onto document.body, so `screen.*` and never `within(container)`.
    expect(await screen.findByTestId("discard-confirm")).toBeInTheDocument();
    expect(screen.getByTestId("faq-edit")).toBeInTheDocument();
  });

  it("keeps the edit view and the typed text when the discard is refused", async () => {
    render(<FaqAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Entry 1"), {
      target: { value: "Visiting hours are 9am to 6pm." },
    });
    press(screen.getByText("Back to the documents"));

    press(
      within(await screen.findByTestId("discard-confirm")).getByRole("button", {
        name: "Keep editing",
      }),
    );

    await waitFor(() =>
      expect(screen.queryByTestId("discard-confirm")).toBeNull(),
    );
    expect(screen.getByLabelText("Entry 1")).toHaveValue(
      "Visiting hours are 9am to 6pm.",
    );
  });

  it("returns to the list, saving nothing, when the discard is confirmed", async () => {
    const update = vi.spyOn(consoleApi, "updateFaqEntry");

    render(<FaqAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Entry 1"), {
      target: { value: "Visiting hours are 9am to 6pm." },
    });
    press(screen.getByText("Back to the documents"));
    press(
      within(await screen.findByTestId("discard-confirm")).getByRole("button", {
        name: "Discard",
      }),
    );

    expect(await screen.findByTestId("faq-entry")).toBeInTheDocument();
    // FR-035b: leaving never writes. The entry still answers what it answered.
    expect(update).not.toHaveBeenCalled();
    expect(screen.getByTestId("faq-entry")).toHaveTextContent(
      "Visiting hours are 8am to 5pm.",
    );
  });

  it("asks before abandoning a half-written entry", async () => {
    render(<FaqAdmin />);
    await openTheCreateView();
    fireEvent.change(screen.getByLabelText("New entry"), {
      target: { value: "Parking is free." },
    });

    press(screen.getByText("Back to the documents"));

    expect(await screen.findByTestId("discard-confirm")).toBeInTheDocument();
  });

  it("returns from an untouched create view, asking nothing", async () => {
    render(<FaqAdmin />);
    await openTheCreateView();

    press(screen.getByText("Back to the documents"));

    expect(screen.queryByTestId("discard-confirm")).toBeNull();
    expect(await screen.findByTestId("faq-entry")).toBeInTheDocument();
  });
});

describe("FaqAdmin: writing", () => {
  it("adds an entry and shows what was stored", async () => {
    const create = vi
      .spyOn(consoleApi, "createFaqEntry")
      .mockResolvedValue(entry({ id: 2, content: "Parking is free." }));

    render(<FaqAdmin />);
    await openTheCreateView();
    fireEvent.change(screen.getByLabelText("New entry"), {
      target: { value: "Parking is free." },
    });
    fireEvent.click(screen.getByText("Add entry"));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("Parking is free."),
    );
    expect(await screen.findByText("Parking is free.")).toBeInTheDocument();
    // The box being cleared is now the create view being left: what was stored is on
    // the list, and the next entry starts from an empty box rather than from the last
    // one, which is the property the cleared box protected.
    await openTheCreateView();
    expect(screen.getByLabelText("New entry")).toHaveValue("");
  });

  it("renders a refused create's reason and adds nothing", async () => {
    vi.spyOn(consoleApi, "createFaqEntry").mockRejectedValue(
      new Error("this session's corpus is full (200 entries) - delete one first"),
    );

    render(<FaqAdmin />);
    await openTheCreateView();
    fireEvent.change(screen.getByLabelText("New entry"), {
      target: { value: "One too many." },
    });
    fireEvent.click(screen.getByText("Add entry"));

    await waitFor(() =>
      expect(screen.getByTestId("faq-error")).toHaveTextContent("corpus is full"),
    );
    // What was typed is kept: the entry was not saved, and retyping it is the one
    // thing a failed save must not ask for.
    expect(screen.getByLabelText("New entry")).toHaveValue("One too many.");
    // And nothing was added - which is read off the list, since the refusal left the
    // create view up rather than returning to it.
    press(screen.getByText("Back to the documents"));
    press(
      within(await screen.findByTestId("discard-confirm")).getByRole("button", {
        name: "Discard",
      }),
    );
    expect(await screen.findAllByTestId("faq-entry")).toHaveLength(1);
  });

  it("saves an edit and shows the text the server stored", async () => {
    const update = vi
      .spyOn(consoleApi, "updateFaqEntry")
      .mockResolvedValue(entry({ id: 1, content: "Visiting hours are 9am to 6pm." }));

    render(<FaqAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Entry 1"), {
      target: { value: "Visiting hours are 9am to 6pm." },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(1, "Visiting hours are 9am to 6pm."),
    );
    expect(
      await screen.findByText("Visiting hours are 9am to 6pm."),
    ).toBeInTheDocument();
  });

  it("leaves the entry answering its old text when a save is refused", async () => {
    // A save that lost a race, or hit an unreachable dependency, changed nothing —
    // and the screen must not imply otherwise.
    vi.spyOn(consoleApi, "updateFaqEntry").mockRejectedValue(
      new Error("That entry was changed by another save. Please try again."),
    );

    render(<FaqAdmin />);
    await openTheEditView();
    fireEvent.change(screen.getByLabelText("Entry 1"), {
      target: { value: "Visiting hours are 9am to 6pm." },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByTestId("faq-error")).toHaveTextContent(
        "changed by another save",
      ),
    );
    // The refusal changed nothing, and the screen must not imply otherwise: leaving
    // shows the entry still answering the text it answered before.
    press(screen.getByText("Back to the documents"));
    press(
      within(await screen.findByTestId("discard-confirm")).getByRole("button", {
        name: "Discard",
      }),
    );
    expect(
      await screen.findByText("Visiting hours are 8am to 5pm."),
    ).toBeInTheDocument();
  });

  it("asks before deleting, and deletes nothing until confirmed", async () => {
    const remove = vi
      .spyOn(consoleApi, "deleteFaqEntry")
      .mockResolvedValue(undefined);

    render(<FaqAdmin />);
    fireEvent.click(await screen.findByLabelText("Delete entry 1"));

    // What is lost is what the assistant could have answered from, which is the whole
    // reason this deletion is worth asking about.
    expect(await screen.findByTestId("delete-confirm")).toHaveTextContent(
      "the assistant can no longer answer from it",
    );
    expect(remove).not.toHaveBeenCalled();
    expect(screen.getByTestId("faq-entry")).toBeInTheDocument();
  });

  it("names the entry by its question where it has one", async () => {
    // "This entry" tells two entries apart no better than nothing does, and the row it
    // was clicked on is behind the overlay. An entry carrying no question is not given
    // one: `splitEntry` does not invent a shape, and neither does the prompt.
    vi.spyOn(consoleApi, "fetchFaqEntries").mockResolvedValue([
      entry({
        id: 1,
        content: "Question: Do I need a referral?\nAnswer: You can book without one.",
      }),
    ]);

    render(<FaqAdmin />);
    fireEvent.click(await screen.findByLabelText("Delete entry 1"));

    expect(await screen.findByTestId("delete-confirm")).toHaveTextContent(
      "Do I need a referral?",
    );
  });

  it("cancels a deletion without reporting it", async () => {
    const remove = vi
      .spyOn(consoleApi, "deleteFaqEntry")
      .mockResolvedValue(undefined);

    render(<FaqAdmin />);
    fireEvent.click(await screen.findByLabelText("Delete entry 1"));
    fireEvent.click(
      within(await screen.findByTestId("delete-confirm")).getByRole("button", {
        name: "Cancel",
      }),
    );

    await waitFor(() =>
      expect(screen.queryByTestId("delete-confirm")).toBeNull(),
    );
    expect(remove).not.toHaveBeenCalled();
    expect(
      screen.getByText("Visiting hours are 8am to 5pm."),
    ).toBeInTheDocument();
  });

  it("deletes an entry once confirmed", async () => {
    const remove = vi
      .spyOn(consoleApi, "deleteFaqEntry")
      .mockResolvedValue(undefined);

    render(<FaqAdmin />);
    await deleteAndConfirm("Delete entry 1");

    await waitFor(() => expect(remove).toHaveBeenCalledWith(1));
    await waitFor(() =>
      expect(screen.getByTestId("no-faq-entries")).toBeInTheDocument(),
    );
  });

  it("adds one entry, not two, when the button is clicked again before it lands", async () => {
    // The box is only cleared once the create lands, so a second click before then reads
    // the very same text and passes the very same guard. Two identical entries are each
    // chunked, embedded and indexed, and each counts against the session's entry cap.
    let landCreate: (created: FaqEntry) => void = () => undefined;
    const create = vi.spyOn(consoleApi, "createFaqEntry").mockReturnValue(
      new Promise<FaqEntry>((resolve) => {
        landCreate = resolve;
      }),
    );

    render(<FaqAdmin />);
    await openTheCreateView();
    fireEvent.change(screen.getByLabelText("New entry"), {
      target: { value: "We open at 8am." },
    });
    fireEvent.click(screen.getByText("Add entry"));
    fireEvent.click(screen.getByText("Add entry"));

    expect(create).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledWith("We open at 8am.");

    await act(async () => {
      landCreate(entry({ id: 9, content: "We open at 8am." }));
    });
  });

  it("takes the next entry after one that failed", async () => {
    // The latch is released on the error path too: the text stays in the box by design,
    // and a latch left closed would leave a staff member holding an entry they can no
    // longer add.
    const create = vi
      .spyOn(consoleApi, "createFaqEntry")
      .mockRejectedValueOnce(new Error("nope"))
      .mockResolvedValue(entry({ id: 9, content: "We open at 8am." }));

    render(<FaqAdmin />);
    await openTheCreateView();
    fireEvent.change(screen.getByLabelText("New entry"), {
      target: { value: "We open at 8am." },
    });
    fireEvent.click(screen.getByText("Add entry"));
    await waitFor(() => expect(screen.getByTestId("faq-error")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Add entry"));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  });

  it("sends nothing for whitespace alone", async () => {
    const create = vi.spyOn(consoleApi, "createFaqEntry");

    render(<FaqAdmin />);
    await openTheCreateView();
    fireEvent.change(screen.getByLabelText("New entry"), {
      target: { value: "   " },
    });
    // Not disabled: a click that never reaches the handler would make "not called"
    // free, and prove nothing about the guard this test is here for.
    expect(screen.getByText("Add entry")).toBeEnabled();
    fireEvent.click(screen.getByText("Add entry"));

    expect(create).not.toHaveBeenCalled();
  });

  it("does not save or delete an entry twice on a double click", async () => {
    // The latch was on Add alone. A save is a whole revision write - the entry is
    // chunked, embedded and indexed again - so a double click did that twice, and the
    // second publish then failed its own staleness guard against the revision the first
    // had already published, reporting a conflict over an entry that saved fine.
    let landSave!: (saved: FaqEntry) => void;
    const save = vi.spyOn(consoleApi, "updateFaqEntry").mockReturnValue(
      new Promise<FaqEntry>((resolve) => {
        landSave = resolve;
      }),
    );
    let landDelete!: () => void;
    const remove = vi.spyOn(consoleApi, "deleteFaqEntry").mockReturnValue(
      new Promise<void>((resolve) => {
        landDelete = resolve;
      }),
    );

    render(<FaqAdmin />);
    await openTheEditView();

    fireEvent.click(screen.getByText("Save"));
    fireEvent.click(screen.getByText("Save"));
    expect(save).toHaveBeenCalledTimes(1);
    await act(async () => {
      landSave(entry());
    });

    // The save landed, so the view is back on the list the delete is reached from.
    // The confirmation makes a double-clicked Delete harmless by itself — the second
    // click only re-opens the same question — so what the latch is still holding is the
    // gesture *after* a confirmed delete, while that delete is in flight: the control
    // that would raise the question a second time is refused until it lands.
    const deleteLabel = `Delete entry ${String(entry().id)}`;
    await deleteAndConfirm(deleteLabel);
    expect(remove).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(screen.getByLabelText(deleteLabel)).toBeDisabled(),
    );
    await act(async () => {
      landDelete();
    });
  });
});

describe("FaqAdmin: a write that lands after its view was left", () => {
  it("returns to the list only from the view the write was submitted from", async () => {
    // A save outlived its view: the staff member went back and started a new entry
    // while it was out. Landing must not take that new view - and its unsaved text -
    // away with no prompt, which is the loss the discard confirmation exists for.
    let landSave!: (saved: FaqEntry) => void;
    vi.spyOn(consoleApi, "updateFaqEntry").mockReturnValue(
      new Promise<FaqEntry>((resolve) => {
        landSave = resolve;
      }),
    );

    render(<FaqAdmin />);
    await openTheEditView();
    fireEvent.click(screen.getByText("Save"));
    // Untouched, so going back asks nothing.
    press(screen.getByText("Back to the documents"));
    await openTheCreateView();
    fireEvent.change(screen.getByLabelText("New entry"), {
      target: { value: "We open at 8am." },
    });

    await act(async () => {
      landSave(entry({ content: "Visiting hours are 9am to 6pm." }));
    });

    expect(screen.getByTestId("faq-edit")).toBeInTheDocument();
    expect(screen.getByLabelText("New entry")).toHaveValue("We open at 8am.");
  });
});
