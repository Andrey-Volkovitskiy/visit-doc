import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CHAT_TAB_BUDGET, ChatList, chatLabel } from "../src/components/ChatList";
import type { ChatSummary } from "../src/lib/chatStream";
import { press } from "./press";

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

    // Targeted by accessible name rather than by visible text: the control is an icon
    // now, and a test that could only find it while it read "New chat" was pinning its
    // appearance rather than its reachability.
    fireEvent.click(screen.getByLabelText("Start a new chat"));
    expect(handlers.onCreate).toHaveBeenCalled();
  });

  it("keeps the create control usable when the session holds no chats", () => {
    renderList([]);

    expect(screen.getByLabelText("Start a new chat")).toBeEnabled();
    expect(screen.queryAllByTestId("chat-list-item")).toHaveLength(0);
  });

  it("asks for confirmation before deleting, and deletes nothing until confirmed", () => {
    const handlers = renderList([chat({ id: "a", patient_name: "Ada" })]);

    fireEvent.click(screen.getByLabelText("Delete Ada"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(handlers.onDelete).not.toHaveBeenCalled();

    // By name, not by position. The vendored dialog renders its own close control
    // first, so "the first button in the dialog" is now the X — which would have
    // dismissed the confirmation and reported nothing, and the test would have failed
    // for a reason that had nothing to do with what it protects.
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }),
    );
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

describe("ChatList tab budget and overflow", () => {
  function many(count: number): ChatSummary[] {
    return Array.from({ length: count }, (_, i) =>
      chat({ id: `c${i}`, patient_name: `Patient ${i}` }),
    );
  }

  function directLabels(): string[] {
    return screen
      .getAllByTestId("chat-list-item")
      .map((el) => el.textContent ?? "");
  }

  function overflowLabels(): string[] {
    press(screen.getByTestId("chat-overflow"));
    return screen
      .getAllByTestId("chat-overflow-item")
      .map((el) => el.textContent ?? "");
  }

  it("declares the budget once rather than leaving a 4 in the markup", () => {
    // The test reads the same constant the component does. Hard-coding 4 here would let
    // the two drift and still agree — FR-012 asks for one declaration, not two that
    // happen to match today.
    expect(CHAT_TAB_BUDGET).toBe(4);
  });

  it("shows six chats as four tabs plus an overflow control", () => {
    renderList(many(6), "c0");

    expect(screen.getAllByTestId("chat-list-item")).toHaveLength(CHAT_TAB_BUDGET);
    expect(screen.getByTestId("chat-overflow")).toBeInTheDocument();
  });

  it("shows the first four in the server's order and holds the rest in the overflow", () => {
    renderList(many(6), "c0");

    expect(directLabels().map((t) => t.replace(/Delete.*/, "").trim())).toEqual([
      "Patient 0",
      "Patient 1",
      "Patient 2",
      "Patient 3",
    ]);
    expect(overflowLabels()).toEqual(["Patient 4", "Patient 5"]);
  });

  it("renders no overflow control when every chat fits", () => {
    renderList(many(CHAT_TAB_BUDGET), "c0");

    expect(screen.getAllByTestId("chat-list-item")).toHaveLength(CHAT_TAB_BUDGET);
    expect(screen.queryByTestId("chat-overflow")).toBeNull();
  });

  it("renders no overflow control for fewer chats than the budget", () => {
    renderList(many(2), "c0");

    expect(screen.queryByTestId("chat-overflow")).toBeNull();
  });

  it("gives the open chat the last direct position when it would otherwise overflow", () => {
    renderList(many(6), "c5");

    expect(directLabels().map((t) => t.replace(/Delete.*/, "").trim())).toEqual([
      "Patient 0",
      "Patient 1",
      "Patient 2",
      "Patient 5",
    ]);
  });

  it("moves the tab the open chat displaced into the overflow, in server order", () => {
    renderList(many(6), "c5");

    expect(overflowLabels()).toEqual(["Patient 3", "Patient 4"]);
  });

  it("leaves the order alone when the open chat already fits", () => {
    renderList(many(6), "c2");

    expect(directLabels().map((t) => t.replace(/Delete.*/, "").trim())).toEqual([
      "Patient 0",
      "Patient 1",
      "Patient 2",
      "Patient 3",
    ]);
    expect(overflowLabels()).toEqual(["Patient 4", "Patient 5"]);
  });

  it("selects a chat opened from the overflow", () => {
    const handlers = renderList(many(6), "c0");

    press(screen.getByTestId("chat-overflow"));
    fireEvent.click(screen.getByText("Patient 5"));

    expect(handlers.onSelect).toHaveBeenCalledWith("c5");
  });

  it("shows every chat somewhere — none is unreachable", () => {
    renderList(many(9), "c0");

    const direct = directLabels().map((t) => t.replace(/Delete.*/, "").trim());
    const all = [...direct, ...overflowLabels()];
    expect(all).toHaveLength(9);
    expect(new Set(all).size).toBe(9);
  });
});
