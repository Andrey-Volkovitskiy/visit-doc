import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import * as chatStream from "../src/lib/chatStream";
import type { ChatListing, ChatSummary } from "../src/lib/chatStream";

function chat(overrides: Partial<ChatSummary> = {}): ChatSummary {
  return {
    id: "01CHAT000000000000000000",
    patient_name: "Ada Lovelace",
    created_at: "2026-08-14T14:32:00",
    last_message_at: null,
    ...overrides,
  };
}

function listing(overrides: Partial<ChatListing> = {}): ChatListing {
  return { chats: [], session_exists: true, ...overrides };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
});

describe("App: first arrival", () => {
  it("creates a chat when the server reports no session", async () => {
    // The server is the only thing that knows this is a first arrival - the session
    // cookie is HttpOnly, so the SPA cannot tell by looking.
    const fetchChats = vi
      .spyOn(chatStream, "fetchChats")
      .mockResolvedValueOnce(listing({ session_exists: false }))
      .mockResolvedValue(listing({ chats: [chat()] }));
    const createChat = vi.spyOn(chatStream, "createChat").mockResolvedValue(chat());

    render(<App />);

    await waitFor(() => expect(createChat).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("Ada Lovelace")).toBeInTheDocument());
    expect(fetchChats).toHaveBeenCalled();
  });

  it("opens the chat it just created rather than leaving the area muted", async () => {
    vi.spyOn(chatStream, "fetchChats")
      .mockResolvedValueOnce(listing({ session_exists: false }))
      .mockResolvedValue(listing({ chats: [chat({ id: "01NEW" })] }));
    vi.spyOn(chatStream, "createChat").mockResolvedValue(chat({ id: "01NEW" }));

    render(<App />);

    await waitFor(() => expect(screen.getByLabelText("question")).toBeInTheDocument());
    expect(screen.queryByTestId("no-chat")).toBeNull();
  });

  it("creates nothing when a recognized session has emptied its chat list", async () => {
    // FR-040: deleting the last chat must not provision a replacement. Same empty
    // list as a first arrival, opposite required behavior.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [], session_exists: true }),
    );
    const createChat = vi.spyOn(chatStream, "createChat");

    render(<App />);

    await waitFor(() => expect(screen.getByTestId("no-chat")).toBeInTheDocument());
    expect(createChat).not.toHaveBeenCalled();
  });

  it("creates nothing when a recognized session already holds chats", async () => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()], session_exists: true }),
    );
    const createChat = vi.spyOn(chatStream, "createChat");

    render(<App />);

    await waitFor(() => expect(screen.getByText("Ada Lovelace")).toBeInTheDocument());
    expect(createChat).not.toHaveBeenCalled();
  });

  it("opens the most recently active chat, which the server sorts first", async () => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({
        chats: [
          chat({ id: "01ACTIVE", patient_name: "Newest activity" }),
          chat({ id: "01STALE", patient_name: "Older" }),
        ],
      }),
    );

    render(<App />);

    await waitFor(() =>
      expect(chatStream.fetchChatHistory).toHaveBeenCalledWith("01ACTIVE"),
    );
  });

  it("leaves the create control usable after a failed first-arrival creation", async () => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ session_exists: false }),
    );
    vi.spyOn(chatStream, "createChat").mockRejectedValue(new Error("network error"));

    render(<App />);

    await waitFor(() => expect(screen.getByText("New chat")).toBeEnabled());
    expect(screen.getByTestId("chat-list-error")).toBeInTheDocument();
  });
});

describe("App: renaming a chat's patient", () => {
  it("shows the new name without refetching the list", async () => {
    // The whole point of the round trip: the name the user just chose is on screen the
    // moment the request returns, so there is no window in which they wonder whether it
    // worked.
    const fetchChats = vi
      .spyOn(chatStream, "fetchChats")
      .mockResolvedValue(listing({ chats: [chat({ id: "a", patient_name: "Ada" })] }));
    vi.spyOn(chatStream, "renameChatPatient").mockResolvedValue({
      chat_id: "a",
      patient_name: "Grace Hopper",
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Ada")).toBeInTheDocument());
    const callsBefore = fetchChats.mock.calls.length;

    fireEvent.click(screen.getByLabelText("Rename Ada"));
    fireEvent.change(screen.getByLabelText("Patient name"), {
      target: { value: "Grace Hopper" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByText("Grace Hopper")).toBeInTheDocument(),
    );
    expect(fetchChats.mock.calls.length).toBe(callsBefore);
  });

  it("renders the name the server stored, not the one that was typed", async () => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat({ id: "a", patient_name: "Ada" })] }),
    );
    vi.spyOn(chatStream, "renameChatPatient").mockResolvedValue({
      chat_id: "a",
      patient_name: "Grace B. Hopper",
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText("Ada")).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText("Rename Ada"));
    fireEvent.change(screen.getByLabelText("Patient name"), {
      target: { value: "Grace Hopper" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(screen.getByText("Grace B. Hopper")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Grace Hopper")).toBeNull();
  });

  it("leaves the old name in place when the rename fails", async () => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat({ id: "a", patient_name: "Ada" })] }),
    );
    vi.spyOn(chatStream, "renameChatPatient").mockRejectedValue(
      new Error("that name is already used"),
    );

    render(<App />);
    await waitFor(() => expect(screen.getByText("Ada")).toBeInTheDocument());

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
    expect(screen.getByLabelText("Patient name")).toBeInTheDocument();
  });
});
