import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import * as chatStream from "../src/lib/chatStream";
import * as consoleApi from "../src/lib/consoleApi";
import * as consolePoll from "../src/lib/useConsolePoll";
import { press } from "./press";
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
  vi.spyOn(consoleApi, "fetchSpecialties").mockResolvedValue([
    "General Practice",
  ]);
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
    const createChat = vi
      .spyOn(chatStream, "createChat")
      .mockResolvedValue(chat());

    render(<App />);

    await waitFor(() => expect(createChat).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByText("Ada Lovelace")).toBeInTheDocument(),
    );
    expect(fetchChats).toHaveBeenCalled();
  });

  it("opens the chat it just created rather than leaving the area muted", async () => {
    vi.spyOn(chatStream, "fetchChats")
      .mockResolvedValueOnce(listing({ session_exists: false }))
      .mockResolvedValue(listing({ chats: [chat({ id: "01NEW" })] }));
    vi.spyOn(chatStream, "createChat").mockResolvedValue(chat({ id: "01NEW" }));

    render(<App />);

    await waitFor(() =>
      expect(screen.getByLabelText("question")).toBeInTheDocument(),
    );
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

    await waitFor(() =>
      expect(screen.getByTestId("no-chat")).toBeInTheDocument(),
    );
    expect(createChat).not.toHaveBeenCalled();
  });

  it("creates nothing when a recognized session already holds chats", async () => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()], session_exists: true }),
    );
    const createChat = vi.spyOn(chatStream, "createChat");

    render(<App />);

    await waitFor(() =>
      expect(screen.getByText("Ada Lovelace")).toBeInTheDocument(),
    );
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
    vi.spyOn(chatStream, "createChat").mockRejectedValue(
      new Error("network error"),
    );

    render(<App />);

    // By accessible name, not by visible text: the control is an icon now, and a test
    // that could only find it while it read "New chat" was pinning its appearance
    // rather than the property it exists for — that a failed first arrival leaves a way
    // to try again.
    await waitFor(() =>
      expect(screen.getByLabelText("Start a new chat")).toBeEnabled(),
    );
    expect(screen.getByTestId("chat-list-error")).toBeInTheDocument();
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

    await waitFor(() =>
      expect(screen.getByTestId("patient-pane")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("staff-pane")).toBeInTheDocument();
  });

  it("asks nobody to sign in, anywhere", async () => {
    // FR-031/SC-017: there is no authentication in this phase, and a prompt for one
    // would be a control that cannot be satisfied.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()] }),
    );

    const { container } = render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("staff-pane")).toBeInTheDocument(),
    );
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

    await waitFor(() =>
      expect(screen.getByTestId("staff-pane")).toBeInTheDocument(),
    );
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
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValue([]);
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
          booking_acts_version: 0,
        },
      ],
    });

    render(<App />);

    fireEvent.click(await screen.findByTestId("staff-conversation"));

    await waitFor(() =>
      expect(fetchThread).toHaveBeenCalledWith(
        "01STAFFCHAT",
        expect.any(AbortSignal),
      ),
    );
    expect(chatStream.fetchChatHistory).toHaveBeenCalledWith(
      "01PATIENTCHAT",
      expect.any(AbortSignal),
    );
  });

  it("re-reads the open staff thread when only its booking-record version moves", async () => {
    // 016 FR-016a, end to end through the one poll: the row's version has to reach the
    // staff thread, or an act settled by a turn that wrote no message reads "unknown"
    // until some later message happens to trigger a read.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const row = (booking_acts_version: number) => ({
        attention_total: 0,
        conversations: [
          {
            chat_id: "01STAFFCHAT",
            patient_name: "Grace Hopper",
            last_message_at: "2026-09-01T12:00:00",
            emphasized: false,
            escalated: false,
            escalation_reason: null,
            attention_since: null,
            assistant_may_reply: true,
            pause_seconds_remaining: null,
            booking_acts_version,
          },
        ],
      });
      const fetchThread = vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([]);
      const listingSpy = vi
        .spyOn(consoleApi, "fetchConsoleListing")
        .mockResolvedValue(row(1));

      render(<App />);
      fireEvent.click(await screen.findByTestId("staff-conversation"));
      await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(1));

      listingSpy.mockResolvedValue(row(2));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(consolePoll.POLL_INTERVAL_MS);
      });

      await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(2));
      expect(fetchThread).toHaveBeenLastCalledWith("01STAFFCHAT", expect.any(AbortSignal));
    } finally {
      vi.useRealTimers();
    }
  });
});

// --- each pane reports its own failures ---------------------------------------------

/**
 * Put one conversation in the console listing. Called *before* `render`, because the
 * poll reads once immediately and the next tick is two seconds away.
 */
function stubOneConversation(): void {
  vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
    attention_total: 0,
    conversations: [
      {
        chat_id: "01STAFFCHAT",
        patient_name: "Grace Hopper",
        last_message_at: null,
        emphasized: false,
        escalated: false,
        escalation_reason: null,
        attention_since: null,
        assistant_may_reply: true,
        pause_seconds_remaining: null,
        booking_acts_version: 0,
      },
    ],
  });
}

/** Open it, so the assistant switch is on screen. */
async function openStaffConversation(): Promise<void> {
  fireEvent.click(await screen.findByTestId("staff-conversation"));
  await screen.findByTestId("assistant-switch");
}

describe("App: a pane reports its own failures and nobody else's", () => {
  it("puts a failed assistant switch in the staff pane, not the patient's", async () => {
    // The switch is a staff gesture. Reported through the patient pane's banner it
    // became a sentence about a control the patient cannot see, sitting where they read
    // about their own chats.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat({ id: "01PATIENTCHAT" })] }),
    );
    vi.spyOn(consoleApi, "setAssistant").mockRejectedValue(
      new Error("the switch would not move"),
    );
    stubOneConversation();

    render(<App />);
    await openStaffConversation();

    press(screen.getByTestId("assistant-switch"));

    const banner = await screen.findByTestId("staff-pane-error");
    expect(banner).toHaveTextContent("the switch would not move");
    expect(
      within(screen.getByTestId("staff-pane")).getByTestId("staff-pane-error"),
    ).toBe(banner);
    expect(screen.queryByTestId("chat-list-error")).toBeNull();
  });

  it("does not clear the patient pane's banner when the switch is flipped", async () => {
    // Two failures, two banners, and one is not disproved by the other's gesture: a
    // chat list that would not load is still not loaded after a staff member touches a
    // switch. One shared value cleared it on the way in.
    vi.spyOn(chatStream, "fetchChats").mockRejectedValue(
      new Error("could not reach the chat list"),
    );
    const setAssistant = vi
      .spyOn(consoleApi, "setAssistant")
      .mockResolvedValue({
        assistant_may_reply: false,
        pause_seconds_remaining: 900,
      });
    stubOneConversation();

    render(<App />);
    await screen.findByTestId("chat-list-error");
    await openStaffConversation();

    press(screen.getByTestId("assistant-switch"));

    await waitFor(() => expect(setAssistant).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("chat-list-error")).toHaveTextContent(
      "could not reach the chat list",
    );
    expect(screen.queryByTestId("staff-pane-error")).toBeNull();
  });

  it("drops the staff banner when another conversation is opened", async () => {
    // Its one sentence says "this conversation". Left standing across a switch it names
    // the wrong one — a failure the staff member cannot see, retry or act on, reported
    // over a conversation nothing went wrong in.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat({ id: "01PATIENTCHAT" })] }),
    );
    vi.spyOn(consoleApi, "setAssistant").mockRejectedValue(
      new Error("the switch would not move"),
    );
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([]);
    vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
      attention_total: 0,
      conversations: [
        {
          chat_id: "01STAFFCHAT",
          patient_name: "Grace Hopper",
          last_message_at: null,
          emphasized: false,
          escalated: false,
          escalation_reason: null,
          attention_since: null,
          assistant_may_reply: true,
          pause_seconds_remaining: null,
          booking_acts_version: 0,
        },
        {
          chat_id: "01OTHERCHAT",
          patient_name: "Alan Turing",
          last_message_at: null,
          emphasized: false,
          escalated: false,
          escalation_reason: null,
          attention_since: null,
          assistant_may_reply: true,
          pause_seconds_remaining: null,
          booking_acts_version: 0,
        },
      ],
    });

    render(<App />);
    fireEvent.click(await screen.findByText("Grace Hopper"));
    await screen.findByTestId("assistant-switch");

    press(screen.getByTestId("assistant-switch"));
    await screen.findByTestId("staff-pane-error");

    fireEvent.click(screen.getByText("Alan Turing"));

    expect(screen.queryByTestId("staff-pane-error")).toBeNull();
  });
});

// --- the panels that can only read a session ----------------------------------------

function practitioner(
  overrides: Partial<consoleApi.Practitioner> = {},
): consoleApi.Practitioner {
  return {
    id: "01PRACT0000000000000000000",
    full_name: "Dr. Ada Lovelace",
    specialty: "General Practice",
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

    // Both panels now live behind a tab, so the read they must not make is only
    // reachable once the tab is open. Opening it *before* the session is minted is what
    // keeps this test about the session gate rather than about the tab: with the tab
    // shut, "no read happened" would be true for a reason that has nothing to do with
    // the gate, and would stay true with the gate deleted.
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    openTab("Practitioners");
    openTab("FAQ");

    // The POST is in flight and there is still no session, which is exactly the window
    // the old code fetched in.
    await waitFor(() => expect(createChat).toHaveBeenCalledTimes(1));
    expect(consoleApi.fetchPractitioners).not.toHaveBeenCalled();
    expect(consoleApi.fetchFaqEntries).not.toHaveBeenCalled();

    mintSession();

    openTab("Practitioners");
    await waitFor(() =>
      expect(consoleApi.fetchPractitioners).toHaveBeenCalled(),
    );
    openTab("FAQ");
    await waitFor(() => expect(consoleApi.fetchFaqEntries).toHaveBeenCalled());
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
    // The chooser's set is read on the same mount and is refused the same way, so a
    // panel let in too early would hold *its* refusal for good just as surely.
    vi.spyOn(consoleApi, "fetchSpecialties").mockImplementation(async () => {
      if (!sessionMinted) throw new Error("no session");
      return ["General Practice"];
    });

    render(<App />);

    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    openTab("Practitioners");

    // Nothing here reloads the page or remounts anything by hand: the roster arrives
    // because the panel was withheld until the session it reads existed.
    await waitFor(() =>
      expect(screen.getByTestId("practitioner")).toBeInTheDocument(),
    );
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

    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    openTab("Practitioners");
    await waitFor(() =>
      expect(consoleApi.fetchPractitioners).toHaveBeenCalled(),
    );
    openTab("FAQ");
    await waitFor(() => expect(consoleApi.fetchFaqEntries).toHaveBeenCalled());
    expect(createChat).not.toHaveBeenCalled();
  });

  it("shows no empty roster when the session could not be provisioned at all", async () => {
    // Failing to provision leaves this browser with no session, so there is no roster
    // to be empty and no corpus to be empty. Painting either would be this screen
    // stating something it has no way to know.
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ session_exists: false }),
    );
    vi.spyOn(chatStream, "createChat").mockRejectedValue(
      new Error("network error"),
    );

    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("chat-list-error")).toBeInTheDocument(),
    );

    // The tab is opened *first*, and that is the whole strength of this test. Behind a
    // shut tab every assertion below is free — the panel is absent because nothing
    // rendered it, not because the session gate withheld it — and the test would go on
    // passing with that gate deleted outright, which is the regression it exists for.
    openTab("Practitioners");
    expect(screen.queryByTestId("practitioner-admin")).toBeNull();
    expect(screen.queryByTestId("no-practitioners")).toBeNull();
    expect(consoleApi.fetchPractitioners).not.toHaveBeenCalled();

    openTab("FAQ");
    expect(screen.queryByTestId("faq-admin")).toBeNull();
    expect(consoleApi.fetchFaqEntries).not.toHaveBeenCalled();

    // And the section says so rather than rendering an empty result (FR-025).
    expect(screen.getByTestId("region-loading")).toBeInTheDocument();
  });
});

// --- 015 Phase 4: the shell -----------------------------------------------------------

/** Open one of the console's three sections by name. */
function openTab(name: string): void {
  press(screen.getByRole("tab", { name }));
}

describe("App: the console's three sections (FR-020)", () => {
  beforeEach(() => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()] }),
    );
  });

  it("exposes the three sections as a tab set with accessible names", async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    const names = screen.getAllByRole("tab").map((el) => el.textContent);
    expect(names).toEqual(["Conversations", "Practitioners", "FAQ"]);
  });

  it("renders only the open section's panel", async () => {
    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("staff-console")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("practitioner-admin")).toBeNull();
    expect(screen.queryByTestId("faq-admin")).toBeNull();

    openTab("Practitioners");

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-admin")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("staff-console")).toBeNull();
    expect(screen.queryByTestId("faq-admin")).toBeNull();
  });

  it("opens the FAQ section on its own", async () => {
    render(<App />);
    await waitFor(() =>
      expect(screen.getByTestId("staff-console")).toBeInTheDocument(),
    );

    openTab("FAQ");

    await waitFor(() => expect(screen.getByTestId("faq-admin")).toBeInTheDocument());
    expect(screen.queryByTestId("practitioner-admin")).toBeNull();
  });

  it("reports which section is open to assistive technology", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());

    expect(
      screen.getByRole("tab", { name: "Conversations" }),
    ).toHaveAttribute("aria-selected", "true");

    openTab("Practitioners");

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Practitioners" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.getByRole("tab", { name: "Conversations" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  it("refreshes an open practitioner's bookings on the console poll's tick (016 R8)", async () => {
    // The poll is replaced at its seam so the test can advance it without waiting out
    // the real interval: what is under test is that App hands the tick through.
    let tick = 1;
    vi.spyOn(consolePoll, "useConsolePoll").mockImplementation(() => ({
      conversations: [],
      attentionTotal: 0,
      tick,
    }));
    vi.spyOn(consoleApi, "fetchPractitioners").mockResolvedValue([practitioner()]);
    const week = vi
      .spyOn(consoleApi, "fetchPractitionerWeek")
      .mockResolvedValue([]);

    const { rerender } = render(<App />);
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    openTab("Practitioners");
    press(await screen.findByTestId("bookings-toggle"));
    await screen.findByTestId("week-empty");
    expect(week).toHaveBeenCalledTimes(1);

    tick = 2;
    rerender(<App />);

    await waitFor(() => expect(week).toHaveBeenCalledTimes(2));
  });
});

describe("App: the attention total in the console header (FR-021)", () => {
  beforeEach(() => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()] }),
    );
  });

  async function withTotal(attention_total: number): Promise<void> {
    vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
      attention_total,
      conversations: [],
    });
    render(<App />);
    // Waited for by its *text*, not its presence: the element is on screen before the
    // poll answers, holding a placeholder rather than a count, so waiting for it to
    // exist would let an assertion run against the waiting state.
    await waitFor(() =>
      expect(screen.getByTestId("attention-total")).toHaveTextContent(
        String(attention_total),
      ),
    );
  }

  it("renders how many conversations need a person", async () => {
    await withTotal(1);
    expect(screen.getByTestId("attention-total")).toHaveTextContent("1");
  });

  it("renders a zero total as plainly zero rather than hiding it", async () => {
    // A missing badge and a badge reading zero say different things: one is "nothing
    // needs you", the other is "this may not be working".
    await withTotal(0);
    expect(screen.getByTestId("attention-total")).toHaveTextContent("0");
  });

  it("shows the server's total, not a count of the rows it happens to hold", async () => {
    // The total counts a conversation once however many marks sit inside it, and the
    // server is the only thing that can see that. Two rows, a total of five.
    vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
      attention_total: 5,
      conversations: [
        {
          chat_id: "a",
          patient_name: "Ada",
          last_message_at: null,
          emphasized: true,
          escalated: true,
          escalation_reason: "patient_asked_for_person",
          attention_since: "2026-09-01T12:00:00",
          assistant_may_reply: true,
          pause_seconds_remaining: null,
          booking_acts_version: 0,
        },
        {
          chat_id: "b",
          patient_name: "Bram",
          last_message_at: null,
          emphasized: true,
          escalated: true,
          escalation_reason: "patient_asked_for_person",
          attention_since: "2026-09-01T12:00:00",
          assistant_may_reply: true,
          pause_seconds_remaining: null,
          booking_acts_version: 0,
        },
      ],
    });
    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("attention-total")).toHaveTextContent("5"),
    );
    expect(screen.getAllByTestId("staff-conversation")).toHaveLength(2);
  });

  it("sits outside the tabbed region, so it survives opening another section", async () => {
    // This is the whole reason it moved out of StaffConsole. Inside the tab it would
    // vanish the moment staff went to look at the roster — and a count you have to
    // navigate back to is not a signal.
    await withTotal(3);

    openTab("Practitioners");

    await waitFor(() =>
      expect(screen.getByTestId("practitioner-admin")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("attention-total")).toHaveTextContent("3");

    openTab("FAQ");
    await waitFor(() => expect(screen.getByTestId("faq-admin")).toBeInTheDocument());
    expect(screen.getByTestId("attention-total")).toHaveTextContent("3");
  });

  it("renders exactly one total, not one per section", async () => {
    await withTotal(2);
    expect(screen.getAllByTestId("attention-total")).toHaveLength(1);
  });

  it("does not read as zero before the console has answered (FR-010a)", async () => {
    // "Nobody needs you" and "nothing is known yet" are the two situations FR-010a
    // keeps apart everywhere else on this pane — the rail beside this count says in
    // words that it is still loading. A zero rendered over that rail is the collapse
    // that requirement exists to prevent, and it is the one a staff member acts on by
    // looking away.
    vi.spyOn(consoleApi, "fetchConsoleListing").mockImplementation(
      () => new Promise(() => undefined),
    );
    render(<App />);

    const total = screen.getByTestId("attention-total");
    expect(total).toHaveAttribute("data-counted", "false");
    expect(total).not.toHaveTextContent("0");
    expect(total).toHaveAccessibleName(/not counted yet/i);
  });

  it("marks the total as counted once the console has answered", async () => {
    await withTotal(0);
    expect(screen.getByTestId("attention-total")).toHaveAttribute(
      "data-counted",
      "true",
    );
  });
});

describe("App: structure before any answer (FR-010a, SC-011)", () => {
  /** Hold every read open, so the first paint is the only thing on screen. */
  function holdEverything(): void {
    const never = () => new Promise<never>(() => undefined);
    vi.spyOn(chatStream, "fetchChats").mockImplementation(never);
    vi.spyOn(consoleApi, "fetchConsoleListing").mockImplementation(never);
    vi.spyOn(consoleApi, "fetchPractitioners").mockImplementation(never);
    vi.spyOn(consoleApi, "fetchFaqEntries").mockImplementation(never);
  }

  it("renders the header and both panes before any request returns", () => {
    holdEverything();
    render(<App />);

    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByTestId("patient-pane")).toBeInTheDocument();
    expect(screen.getByTestId("staff-pane")).toBeInTheDocument();
  });

  it("names the product in the header before any request returns", () => {
    holdEverything();
    render(<App />);

    expect(
      screen.getByRole("heading", { name: /AI Clinic Receptionist/i }),
    ).toBeInTheDocument();
  });

  it("renders each pane's heading and the tab set before any request returns", () => {
    holdEverything();
    render(<App />);

    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(3);
  });

  it("says in words that a waiting region is waiting, and names which", () => {
    holdEverything();
    render(<App />);

    const waiting = screen.getAllByTestId("region-loading");
    expect(waiting.length).toBeGreaterThan(0);
    for (const region of waiting) {
      expect(region.getAttribute("data-region")).toBeTruthy();
      expect(region.textContent?.trim()).not.toBe("");
    }
  });

  it("distinguishes waiting from arrived-empty", async () => {
    // The two call for different things from the reader, so they must not render the
    // same (FR-010a). Waiting says so; empty says there is nothing.
    holdEverything();
    const { unmount } = render(<App />);
    const conversationsWaiting = screen
      .getAllByTestId("region-loading")
      .some((el) => el.getAttribute("data-region") === "conversations");
    expect(conversationsWaiting).toBe(true);
    expect(screen.queryByTestId("staff-no-conversations")).toBeNull();
    unmount();

    vi.restoreAllMocks();
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(listing());
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
      attention_total: 0,
      conversations: [],
    });
    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("staff-no-conversations")).toBeInTheDocument(),
    );
    expect(
      screen
        .queryAllByTestId("region-loading")
        .some((el) => el.getAttribute("data-region") === "conversations"),
    ).toBe(false);
  });

  it("distinguishes failed from both of them", async () => {
    vi.spyOn(chatStream, "fetchChats").mockRejectedValue(
      new Error("network error"),
    );
    vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
      attention_total: 0,
      conversations: [],
    });

    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("chat-list-error")).toBeInTheDocument(),
    );
    expect(
      screen
        .queryAllByTestId("region-loading")
        .some((el) => el.getAttribute("data-region") === "chats"),
    ).toBe(false);
  });

  it("stands in for nothing with a placeholder shape", () => {
    // FR-010a forbids skeletons outright: they assert a shape the server has not
    // confirmed, and are wrong precisely when the answer turns out to be nothing.
    holdEverything();
    const { container } = render(<App />);

    expect(container.querySelector(".animate-pulse")).toBeNull();
    expect(container.querySelector("[aria-busy='true'] .rounded-md.bg-rule")).toBeNull();
  });
});

describe("App: landmarks and heading structure (FR-040)", () => {
  beforeEach(() => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()] }),
    );
  });

  it("puts the page's content in named landmarks", async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByRole("banner")).toBeInTheDocument());
    expect(screen.getByRole("main")).toBeInTheDocument();

    // Both panes are regions a screen reader can jump between, each named for what it
    // holds rather than left as an anonymous div.
    const regions = screen.getAllByRole("region");
    const names = regions.map((el) => el.getAttribute("aria-label"));
    expect(names).toContain("Patient messenger");
    expect(names).toContain("Staff console");
  });

  it("has exactly one first-level heading, naming the product", async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByRole("banner")).toBeInTheDocument());
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/AI Clinic Receptionist/);
  });

  it("descends one level at a time, never skipping from h1 to h3", async () => {
    // A heading level skipped is a level a screen reader reports as missing structure.
    render(<App />);

    await waitFor(() => expect(screen.getByRole("banner")).toBeInTheDocument());
    const levels = screen
      .getAllByRole("heading")
      .map((el) => Number(el.tagName.slice(1)));

    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i += 1) {
      expect(levels[i]! - levels[i - 1]!).toBeLessThanOrEqual(1);
    }
  });

  it("names both panes with a heading of their own", async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByRole("banner")).toBeInTheDocument());
    expect(
      screen.getByRole("heading", { level: 2, name: "Patient messenger" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "Staff console" }),
    ).toBeInTheDocument();
  });

  it("gives every interactive control an accessible name", async () => {
    // FR-039/FR-040's floor, checked over the whole rendered shell rather than
    // component by component: an unnamed control is one a screen reader announces as
    // "button", which is no name at all.
    const { container } = render(<App />);
    await waitFor(() => expect(screen.getByRole("banner")).toBeInTheDocument());

    const controls = container.querySelectorAll(
      "button, a[href], input, textarea, select",
    );
    expect(controls.length).toBeGreaterThan(0);

    const unnamed: string[] = [];
    for (const el of controls) {
      const name =
        el.getAttribute("aria-label") ??
        (el.getAttribute("aria-labelledby") !== null
          ? document.getElementById(el.getAttribute("aria-labelledby")!)?.textContent
          : null) ??
        el.textContent;
      if ((name ?? "").trim() === "") unnamed.push(el.outerHTML.slice(0, 120));
    }
    expect(unnamed).toEqual([]);
  });

  it("uses a real semantic element for every control, never a clickable div", async () => {
    const { container } = render(<App />);
    await waitFor(() => expect(screen.getByRole("banner")).toBeInTheDocument());

    // A div with an onClick is unreachable by keyboard and invisible to assistive
    // technology. React attaches handlers at the root, so this looks for the shape
    // instead: anything carrying a button/link role that is not one.
    const faked = container.querySelectorAll(
      "div[role='button'], span[role='button'], div[role='link'], span[role='link']",
    );
    expect(Array.from(faked).map((el) => el.outerHTML.slice(0, 120))).toEqual([]);
  });
});

// --- T071 (FR-035b): leaving a dirty edit by choosing another tab --------------------
//
// The back control was guarded from the start. This is the *other* route FR-035b names —
// "by the back control **or by choosing another tab**" — and it was open: Radix destroys
// the inactive tab panel, so a switch took the typed text with it and asked nothing.
//
// The guard is a new mechanism, so it gets one test per invariant rather than one test
// for the happy path:
//   1. a switch away from a DIRTY section is intercepted and nothing moves
//   2. confirming completes the switch the reader asked for
//   3. cancelling leaves both the tab and the typed text exactly as they were
//   4. a switch away from a CLEAN section is not intercepted at all
//   5. the flag does not survive the section that set it — the lock-out invariant

describe("App: leaving a dirty edit by choosing another tab (FR-035b)", () => {
  beforeEach(() => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()] }),
    );
    vi.spyOn(consoleApi, "fetchPractitioners").mockResolvedValue([practitioner()]);
  });

  /** Open Practitioners, enter the edit view, and type into it. */
  async function dirtyTheEditView(): Promise<void> {
    render(<App />);
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    openTab("Practitioners");
    await waitFor(() =>
      expect(screen.getByTestId("practitioner")).toBeInTheDocument(),
    );
    press(screen.getByRole("button", { name: /edit/i }));
    await waitFor(() =>
      expect(screen.getByTestId("practitioner-edit")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Dr. Grace Hopper" },
    });
  }

  it("asks before abandoning typed changes, and moves nothing until answered", async () => {
    await dirtyTheEditView();

    openTab("FAQ");

    expect(await screen.findByTestId("discard-confirm")).toBeInTheDocument();

    // Still on Practitioners, still in the edit view, text intact.
    //
    // Asserted through the DOM rather than through `getByRole`: an open modal inerts
    // the background, so the tab set is deliberately absent from the accessibility tree
    // while the prompt is up — which is the dialog working, not the switch having
    // happened. The FAQ panel never rendered, and that is the thing that matters.
    expect(
      document
        .querySelector('[role="tab"][aria-selected="true"]')
        ?.textContent?.trim(),
    ).toBe("Practitioners");
    expect(screen.getByTestId("practitioner-edit")).toBeInTheDocument();
    expect(screen.queryByTestId("faq-admin")).toBeNull();
    expect(screen.getByLabelText("Full name")).toHaveValue("Dr. Grace Hopper");
  });

  it("completes the switch once the discard is confirmed", async () => {
    await dirtyTheEditView();
    openTab("FAQ");
    await screen.findByTestId("discard-confirm");

    fireEvent.click(
      within(screen.getByTestId("discard-confirm")).getByRole("button", {
        name: /discard/i,
      }),
    );

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "FAQ" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.queryByTestId("practitioner-edit")).toBeNull();
  });

  it("stays put, with the text untouched, when the discard is refused", async () => {
    await dirtyTheEditView();
    openTab("FAQ");
    await screen.findByTestId("discard-confirm");

    fireEvent.click(
      within(screen.getByTestId("discard-confirm")).getByRole("button", {
        name: /keep editing/i,
      }),
    );

    await waitFor(() => expect(screen.queryByTestId("discard-confirm")).toBeNull());
    expect(screen.getByRole("tab", { name: "Practitioners" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByLabelText("Full name")).toHaveValue("Dr. Grace Hopper");
  });

  it("does not interrupt a switch away from an untouched edit view", async () => {
    // FR-035b guards work, not navigation. Opening a practitioner to look at them and
    // going elsewhere is the common case, and a prompt there is noise.
    render(<App />);
    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    openTab("Practitioners");
    await waitFor(() =>
      expect(screen.getByTestId("practitioner")).toBeInTheDocument(),
    );
    press(screen.getByRole("button", { name: /edit/i }));
    await waitFor(() =>
      expect(screen.getByTestId("practitioner-edit")).toBeInTheDocument(),
    );

    openTab("FAQ");

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "FAQ" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.queryByTestId("discard-confirm")).toBeNull();
  });

  it("does not leave the guard armed once the section that armed it is gone", async () => {
    // The lock-out this mechanism could cause. The flag lives in `App` but is owned by a
    // component that unmounts on every tab switch — so if it outlived its section, the
    // *next* switch, from a section holding nothing, would be blocked by a dirty form
    // that no longer exists, with no way to answer the prompt about it.
    await dirtyTheEditView();
    openTab("FAQ");
    await screen.findByTestId("discard-confirm");
    fireEvent.click(
      within(screen.getByTestId("discard-confirm")).getByRole("button", {
        name: /discard/i,
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "FAQ" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );

    // FAQ holds nothing typed, so leaving it must be immediate.
    openTab("Conversations");

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Conversations" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.queryByTestId("discard-confirm")).toBeNull();
  });
});

// The Conversations panel is unmounted by a tab switch exactly as the other two are, so
// a staff reply typed and not yet sent went with it, unasked. The reply box reports its
// work through the same guard the editors use.
describe("App: leaving an unsent staff reply by choosing another tab", () => {
  beforeEach(() => {
    vi.spyOn(chatStream, "fetchChats").mockResolvedValue(
      listing({ chats: [chat()] }),
    );
    vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue({
      attention_total: 0,
      conversations: [
        {
          chat_id: "01STAFFCHAT",
          patient_name: "Grace Hopper",
          last_message_at: null,
          emphasized: false,
          escalated: false,
          escalation_reason: null,
          attention_since: null,
          assistant_may_reply: true,
          pause_seconds_remaining: null,
          booking_acts_version: 0,
        },
      ],
    });
  });

  /** Open the staff conversation and put `text` in its reply box. */
  async function typeStaffReply(text: string): Promise<void> {
    render(<App />);
    fireEvent.click(await screen.findByTestId("staff-conversation"));
    const box = await screen.findByLabelText("reply as staff");
    fireEvent.change(box, { target: { value: text } });
  }

  it("asks before discarding a typed reply, and keeps it when the discard is refused", async () => {
    await typeStaffReply("I've got this one.");

    openTab("Practitioners");
    await screen.findByTestId("discard-confirm");
    expect(screen.queryByTestId("practitioner-admin")).toBeNull();

    fireEvent.click(
      within(screen.getByTestId("discard-confirm")).getByRole("button", {
        name: /keep editing/i,
      }),
    );

    await waitFor(() => expect(screen.queryByTestId("discard-confirm")).toBeNull());
    expect(screen.getByRole("tab", { name: "Conversations" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByLabelText("reply as staff")).toHaveValue("I've got this one.");
  });

  it("does not interrupt once the reply box is empty again", async () => {
    // The flag follows the box, not the fact that something was once typed in it.
    await typeStaffReply("I've got this one.");
    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "" },
    });

    openTab("Practitioners");

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Practitioners" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.queryByTestId("discard-confirm")).toBeNull();
  });

  it("does not leave the guard armed once the discarded reply's panel is gone", async () => {
    await typeStaffReply("I've got this one.");
    openTab("FAQ");
    await screen.findByTestId("discard-confirm");
    fireEvent.click(
      within(screen.getByTestId("discard-confirm")).getByRole("button", {
        name: /discard/i,
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "FAQ" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );

    // Back to Conversations - where the box is empty now - and away again.
    openTab("Conversations");
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Conversations" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    openTab("Practitioners");

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Practitioners" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.queryByTestId("discard-confirm")).toBeNull();
  });
});
