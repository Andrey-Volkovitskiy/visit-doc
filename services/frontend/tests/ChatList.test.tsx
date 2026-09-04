import { fireEvent, render, screen } from "@testing-library/react";
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

  it("offers no way to rename a chat", () => {
    // A patient's name is assigned once, when the scheduler creates them, and this
    // screen is a reader of it - there is no control here that could disagree.
    renderList([chat({ id: "a", patient_name: "Ada" })]);

    expect(screen.queryByLabelText(/^Rename/)).toBeNull();
    expect(screen.queryByLabelText("Patient name")).toBeNull();
  });
});
