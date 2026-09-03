import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChatWindow } from "../src/components/ChatWindow";
import * as chatStream from "../src/lib/chatStream";
import type { ChatEvent, Message } from "../src/lib/chatStream";

async function* fakeEvents(events: ChatEvent[]): AsyncGenerator<ChatEvent> {
  for (const event of events) {
    yield event;
  }
}

const CHAT_ID = "01CHAT000000000000000000";

async function renderReady(): Promise<void> {
  render(<ChatWindow chatId={CHAT_ID} />);
  await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());
}

describe("ChatWindow", () => {
  it("hydrates prior chat history on mount", async () => {
    const history: Message[] = [
      {
        id: "1",
        sender: "patient",
        content: "I'm going to come on Tuesday",
        grounded: null,
        citations: null,
        attention_mark: null,
        created_at: "2026-08-06T00:00:00Z",
      },
      {
        id: "2",
        sender: "assistant",
        content: "Noted.",
        grounded: true,
        citations: [],
        attention_mark: null,
        created_at: "2026-08-06T00:00:01Z",
      },
    ];
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue(history);

    render(<ChatWindow chatId={CHAT_ID} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("message")).toHaveLength(2);
    });
    expect(screen.getByText("I'm going to come on Tuesday")).toBeInTheDocument();
    expect(screen.getByText("Noted.")).toBeInTheDocument();
  });

  it("displays a burst of patient messages followed by one reply without forced alternation", async () => {
    const history: Message[] = [
      {
        id: "1",
        sender: "patient",
        content: "When can I see",
        grounded: null,
        citations: null,
        attention_mark: null,
        created_at: "2026-08-06T00:00:00Z",
      },
      {
        id: "2",
        sender: "patient",
        content: "Dr. Josh?",
        grounded: null,
        citations: null,
        attention_mark: null,
        created_at: "2026-08-06T00:00:01Z",
      },
      {
        id: "3",
        sender: "assistant",
        content: "Dr. Josh is available Tuesdays.",
        grounded: true,
        citations: [],
        attention_mark: null,
        created_at: "2026-08-06T00:00:02Z",
      },
    ];
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue(history);

    render(<ChatWindow chatId={CHAT_ID} />);

    await waitFor(() => {
      expect(screen.getAllByTestId("message")).toHaveLength(3);
    });
    const messages = screen.getAllByTestId("message");
    expect(messages.map((m) => m.getAttribute("data-sender"))).toEqual([
      "patient",
      "patient",
      "assistant",
    ]);
  });

  it("renders streamed tokens and citations for a grounded answer", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        { type: "token", text: "Visiting " },
        { type: "token", text: "hours are 8am to 5pm." },
        {
          type: "done",
          grounded: true,
          answer_source: "faq",
          citations: [
            { entry_id: 1, chunk_index: 0, chunk_text: "Visiting hours are 8am to 5pm." },
          ],
        },
      ]),
    );

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));

    // The fake citation's chunk_text happens to equal the answer text, so both the
    // message body and the citation list legitimately match - assert count, not a
    // single unique match.
    await waitFor(() => {
      expect(screen.getAllByText("Visiting hours are 8am to 5pm.").length).toBe(2);
    });
    expect(screen.getByTestId("citations")).toHaveTextContent(
      "Visiting hours are 8am to 5pm.",
    );
  });

  it("renders the abstention message when not grounded", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        {
          type: "done",
          grounded: false,
          citations: [],
          answer_source: "faq",
          message: "I don't have a confident answer to that.",
        },
      ]),
    );

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "what's the weather?" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(
        screen.getByText("I don't have a confident answer to that."),
      ).toBeInTheDocument();
    });
  });

  it("keeps the streamed tokens when done carries an empty message", async () => {
    // The server stores `done_event.message or answer` - an empty `message` is stored
    // as the streamed text. Rendering the empty string instead would put a bubble on
    // screen holding text the thread does not hold, and `reconcile` matches on
    // content, so no history read could ever account for it.
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        { type: "token", text: "Visiting hours are 8am to 5pm." },
        {
          type: "done",
          grounded: true,
          citations: [],
          answer_source: "faq",
          message: "",
        },
      ]),
    );

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() =>
      expect(screen.getByText("Visiting hours are 8am to 5pm.")).toBeInTheDocument(),
    );
    // The reply is a settled message, not a bubble still being streamed into.
    expect(screen.getAllByTestId("message")).toHaveLength(2);
  });

  it("sends the message when Enter is pressed without Shift", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([{ type: "done", grounded: true, citations: [], answer_source: "faq" }]),
    );

    await renderReady();
    const textbox = screen.getByLabelText("question");
    fireEvent.change(textbox, { target: { value: "when can I visit?" } });
    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: false });

    await waitFor(() => {
      expect(screen.getByText("when can I visit?")).toBeInTheDocument();
    });
    expect(chatStream.askChat).toHaveBeenCalledWith(
      CHAT_ID,
      "when can I visit?",
      expect.anything(),
    );
  });

  it("does not send when Shift+Enter is pressed, leaving the draft for a newline", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    const askChatSpy = vi.spyOn(chatStream, "askChat");
    const callsBefore = askChatSpy.mock.calls.length;

    await renderReady();
    const textbox = screen.getByLabelText("question") as HTMLTextAreaElement;
    fireEvent.change(textbox, { target: { value: "line one" } });
    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: true });

    expect(askChatSpy.mock.calls.length).toBe(callsBefore);
    expect(textbox.value).toBe("line one");
  });

  it("shows a red warning and blocks sending when the message exceeds the length limit", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    const askChatSpy = vi.spyOn(chatStream, "askChat");
    const callsBefore = askChatSpy.mock.calls.length;
    const tooLong = "a".repeat(2001);

    await renderReady();
    const textbox = screen.getByLabelText("question") as HTMLTextAreaElement;
    fireEvent.change(textbox, { target: { value: tooLong } });

    const warning = screen.getByTestId("length-error");
    expect(warning).toHaveTextContent("2001/2000");
    expect(warning).toHaveStyle({ color: "rgb(255, 0, 0)" });

    fireEvent.click(screen.getByText("Send"));
    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: false });

    expect(askChatSpy.mock.calls.length).toBe(callsBefore);
    expect(textbox.value).toBe(tooLong);
  });

  it("clears the length warning once the draft is shortened back under the limit", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);

    await renderReady();
    const textbox = screen.getByLabelText("question");
    fireEvent.change(textbox, { target: { value: "a".repeat(2001) } });
    expect(screen.getByTestId("length-error")).toBeInTheDocument();

    fireEvent.change(textbox, { target: { value: "a".repeat(2000) } });
    expect(screen.queryByTestId("length-error")).toBeNull();
  });

  it("re-enables Send and shows an error when askChat rejects", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockRejectedValue(new Error("network error"));

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(screen.getByText("Send")).not.toBeDisabled();
    });
    expect(screen.getByTestId("error").textContent).not.toBe("");
  });

  it("restores the drafted text to the input box when askChat rejects", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockRejectedValue(new Error("network error"));

    await renderReady();
    const textbox = screen.getByLabelText("question") as HTMLTextAreaElement;
    fireEvent.change(textbox, { target: { value: "when can I visit?" } });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(screen.getByText("Send")).not.toBeDisabled();
    });
    expect(textbox.value).toBe("when can I visit?");
  });

  it("re-enables Send and shows an error when the stream throws mid-iteration", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    async function* failingEvents(): AsyncGenerator<ChatEvent> {
      yield { type: "token", text: "partial" };
      throw new Error("stream dropped");
    }
    vi.spyOn(chatStream, "askChat").mockResolvedValue(failingEvents());

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(screen.getByText("Send")).not.toBeDisabled();
    });
    expect(screen.getByTestId("error").textContent).not.toBe("");
  });

  it("removes the in-progress bubble and any partial tokens on a cancelled event, with no error shown", async () => {
    // A real macrotask delay between events (not `fakeEvents`' synchronous yields)
    // so React 18's automatic batching doesn't coalesce the token and cancelled
    // state updates into one render, which would make the intermediate paint
    // unobservable.
    async function* delayedThenCancelled(): AsyncGenerator<ChatEvent> {
      yield { type: "token", text: "partial answer" };
      await new Promise((resolve) => setTimeout(resolve, 10));
      yield { type: "cancelled" };
    }
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(delayedThenCancelled());

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "When can I see" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(screen.getByText("When can I see")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText("partial answer")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.queryByText("partial answer")).toBeNull();
    });
    expect(screen.queryByTestId("error")).toBeNull();
  });

  it("clears the in-progress bubble when a stream ends with no terminal event", async () => {
    // The server promises exactly one terminal event per turn; this is what the pane
    // does when it cannot keep that promise. Nothing else on this path clears the
    // bubble - no `done`, no `cancelled`, no error to catch - so the partial answer
    // used to sit on screen until the patient switched chats.
    async function* endedWithoutAnEnding(): AsyncGenerator<ChatEvent> {
      yield { type: "token", text: "half an answer" };
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(endedWithoutAnEnding());

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "When can I see" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(screen.getByText("half an answer")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.queryByText("half an answer")).toBeNull();
    });
    // The patient's own message stays: it was sent, and nothing said otherwise.
    expect(screen.getByText("When can I see")).toBeInTheDocument();
    expect(screen.queryByTestId("error")).toBeNull();
  });

  it("keeps a second turn's streamed text when an earlier turn is cancelled", async () => {
    // The component deliberately lets several turns run at once. With one shared
    // streaming slot, turn A's `cancelled` handler cleared the bubble outright and
    // took turn B's still-streaming reply down with it.
    let cancelA!: () => void;
    const aCancelled = new Promise<void>((resolve) => {
      cancelA = resolve;
    });

    async function* turnA(): AsyncGenerator<ChatEvent> {
      yield { type: "token", text: "superseded partial" };
      await aCancelled;
      yield { type: "cancelled" };
    }
    // Turn B's second token is released by the test rather than by a timer. On a timer
    // it raced the assertions below: the bubble's text grows to "the surviving reply
    // continues", and an exact-text query for the first half stops matching, so under
    // load this failed on which of two independent clocks won rather than on anything
    // the component did.
    let continueB!: () => void;
    const bContinues = new Promise<void>((resolve) => {
      continueB = resolve;
    });

    async function* turnB(): AsyncGenerator<ChatEvent> {
      yield { type: "token", text: "the surviving reply" };
      await bContinues;
      yield { type: "token", text: " continues" };
      // Held open rather than ended here: a turn that ends - however it ends - takes
      // its in-progress bubble with it, and the assertion below is about the bubble
      // still being B's own while B is still streaming into it.
      await new Promise(() => {});
    }

    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat")
      .mockResolvedValueOnce(turnA())
      .mockResolvedValueOnce(turnB());

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), { target: { value: "first" } });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => {
      expect(screen.getByText("superseded partial")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText("question"), { target: { value: "second" } });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => {
      expect(screen.getByText("the surviving reply")).toBeInTheDocument();
    });

    cancelA();

    await waitFor(() => {
      expect(screen.queryByText("superseded partial")).toBeNull();
    });
    // B is held mid-stream, so this is the text it had when A was cancelled - the whole
    // point of the assertion, and no longer a guess about how far B had got.
    expect(screen.getByText("the surviving reply")).toBeInTheDocument();

    // And it is still B's own slot afterwards: the next token appends to what survived
    // rather than starting a fresh bubble, which is what a cleared shared slot would do.
    continueB();
    await waitFor(() => {
      expect(
        screen.getByText("the surviving reply continues"),
      ).toBeInTheDocument();
    });
  });

  it("sends a second message via Enter while the first is still in flight", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    const pendingA = new Promise<AsyncGenerator<ChatEvent>>(() => {
      // Never resolves - message A stays in flight for the whole test.
    });
    const pendingB = new Promise<AsyncGenerator<ChatEvent>>(() => {
      // Never resolves either - only the call count and cleared draft matter here.
    });
    const askChatSpy = vi
      .spyOn(chatStream, "askChat")
      .mockReturnValueOnce(pendingA)
      .mockReturnValueOnce(pendingB);
    // vi.spyOn reuses the existing spy across tests in this file (it's never
    // restored), so its call count accumulates - compare deltas, not absolutes,
    // matching the "does not send when Shift+Enter" test above.
    const callsBefore = askChatSpy.mock.calls.length;

    await renderReady();
    const textbox = screen.getByLabelText("question") as HTMLTextAreaElement;
    fireEvent.change(textbox, { target: { value: "first message" } });
    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: false });

    await waitFor(() => {
      expect(screen.getByText("first message")).toBeInTheDocument();
    });
    expect(askChatSpy.mock.calls.length).toBe(callsBefore + 1);

    fireEvent.change(textbox, { target: { value: "second message" } });
    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: false });

    await waitFor(() => {
      expect(screen.getByText("second message")).toBeInTheDocument();
    });
    expect(askChatSpy.mock.calls.length).toBe(callsBefore + 2);
    expect(textbox.value).toBe("");
  });

  it("delivers the reply for an earlier turn still in flight when a newer send starts, instead of dropping it (regression)", async () => {
    // Previously, starting "m" while "n"'s reply hadn't arrived yet aborted "n"'s
    // own fetch - the server had already computed and persisted "n"'s reply, but
    // the client silently threw the abort-triggered rejection away, so "n"'s
    // reply only ever showed up after a page refresh. A send must never abort an
    // earlier, still-genuinely-completing one; only leaving the chat may abort.
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    const abortSpy = vi.spyOn(AbortController.prototype, "abort");
    let resolveN!: (events: AsyncGenerator<ChatEvent>) => void;
    const pendingN = new Promise<AsyncGenerator<ChatEvent>>((resolve) => {
      resolveN = resolve;
    });
    vi.spyOn(chatStream, "askChat")
      .mockReturnValueOnce(pendingN)
      .mockResolvedValueOnce(
        fakeEvents([
          {
            type: "done",
            grounded: false,
            citations: [],
            answer_source: "faq",
            message: "abstained for m",
          },
        ]),
      );

    await renderReady();
    const textbox = screen.getByLabelText("question");
    fireEvent.change(textbox, { target: { value: "n" } });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => expect(screen.getByText("n")).toBeInTheDocument());

    // "m" is sent before "n"'s own request has resolved at all.
    fireEvent.change(textbox, { target: { value: "m" } });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => expect(screen.getByText("m")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("abstained for m")).toBeInTheDocument());

    expect(abortSpy).not.toHaveBeenCalled();

    // "n"'s request finally resolves - its reply must still land, not be
    // silently dropped just because "m" was sent in the meantime.
    resolveN(
      fakeEvents([
        {
          type: "done",
          grounded: false,
          citations: [],
          answer_source: "faq",
          message: "abstained for n",
        },
      ]),
    );
    await waitFor(() => expect(screen.getByText("abstained for n")).toBeInTheDocument());
    abortSpy.mockRestore();
  });

  it("aborts every in-flight send, not just the most recent, when the chat changes", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    const abortSpy = vi.spyOn(AbortController.prototype, "abort");
    const pendingN = new Promise<AsyncGenerator<ChatEvent>>(() => {
      // Never resolves - both sends stay in flight through this test.
    });
    const pendingM = new Promise<AsyncGenerator<ChatEvent>>(() => {
      // Never resolves either.
    });
    vi.spyOn(chatStream, "askChat")
      .mockReturnValueOnce(pendingN)
      .mockReturnValueOnce(pendingM);

    const { rerender } = render(<ChatWindow chatId={CHAT_ID} />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());
    const textbox = screen.getByLabelText("question");
    fireEvent.change(textbox, { target: { value: "n" } });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => expect(screen.getByText("n")).toBeInTheDocument());
    fireEvent.change(textbox, { target: { value: "m" } });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => expect(screen.getByText("m")).toBeInTheDocument());

    // Both replies belong to a thread that is no longer on screen.
    rerender(<ChatWindow chatId="01OTHERCHAT00000000000000" />);
    await waitFor(() => expect(abortSpy).toHaveBeenCalledTimes(2));

    expect(screen.queryByText("n")).toBeNull();
    expect(screen.queryByText("m")).toBeNull();
    abortSpy.mockRestore();
  });

  it("mutes itself and offers no input when the session holds no chats", () => {
    render(<ChatWindow chatId={null} />);

    expect(screen.getByTestId("no-chat")).toBeInTheDocument();
    expect(screen.queryByLabelText("question")).toBeNull();
  });

  it("loads the newly selected chat's own history when the chat changes", async () => {
    const historyByChat: Record<string, Message[]> = {
      [CHAT_ID]: [
        {
          id: "1",
          sender: "patient",
          content: "in the first chat",
          grounded: null,
          citations: null,
          attention_mark: null,
          created_at: "2026-08-06T00:00:00Z",
        },
      ],
      other: [
        {
          id: "2",
          sender: "patient",
          content: "in the other chat",
          grounded: null,
          citations: null,
          attention_mark: null,
          created_at: "2026-08-06T00:00:00Z",
        },
      ],
    };
    vi.spyOn(chatStream, "fetchChatHistory").mockImplementation(
      async (chatId: string) => historyByChat[chatId] ?? [],
    );

    const { rerender } = render(<ChatWindow chatId={CHAT_ID} />);
    await waitFor(() => expect(screen.getByText("in the first chat")).toBeInTheDocument());

    rerender(<ChatWindow chatId="other" />);
    await waitFor(() => expect(screen.getByText("in the other chat")).toBeInTheDocument());
    expect(screen.queryByText("in the first chat")).toBeNull();
  });
});

// --- 007 (FR-029c): a staff reply appears without a reload -------------------------

describe("ChatWindow: refetching when the poll says the thread moved", () => {
  // These assert on *how many* reads happened, which the spies in the rest of this
  // file accumulate across tests.
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  function history(...contents: string[]): Message[] {
    return contents.map((content, index) => ({
      id: `m${index}`,
      sender: index % 2 === 0 ? ("patient" as const) : ("staff" as const),
      content,
      grounded: null,
      citations: null,
      attention_mark: null,
      created_at: `2026-09-01T12:0${index}:00Z`,
    }));
  }

  it("loads the thread again when the newest message time advances", async () => {
    // Something wrote into this conversation that this pane did not - a staff reply -
    // and the poll is how it finds out. No channel of its own, no reload.
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("is anyone there?"))
      .mockResolvedValue(history("is anyone there?", "I've got this one."));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" />,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:01:00Z" />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("messages")).toHaveTextContent(
        "I've got this one.",
      ),
    );
    expect(fetchChatHistory).toHaveBeenCalledTimes(2);
  });

  it("does not refetch while the poll keeps reporting the same time", async () => {
    // A 2-second poll that refetched every thread on every tick would cost one history
    // read per tick per open tab to learn that nothing had happened.
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValue(history("is anyone there?"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(1));

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" />,
    );
    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" />,
    );

    expect(fetchChatHistory).toHaveBeenCalledTimes(1);
  });

  it("does not refetch on the first value it sees for a conversation", async () => {
    // That value describes the history it has just loaded, so there is nothing new in
    // it to fetch.
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValue(history("is anyone there?"));

    render(<ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" />);

    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(1));
    expect(fetchChatHistory).toHaveBeenCalledTimes(1);
  });

  it("fetches the first message written into an open chat the poll called empty", async () => {
    // `null` is the poll's real answer about a chat holding nothing, not the absence of
    // an answer, and only distinguishing the two catches this case: a staff member's
    // opening line into a chat the patient already has open. Its timestamp is both the
    // first value this pane ever sees and genuinely news to it, so the "first value
    // describes the history just loaded" shortcut would swallow exactly one message.
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce([])
      .mockResolvedValue(history("I've got this one."));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt={null} />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(1));
    expect(screen.queryAllByTestId("message")).toHaveLength(0);

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:01:00Z" />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("messages")).toHaveTextContent(
        "I've got this one.",
      ),
    );
    expect(fetchChatHistory).toHaveBeenCalledTimes(2);
  });

  it("leaves the thread alone when a refetch fails", async () => {
    // The next tick tries again; an error banner over a message the patient has not
    // missed would only teach them to ignore the one that matters.
    vi.spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("is anyone there?"))
      .mockRejectedValue(new Error("network blip"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" />,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:01:00Z" />,
    );

    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));
    expect(screen.queryByTestId("error")).toBeNull();
  });

  it("keeps a just-streamed reply the deferred refetch's history does not carry yet", async () => {
    // The server queues `done` before it commits the assistant row, so the read this
    // pane fires the instant the turn ends is legitimately answered by a history that
    // stops short of the reply already on screen. Assigning that answer to the thread
    // blanks the reply until the next tick notices the insert - a couple of seconds in
    // which the patient's question looks unanswered.
    let finishTurn!: () => void;
    const turnFinishes = new Promise<void>((resolve) => {
      finishTurn = resolve;
    });

    async function* turn(): AsyncGenerator<ChatEvent> {
      yield { type: "token", text: "Visiting hours are 8am to 5pm." };
      // Held open so the poll tick below lands mid-stream, which is what defers it to
      // the moment the turn ends.
      await turnFinishes;
      yield { type: "done", grounded: true, answer_source: "faq", citations: [] };
    }

    function serverRow(
      index: number,
      sender: Message["sender"],
      content: string,
    ): Message {
      return {
        id: `m${index}`,
        sender,
        content,
        grounded: null,
        citations: null,
        attention_mark: null,
        created_at: `2026-09-01T12:0${index}:00Z`,
      };
    }

    // The patient's own row has committed; the assistant's has not. The staff line is
    // the landmark that proves this answer reached the thread at all - without it the
    // rendered result is indistinguishable from the refetch never having been applied.
    const withoutTheReply = [
      serverRow(1, "patient", "when can I visit?"),
      serverRow(2, "staff", "I've got this one."),
    ];
    const withTheReply = [
      ...withoutTheReply,
      serverRow(3, "assistant", "Visiting hours are 8am to 5pm."),
    ];

    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce(withoutTheReply)
      .mockResolvedValue(withTheReply);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(turn());

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() =>
      expect(screen.getByText("Visiting hours are 8am to 5pm.")).toBeInTheDocument(),
    );

    // The poll sees the patient row and advances; the tick is deferred, not dropped.
    rerender(<ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:02:00Z" />);
    expect(fetchChatHistory).toHaveBeenCalledTimes(1);

    finishTurn();
    await waitFor(() =>
      expect(screen.getByText("I've got this one.")).toBeInTheDocument(),
    );
    // The reply survives a history that does not know about it yet.
    expect(screen.getByText("Visiting hours are 8am to 5pm.")).toBeInTheDocument();

    // And it is not shown twice once a later read does account for it: the local copy
    // leaves as the server's own row arrives.
    rerender(<ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:03:00Z" />);
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));
    expect(
      screen.getAllByText("Visiting hours are 8am to 5pm."),
    ).toHaveLength(1);
  });

  it("accounts for a reply whose done event carried an empty message", async () => {
    // The same reconciliation, on the one `done` shape that used to be rendered
    // differently from the way the server stores it. A bubble holding the empty
    // string can never match the row the server wrote from the tokens, so it would
    // not leave when that row arrived - it would sit there, empty, until the chat was
    // switched away from.
    function serverRow(
      index: number,
      sender: Message["sender"],
      content: string,
    ): Message {
      return {
        id: `m${index}`,
        sender,
        content,
        grounded: null,
        citations: null,
        attention_mark: null,
        created_at: `2026-09-01T12:0${index}:00Z`,
      };
    }

    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce([])
      .mockResolvedValue([
        serverRow(1, "patient", "when can I visit?"),
        serverRow(2, "assistant", "Visiting hours are 8am to 5pm."),
      ]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        { type: "token", text: "Visiting hours are 8am to 5pm." },
        {
          type: "done",
          grounded: true,
          citations: [],
          answer_source: "faq",
          message: "",
        },
      ]),
    );

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    rerender(<ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:02:00Z" />);
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));

    // Two rows and nothing left over: the server's own reply replaced the local one
    // rather than joining it.
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));
    expect(
      screen.getAllByText("Visiting hours are 8am to 5pm."),
    ).toHaveLength(1);
  });
});
