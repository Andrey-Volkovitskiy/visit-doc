import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import * as chatStream from "../src/lib/chatStream";
import * as consoleApi from "../src/lib/consoleApi";
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
  vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
    attention_total: 0,
    conversations: [],
  });
  vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([]);
  vi.spyOn(consoleApi, "fetchPractitioners").mockResolvedValue([]);
  vi.spyOn(consoleApi, "fetchFaqEntries").mockResolvedValue([]);
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
      expect(chatStream.fetchChatHistory).toHaveBeenCalledWith(
        "01ACTIVE",
        expect.any(AbortSignal),
      ),
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

// --- 007 (FR-030/FR-031): both sides, at once, with no way in -----------------------

describe("App: the patient side and the staff side together", () => {
  it("renders both panes at once", async () => {
    // FR-030: one screen, both roles. This is a single-visitor demonstration, so a
    // staff member and a patient are the same person in two panes.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()] }),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByTestId("patient-pane")).toBeInTheDocument());
    expect(screen.getByTestId("staff-pane")).toBeInTheDocument();
  });

  it("asks nobody to sign in, anywhere", async () => {
    // FR-031/SC-017: there is no authentication in this phase, and a prompt for one
    // would be a control that cannot be satisfied.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()] }),
    );

    const { container } = render(<App />);

    await waitFor(() => expect(screen.getByTestId("staff-pane")).toBeInTheDocument());
    expect(
      screen.queryByText(/sign in|log in|password|username|authenticate/i),
    ).toBeNull();
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });

  it("shows the staff side even when the session holds no chats", async () => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [], session_exists: true }),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByTestId("staff-pane")).toBeInTheDocument());
    expect(screen.getByTestId("no-chat")).toBeInTheDocument();
  });
});

// --- 007 (US2): one poll, both panes ------------------------------------------------

describe("App: the console read model reaches both panes", () => {
  it("keeps the attention total visible while the patient pane has focus", async () => {
    // FR-028: the total is not a thing you go and look at - it is visible from
    // wherever you are, or it is not a signal.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()] }),
    );
    vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
      attention_total: 2,
      conversations: [],
    });

    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("attention-total")).toHaveTextContent("2"),
    );
    fireEvent.focus(screen.getByLabelText("question"));
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "typing in the patient pane" },
    });

    expect(screen.getByTestId("attention-total")).toHaveTextContent("2");
  });

  it("opens the conversation a staff member picked, in the staff pane alone", async () => {
    // The two panes hold separate selections on purpose: a staff member reading one
    // conversation must not move the patient's own thread out from under them.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat({ id: "01PATIENTCHAT" })] }),
    );
    const fetchThread = vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([]);
    vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
      attention_total: 1,
      conversations: [
        {
          chat_id: "01STAFFCHAT",
          patient_name: "Grace Hopper",
          last_message_at: null,
          emphasized: true,
          escalated: true,
          escalation_reason: "patient_asked_for_person",
          attention_since: "2026-09-01T12:00:00Z",
          assistant_may_reply: false,
          pause_seconds_remaining: null,
        },
      ],
    });

    render(<App />);

    fireEvent.click(await screen.findByTestId("staff-conversation"));

    await waitFor(() =>
      expect(fetchThread).toHaveBeenCalledWith("01STAFFCHAT", expect.any(AbortSignal)),
    );
    expect(chatStream.fetchChatHistory).toHaveBeenCalledWith(
      "01PATIENTCHAT",
      expect.any(AbortSignal),
    );
  });
});

// --- the panels that can only read a session ----------------------------------------

function practitioner(
  overrides: Partial<consoleApi.Practitioner> = {},
): consoleApi.Practitioner {
  return {
    id: "01PRACT0000000000000000000",
    full_name: "Dr. Ada Lovelace",
    specialty: "general_practice",
    appointment_duration_minutes: 30,
    schedule: [{ weekday: 0, start_time: "09:00", end_time: "17:00" }],
    ...overrides,
  };
}

describe("App: the panels that can only read a session", () => {
  it("reads nothing session-scoped until a first arrival has provisioned one", async () => {
    // The regression this pins. These panels fetch once, on mount, and their effect has
    // no reason to run again — so a read made before the session existed is the only
    // answer they will ever hold. Mounted beside the provisioning POST, the roster read
    // went out cookie-less, came back 401, and the panel sat on "no session" over an
    // empty list until the visitor reloaded by hand.
    let mintSession = (): void => {};
    const provisioned = new Promise<void>((resolve) => {
      mintSession = resolve;
    });
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ session_exists: false }),
    );
    const createChat = vi
      .spyOn(chatStream, "createChat")
      .mockImplementation(async () => {
        await provisioned;
        return chat();
      });

    render(<App />);

    // The POST is in flight and there is still no session, which is exactly the window
    // the old code fetched in.
    await waitFor(() => expect(createChat).toHaveBeenCalledTimes(1));
    expect(consoleApi.fetchPractitioners).not.toHaveBeenCalled();
    expect(consoleApi.fetchFaqEntries).not.toHaveBeenCalled();

    mintSession();

    await waitFor(() => expect(consoleApi.fetchPractitioners).toHaveBeenCalled());
    expect(consoleApi.fetchFaqEntries).toHaveBeenCalled();
  });

  it("shows the provisioned session's roster without a reload", async () => {
    // The wire as the browser actually answered it: GET /console/practitioners is a 401
    // while no session exists and a roster once one does, and the endpoint is right
    // both times. So the fake refuses until the session is minted — a panel that read
    // it too early holds the refusal for good, because nothing asks it again.
    let sessionMinted = false;
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ session_exists: false }),
    );
    vi.spyOn(chatStream, "createChat").mockImplementation(async () => {
      sessionMinted = true;
      return chat();
    });
    vi.spyOn(consoleApi, "fetchPractitioners").mockImplementation(async () => {
      if (!sessionMinted) throw new Error("no session");
      return [practitioner()];
    });

    render(<App />);

    // Nothing here reloads the page or remounts anything by hand: the roster arrives
    // because the panel was withheld until the session it reads existed.
    await waitFor(() => expect(screen.getByTestId("practitioner")).toBeInTheDocument());
    expect(screen.queryByTestId("no-practitioners")).toBeNull();
    expect(screen.queryByTestId("practitioner-error")).toBeNull();
  });

  it("reads them straight away for a browser that already has a session", async () => {
    // The gate must not cost a returning visitor a round trip they do not need: the
    // listing already said the session exists, so nothing waits on a POST.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()], session_exists: true }),
    );
    const createChat = vi.spyOn(chatStream, "createChat");

    render(<App />);

    await waitFor(() => expect(consoleApi.fetchPractitioners).toHaveBeenCalled());
    expect(consoleApi.fetchFaqEntries).toHaveBeenCalled();
    expect(createChat).not.toHaveBeenCalled();
  });

  it("shows no empty roster when the session could not be provisioned at all", async () => {
    // Failing to provision leaves this browser with no session, so there is no roster
    // to be empty and no corpus to be empty. Painting either would be this screen
    // stating something it has no way to know.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ session_exists: false }),
    );
    vi.spyOn(chatStream, "createChat").mockRejectedValue(new Error("network error"));

    render(<App />);

    await waitFor(() => expect(screen.getByTestId("chat-list-error")).toBeInTheDocument());
    expect(screen.queryByTestId("practitioner-admin")).toBeNull();
    expect(screen.queryByTestId("no-practitioners")).toBeNull();
    expect(screen.queryByTestId("faq-admin")).toBeNull();
    expect(consoleApi.fetchPractitioners).not.toHaveBeenCalled();
  });
});
