import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  CHAR_COUNT_FROM,
  ChatWindow,
  MAX_MESSAGE_LENGTH,
} from "../src/components/ChatWindow";
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
        request_outcomes: null,
        attention_mark: null,
        created_at: "2026-08-06T00:00:00Z",
      },
      {
        id: "2",
        sender: "assistant",
        content: "Noted.",
        request_outcomes: null,
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
        request_outcomes: null,
        attention_mark: null,
        created_at: "2026-08-06T00:00:00Z",
      },
      {
        id: "2",
        sender: "patient",
        content: "Dr. Josh?",
        request_outcomes: null,
        attention_mark: null,
        created_at: "2026-08-06T00:00:01Z",
      },
      {
        id: "3",
        sender: "assistant",
        content: "Dr. Josh is available Tuesdays.",
        request_outcomes: null,
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

  it("renders streamed tokens but never citations, for an answered turn", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        { type: "token", text: "Visiting " },
        { type: "token", text: "hours are 8am to 5pm." },
        {
          type: "done",
          answer_source: "faq",
          request_outcomes: [
            {
              position: 0,
              question: "when can I visit?",
              answer: "Visiting hours are 8am to 5pm.",
              verdict: "answered",
              citations: [
                { entry_id: 1, chunk_index: 0, chunk_text: "Visiting hours are 8am to 5pm." },
              ],
            },
          ],
        },
      ]),
    );

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));

    // Once, not twice: the answer is in the bubble, and the citation list that used
    // to repeat it underneath is now the staff console's alone. A citation is evidence
    // about how the answer was produced, which is not the patient's to reason about.
    await waitFor(() => {
      expect(screen.getAllByText("Visiting hours are 8am to 5pm.").length).toBe(1);
    });
    expect(screen.queryByTestId("citations")).toBeNull();
  });

  it.each([
    "answered",
    "answered_unreranked",
    "abstained_similarity_floor",
  ] as const)("draws no citation list on a %s turn", async (verdict) => {
    const answered = verdict !== "abstained_similarity_floor";
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        { type: "token", text: "an answer" },
        {
          type: "done",
          answer_source: "faq",
          request_outcomes: [
            {
              position: 0,
              question: "when can I visit?",
              answer: answered ? "an answer" : null,
              verdict,
              citations: answered
                ? [{ entry_id: 1, chunk_index: 0, chunk_text: "source text" }]
                : [],
            },
          ],
        },
      ]),
    );

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(screen.getByText("an answer")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("citations")).toBeNull();
    expect(screen.queryByText("source text")).toBeNull();
    expect(screen.queryByTestId("verdict-mark")).toBeNull();
  });

  it("renders the abstention message when the turn abstains", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        {
          type: "done",
          request_outcomes: null,
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
          request_outcomes: null,
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
      fakeEvents([{ type: "done", request_outcomes: null, answer_source: "faq" }]),
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

  it("sends the message when Ctrl+Enter is pressed", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([{ type: "done", request_outcomes: null, answer_source: "faq" }]),
    );

    await renderReady();
    const textbox = screen.getByLabelText("question");
    fireEvent.change(textbox, { target: { value: "when can I visit?" } });
    fireEvent.keyDown(textbox, { key: "Enter", ctrlKey: true });

    await waitFor(() => {
      expect(chatStream.askChat).toHaveBeenCalledWith(
        CHAT_ID,
        "when can I visit?",
        expect.anything(),
      );
    });
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

  it("warns and blocks sending when the message exceeds the length limit", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    const askChatSpy = vi.spyOn(chatStream, "askChat");
    const callsBefore = askChatSpy.mock.calls.length;
    const tooLong = "a".repeat(2001);

    await renderReady();
    const textbox = screen.getByLabelText("question") as HTMLTextAreaElement;
    fireEvent.change(textbox, { target: { value: tooLong } });

    const warning = screen.getByTestId("length-error");
    expect(warning).toHaveTextContent("2001/2000");
    // The colour assertion this test used to carry is gone. It read an *inline* style,
    // which utility classes replace, and jsdom computes nothing for a class name — so
    // it could only ever have been re-pointed at a second copy of the stylesheet. What
    // the requirement actually asks for is that the control agrees with what it will
    // do, and that is assertable: the click below was already free, because a click on
    // a disabled button never reaches the handler.
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
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

    // B is held open past its last token because a turn that ends - however it ends -
    // takes its in-progress bubble with it, and the assertions below are about the
    // bubble still being B's own while B is still streaming into it. The hold is
    // released by the test rather than left parked forever: parked, B's `handleSend`
    // stays suspended for good and its `finally` never runs, which is the one place in
    // this suite where that guarantee would be quietly bypassed.
    let endB!: () => void;
    const bEnds = new Promise<void>((resolve) => {
      endB = resolve;
    });

    async function* turnB(): AsyncGenerator<ChatEvent> {
      yield { type: "token", text: "the surviving reply" };
      await bContinues;
      yield { type: "token", text: " continues" };
      await bEnds;
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

    // Now let B end the way any stream can end - by simply stopping. Its bubble going
    // with it is the observable half of `handleSend`'s `finally`, and the only thing
    // that proves this turn actually settled instead of being left suspended past the
    // end of the test with its controller still in `activeControllersRef`.
    endB();
    await waitFor(() => {
      expect(screen.queryByText("the surviving reply continues")).toBeNull();
    });
    expect(screen.queryByTestId("error")).toBeNull();
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
            request_outcomes: null,
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
          request_outcomes: null,
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
    // In flight until the chat switch aborts them, and then rejected the way a real
    // aborted fetch rejects. Left forever pending instead, the abort would be proved
    // only as far as the call: the rejection it causes would never reach the component,
    // so neither its `signal.aborted` branch nor the `finally` behind it would run.
    let failN!: (reason: unknown) => void;
    let failM!: (reason: unknown) => void;
    const pendingN = new Promise<AsyncGenerator<ChatEvent>>((_resolve, reject) => {
      failN = reject;
    });
    const pendingM = new Promise<AsyncGenerator<ChatEvent>>((_resolve, reject) => {
      failM = reject;
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

    // Both requests now fail the way an aborted fetch does. An abort is not a fault the
    // patient needs telling about - the pane it belonged to is already gone - so both
    // turns must end quietly, leaving no banner on the chat now on screen.
    const aborted = new DOMException("The operation was aborted.", "AbortError");
    await act(async () => {
      failN(aborted);
      failM(aborted);
    });
    expect(screen.queryByTestId("error")).toBeNull();
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
          request_outcomes: null,
          attention_mark: null,
          created_at: "2026-08-06T00:00:00Z",
        },
      ],
      other: [
        {
          id: "2",
          sender: "patient",
          content: "in the other chat",
          request_outcomes: null,
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
      request_outcomes: null,
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
    // read per tick per open tab to learn that nothing had happened. The ticks arrive
    // regardless - they are what lets a failed read be retried - so it is the marker,
    // not the absence of a tick, that has to keep them free.
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValue(history("is anyone there?"));

    const { rerender } = render(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:00:00Z"
        pollTick={1}
      />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(1));

    rerender(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:00:00Z"
        pollTick={2}
      />,
    );
    rerender(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:00:00Z"
        pollTick={3}
      />,
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

  it("brings in the message a failed refetch missed, on a later tick reporting the same time", async () => {
    // The failed read is the only thing that went wrong, and the poll has nothing new to
    // say about it: the staff reply is still the newest message, so every later tick
    // reports the very same time. Only a marker that records what was actually read can
    // notice the read is still owed - one recorded when the read was *issued* matches
    // every one of those ticks, and the reply stays off the screen until somebody writes
    // into the thread again.
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("is anyone there?"))
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(history("is anyone there?", "I've got this one."));

    const { rerender } = render(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:00:00Z"
        pollTick={1}
      />,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:01:00Z"
        pollTick={2}
      />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));
    // Let that read fail before the next tick arrives, as a 2-second interval would.
    await act(async () => undefined);
    expect(screen.getAllByTestId("message")).toHaveLength(1);

    rerender(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:01:00Z"
        pollTick={3}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("messages")).toHaveTextContent(
        "I've got this one.",
      ),
    );
  });

  it("does not clear a failed send's banner when a later read lands", async () => {
    // "Has any read landed yet" is not "did the opening read fail". Answering the first
    // for the second cleared the banner of a message that was never sent, while the
    // restored text still sat in the box - telling the patient their question went in.
    vi.spyOn(chatStream, "askChat").mockRejectedValue(new Error("The network dropped."));
    vi.spyOn(chatStream, "fetchChatHistory")
      .mockRejectedValueOnce(new Error("Could not load this chat's history."))
      .mockResolvedValue(history("is anyone there?"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" pollTick={1} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("error")).toHaveTextContent(
        "Could not load this chat's history.",
      ),
    );

    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));
    await waitFor(() =>
      expect(screen.getByTestId("error")).toHaveTextContent("The network dropped."),
    );

    // The read the failed opening read is owed now succeeds. Asserted on what reached
    // the screen rather than on a call count: a turn starting and ending re-runs the
    // refetch effect too, so how many reads it takes to get here is not this test's
    // business.
    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" pollTick={2} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("messages")).toHaveTextContent("is anyone there?"),
    );

    // The history loaded; the send still failed, and the restored text is still there.
    expect(screen.getByTestId("error")).toHaveTextContent("The network dropped.");
    expect(screen.getByLabelText("question")).toHaveValue("when can I visit?");
  });

  it("discards a stale read even when the newly opened chat is slower", async () => {
    // The retire-on-open assignment, which is the whole reason reads are numbered
    // rather than compared by chat id. Every other test here lets the new chat's
    // opening read resolve immediately, so it advances the marker past the stale read
    // on its own and the retirement never does any work - delete the line and they all
    // still pass. This orders them the other way round, which is the ordering the line
    // exists for: the stale answer arrives first and would be painted under the new
    // chat's name.
    const OTHER = "01CHAT000000000000000099";
    let answerFirst!: (rows: Message[]) => void;
    const firstRead = new Promise<Message[]>((resolve) => {
      answerFirst = resolve;
    });
    let answerSecond!: (rows: Message[]) => void;
    const secondRead = new Promise<Message[]>((resolve) => {
      answerSecond = resolve;
    });
    vi.spyOn(chatStream, "fetchChatHistory")
      .mockReturnValueOnce(firstRead)
      .mockReturnValueOnce(secondRead)
      .mockResolvedValue([]);

    const { rerender } = render(<ChatWindow chatId={CHAT_ID} />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalledTimes(1));
    rerender(<ChatWindow chatId={OTHER} />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalledTimes(2));

    await act(async () => {
      answerFirst(history("the chat you left"));
      await firstRead;
    });
    expect(screen.getByTestId("messages")).not.toHaveTextContent("the chat you left");

    await act(async () => {
      answerSecond(history("the chat you opened"));
      await secondRead;
    });
    expect(screen.getByTestId("messages")).toHaveTextContent("the chat you opened");
  });

  it("applies a read that was still outstanding when the next tick arrived", async () => {
    // A read slower than the two-second interval spans a tick, and every tick re-runs
    // the effect. That says nothing about the answer: what makes one stale is the chat
    // being switched away from, and this chat is still open. Judging it by "the effect
    // has re-run since" instead throws away every read that crosses a tick boundary, and
    // against a backend consistently slower than the interval the pane never updates at
    // all - a read reissued forever and never landing.
    let answerSlowRead!: (rows: Message[]) => void;
    const slowRead = new Promise<Message[]>((resolve) => {
      answerSlowRead = resolve;
    });
    // What the tick arriving mid-read asks. Deliberately not suppressed (see the
    // assertion at the end) and deliberately left unanswered here: resolved instead, it
    // would land *first*, retire the outstanding read, and paint its own answer - so
    // this test would pass with the outstanding read discarded, which is the regression
    // it exists to prevent. Its content differs from the outstanding read's for the
    // same reason.
    let answerTickRead!: (rows: Message[]) => void;
    const tickRead = new Promise<Message[]>((resolve) => {
      answerTickRead = resolve;
    });
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("is anyone there?"))
      .mockReturnValueOnce(slowRead)
      .mockReturnValueOnce(tickRead)
      .mockResolvedValue(history("is anyone there?", "a later answer entirely."));

    const { rerender } = render(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:00:00Z"
        pollTick={1}
      />,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:01:00Z"
        pollTick={2}
      />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));

    // The next tick arrives while that read is still outstanding.
    rerender(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:01:00Z"
        pollTick={3}
      />,
    );

    await act(async () => {
      answerSlowRead(history("is anyone there?", "I've got this one."));
      await slowRead;
    });

    // The outstanding read's own answer, and nothing else's.
    expect(screen.getByTestId("messages")).toHaveTextContent("I've got this one.");
    // A tick that is still owed a read issues one, even with another outstanding, and
    // that is the point rather than an oversight: suppressing it would make this pane's
    // liveness depend on the outstanding read completing, so a wedged connection - the
    // very case a retry exists for - would stop the refetch for as long as it hangs, and
    // a read that never settles would stop it for good. `useConsolePoll` refuses the
    // same trade for the same reason. The cost is bounded by how far a read runs behind
    // the interval, and an answer is only ever discarded for being the older one.
    expect(fetchChatHistory).toHaveBeenCalledTimes(3);

    // And when the tick's own read finally answers it takes over, because it is the
    // newer of the two - the rule is which read is newer, never which arrives first.
    await act(async () => {
      answerTickRead(history("is anyone there?", "a later answer entirely."));
      await tickRead;
    });
    expect(screen.getByTestId("messages")).toHaveTextContent("a later answer entirely.");
  });

  it("discards a read from an earlier visit to the chat now open again", async () => {
    // Leaving a chat and coming back is a second visit, not the same one. A read issued
    // during the first still names the chat on screen, so comparing ids accepts it - and
    // it answers about a history this pane has since thrown away and reloaded. Only its
    // number says so.
    let answerFirstVisit!: (rows: Message[]) => void;
    const firstVisitRead = new Promise<Message[]>((resolve) => {
      answerFirstVisit = resolve;
    });
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("first visit"))
      .mockReturnValueOnce(firstVisitRead)
      .mockResolvedValue(history("reloaded"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" pollTick={1} />,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));
    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:01:00Z" pollTick={2} />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));

    rerender(
      <ChatWindow chatId="01OTHER" lastMessageAt="2026-09-01T12:01:00Z" pollTick={3} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("messages")).toHaveTextContent("reloaded"),
    );
    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:01:00Z" pollTick={4} />,
    );
    await act(async () => undefined);

    await act(async () => {
      answerFirstVisit(history("from the first visit"));
      await firstVisitRead;
    });

    expect(screen.getByTestId("messages")).not.toHaveTextContent("from the first visit");
  });

  it("takes over from an opening read that failed, on a tick reporting the same value", async () => {
    // The read that opens a chat used to have its poll value filed as handled whether or
    // not it landed. When it failed, every later tick reported the very value already
    // filed, so nothing fetched the history and the pane sat blank behind the banner
    // until somebody wrote into the thread.
    vi.spyOn(chatStream, "fetchChatHistory")
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(history("loaded on the retry"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" pollTick={1} />,
    );
    await waitFor(() => expect(screen.getByTestId("error").textContent).not.toBe(""));

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" pollTick={2} />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("messages")).toHaveTextContent("loaded on the retry"),
    );
  });

  it("goes on reading the history while an earlier read hangs", async () => {
    // A read that never settles must not disable this pane. An in-flight latch cleared
    // in `.finally` never runs for a request that hangs - and none of these reads carries
    // a timeout or an abort signal - so the refetch would be dead until a full reload.
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("first"))
      .mockReturnValueOnce(new Promise<Message[]>(() => undefined))
      .mockResolvedValue(history("first", "arrived anyway"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00Z" pollTick={1} />,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:01:00Z" pollTick={2} />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));

    // That read for 12:01 never answers. A later message must still be read.
    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:02:00Z" pollTick={3} />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("messages")).toHaveTextContent("arrived anyway"),
    );
  });

  it("discards a read that answers after the chat was switched away from", async () => {
    // The other half of judging a read by the chat it was issued for: a poll-driven read
    // outstanding across a chat switch is answered from a thread no longer on screen,
    // and appending that answer would put one chat's messages under another's name.
    let answerSlowRead!: (rows: Message[]) => void;
    const slowRead = new Promise<Message[]>((resolve) => {
      answerSlowRead = resolve;
    });
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("in the first chat"))
      .mockReturnValueOnce(slowRead)
      .mockResolvedValue(history("in the other chat"));

    const { rerender } = render(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:00:00Z"
        pollTick={1}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("in the first chat")).toBeInTheDocument(),
    );

    rerender(
      <ChatWindow
        chatId={CHAT_ID}
        lastMessageAt="2026-09-01T12:01:00Z"
        pollTick={2}
      />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));

    // The patient opens another chat while that read is still outstanding.
    rerender(
      <ChatWindow
        chatId="01OTHER"
        lastMessageAt="2026-09-01T12:05:00Z"
        pollTick={3}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("in the other chat")).toBeInTheDocument(),
    );

    await act(async () => {
      answerSlowRead(history("in the first chat", "a staff reply to the first chat"));
      await slowRead;
    });

    expect(screen.queryByText("a staff reply to the first chat")).toBeNull();
    expect(screen.getByText("in the other chat")).toBeInTheDocument();
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
      yield { type: "done", request_outcomes: null, answer_source: "faq" };
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
        request_outcomes: null,
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
        request_outcomes: null,
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
          request_outcomes: null,
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


// --- Phase 1h: the patient pane draws nothing about a request's outcome -------------

describe("ChatWindow request outcomes", () => {
  it("draws no outcome block for a message of any sender, in progress or final", async () => {
    // The optimistic patient bubble carries no outcomes because a patient message was
    // never retrieved against; the reply's arrive with its terminal event and are the
    // staff console's to draw. Neither is drawn here (FR-042).
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        { type: "token", text: "Visiting hours are 8am to 5pm." },
        {
          type: "done",
          answer_source: "faq",
          request_outcomes: [
            {
              position: 0,
              question: "when can I visit?",
              answer: "Visiting hours are 8am to 5pm.",
              verdict: "answered",
              citations: [{ entry_id: 1, chunk_index: 0, chunk_text: "8am to 5pm." }],
            },
          ],
        },
      ]),
    );

    await renderReady();
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(screen.getAllByTestId("message")).toHaveLength(2);
    });
    expect(screen.queryByTestId("request-outcome")).toBeNull();
    expect(screen.queryByTestId("citations")).toBeNull();
    expect(screen.queryByTestId("verdict-mark")).toBeNull();
  });
});

describe("ChatWindow composer limits (FR-019a, FR-019b)", () => {
  beforeEach(() => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
  });

  function send(): HTMLElement {
    return screen.getByRole("button", { name: "Send" });
  }

  async function type(value: string): Promise<HTMLTextAreaElement> {
    await renderReady();
    const textbox = screen.getByLabelText("question") as HTMLTextAreaElement;
    fireEvent.change(textbox, { target: { value } });
    return textbox;
  }

  it("shows no character count for a message nowhere near the limit", async () => {
    await type("when can I visit?");
    expect(screen.queryByTestId("char-count")).toBeNull();
  });

  it("shows the character count as the message approaches the limit", async () => {
    await type("a".repeat(CHAR_COUNT_FROM));
    expect(screen.getByTestId("char-count")).toHaveTextContent(
      `${CHAR_COUNT_FROM}/${MAX_MESSAGE_LENGTH}`,
    );
  });

  it("shows no count one character before the approach begins", async () => {
    await type("a".repeat(CHAR_COUNT_FROM - 1));
    expect(screen.queryByTestId("char-count")).toBeNull();
  });

  it("still shows the count at exactly the limit, where there is no error", async () => {
    // The pair FR-019a needs and the reason the counter and the error are two hooks: at
    // exactly 2000 the message is sendable, so there is no error, and the counter is
    // precisely what tells the patient they are at the edge.
    await type("a".repeat(MAX_MESSAGE_LENGTH));
    expect(screen.getByTestId("char-count")).toBeInTheDocument();
    expect(screen.queryByTestId("length-error")).toBeNull();
    expect(send()).toBeEnabled();
  });

  it("disables Send for an empty composer", async () => {
    await renderReady();
    expect(send()).toBeDisabled();
  });

  it("disables Send for whitespace alone", async () => {
    await type("   \n  ");
    expect(send()).toBeDisabled();
  });

  it("disables Send past the limit", async () => {
    await type("a".repeat(MAX_MESSAGE_LENGTH + 1));
    expect(send()).toBeDisabled();
  });

  it("enables Send for a message that would actually go", async () => {
    await type("when can I visit?");
    expect(send()).toBeEnabled();
  });

  it("gives the disabled reason to assistive technology, not to the eye alone", async () => {
    await type("a".repeat(MAX_MESSAGE_LENGTH + 1));
    const described = send().getAttribute("aria-describedby");
    expect(described).not.toBeNull();
    expect(document.getElementById(described!)).toHaveTextContent(/too long/i);
  });

  it("never truncates what the patient typed", async () => {
    const pasted = "a".repeat(MAX_MESSAGE_LENGTH + 500);
    const textbox = await type(pasted);
    expect(textbox.value).toBe(pasted);
    expect(textbox.value).toHaveLength(MAX_MESSAGE_LENGTH + 500);
  });
});

describe("ChatWindow working indicator (FR-016, FR-017)", () => {
  beforeEach(() => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
  });

  /** A turn that has been accepted and produces nothing until released. */
  function heldTurn(): { release: () => void } {
    let release = (): void => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.spyOn(chatStream, "askChat").mockImplementation(async () => {
      await gate;
      return fakeEvents([{ type: "done", message: "ok", request_outcomes: null, answer_source: "faq" }]);
    });
    return { release };
  }

  async function sendOne(text: string): Promise<void> {
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: text },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
    });
  }

  it("shows the indicator in the same render as the patient's own message", async () => {
    // SC-005's half-second is true by construction rather than by timing luck: there is
    // no render in which the message is up and the indicator is not, so no delay exists
    // to measure.
    heldTurn();
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    await sendOne("when can I visit?");

    expect(screen.getByText("when can I visit?")).toBeInTheDocument();
    expect(screen.getByTestId("working-indicator")).toBeInTheDocument();
  });

  it("shows one indicator per concurrent turn", async () => {
    heldTurn();
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    await sendOne("first");
    await sendOne("second");

    expect(screen.getAllByTestId("working-indicator")).toHaveLength(2);
  });

  it("removes the indicator when the turn produces a reply", async () => {
    const turn = heldTurn();
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    await sendOne("when can I visit?");
    expect(screen.getByTestId("working-indicator")).toBeInTheDocument();

    await act(async () => {
      turn.release();
    });
    await waitFor(() =>
      expect(screen.queryByTestId("working-indicator")).toBeNull(),
    );
  });

  it("removes the indicator when the turn is superseded", async () => {
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([{ type: "cancelled" }]),
    );
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    await sendOne("when can I visit?");

    await waitFor(() =>
      expect(screen.queryByTestId("working-indicator")).toBeNull(),
    );
  });

  it("removes the indicator when the turn fails", async () => {
    vi.spyOn(chatStream, "askChat").mockRejectedValue(new Error("network error"));
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    await sendOne("when can I visit?");

    await waitFor(() =>
      expect(screen.queryByTestId("working-indicator")).toBeNull(),
    );
  });

  it("shows no indicator at all while the assistant is paused", async () => {
    heldTurn();
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply={false} />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    await sendOne("when can I visit?");

    expect(screen.getByText("when can I visit?")).toBeInTheDocument();
    expect(screen.queryByTestId("working-indicator")).toBeNull();
  });

  it("substitutes nothing for the indicator while paused", async () => {
    // FR-017 forbids a notice, a placeholder or a countdown in its place. The paused
    // thread holds exactly what the patient sent and nothing else — so an empty
    // assistant bubble standing in for the absent reply is the defect this pins.
    heldTurn();
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply={false} />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    await sendOne("when can I visit?");

    const messages = screen.getAllByTestId("message");
    expect(messages).toHaveLength(1);
    expect(messages[0]).toHaveAttribute("data-sender", "patient");
  });

  it("still streams the reply's text when one arrives", async () => {
    // FR-018 is not weakened by any of the above: the indicator gives way to the
    // bubble, it does not replace it.
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        { type: "token", text: "We open " },
        { type: "token", text: "at nine." },
        { type: "done", message: "", request_outcomes: null, answer_source: "faq" },
      ]),
    );
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    await sendOne("when do you open?");

    await waitFor(() =>
      expect(screen.getByText("We open at nine.")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("working-indicator")).toBeNull();
  });

  it("defaults to showing the indicator when the caller says nothing", async () => {
    // A brand-new chat has no poll row yet, and withholding the indicator for every
    // turn in a chat's first two seconds is the larger error (research.md Decision 3).
    heldTurn();
    render(<ChatWindow chatId={CHAT_ID} />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    await sendOne("when can I visit?");

    expect(screen.getByTestId("working-indicator")).toBeInTheDocument();
  });
});

describe("ChatWindow empty-thread greeting (FR-019c)", () => {
  function history(...contents: string[]): Message[] {
    return contents.map((content, i) => ({
      id: `m${i}`,
      sender: "patient" as const,
      content,
      request_outcomes: null,
      attention_mark: null,
      created_at: "2026-09-01T12:00:00",
    }));
  }

  it("greets an open chat whose history loaded and holds nothing", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    await renderReady();

    const greeting = await screen.findByTestId("thread-greeting");
    expect(greeting).toBeInTheDocument();
  });

  it("names only capabilities the assistant actually has", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    await renderReady();

    const greeting = await screen.findByTestId("thread-greeting");
    expect(greeting).toHaveTextContent(/appointment/i);
    expect(greeting).toHaveTextContent(/question/i);
  });

  it("carries no sender, so it cannot read as something the assistant said", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    await renderReady();

    const greeting = await screen.findByTestId("thread-greeting");
    expect(greeting.closest("[data-testid='message']")).toBeNull();
    expect(screen.queryAllByTestId("message")).toHaveLength(0);
  });

  it("is gone once the chat holds a message", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue(
      history("is anyone there?"),
    );
    render(<ChatWindow chatId={CHAT_ID} />);

    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));
    expect(screen.queryByTestId("thread-greeting")).toBeNull();
  });

  it("is absent while the history is still loading", async () => {
    // Waiting and arrived-empty are two different situations (FR-010a), and greeting a
    // thread that has not answered yet asserts it is empty before anyone knows.
    let resolve = (_: Message[]): void => undefined;
    vi.spyOn(chatStream, "fetchChatHistory").mockReturnValue(
      new Promise<Message[]>((r) => {
        resolve = r;
      }),
    );
    render(<ChatWindow chatId={CHAT_ID} />);

    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());
    expect(screen.queryByTestId("thread-greeting")).toBeNull();

    await act(async () => {
      resolve([]);
    });
    expect(screen.getByTestId("thread-greeting")).toBeInTheDocument();
  });

  it("is absent when the history failed to load", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockRejectedValue(
      new Error("network blip"),
    );
    render(<ChatWindow chatId={CHAT_ID} />);

    await waitFor(() => expect(screen.getByTestId("error")).toBeInTheDocument());
    expect(screen.queryByTestId("thread-greeting")).toBeNull();
  });
});

describe("ChatWindow renders no times (FR-010b)", () => {
  it("shows no sent, received or elapsed time on any message", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([
      {
        id: "1",
        sender: "patient",
        content: "I'm going to come on Tuesday",
        request_outcomes: null,
        attention_mark: null,
        created_at: "2026-08-06T09:41:00",
      },
      {
        id: "2",
        sender: "assistant",
        content: "Noted.",
        request_outcomes: null,
        attention_mark: null,
        created_at: "2026-08-06T09:41:07",
      },
    ]);
    render(<ChatWindow chatId={CHAT_ID} />);
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    const thread = screen.getByTestId("messages").textContent ?? "";
    // The payload carries these; the screen deliberately does not. Any clock-shaped
    // string in the thread is the regression, whatever produced it.
    expect(thread).not.toMatch(/\d{1,2}:\d{2}/);
    expect(thread).not.toMatch(/2026-08-06/);
    expect(thread).not.toMatch(/\bago\b/i);
  });
});

describe("ChatWindow scroll behaviour (FR-015, FR-015a)", () => {
  // Three of these assert on *how many* reads happened, and the spies in the rest of
  // this file accumulate across tests — same reason, and same remedy, as the block
  // above that says so.
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  /**
   * Give the thread container real measurements.
   *
   * jsdom reports `scrollHeight` and `clientHeight` as 0 for every element, so without
   * this the rule reads `0 - 0 <= threshold` → always pinned, and the hold-position
   * branch below would never execute while its test passed (`research.md` Decision 4).
   *
   * The stub is installed *before* the content whose arrival is being tested, because
   * that is when the container acquires its size in a browser too: a thread that is
   * 400px tall is 400px tall before the message lands in it.
   *
   * Scrolling is done by *assigning* `scrollTop`. `scrollIntoView` is `undefined` in
   * this jsdom and calling it throws.
   */
  function measure(el: HTMLElement, scrollHeight: () => number): void {
    Object.defineProperty(el, "scrollHeight", {
      configurable: true,
      get: scrollHeight,
    });
    Object.defineProperty(el, "clientHeight", { configurable: true, get: () => 400 });
  }

  function thread(): HTMLElement {
    return screen.getByTestId("messages");
  }

  function history(...contents: string[]): Message[] {
    return contents.map((content, i) => ({
      id: `m${i}`,
      sender: "patient" as const,
      content,
      request_outcomes: null,
      attention_mark: null,
      created_at: "2026-09-01T12:00:00",
    }));
  }

  /**
   * Render a thread whose height grows with the number of messages in it — 400px of
   * viewport plus 200px per message — so "the bottom" moves as content arrives, which
   * is the whole situation FR-015a describes.
   */
  async function renderMeasured(first: Message[]): Promise<void> {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue(first);
    render(<ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00" />);
    measure(
      thread(),
      () => 400 + screen.queryAllByTestId("message").length * 200,
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("message")).toHaveLength(first.length),
    );
  }

  it("lands at the bottom when a chat is opened", async () => {
    await renderMeasured(history("one", "two", "three"));

    // 400 + 3*200 = 1000 of content in a 400 viewport
    await waitFor(() => expect(thread().scrollTop).toBe(600));
  });

  it("follows new content while the reader is at the bottom", async () => {
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("one"))
      .mockResolvedValue(history("one", "two"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00" />,
    );
    measure(
      thread(),
      () => 400 + screen.queryAllByTestId("message").length * 200,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(1));
    await waitFor(() => expect(thread().scrollTop).toBe(200));

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:01:00" />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    await waitFor(() => expect(thread().scrollTop).toBe(400));
  });

  it("holds the reader's position when they have scrolled up", async () => {
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("one", "two"))
      .mockResolvedValue(history("one", "two", "three"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00" />,
    );
    measure(
      thread(),
      () => 400 + screen.queryAllByTestId("message").length * 200,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    // The reader scrolls well up from the bottom, and the component learns of it the
    // only way a browser tells it: a scroll event.
    thread().scrollTop = 120;
    fireEvent.scroll(thread());

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:01:00" />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));

    // The arriving message must not move the view. This is the branch that cannot run
    // at all without the stubbed measurements above.
    expect(thread().scrollTop).toBe(120);
  });

  it("resumes following once the reader scrolls back to the bottom", async () => {
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("one", "two"))
      .mockResolvedValueOnce(history("one", "two", "three"))
      .mockResolvedValue(history("one", "two", "three", "four"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00" />,
    );
    measure(
      thread(),
      () => 400 + screen.queryAllByTestId("message").length * 200,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    thread().scrollTop = 120;
    fireEvent.scroll(thread());

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:01:00" />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));
    expect(thread().scrollTop).toBe(120);

    // Back to the bottom, by hand: 400 + 3*200 - 400 = 600.
    thread().scrollTop = 600;
    fireEvent.scroll(thread());

    rerender(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:02:00" />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(4));

    await waitFor(() => expect(thread().scrollTop).toBe(800));
  });

  it("follows the patient's own message however far up they had scrolled", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue(
      history("one", "two"),
    );
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([{ type: "silent" }]),
    );
    render(<ChatWindow chatId={CHAT_ID} />);
    measure(
      thread(),
      () => 400 + screen.queryAllByTestId("message").length * 200,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    thread().scrollTop = 0;
    fireEvent.scroll(thread());

    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "is anyone there?" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
    });

    // Sending is not unbidden content: the patient acted, so their own message is
    // followed whatever they had scrolled to.
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));
    expect(thread().scrollTop).toBe(600);
  });

  it("opens the next chat at its newest message, after scrolling up in this one", async () => {
    // The hold-position answer belongs to the conversation it was given about. Carried
    // into the next one it is not a held position but a refusal to open at the bottom
    // (FR-015), and the reader lands on the oldest message in a thread they have never
    // seen.
    const fetchChatHistory = vi
      .spyOn(chatStream, "fetchChatHistory")
      .mockResolvedValueOnce(history("one", "two", "three"))
      .mockResolvedValue(history("four", "five", "six"));

    const { rerender } = render(
      <ChatWindow chatId={CHAT_ID} lastMessageAt="2026-09-01T12:00:00" />,
    );
    measure(
      thread(),
      () => 400 + screen.queryAllByTestId("message").length * 200,
    );
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));

    thread().scrollTop = 0;
    fireEvent.scroll(thread());

    rerender(
      <ChatWindow chatId="other-chat" lastMessageAt="2026-09-01T12:05:00" />,
    );
    await waitFor(() => expect(fetchChatHistory).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByText("six")).toBeInTheDocument(),
    );

    await waitFor(() => expect(thread().scrollTop).toBe(600));
  });
});

describe("ChatWindow: the patient's own messages take their side (FR-013)", () => {
  it("puts the patient's messages opposite the assistant's and staff's", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([
      {
        id: "1",
        sender: "patient",
        content: "is anyone there?",
        request_outcomes: null,
        attention_mark: null,
        created_at: "2026-09-01T12:00:00",
      },
      {
        id: "2",
        sender: "assistant",
        content: "I can help with that.",
        request_outcomes: null,
        attention_mark: null,
        created_at: "2026-09-01T12:00:01",
      },
      {
        id: "3",
        sender: "staff",
        content: "I've got this one.",
        request_outcomes: null,
        attention_mark: null,
        created_at: "2026-09-01T12:00:02",
      },
    ]);
    render(<ChatWindow chatId={CHAT_ID} />);

    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(3));
    const [patient, assistant, staff] = screen.getAllByTestId("message");

    // The reader here is the patient, so the mirror of the staff thread's rule.
    expect(patient).toHaveAttribute("data-mine", "true");
    expect(assistant).not.toHaveAttribute("data-mine");
    expect(staff).not.toHaveAttribute("data-mine");
  });
});

describe("ChatWindow: reduced motion suppresses the animation, not the state (FR-042)", () => {
  it("carries the indicator's meaning in the DOM, where no media query can reach it", async () => {
    // The half of FR-042 a test can hold. `prefers-reduced-motion` turns off the dots'
    // keyframes in `app.css`; it cannot turn off a role and an accessible name, and
    // that is the point — the indicator must still *say* a reply is coming when it has
    // stopped moving. A build that conveyed "working" only by motion would pass every
    // other indicator test in this file and fail this one.
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockImplementation(
      () => new Promise(() => undefined),
    );
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
    });

    const indicator = screen.getByTestId("working-indicator");
    expect(indicator).toHaveAttribute("role", "status");
    expect(indicator).toHaveAccessibleName(/replying/i);
  });

  it("marks the animated dots so the stylesheet can single them out", async () => {
    // `.working-dot` is what the reduced-motion block targets to stop the dots while
    // leaving them visible at a resting opacity. Without the class the global rule would
    // still shorten the animation, but nothing would hold the dots at a readable
    // opacity — they would settle wherever the keyframe left them, which is 0.28.
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockImplementation(
      () => new Promise(() => undefined),
    );
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
    });

    const dots = screen
      .getByTestId("working-indicator")
      .querySelectorAll(".working-dot");
    expect(dots).toHaveLength(3);
  });
});

describe("ChatWindow: the indicator wears the assistant's own icon (FR-014, FR-016)", () => {
  it("shows the same sender icon the assistant's messages show", async () => {
    // The indicator occupies the slot the reply is about to occupy. A different glyph
    // there means one turn draws two different assistants a second apart.
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockImplementation(
      () => new Promise(() => undefined),
    );
    render(<ChatWindow chatId={CHAT_ID} assistantMayReply />);
    await waitFor(() => expect(chatStream.fetchChatHistory).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
    });

    // The indicator's avatar is the sibling before it, in the same row.
    const row = screen.getByTestId("working-indicator").parentElement!;
    const avatar = row.firstElementChild!;
    expect(avatar.querySelector("svg")).not.toBeNull();
  });
});

describe("ChatWindow shows none of the clinic's working notes (FR-019)", () => {
  it("renders no attention mark and no evidence marker, for a message carrying both", async () => {
    // The citations and outcome halves of FR-019 were already pinned. This is the mark
    // half, which never was — and since the outcome block moved behind a marker there
    // are now two things that must be absent, not one. An outcome is the clinic's note
    // on how an answer was produced, and the console is the surface that shows it; the
    // patient asked a question and gets the answer.
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([
      {
        id: "1",
        sender: "patient",
        content: "I have chest pain.",
        request_outcomes: null,
        attention_mark: "urgent_condition",
        created_at: "2026-09-01T12:00:00",
      },
      {
        id: "2",
        sender: "assistant",
        content: "Please call 999.",
        request_outcomes: [
          {
            position: 0,
            question: "what should I do about chest pain?",
            answer: null,
            verdict: "abstained_empty_pool",
            citations: [],
          },
        ],
        attention_mark: null,
        created_at: "2026-09-01T12:00:01",
      },
    ]);

    render(<ChatWindow chatId={CHAT_ID} />);
    await waitFor(() => expect(screen.getAllByTestId("message")).toHaveLength(2));

    expect(screen.queryByTestId("attention-mark")).toBeNull();
    expect(screen.queryByTestId("outcome-marker")).toBeNull();
    expect(screen.queryByTestId("request-outcome")).toBeNull();
    expect(screen.queryByTestId("outcome-unanswered")).toBeNull();
    expect(screen.queryByTestId("citations")).toBeNull();

    // And the messages themselves are still shown in full — hiding the notes is not
    // hiding the conversation.
    expect(screen.getByText("I have chest pain.")).toBeInTheDocument();
    expect(screen.getByText("Please call 999.")).toBeInTheDocument();
  });
});
