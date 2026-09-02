import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StaffThread } from "../src/components/StaffThread";
import * as consoleApi from "../src/lib/consoleApi";
import type { Message } from "../src/lib/chatStream";

function message(overrides: Partial<Message> = {}): Message {
  return {
    id: "01M",
    sender: "patient",
    content: "is anyone there?",
    grounded: null,
    citations: null,
    attention_mark: null,
    created_at: "2026-09-01T12:00:00",
    ...overrides,
  };
}

const CHAT_ID = "01CHAT000000000000000000";

/** Render the thread as it appears for a conversation the assistant is still on. */
function renderThread(chatId: string | null = CHAT_ID) {
  return render(
    <StaffThread
      chatId={chatId}
      assistantMayReply={true}
      pauseSecondsRemaining={null}
      onSetAssistant={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([]);
});

describe("StaffThread: reading the conversation", () => {
  it("shows the whole thread, every sender, in one ordered list", async () => {
    // FR-025: a staff member reads what the patient already saw, not a filtered
    // extract of it - the assistant's replies included.
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({ id: "p1", sender: "patient", content: "when can I visit?" }),
      message({ id: "a1", sender: "assistant", content: "I don't have an answer." }),
      message({ id: "s1", sender: "staff", content: "8am to 5pm." }),
    ]);

    renderThread();

    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));
    const senders = screen
      .getAllByTestId("message")
      .map((node) => node.getAttribute("data-sender"));
    expect(senders).toEqual(["patient", "assistant", "staff"]);
  });

  it("reads the conversation it was given", async () => {
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValue([]);

    renderThread();

    await waitFor(() => expect(fetchThread).toHaveBeenCalledWith(CHAT_ID));
  });

  it("loads the newly opened conversation's own thread when it changes", async () => {
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValue([]);

    const { rerender } = renderThread();
    await waitFor(() => expect(fetchThread).toHaveBeenCalledWith(CHAT_ID));
    rerender(
      <StaffThread
        chatId="01OTHER"
        assistantMayReply={true}
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );

    await waitFor(() => expect(fetchThread).toHaveBeenCalledWith("01OTHER"));
  });

  it("says why when the conversation could not be read", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockRejectedValue(
      new Error("This conversation no longer exists."),
    );

    renderThread();

    await waitFor(() =>
      expect(screen.getByTestId("staff-error")).toHaveTextContent(
        "This conversation no longer exists.",
      ),
    );
  });

  it("offers no composer until a conversation is open", () => {
    renderThread(null);

    expect(screen.queryByLabelText("reply as staff")).toBeNull();
    expect(screen.getByTestId("staff-no-thread")).toBeInTheDocument();
  });
});

describe("StaffThread: writing into it", () => {
  it("posts the reply into the patient's own conversation", async () => {
    const post = vi
      .spyOn(consoleApi, "postStaffMessage")
      .mockResolvedValue(message({ id: "s1", sender: "staff", content: "On it." }));

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));

    await waitFor(() => expect(post).toHaveBeenCalledWith(CHAT_ID, "On it."));
  });

  it("shows the posted reply in the thread without a reload", async () => {
    vi.spyOn(consoleApi, "postStaffMessage").mockResolvedValue(
      message({ id: "s1", sender: "staff", content: "On it." }),
    );

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));

    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent("On it."),
    );
    expect(screen.getByLabelText("reply as staff")).toHaveValue("");
  });

  it("renders the message the server stored, not the text that was typed", async () => {
    // The response *is* the stored message, so what appears is what the patient will
    // see - it cannot drift from it.
    vi.spyOn(consoleApi, "postStaffMessage").mockResolvedValue(
      message({ id: "s1", sender: "staff", content: "On it." }),
    );

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));

    await waitFor(() => expect(screen.getByTestId("message")).toBeInTheDocument());
    expect(screen.getByTestId("message")).toHaveAttribute("data-sender", "staff");
  });

  it("keeps what was typed when the post fails, and says why", async () => {
    vi.spyOn(consoleApi, "postStaffMessage").mockRejectedValue(
      new Error("This conversation no longer exists."),
    );

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));

    await waitFor(() =>
      expect(screen.getByTestId("staff-error")).toHaveTextContent(
        "This conversation no longer exists.",
      ),
    );
    expect(screen.getByLabelText("reply as staff")).toHaveValue("On it.");
  });

  it("sends nothing for whitespace alone", async () => {
    const post = vi.spyOn(consoleApi, "postStaffMessage");

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "   " },
    });
    fireEvent.click(screen.getByText("Send as staff"));

    expect(post).not.toHaveBeenCalled();
  });
});

// --- 007 (US2): the mark on the message that needs a person -----------------------

describe("StaffThread: per-message marks", () => {
  it("renders the mark a message carries", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({ id: "p1", attention_mark: "unanswered" }),
    ]);

    renderThread();

    expect(await screen.findByTestId("attention-mark")).toBeInTheDocument();
  });

  it("says which of the four kinds it is when asked", async () => {
    // FR-027a: the list says a conversation needs a person; the message is where the
    // reason lives, and a staff member has to be able to read it.
    const kinds = [
      "patient_asked_for_person",
      "corpus_could_not_answer",
      "assistant_failed",
      "unanswered",
    ] as const;
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue(
      kinds.map((kind, index) =>
        message({ id: `p${index}`, attention_mark: kind }),
      ),
    );

    renderThread();

    const marks = await screen.findAllByTestId("attention-mark");
    expect(marks.map((node) => node.getAttribute("data-mark"))).toEqual([...kinds]);
    // Each renders a distinct human reading, so the four are not one indicator with
    // four invisible meanings.
    const readings = new Set(marks.map((node) => node.textContent));
    expect(readings.size).toBe(4);
  });

  it("renders no mark on a message that carries none", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({ id: "p1", attention_mark: null }),
    ]);

    renderThread();

    await screen.findByTestId("message");
    expect(screen.queryByTestId("attention-mark")).toBeNull();
  });
});

// --- 007 (US3): the switch, and the countdown it shows ----------------------------

describe("StaffThread: the assistant switch", () => {
  function renderWithSwitch(
    props: Partial<{
      assistantMayReply: boolean;
      pauseSecondsRemaining: number | null;
      onSetAssistant: (enabled: boolean) => void;
    }> = {},
  ) {
    const onSetAssistant = props.onSetAssistant ?? vi.fn();
    render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply={props.assistantMayReply ?? true}
        pauseSecondsRemaining={props.pauseSecondsRemaining ?? null}
        onSetAssistant={onSetAssistant}
      />,
    );
    return onSetAssistant;
  }

  it("always states where the assistant stands, not only when something is wrong", () => {
    // FR-017: a control that appears only while a conversation is silenced makes a
    // staff member infer the ordinary case from its absence.
    renderWithSwitch({ assistantMayReply: true });

    const control = screen.getByTestId("assistant-switch");
    expect(control).toBeInTheDocument();
    expect(control).toBeChecked();
  });

  it("reads as off on a conversation the patient just escalated", () => {
    // US2 scenario 9: the staff member does not have to work out that the assistant
    // has gone quiet - the switch already says so.
    renderWithSwitch({ assistantMayReply: false, pauseSecondsRemaining: null });

    expect(screen.getByTestId("assistant-switch")).not.toBeChecked();
  });

  it("shows no deadline while escalated, because there is none", () => {
    // Rendering a zero would claim a countdown had run out; an escalation never had
    // one to run out.
    renderWithSwitch({ assistantMayReply: false, pauseSecondsRemaining: null });

    expect(screen.queryByTestId("pause-countdown")).toBeNull();
  });

  it("shows the remaining time while a pause is running", () => {
    renderWithSwitch({ assistantMayReply: false, pauseSecondsRemaining: 118 });

    const countdown = screen.getByTestId("pause-countdown");
    expect(countdown).toHaveAttribute("data-seconds", "118");
    expect(countdown).toHaveTextContent("1:58");
  });

  it("turns the assistant back on", () => {
    const onSetAssistant = renderWithSwitch({ assistantMayReply: false });

    fireEvent.click(screen.getByTestId("assistant-switch"));

    expect(onSetAssistant).toHaveBeenCalledWith(true);
  });

  it("turns the assistant off", () => {
    const onSetAssistant = renderWithSwitch({ assistantMayReply: true });

    fireEvent.click(screen.getByTestId("assistant-switch"));

    expect(onSetAssistant).toHaveBeenCalledWith(false);
  });

  it("re-syncs the countdown from the server rather than counting locally", () => {
    // FR-018: the deadline is stored, and the number shown is the server's own
    // arithmetic over it. Two tabs therefore agree, and one that was open longer does
    // not drift from one just loaded.
    const { rerender } = render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply={false}
        pauseSecondsRemaining={110}
        onSetAssistant={vi.fn()}
      />,
    );
    expect(screen.getByTestId("pause-countdown")).toHaveAttribute(
      "data-seconds",
      "110",
    );

    rerender(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply={false}
        pauseSecondsRemaining={108}
        onSetAssistant={vi.fn()}
      />,
    );

    expect(screen.getByTestId("pause-countdown")).toHaveAttribute(
      "data-seconds",
      "108",
    );
  });

  it("offers no switch until a conversation is open", () => {
    render(
      <StaffThread
        chatId={null}
        assistantMayReply={true}
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );

    expect(screen.queryByTestId("assistant-switch")).toBeNull();
  });
});

// --- the open thread following the console poll --------------------------------------
//
// Not an FR: FR-029c asks only that a newly emphasized conversation reach the *list*
// without a reload, and that a staff message reach the *patient's* thread. Both already
// held. What did not is the conversation a staff member is sitting with open — it was
// loaded once and never again, so a patient message arriving into it was invisible until
// they clicked away and back. These pin the mechanism that closes that.

describe("StaffThread: following the poll", () => {
  function thread(...messages: Partial<Message>[]): Message[] {
    return messages.map((overrides, index) =>
      message({ id: `m${index}`, created_at: `2026-09-01T12:0${index}:00`, ...overrides }),
    );
  }

  function renderPolled(lastMessageAt: string | null) {
    return render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply={false}
        pauseSecondsRemaining={null}
        lastMessageAt={lastMessageAt}
        onSetAssistant={vi.fn()}
      />,
    );
  }

  function polled(lastMessageAt: string | null) {
    return (
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply={false}
        pauseSecondsRemaining={null}
        lastMessageAt={lastMessageAt}
        onSetAssistant={vi.fn()}
      />
    );
  }

  it("loads the thread again when the poll reports a newer message", async () => {
    // A patient wrote into the conversation this staff member is reading. Nothing was
    // clicked and nothing was reloaded; the poll already running is what says so.
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "when can I visit?" }))
      .mockResolvedValue(
        thread({ content: "when can I visit?" }, { content: "anyone there?" }),
      );

    const { rerender } = renderPolled("2026-09-01T12:00:00");
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(polled("2026-09-01T12:01:00"));

    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent("anyone there?"),
    );
    expect(fetchThread).toHaveBeenCalledTimes(2);
  });

  it("brings the mark on the message that arrived, not just its text", async () => {
    // The mark is the whole reason the staff side reads a conversation - a message that
    // arrives without it says a person is needed nowhere in particular.
    vi.spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce([])
      .mockResolvedValue(thread({ content: "anyone there?", attention_mark: "unanswered" }));

    const { rerender } = renderPolled(null);
    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalledTimes(1));

    rerender(polled("2026-09-01T12:00:00"));

    const mark = await screen.findByTestId("attention-mark");
    expect(mark).toHaveAttribute("data-mark", "unanswered");
  });

  it("fetches the first message to arrive in a conversation the poll called empty", async () => {
    // A conversation with nothing in it polls as `last_message_at: null`, and that is a
    // real answer describing the thread just loaded - not the absence of one. Treating
    // the two alike would make the very first message sent into an open conversation
    // look like it was already accounted for, and it would never be fetched.
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce([])
      .mockResolvedValue(thread({ content: "first thing I've said" }));

    const { rerender } = renderPolled(null);
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(1));

    rerender(polled("2026-09-01T12:00:00"));

    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent(
        "first thing I've said",
      ),
    );
  });

  it("does not refetch while the poll keeps reporting the same time", async () => {
    // A 2-second poll that reread every open thread on every tick would cost one
    // history read per tick per open tab to learn that nothing had happened.
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValue(thread({ content: "when can I visit?" }));

    const { rerender } = renderPolled("2026-09-01T12:00:00");
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(1));

    rerender(polled("2026-09-01T12:00:00"));
    rerender(polled("2026-09-01T12:00:00"));

    expect(fetchThread).toHaveBeenCalledTimes(1);
  });

  it("does not refetch on the first value it sees for a conversation", async () => {
    // That value describes the thread the pane has just loaded, so there is nothing new
    // in it to fetch.
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValue(thread({ content: "when can I visit?" }));

    renderPolled("2026-09-01T12:00:00");

    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(1));
    expect(fetchThread).toHaveBeenCalledTimes(1);
  });

  it("leaves the thread as it was, and silent, when a refetch fails", async () => {
    // The next tick tries again. An error banner over a blip a staff member cannot act
    // on would only teach them to ignore the one that matters.
    vi.spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "when can I visit?" }))
      .mockRejectedValue(new Error("network blip"));

    const { rerender } = renderPolled("2026-09-01T12:00:00");
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(polled("2026-09-01T12:01:00"));

    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("staff-thread")).toHaveTextContent("when can I visit?");
    expect(screen.queryByTestId("staff-error")).toBeNull();
  });

  it("shows a staff member's own reply once, not twice, after the refetch it causes", async () => {
    // The post is shown the moment it lands, and the poll then reports that same message
    // as the newest one - so the refetch it triggers covers ground the pane already has.
    // It replaces the thread rather than adding to it, which is what makes that safe.
    const posted = message({
      id: "s1",
      sender: "staff",
      content: "I'm here, one moment.",
      created_at: "2026-09-01T12:01:00",
    });
    vi.spyOn(consoleApi, "postStaffMessage").mockResolvedValue(posted);
    vi.spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(
        thread({ content: "anyone there?", attention_mark: "unanswered" }),
      )
      .mockResolvedValue([
        message({
          id: "m0",
          content: "anyone there?",
          attention_mark: null,
          created_at: "2026-09-01T12:00:00",
        }),
        posted,
      ]);

    const { rerender } = renderPolled("2026-09-01T12:00:00");
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "I'm here, one moment." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent(
        "I'm here, one moment.",
      ),
    );

    // The poll catches up: its newest message is now the reply that was just posted.
    rerender(polled("2026-09-01T12:01:00"));

    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        screen
          .getAllByTestId("message")
          .filter((node) => node.getAttribute("data-sender") === "staff"),
      ).toHaveLength(1),
    );
    expect(screen.getAllByTestId("message")).toHaveLength(2);
  });

  it("clears the marks a staff reply answers, which no local append could", async () => {
    // FR-029a's clearing happens server-side, across messages already on screen. The
    // posted reply is appended locally and says nothing about them; the refetch is what
    // carries it.
    vi.spyOn(consoleApi, "postStaffMessage").mockResolvedValue(
      message({ id: "s1", sender: "staff", content: "On it.", created_at: "2026-09-01T12:01:00" }),
    );
    vi.spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(
        thread({ content: "anyone there?", attention_mark: "unanswered" }),
      )
      .mockResolvedValue([
        message({ id: "m0", content: "anyone there?", attention_mark: null }),
        message({ id: "s1", sender: "staff", content: "On it." }),
      ]);

    const { rerender } = renderPolled("2026-09-01T12:00:00");
    expect(await screen.findByTestId("attention-mark")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    await waitFor(() => expect(consoleApi.postStaffMessage).toHaveBeenCalled());
    rerender(polled("2026-09-01T12:01:00"));

    await waitFor(() => expect(screen.queryByTestId("attention-mark")).toBeNull());
  });

  it("waits for a post in flight before acting on a newer poll value", async () => {
    // A refetch issued mid-post would be answered from before the reply was stored, and
    // replacing the thread with that answer would take it back off the screen. The tick
    // is left unhandled and retried once the post lands, so nothing is lost by waiting.
    let landPost: (posted: Message) => void = () => undefined;
    vi.spyOn(consoleApi, "postStaffMessage").mockReturnValue(
      new Promise<Message>((resolve) => {
        landPost = resolve;
      }),
    );
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValue(thread({ content: "anyone there?" }));

    const { rerender } = renderPolled("2026-09-01T12:00:00");
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    rerender(polled("2026-09-01T12:01:00"));

    // Still only the load from mount: the tick was seen and deliberately not acted on.
    expect(fetchThread).toHaveBeenCalledTimes(1);

    landPost(
      message({ id: "s1", sender: "staff", content: "On it.", created_at: "2026-09-01T12:01:00" }),
    );

    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(2));
  });
});
