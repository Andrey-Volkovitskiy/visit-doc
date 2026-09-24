import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StaffThread } from "../src/components/StaffThread";
import * as consoleApi from "../src/lib/consoleApi";
import type {
  BookingActOutcome,
  Message,
  RequestOutcome,
} from "../src/lib/chatStream";
import {
  READ_TIMEOUT_MESSAGE,
  READ_TIMEOUT_MS,
} from "../src/lib/useThreadReads";

function message(overrides: Partial<Message> = {}): Message {
  return {
    id: "01M",
    sender: "patient",
    content: "is anyone there?",
    request_outcomes: null,
    attention_mark: null,
    booking_acts: null,
    created_at: "2026-09-01T12:00:00",
    ...overrides,
  };
}

const CHAT_ID = "01CHAT000000000000000000";

/** The staff side is where an answer can be audited against what it stood on. */
const CITED = [
  { entry_id: 7, chunk_index: 0, chunk_text: "Bring your referral letter." },
];

/** One answered request's outcome, the shape a reply now carries per question. */
/**
 * Open every evidence marker in the thread.
 *
 * The outcome blocks sit behind them now (FR-027), so the five tests below each need
 * one step before they can read what an outcome says. Each property is unchanged; only
 * the moment the block exists has moved (FR-038).
 */
function expandAllMarkers(): void {
  for (const marker of screen.getAllByTestId("outcome-marker")) {
    fireEvent.click(marker);
  }
}

function answeredOutcome(
  position: number,
  question: string,
  answer: string,
  verdict: RequestOutcome["verdict"] = "answered",
): RequestOutcome {
  return { position, question, answer, verdict, citations: CITED };
}


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

    await waitFor(() =>
      expect(fetchThread).toHaveBeenCalledWith(CHAT_ID, expect.any(AbortSignal)),
    );
  });

  it("loads the newly opened conversation's own thread when it changes", async () => {
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValue([]);

    const { rerender } = renderThread();
    await waitFor(() =>
      expect(fetchThread).toHaveBeenCalledWith(CHAT_ID, expect.any(AbortSignal)),
    );
    rerender(
      <StaffThread
        chatId="01OTHER"
        assistantMayReply={true}
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(fetchThread).toHaveBeenCalledWith("01OTHER", expect.any(AbortSignal)),
    );
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

  it("posts the reply when Enter is pressed, and when Ctrl+Enter is", async () => {
    const post = vi
      .spyOn(consoleApi, "postStaffMessage")
      .mockResolvedValue(message({ id: "s1", sender: "staff", content: "On it." }));

    renderThread();
    const box = await screen.findByLabelText("reply as staff");
    fireEvent.change(box, { target: { value: "On it." } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: false });

    await waitFor(() => expect(post).toHaveBeenCalledWith(CHAT_ID, "On it."));

    fireEvent.change(box, { target: { value: "And again." } });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });

    await waitFor(() => expect(post).toHaveBeenCalledWith(CHAT_ID, "And again."));
  });

  it("does not post on Shift+Enter, leaving the draft for a newline", async () => {
    const post = vi.spyOn(consoleApi, "postStaffMessage");

    renderThread();
    const box = (await screen.findByLabelText(
      "reply as staff",
    )) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "line one" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });

    expect(post).not.toHaveBeenCalled();
    expect(box.value).toBe("line one");
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

  it("sends nothing for whitespace alone, and says so on the control", async () => {
    const post = vi.spyOn(consoleApi, "postStaffMessage");

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "   " },
    });

    // The disabled assertion is the load-bearing half now. FR-019b disables the
    // control for exactly this input, and a click on a disabled button never reaches
    // the handler — so "not called" became free the moment the control agreed with what
    // it would do, and would stay true with the guard inside the handler deleted.
    const send = screen.getByRole("button", { name: /Send as staff/ });
    expect(send).toBeDisabled();

    fireEvent.click(send);
    expect(post).not.toHaveBeenCalled();
  });

  it("posts one reply, not two, when the send is clicked again before it lands", async () => {
    // A staff member who clicks again because nothing appeared to happen must not put a
    // second copy of the same sentence into a patient's thread. The box is only cleared
    // once the post lands, so a second call before then reads the very same text and
    // passes the very same guards - and both copies are stored, not just shown.
    let landPost: (posted: Message) => void = () => undefined;
    const post = vi.spyOn(consoleApi, "postStaffMessage").mockReturnValue(
      new Promise<Message>((resolve) => {
        landPost = resolve;
      }),
    );

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    fireEvent.click(screen.getByText("Send as staff"));

    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(CHAT_ID, "On it.");

    await act(async () => {
      landPost(message({ id: "s1", sender: "staff", content: "On it." }));
    });
  });

  it("refuses the second send in the handler, before any repaint could disable it", async () => {
    // Both clicks land in one batch, so nothing has re-rendered between them: the button
    // still carries the enabled markup React painted before the first, and a guard read
    // from state would still read the state that first click has not committed yet. What
    // stops the duplicate here is the handler's own latch.
    let landPost: (posted: Message) => void = () => undefined;
    const post = vi.spyOn(consoleApi, "postStaffMessage").mockReturnValue(
      new Promise<Message>((resolve) => {
        landPost = resolve;
      }),
    );

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    const send = screen.getByText("Send as staff");
    await act(async () => {
      fireEvent.click(send);
      fireEvent.click(send);
    });

    expect(post).toHaveBeenCalledTimes(1);

    await act(async () => {
      landPost(message({ id: "s1", sender: "staff", content: "On it." }));
    });
  });

  it("says the send is under way, so nobody clicks again to find out", async () => {
    let landPost: (posted: Message) => void = () => undefined;
    vi.spyOn(consoleApi, "postStaffMessage").mockReturnValue(
      new Promise<Message>((resolve) => {
        landPost = resolve;
      }),
    );

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));

    await waitFor(() =>
      expect(screen.getByText("Send as staff")).toBeDisabled(),
    );

    await act(async () => {
      landPost(message({ id: "s1", sender: "staff", content: "On it." }));
    });
    // And it takes the reply again afterwards - the guard is for the send in flight, not
    // for the rest of the shift.
    //
    // The text goes in first. A landed post clears the box, so an empty composer is now
    // a second, unrelated reason for Send to be disabled (FR-019b) — asserting straight
    // away would pin *that* rather than the latch releasing, and would keep failing with
    // the latch working perfectly.
    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "Anything else?" },
    });
    expect(screen.getByRole("button", { name: /Send as staff/ })).toBeEnabled();
  });

  it("takes the next send after one that failed", async () => {
    // The failed reply is still in the box by design; a latch left closed would leave a
    // staff member holding text they can no longer send.
    const post = vi
      .spyOn(consoleApi, "postStaffMessage")
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(message({ id: "s1", sender: "staff", content: "On it." }));

    renderThread();
    fireEvent.change(await screen.findByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    await waitFor(() => expect(screen.getByTestId("staff-error")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Send as staff"));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
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

  function renderPolled(lastMessageAt: string | null, pollTick?: number) {
    return render(polled(lastMessageAt, pollTick));
  }

  function polled(lastMessageAt: string | null, pollTick?: number) {
    return (
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply={false}
        pauseSecondsRemaining={null}
        lastMessageAt={lastMessageAt}
        pollTick={pollTick}
        onSetAssistant={vi.fn()}
      />
    );
  }

  describe("when only the booking record changes (016 FR-016a, SC-007)", () => {
    const AT = "2026-09-01T12:00:00";

    function withActs(bookingActsVersion: number, pollTick: number) {
      return (
        <StaffThread
          chatId={CHAT_ID}
          assistantMayReply={false}
          pauseSecondsRemaining={null}
          lastMessageAt={AT}
          bookingActsVersion={bookingActsVersion}
          pollTick={pollTick}
          onSetAssistant={vi.fn()}
        />
      );
    }

    /** The patient's message with one cancellation on it, settled or not yet. */
    function cancelling(outcome: BookingActOutcome | null): Message[] {
      return thread({
        content: "please cancel my Friday appointment",
        booking_acts: [
          {
            operation: "cancel",
            outcome,
            refusal_reason: null,
            practitioner_full_name: "Andreas Vesalius",
            starts_at: "2027-01-15T09:00:00",
            ends_at: null,
            previous_practitioner_full_name: null,
            previous_starts_at: null,
          },
        ],
      });
    }

    it("re-reads once when an act settles, and the outcome replaces unknown", async () => {
      // The turn cancelled the appointment and then failed before replying, so no
      // message was written after the act and `last_message_at` never moves. Only the
      // booking-record version says the act has since settled.
      const fetchThread = vi
        .spyOn(consoleApi, "fetchThread")
        .mockResolvedValueOnce(cancelling(null))
        .mockResolvedValue(cancelling("done"));

      const { rerender } = render(withActs(1, 1));
      await screen.findByTestId("outcome-marker");
      fireEvent.click(screen.getByTestId("outcome-marker"));
      expect(screen.getByTestId("booking-act")).toHaveAttribute("data-outcome", "unknown");
      expect(screen.getByTestId("booking-act")).toHaveTextContent("Outcome unknown");

      rerender(withActs(2, 2));

      await waitFor(() =>
        expect(screen.getByTestId("booking-act")).toHaveAttribute("data-outcome", "done"),
      );
      expect(screen.getByTestId("booking-act")).not.toHaveTextContent(/unknown/i);
      expect(screen.getByTestId("outcome-marker")).toHaveAttribute(
        "data-outcome-state",
        "served",
      );
      // Once, not once per tick: the pair it re-read for is now the pair it holds.
      rerender(withActs(2, 3));
      rerender(withActs(2, 4));
      await act(async () => undefined);
      expect(fetchThread).toHaveBeenCalledTimes(2);
    });

    it("does not re-read while the pair stays the same", async () => {
      const fetchThread = vi
        .spyOn(consoleApi, "fetchThread")
        .mockResolvedValue(cancelling("done"));

      const { rerender } = render(withActs(3, 1));
      await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(1));

      rerender(withActs(3, 2));
      rerender(withActs(3, 3));
      await act(async () => undefined);

      expect(fetchThread).toHaveBeenCalledTimes(1);
    });

    it("retries a version's re-read that failed, on a later tick reporting the same pair", async () => {
      // The same "accounted for only on landing" rule as for a message: a failed read
      // must leave the version owed, or the settled outcome never replaces "unknown".
      const fetchThread = vi
        .spyOn(consoleApi, "fetchThread")
        .mockResolvedValueOnce(cancelling(null))
        .mockRejectedValueOnce(new Error("network blip"))
        .mockResolvedValue(cancelling("done"));

      const { rerender } = render(withActs(1, 1));
      await screen.findByTestId("outcome-marker");

      rerender(withActs(2, 2));
      await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(2));
      await act(async () => undefined);

      rerender(withActs(2, 3));

      await waitFor(() =>
        expect(screen.getByTestId("outcome-marker")).toHaveAttribute(
          "data-outcome-state",
          "served",
        ),
      );
      expect(fetchThread).toHaveBeenCalledTimes(3);
    });
  });

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
    // history read per tick per open tab to learn that nothing had happened. The ticks
    // arrive regardless - they are what lets a failed read be retried - so it is the
    // marker, not the absence of a tick, that has to keep them free.
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValue(thread({ content: "when can I visit?" }));

    const { rerender } = renderPolled("2026-09-01T12:00:00", 1);
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(1));

    rerender(polled("2026-09-01T12:00:00", 2));
    rerender(polled("2026-09-01T12:00:00", 3));

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

  it("brings in the message a failed refetch missed, on a later tick reporting the same time", async () => {
    // Nothing else has happened, so the poll goes on reporting the very time whose read
    // failed. A marker recorded when that read was *issued* matches every one of those
    // ticks, and the patient's message stays off the staff member's screen until they
    // click away and back. Recording what was actually read is what leaves the tick
    // owed, and the next one pays it.
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "when can I visit?" }))
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(
        thread({ content: "when can I visit?" }, { content: "anyone there?" }),
      );

    const { rerender } = renderPolled("2026-09-01T12:00:00", 1);
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(polled("2026-09-01T12:01:00", 2));
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(2));
    // Let that read fail before the next tick arrives, as a 2-second interval would.
    await act(async () => undefined);
    expect(screen.getAllByTestId("message")).toHaveLength(1);

    rerender(polled("2026-09-01T12:01:00", 3));

    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent("anyone there?"),
    );
  });

  it("applies a read that was still outstanding when the next tick arrived", async () => {
    // A read slower than the two-second interval spans a tick, and every tick re-runs
    // the effect. That says nothing about the answer: what makes one stale is the
    // conversation being closed, and this one is still open. Judging it by "the effect
    // has re-run since" instead throws away every read that crosses a tick boundary, and
    // against a backend consistently slower than the interval the thread never updates
    // at all - a read reissued forever and never landing.
    let answerSlowRead!: (messages: Message[]) => void;
    const slowRead = new Promise<Message[]>((resolve) => {
      answerSlowRead = resolve;
    });
    // What the tick arriving mid-read asks. Deliberately not suppressed (see the
    // assertion at the end) and deliberately left unanswered here: resolved instead, it
    // would land *first*, retire the outstanding read, and paint its own answer - so
    // this test would pass with the outstanding read discarded, which is the regression
    // it exists to prevent. Its content differs from the outstanding read's for the
    // same reason.
    let answerTickRead!: (messages: Message[]) => void;
    const tickRead = new Promise<Message[]>((resolve) => {
      answerTickRead = resolve;
    });
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "when can I visit?" }))
      .mockReturnValueOnce(slowRead)
      .mockReturnValueOnce(tickRead)
      .mockResolvedValue(
        thread({ content: "when can I visit?" }, { content: "a later answer" }),
      );

    const { rerender } = renderPolled("2026-09-01T12:00:00", 1);
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(polled("2026-09-01T12:01:00", 2));
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(2));

    // The next tick arrives while that read is still outstanding.
    rerender(polled("2026-09-01T12:01:00", 3));

    await act(async () => {
      answerSlowRead(
        thread({ content: "when can I visit?" }, { content: "anyone there?" }),
      );
      await slowRead;
    });

    // The outstanding read's own answer, and nothing else's.
    expect(screen.getByTestId("staff-thread")).toHaveTextContent("anyone there?");
    // A tick that is still owed a read issues one, even with another outstanding, and
    // that is the point rather than an oversight: suppressing it would make this pane's
    // liveness depend on the outstanding read completing, so a wedged connection - the
    // very case a retry exists for - would stop the refetch for as long as it hangs, and
    // a read that never settles would stop it for good. `useConsolePoll` refuses the
    // same trade for the same reason. The cost is bounded by how far a read runs behind
    // the interval, and an answer is only ever discarded for being the older one.
    expect(fetchThread).toHaveBeenCalledTimes(3);

    // And when the tick's own read finally answers it takes over, because it is the
    // newer of the two - the rule is which read is newer, never which arrives first.
    await act(async () => {
      answerTickRead(
        thread({ content: "when can I visit?" }, { content: "a later answer" }),
      );
      await tickRead;
    });
    expect(screen.getByTestId("staff-thread")).toHaveTextContent("a later answer");
  });

  it("discards a stale read even when the newly opened conversation is slower", async () => {
    // The retire-on-open assignment, which is the whole reason reads are numbered
    // rather than compared by chat id. Every other test here lets the new
    // conversation's opening read resolve immediately, so it advances the marker past
    // the stale read on its own and the retirement never does any work - delete the
    // line and they all still pass. This orders them the other way round, which is the
    // ordering the line exists for: the stale answer arrives first, is numbered above
    // nothing, and would be painted under the new conversation's name.
    const OTHER = "01CHAT000000000000000099";
    let answerFirst!: (messages: Message[]) => void;
    const firstRead = new Promise<Message[]>((resolve) => {
      answerFirst = resolve;
    });
    let answerSecond!: (messages: Message[]) => void;
    const secondRead = new Promise<Message[]>((resolve) => {
      answerSecond = resolve;
    });
    vi.spyOn(consoleApi, "fetchThread")
      .mockReturnValueOnce(firstRead)
      .mockReturnValueOnce(secondRead)
      .mockResolvedValue([]);

    function pane(chatId: string) {
      return (
        <StaffThread
          chatId={chatId}
          assistantMayReply={false}
          pauseSecondsRemaining={null}
          onSetAssistant={vi.fn()}
        />
      );
    }

    const { rerender } = render(pane(CHAT_ID));
    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalledTimes(1));
    rerender(pane(OTHER));
    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalledTimes(2));

    // The first conversation's read answers, with the second's still out.
    await act(async () => {
      answerFirst(thread({ content: "the conversation you left" }));
      await firstRead;
    });
    expect(screen.getByTestId("staff-thread")).not.toHaveTextContent(
      "the conversation you left",
    );

    await act(async () => {
      answerSecond(thread({ content: "the conversation you opened" }));
      await secondRead;
    });
    expect(screen.getByTestId("staff-thread")).toHaveTextContent(
      "the conversation you opened",
    );
  });

  it("abandons a read that hangs, and retries it on the next tick", async () => {
    // A read with no deadline is not slow, it is permanent: it neither resolves nor
    // rejects, so nothing releases the opening latch and every later tick reporting the
    // value it went out for returns early - the pane sits blank, with no banner and no
    // retry, for the rest of the visit. A read that *rejects* was always retried; this
    // is the one that never answers at all.
    vi.useFakeTimers();
    try {
      const fetchThread = vi
        .spyOn(consoleApi, "fetchThread")
        .mockReturnValueOnce(new Promise<Message[]>(() => undefined))
        .mockResolvedValue(thread({ content: "loaded at last" }));

      const { rerender } = renderPolled("2026-09-01T12:00:00", 1);
      await act(async () => undefined);
      expect(fetchThread).toHaveBeenCalledTimes(1);
      // The tick that follows asks nothing: the opening read is out for this very value.
      rerender(polled("2026-09-01T12:00:00", 2));
      await act(async () => undefined);
      expect(fetchThread).toHaveBeenCalledTimes(1);

      await act(async () => {
        vi.advanceTimersByTime(READ_TIMEOUT_MS);
      });
      expect(screen.getByTestId("staff-error")).toHaveTextContent(
        READ_TIMEOUT_MESSAGE,
      );

      // The latch is released, so the very next tick takes the read over.
      rerender(polled("2026-09-01T12:00:00", 3));
      await act(async () => undefined);
      expect(fetchThread).toHaveBeenCalledTimes(2);
      expect(screen.getByTestId("staff-thread")).toHaveTextContent("loaded at last");
      // And the banner it raised goes with the answer that disproves it.
      expect(screen.queryByTestId("staff-error")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not clear a failed send's banner when a later read lands", async () => {
    // "Has any read landed yet" is not "did the opening read fail". Answering the first
    // for the second cleared the banner of a reply that was never sent, while the reply
    // itself still sat in the box - telling a staff member their message went through.
    vi.spyOn(consoleApi, "postStaffMessage").mockRejectedValue(
      new Error("Could not send that reply. Please try again."),
    );
    vi.spyOn(consoleApi, "fetchThread")
      .mockRejectedValueOnce(new Error("Could not load this conversation."))
      .mockResolvedValue(thread({ content: "anyone there?" }));

    const { rerender } = renderPolled("2026-09-01T12:00:00", 1);
    await waitFor(() =>
      expect(screen.getByTestId("staff-error")).toHaveTextContent(
        "Could not load this conversation.",
      ),
    );

    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    await waitFor(() =>
      expect(screen.getByTestId("staff-error")).toHaveTextContent(
        "Could not send that reply.",
      ),
    );

    // The read the failed opening read is owed now succeeds. Asserted on what reached
    // the screen rather than on a call count, which is not this test's business.
    rerender(polled("2026-09-01T12:00:00", 2));
    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent("anyone there?"),
    );

    // The thread loaded; the send still failed, and the unsent reply is still in the box.
    expect(screen.getByTestId("staff-error")).toHaveTextContent(
      "Could not send that reply.",
    );
    expect(screen.getByLabelText("reply as staff")).toHaveValue("On it.");
  });

  it("does not show a reply twice when a reload already published it", async () => {
    // Post, leave, come back: the return reloads the thread, and if the post lands
    // after that reload the reload already carries it. Appending it anyway shows a
    // staff member their own reply twice, in the conversation they wrote it in, with
    // nothing to take the copy off again.
    const OTHER = "01CHAT000000000000000099";
    const posted = message({ id: "s1", sender: "staff", content: "On it." });
    let landPost!: (sent: Message) => void;
    vi.spyOn(consoleApi, "postStaffMessage").mockReturnValue(
      new Promise<Message>((resolve) => {
        landPost = resolve;
      }),
    );
    vi.spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "anyone there?" }))
      .mockResolvedValueOnce([])
      // The reload on coming back: the server has the reply by now.
      .mockResolvedValue([message({ id: "m0", content: "anyone there?" }), posted]);

    function pane(chatId: string) {
      return (
        <StaffThread
          chatId={chatId}
          assistantMayReply={false}
          pauseSecondsRemaining={null}
          onSetAssistant={vi.fn()}
        />
      );
    }

    const { rerender } = render(pane(CHAT_ID));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    await waitFor(() => expect(consoleApi.postStaffMessage).toHaveBeenCalled());

    rerender(pane(OTHER));
    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalledTimes(2));
    rerender(pane(CHAT_ID));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    await act(async () => {
      landPost(posted);
    });

    expect(screen.getAllByTestId("message")).toHaveLength(2);
  });

  it("discards a read that answers after another conversation was opened", async () => {
    // The other half of judging a read by the conversation it was issued for: one
    // outstanding across a change of conversation is answered from a thread no longer on
    // screen, and showing that answer would put one patient's messages under another's
    // name - the one mistake this pane must never make.
    let answerSlowRead!: (messages: Message[]) => void;
    const slowRead = new Promise<Message[]>((resolve) => {
      answerSlowRead = resolve;
    });
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "when can I visit?" }))
      .mockReturnValueOnce(slowRead)
      .mockResolvedValue(thread({ content: "a different patient entirely" }));

    const { rerender } = renderPolled("2026-09-01T12:00:00", 1);
    await waitFor(() =>
      expect(screen.getByText("when can I visit?")).toBeInTheDocument(),
    );

    rerender(polled("2026-09-01T12:01:00", 2));
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(2));

    // The staff member opens another conversation while that read is outstanding.
    rerender(
      <StaffThread
        chatId="01OTHER"
        assistantMayReply={false}
        pauseSecondsRemaining={null}
        lastMessageAt="2026-09-01T12:05:00"
        pollTick={3}
        onSetAssistant={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("a different patient entirely")).toBeInTheDocument(),
    );

    await act(async () => {
      answerSlowRead(
        thread({ content: "when can I visit?" }, { content: "anyone there?" }),
      );
      await slowRead;
    });

    expect(screen.queryByText("anyone there?")).toBeNull();
    expect(screen.getByText("a different patient entirely")).toBeInTheDocument();
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

  it("keeps refreshing a conversation while a post to it is in flight", async () => {
    // A refetch issued mid-post is answered from before the reply was stored, and this
    // pane used to hold the tick back for exactly that reason. It no longer needs to:
    // the reply is held as a pending post and merged back on top of whatever a read
    // brings, so a read that predates it cannot take it off the screen. Holding the
    // tick was one more way for a conversation to stop refreshing - a patient message
    // arriving while a staff member's post hung would not be shown at all.
    vi.spyOn(consoleApi, "postStaffMessage").mockReturnValue(
      new Promise<Message>(() => undefined),
    );
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "anyone there?" }))
      .mockResolvedValue(
        thread({ content: "anyone there?" }, { content: "still waiting" }),
      );

    const { rerender } = renderPolled("2026-09-01T12:00:00");
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    rerender(polled("2026-09-01T12:01:00"));

    // The patient's new message is read and shown, with the post still out.
    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent("still waiting"),
    );
  });

  it("leaves a conversation switched to mid-post able to send and to refresh", async () => {
    // The latch is per conversation. One shared flag belonged to whichever conversation
    // posted last, so switching away mid-post left the newly opened one with Send
    // painted disabled and its refetch held back - for a post that was never about it,
    // with nothing on screen explaining either.
    const OTHER = "01CHAT000000000000000099";
    vi.spyOn(consoleApi, "postStaffMessage").mockReturnValue(
      new Promise<Message>(() => undefined),
    );
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValue(thread({ content: "hello" }));

    function pane(chatId: string, lastMessageAt: string, pollTick: number) {
      return (
        <StaffThread
          chatId={chatId}
          assistantMayReply={false}
          pauseSecondsRemaining={null}
          lastMessageAt={lastMessageAt}
          pollTick={pollTick}
          onSetAssistant={vi.fn()}
        />
      );
    }

    const { rerender } = render(pane(CHAT_ID, "2026-09-01T12:00:00", 1));
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    await waitFor(() => expect(consoleApi.postStaffMessage).toHaveBeenCalled());

    rerender(pane(OTHER, "2026-09-01T12:00:00", 2));
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(2));

    // Sendable, and still following the poll. The text goes in *first*: switching
    // conversations clears the box, so an empty composer is now a second, unrelated
    // reason for Send to be disabled (FR-019b), and asserting on it before typing would
    // fail for a reason that has nothing to do with the per-conversation latch this
    // test exists for.
    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "Different patient." },
    });
    expect(
      screen.getByRole("button", { name: /Send as staff/ }),
    ).not.toBeDisabled();
    rerender(pane(OTHER, "2026-09-01T12:01:00", 3));
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(3));

    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "Different patient." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    await waitFor(() =>
      expect(consoleApi.postStaffMessage).toHaveBeenCalledWith(
        OTHER,
        "Different patient.",
      ),
    );
  });

  it("does not let a read issued before a staff post take the reply back off screen", async () => {
    // The `posting` guard stops a read being *issued* across the post; it says nothing
    // about one already out. That read was composed before the reply existed, so
    // applying it would erase the reply in front of the person who just wrote it, for as
    // long as a poll interval. Landing the post retires every read still in flight.
    let answerSlowRead!: (messages: Message[]) => void;
    const slowRead = new Promise<Message[]>((resolve) => {
      answerSlowRead = resolve;
    });
    let landPost!: (posted: Message) => void;
    const post = new Promise<Message>((resolve) => {
      landPost = resolve;
    });
    vi.spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "are you there?" }))
      .mockReturnValueOnce(slowRead)
      // What the refetch after the post reads: the server has the reply by then, so it
      // carries it. That refetch is wanted - it is what brings the cleared marks - and
      // the marker is deliberately left unfiled on the post so it still happens.
      .mockResolvedValue(
        thread({ content: "are you there?" }, { content: "On it.", sender: "staff" }),
      );
    vi.spyOn(consoleApi, "postStaffMessage").mockReturnValueOnce(post);

    const { rerender } = renderPolled("2026-09-01T12:00:00", 1);
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    // A patient message arrives and the read for it is slow.
    rerender(polled("2026-09-01T12:01:00", 2));
    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalledTimes(2));

    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "On it." },
    });
    fireEvent.click(screen.getByText("Send as staff"));
    await act(async () => {
      landPost(message({ id: "s1", sender: "staff", content: "On it." }));
      await post;
    });
    expect(screen.getByTestId("staff-thread")).toHaveTextContent("On it.");

    // The pre-post read now answers.
    await act(async () => {
      answerSlowRead(thread({ content: "are you there?" }, { content: "hello?" }));
      await slowRead;
    });

    expect(screen.getByTestId("staff-thread")).toHaveTextContent("On it.");
  });

  it("discards a read from an earlier visit to the conversation now open again", async () => {
    // Leaving a conversation and coming back is a second visit, not the same one. A read
    // issued during the first still names the conversation on screen, so comparing ids
    // accepts it - and it answers about a thread this pane has since thrown away and
    // reloaded. Only its number says so.
    const OTHER = "01CHAT000000000000000099";
    let answerFirstVisit!: (messages: Message[]) => void;
    const firstVisitRead = new Promise<Message[]>((resolve) => {
      answerFirstVisit = resolve;
    });
    vi.spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "first visit" }))
      .mockReturnValueOnce(firstVisitRead)
      .mockResolvedValue(thread({ content: "reloaded" }));

    function pane(chatId: string, lastMessageAt: string, pollTick: number) {
      return (
        <StaffThread
          chatId={chatId}
          assistantMayReply={false}
          pauseSecondsRemaining={null}
          lastMessageAt={lastMessageAt}
          pollTick={pollTick}
          onSetAssistant={vi.fn()}
        />
      );
    }

    const { rerender } = render(pane(CHAT_ID, "2026-09-01T12:00:00", 1));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));
    rerender(pane(CHAT_ID, "2026-09-01T12:01:00", 2));
    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalledTimes(2));

    rerender(pane(OTHER, "2026-09-01T12:01:00", 3));
    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent("reloaded"),
    );
    rerender(pane(CHAT_ID, "2026-09-01T12:01:00", 4));
    await act(async () => undefined);

    await act(async () => {
      answerFirstVisit(thread({ content: "from the first visit" }));
      await firstVisitRead;
    });

    expect(screen.getByTestId("staff-thread")).not.toHaveTextContent(
      "from the first visit",
    );
  });

  it("takes over from an opening read that failed, on a tick reporting the same value", async () => {
    // The read that opens a conversation used to have its poll value filed as handled
    // whether or not it landed. When it failed, every later tick reported the very value
    // already filed, so nothing fetched the thread and the pane sat blank behind the
    // banner until somebody wrote into the conversation.
    vi.spyOn(consoleApi, "fetchThread")
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(thread({ content: "loaded on the retry" }));

    const { rerender } = renderPolled("2026-09-01T12:00:00", 1);
    await waitFor(() => expect(screen.getByTestId("staff-error")).toBeTruthy());

    rerender(polled("2026-09-01T12:00:00", 2));

    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent(
        "loaded on the retry",
      ),
    );
    // And the banner goes with it: it described a load this answer just disproved.
    expect(screen.queryByTestId("staff-error")).toBeNull();
  });

  it("goes on reading the thread while an earlier read hangs", async () => {
    // A read that never settles must not disable this pane. An in-flight latch cleared
    // in `.finally` never runs for a request that hangs - and none of these reads carries
    // a timeout or an abort signal - so the refetch would be dead until a full reload.
    vi.spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(thread({ content: "first" }))
      .mockReturnValueOnce(new Promise<Message[]>(() => undefined))
      .mockResolvedValue(thread({ content: "first" }, { content: "arrived anyway" }));

    const { rerender } = renderPolled("2026-09-01T12:00:00", 1);
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(polled("2026-09-01T12:01:00", 2));
    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalledTimes(2));

    // That read for 12:01 never answers. A later message must still be read.
    rerender(polled("2026-09-01T12:02:00", 3));

    await waitFor(() =>
      expect(screen.getByTestId("staff-thread")).toHaveTextContent("arrived anyway"),
    );
  });

  it("renders citations on an assistant message, unlike the patient pane", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({
        id: "01A",
        sender: "assistant",
        content: "Bring your referral letter.",
        request_outcomes: [
          answeredOutcome(0, "what should I bring?", "Bring your referral letter."),
        ],
      }),
    ]);

    renderThread();

    await waitFor(() =>
      expect(screen.getByTestId("outcome-marker")).toBeInTheDocument(),
    );
    expandAllMarkers();
    expect(screen.getByTestId("citations")).toHaveTextContent(
      "Bring your referral letter.",
    );
  });

  it("marks an answer produced without reranking, and only that one", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({
        id: "01A",
        sender: "assistant",
        content: "one",
        request_outcomes: [answeredOutcome(0, "what should I bring?", "one")],
      }),
      message({
        id: "01B",
        sender: "assistant",
        content: "two",
        request_outcomes: [
          answeredOutcome(0, "when are you open?", "two", "answered_unreranked"),
        ],
      }),
    ]);

    renderThread();

    await waitFor(() => {
      expect(screen.getAllByTestId("message").length).toBe(2);
    });
    // Both are expanded, then counted: with only one open, "exactly one mark" would be
    // true of a build that marked every block.
    expandAllMarkers();
    expect(screen.getAllByTestId("verdict-mark").length).toBe(1);
  });

  it("shows no score anywhere - every number lives in the log", async () => {
    // A score rendered here and logged there is a second copy that can disagree with
    // the first, and a citation list cannot show what a gate *rejected* anyway, which
    // is the half a floor is actually tuned against.
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({
        id: "01A",
        sender: "assistant",
        content: "Bring your referral letter.",
        request_outcomes: [
          answeredOutcome(0, "what should I bring?", "Bring your referral letter."),
        ],
      }),
    ]);

    renderThread();

    await waitFor(() =>
      expect(screen.getByTestId("outcome-marker")).toBeInTheDocument(),
    );
    // Expanded first: a scan of a collapsed thread cannot find a score that is only
    // rendered inside the block, which is the one place it could appear.
    expandAllMarkers();
    expect(screen.getByTestId("citations")).toBeInTheDocument();
    expect(screen.getByTestId("staff-thread").textContent).not.toMatch(/0\.\d/);
  });
});


// --- Phase 1h: the thread hands each message's outcomes to the block that draws them --

describe("StaffThread request outcomes", () => {
  it("passes every outcome of a reply through to its own block", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({
        id: "01A",
        sender: "assistant",
        content: "We are at 5 Oak Street, and bring your referral letter.",
        request_outcomes: [
          answeredOutcome(0, "where are you?", "We are at 5 Oak Street."),
          answeredOutcome(1, "what should I bring?", "Bring your referral letter."),
        ],
      }),
    ]);

    renderThread();

    await waitFor(() =>
      expect(screen.getByTestId("outcome-marker")).toBeInTheDocument(),
    );
    expandAllMarkers();
    expect(screen.getAllByTestId("request-outcome")).toHaveLength(2);
    const blocks = screen.getAllByTestId("request-outcome");
    expect(blocks[0]).toHaveTextContent("where are you?");
    expect(blocks[1]).toHaveTextContent("what should I bring?");
  });

  it("draws no outcome block for a reply that ran no FAQ half", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({
        id: "01A",
        sender: "assistant",
        content: "You're booked for Friday at 9.",
        request_outcomes: null,
      }),
    ]);

    renderThread();

    await waitFor(() => {
      expect(screen.getByTestId("staff-thread")).toHaveTextContent("booked for Friday");
    });

    // Strengthened to assert no *marker* is rendered. The absence of a block became
    // free the moment blocks lived behind a marker — with nothing to expand, "no block"
    // would be true of a build that drew a marker on every message and of one that drew
    // no blocks at all. The marker is the thing FR-026 says must not be there.
    expect(screen.queryByTestId("outcome-marker")).toBeNull();
    expect(screen.queryByTestId("request-outcome")).toBeNull();
  });
});

// --- 015 Phase 5 (US2): the console thread as a designed surface ----------------------

describe("StaffThread: the assistant explanation (FR-024a)", () => {
  beforeEach(() => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([]);
  });

  function renderThread(assistantMayReply: boolean, pauseSecondsRemaining: number | null = null) {
    render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply={assistantMayReply}
        pauseSecondsRemaining={pauseSecondsRemaining}
        onSetAssistant={vi.fn()}
      />,
    );
  }

  it("is present with the switch on", () => {
    renderThread(true);
    expect(screen.getByTestId("assistant-explanation")).toBeInTheDocument();
  });

  it("is present with the switch off", () => {
    renderThread(false);
    expect(screen.getByTestId("assistant-explanation")).toBeInTheDocument();
  });

  it("is present while a pause is counting down", () => {
    renderThread(false, 300);
    expect(screen.getByTestId("assistant-explanation")).toBeInTheDocument();
  });

  it("is visible without hover, focus or activation", () => {
    // FR-024a rules out a tooltip, a `?` affordance, or anything else that has to be
    // discovered. This is the one control whose effect reaches a real patient
    // immediately, so nothing about its explanation may be hidden.
    renderThread(true);
    const explanation = screen.getByTestId("assistant-explanation");

    // Not inside a `title`, and not behind any of the attributes a reveal-on-demand
    // affordance is built from.
    expect(explanation).toBeVisible();
    expect(explanation.closest("[hidden]")).toBeNull();
    expect(explanation.getAttribute("role")).not.toBe("tooltip");
    expect(explanation.closest("[role='tooltip']")).toBeNull();
    expect(explanation.closest("[aria-hidden='true']")).toBeNull();
  });

  it("says what turning the control off does, and that the pause expires on its own", () => {
    renderThread(true);
    const text = screen.getByTestId("assistant-explanation").textContent ?? "";

    expect(text).toMatch(/stops replying|stop replying/i);
    expect(text).toMatch(/person|staff/i);
    expect(text).toMatch(/expires|on its own|by itself|automatically/i);
  });
});

describe("StaffThread: an open conversation holding nothing (FR-025a)", () => {
  it("says plainly that the conversation is empty", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([]);
    render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );

    expect(await screen.findByTestId("staff-empty-thread")).toBeInTheDocument();
  });

  it("is a different statement from no conversation being selected", async () => {
    // `staff-no-thread` means "pick one"; `staff-empty-thread` means "this one has
    // nothing in it". Same appearance would make a staff member think their click did
    // not register.
    const { unmount } = render(
      <StaffThread
        chatId={null}
        assistantMayReply
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );
    expect(screen.getByTestId("staff-no-thread")).toBeInTheDocument();
    expect(screen.queryByTestId("staff-empty-thread")).toBeNull();
    const noThread = screen.getByTestId("staff-no-thread").textContent;
    unmount();

    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([]);
    render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );
    const empty = await screen.findByTestId("staff-empty-thread");
    expect(screen.queryByTestId("staff-no-thread")).toBeNull();
    expect(empty.textContent).not.toBe(noThread);
  });

  it("does not borrow the patient side's greeting", async () => {
    // FR-019c's greeting orients a first-time visitor to what the assistant can do.
    // A staff member needs neither the orientation nor the offer.
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([]);
    render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );

    await screen.findByTestId("staff-empty-thread");
    expect(screen.queryByTestId("thread-greeting")).toBeNull();
  });

  it("says nothing of the sort while the thread is still loading", async () => {
    let resolve = (_: Message[]): void => undefined;
    vi.spyOn(consoleApi, "fetchThread").mockReturnValue(
      new Promise<Message[]>((r) => {
        resolve = r;
      }),
    );
    render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );

    await waitFor(() => expect(consoleApi.fetchThread).toHaveBeenCalled());
    expect(screen.queryByTestId("staff-empty-thread")).toBeNull();

    await act(async () => {
      resolve([]);
    });
    expect(screen.getByTestId("staff-empty-thread")).toBeInTheDocument();
  });

  it("says nothing of the sort for a thread that failed to load", async () => {
    // A thread that would not load is a third situation again (FR-010a), and the
    // banner speaks for it.
    vi.spyOn(consoleApi, "fetchThread").mockRejectedValue(new Error("network blip"));
    render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("staff-error")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("staff-empty-thread")).toBeNull();
  });

  it("is gone once the conversation holds a message", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([message()]);
    render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply
        pauseSecondsRemaining={null}
        onSetAssistant={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));
    expect(screen.queryByTestId("staff-empty-thread")).toBeNull();
  });
});

describe("StaffThread renders no times (FR-010b)", () => {
  it("shows no time on any message, while the pause countdown still renders", async () => {
    // The countdown is one of FR-010b's two named exceptions: it is a deadline on a
    // control, not a record of when anything happened. Asserting both in one test is
    // what keeps a sweeping "no digits anywhere" fix from deleting it.
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({ id: "1", sender: "patient", created_at: "2026-09-01T09:41:00" }),
      message({
        id: "2",
        sender: "staff",
        content: "I've got this one.",
        created_at: "2026-09-01T09:41:30",
      }),
    ]);
    render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply={false}
        pauseSecondsRemaining={300}
        onSetAssistant={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    const thread = screen.getByTestId("staff-thread").textContent ?? "";
    expect(thread).not.toMatch(/\d{1,2}:\d{2}/);
    expect(thread).not.toMatch(/2026-09-01/);
    expect(thread).not.toMatch(/\bago\b/i);

    expect(screen.getByTestId("pause-countdown")).toHaveTextContent("5:00");
  });
});

describe("StaffThread scroll behaviour (FR-015b)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  function measure(el: HTMLElement, scrollHeight: () => number): void {
    Object.defineProperty(el, "scrollHeight", { configurable: true, get: scrollHeight });
    Object.defineProperty(el, "clientHeight", { configurable: true, get: () => 400 });
  }

  function thread(): HTMLElement {
    return screen.getByTestId("staff-thread");
  }

  function messages(...contents: string[]): Message[] {
    return contents.map((content, i) => message({ id: `m${i}`, content }));
  }

  function renderAt(lastMessageAt: string) {
    return render(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply
        pauseSecondsRemaining={null}
        lastMessageAt={lastMessageAt}
        onSetAssistant={vi.fn()}
      />,
    );
  }

  it("lands at the bottom when a conversation is opened", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue(
      messages("one", "two", "three"),
    );
    renderAt("2026-09-01T12:00:00");
    measure(thread(), () => 400 + screen.queryAllByTestId("message").length * 200);

    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));
    await waitFor(() => expect(thread().scrollTop).toBe(600));
  });

  it("follows an arriving patient message while the reader is at the bottom", async () => {
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(messages("one"))
      .mockResolvedValue(messages("one", "two"));

    const { rerender } = renderAt("2026-09-01T12:00:00");
    measure(thread(), () => 400 + screen.queryAllByTestId("message").length * 200);
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));
    await waitFor(() => expect(thread().scrollTop).toBe(200));

    rerender(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply
        pauseSecondsRemaining={null}
        lastMessageAt="2026-09-01T12:01:00"
        onSetAssistant={vi.fn()}
      />,
    );
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    await waitFor(() => expect(thread().scrollTop).toBe(400));
  });

  it("holds a staff member's position when they have scrolled back through it", async () => {
    // FR-015b: a staff member reading back through a conversation is doing the same
    // thing a patient is, and the poll that repaints their thread arrives just as
    // unbidden.
    const fetchThread = vi
      .spyOn(consoleApi, "fetchThread")
      .mockResolvedValueOnce(messages("one", "two"))
      .mockResolvedValue(messages("one", "two", "three"));

    const { rerender } = renderAt("2026-09-01T12:00:00");
    measure(thread(), () => 400 + screen.queryAllByTestId("message").length * 200);
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    thread().scrollTop = 120;
    fireEvent.scroll(thread());

    rerender(
      <StaffThread
        chatId={CHAT_ID}
        assistantMayReply
        pauseSecondsRemaining={null}
        lastMessageAt="2026-09-01T12:01:00"
        onSetAssistant={vi.fn()}
      />,
    );
    await waitFor(() => expect(fetchThread).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));

    expect(thread().scrollTop).toBe(120);
  });

  it("follows the staff member's own reply however far up they had scrolled", async () => {
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue(messages("one", "two"));
    vi.spyOn(consoleApi, "postStaffMessage").mockResolvedValue(
      message({ id: "posted", sender: "staff", content: "I've got this one." }),
    );
    renderAt("2026-09-01T12:00:00");
    measure(thread(), () => 400 + screen.queryAllByTestId("message").length * 200);
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    thread().scrollTop = 0;
    fireEvent.scroll(thread());

    fireEvent.change(screen.getByLabelText("reply as staff"), {
      target: { value: "I've got this one." },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Send as staff/ }));
    });

    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));
    expect(thread().scrollTop).toBe(600);
  });
});

describe("StaffThread: staff messages take the reader's own side (FR-023)", () => {
  it("puts staff messages opposite the patient's and the assistant's", async () => {
    // The staff console's reader is a staff member, so *their* messages are the ones on
    // the reader's own side — the mirror of what the patient pane does with the
    // patient's. Without this the staff member's replies sit among the messages they
    // are replying to, which is the one distinction a thread has to make.
    vi.spyOn(consoleApi, "fetchThread").mockResolvedValue([
      message({ id: "1", sender: "patient", content: "is anyone there?" }),
      message({ id: "2", sender: "assistant", content: "I can help with that." }),
      message({ id: "3", sender: "staff", content: "I've got this one." }),
    ]);
    renderThread();

    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));
    const [patient, assistant, staff] = screen.getAllByTestId("message");

    expect(staff).toHaveAttribute("data-mine", "true");
    expect(patient).not.toHaveAttribute("data-mine");
    expect(assistant).not.toHaveAttribute("data-mine");
  });
});
