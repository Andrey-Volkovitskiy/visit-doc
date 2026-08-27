import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatList, chatLabel } from "../src/components/ChatList";
import type { ChatSummary } from "../src/lib/chatStream";

function chat(overrides: Partial<ChatSummary> = {}): ChatSummary {
  return {
    id: "01CHAT000000000000000000",
    patient_name: "Ada Lovelace",
    created_at: "2026-08-14T14:32:00",
    last_message_at: null,
    ...overrides,
  };
}

function renderList(chats: ChatSummary[], activeChatId: string | null = null) {
  const handlers = {
    onSelect: vi.fn(),
    onCreate: vi.fn(),
    onDelete: vi.fn(),
    onRename: vi.fn().mockResolvedValue(undefined),
  };
  render(<ChatList chats={chats} activeChatId={activeChatId} {...handlers} />);
  return handlers;
}

describe("ChatList", () => {
  it("lists each chat by its patient's name", () => {
    renderList([
      chat({ id: "a", patient_name: "Ada Lovelace" }),
      chat({ id: "b", patient_name: "Bram Stoker" }),
    ]);

    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("Bram Stoker")).toBeInTheDocument();
  });

  it("labels a chat with no patient yet by its creation time", () => {
    renderList([chat({ patient_name: null, created_at: "2026-08-14T14:32:00" })]);

    expect(screen.getByText("Unnamed · 14:32")).toBeInTheDocument();
  });

  it("pads a single-digit creation time to two digits", () => {
    expect(
      chatLabel(chat({ patient_name: null, created_at: "2026-08-14T09:05:00" })),
    ).toBe("Unnamed · 09:05");
  });

  it("marks the active chat and reports a selection", () => {
    const handlers = renderList(
      [chat({ id: "a", patient_name: "Ada" }), chat({ id: "b", patient_name: "Bram" })],
      "a",
    );

    expect(screen.getByText("Ada")).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("Bram")).not.toHaveAttribute("aria-current");

    fireEvent.click(screen.getByText("Bram"));
    expect(handlers.onSelect).toHaveBeenCalledWith("b");
  });

  it("reports a create request", () => {
    const handlers = renderList([]);

    fireEvent.click(screen.getByText("New chat"));
    expect(handlers.onCreate).toHaveBeenCalled();
  });

  it("keeps the create control usable when the session holds no chats", () => {
    renderList([]);

    expect(screen.getByText("New chat")).toBeEnabled();
    expect(screen.queryAllByTestId("chat-list-item")).toHaveLength(0);
  });

  it("asks for confirmation before deleting, and deletes nothing until confirmed", () => {
    const handlers = renderList([chat({ id: "a", patient_name: "Ada" })]);

    fireEvent.click(screen.getByLabelText("Delete Ada"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(handlers.onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("dialog").querySelector("button")!);
    expect(handlers.onDelete).toHaveBeenCalledWith("a");
  });

  it("cancels a deletion without reporting it", () => {
    const handlers = renderList([chat({ id: "a", patient_name: "Ada" })]);

    fireEvent.click(screen.getByLabelText("Delete Ada"));
    fireEvent.click(screen.getByText("Cancel"));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(handlers.onDelete).not.toHaveBeenCalled();
  });

  it("opens the rename field pre-filled with the current name", () => {
    renderList([chat({ id: "a", patient_name: "Ada" })]);

    fireEvent.click(screen.getByLabelText("Rename Ada"));

    expect(screen.getByLabelText("Patient name")).toHaveValue("Ada");
  });

  it("leaves the rename field empty for a chat with no patient yet", () => {
    // The visible label is derived from a timestamp, not a name anyone chose.
    renderList([chat({ id: "a", patient_name: null })]);

    fireEvent.click(screen.getByLabelText(/^Rename Unnamed/));

    expect(screen.getByLabelText("Patient name")).toHaveValue("");
  });

  it("reports the trimmed name and closes the field", async () => {
    const handlers = renderList([chat({ id: "a", patient_name: "Ada" })]);

    fireEvent.click(screen.getByLabelText("Rename Ada"));
    fireEvent.change(screen.getByLabelText("Patient name"), {
      target: { value: "  Grace Hopper  " },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(handlers.onRename).toHaveBeenCalledWith("a", "Grace Hopper"),
    );
    await waitFor(() => expect(screen.queryByLabelText("Patient name")).toBeNull());
  });

  it("refuses a blank name without asking the server", () => {
    const handlers = renderList([chat({ id: "a", patient_name: "Ada" })]);

    fireEvent.click(screen.getByLabelText("Rename Ada"));
    fireEvent.change(screen.getByLabelText("Patient name"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByText("Save"));

    expect(handlers.onRename).not.toHaveBeenCalled();
    expect(screen.getByTestId("rename-error")).toBeInTheDocument();
  });

  it("keeps the field open and shows why when the rename is refused", async () => {
    const handlers = renderList([chat({ id: "a", patient_name: "Ada" })]);
    handlers.onRename.mockRejectedValue(new Error("that name is already used"));

    fireEvent.click(screen.getByLabelText("Rename Ada"));
    fireEvent.change(screen.getByLabelText("Patient name"), {
      target: { value: "Bram" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByTestId("rename-error")).toHaveTextContent(
        "that name is already used",
      ),
    );
    // Still open, still holding what was typed, so it can be corrected in place.
    expect(screen.getByLabelText("Patient name")).toHaveValue("Bram");
  });

  it("cancels a rename without reporting it", () => {
    const handlers = renderList([chat({ id: "a", patient_name: "Ada" })]);

    fireEvent.click(screen.getByLabelText("Rename Ada"));
    fireEvent.change(screen.getByLabelText("Patient name"), {
      target: { value: "Grace" },
    });
    fireEvent.click(screen.getByText("Cancel"));

    expect(handlers.onRename).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Patient name")).toBeNull();
    expect(screen.getByText("Ada")).toBeInTheDocument();
  });
});
