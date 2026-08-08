import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatWindow } from "../src/components/ChatWindow";
import * as chatStream from "../src/lib/chatStream";
import type { ChatEvent, Message } from "../src/lib/chatStream";

async function* fakeEvents(events: ChatEvent[]): AsyncGenerator<ChatEvent> {
  for (const event of events) {
    yield event;
  }
}

async function renderReady(): Promise<void> {
  render(<ChatWindow />);
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
        created_at: "2026-08-06T00:00:00Z",
      },
      {
        id: "2",
        sender: "assistant",
        content: "Noted.",
        grounded: true,
        citations: [],
        created_at: "2026-08-06T00:00:01Z",
      },
    ];
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue(history);

    render(<ChatWindow />);

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
        created_at: "2026-08-06T00:00:00Z",
      },
      {
        id: "2",
        sender: "patient",
        content: "Dr. Josh?",
        grounded: null,
        citations: null,
        created_at: "2026-08-06T00:00:01Z",
      },
      {
        id: "3",
        sender: "assistant",
        content: "Dr. Josh is available Tuesdays.",
        grounded: true,
        citations: [],
        created_at: "2026-08-06T00:00:02Z",
      },
    ];
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue(history);

    render(<ChatWindow />);

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

  it("sends the message when Enter is pressed without Shift", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([{ type: "done", grounded: true, citations: [] }]),
    );

    await renderReady();
    const textbox = screen.getByLabelText("question");
    fireEvent.change(textbox, { target: { value: "when can I visit?" } });
    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: false });

    await waitFor(() => {
      expect(screen.getByText("when can I visit?")).toBeInTheDocument();
    });
    expect(chatStream.askChat).toHaveBeenCalledWith("when can I visit?", expect.anything());
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

  it("ignores Enter while a send is already loading, unlike the disabled Send button", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    const pending = new Promise<AsyncGenerator<ChatEvent>>(() => {
      // Never resolves - message A stays in flight for the whole test.
    });
    const askChatSpy = vi.spyOn(chatStream, "askChat").mockReturnValue(pending);
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
    expect(screen.getByText("Send")).toBeDisabled();
    expect(askChatSpy.mock.calls.length).toBe(callsBefore + 1);

    fireEvent.change(textbox, { target: { value: "second message" } });
    fireEvent.keyDown(textbox, { key: "Enter", shiftKey: false });

    expect(askChatSpy.mock.calls.length).toBe(callsBefore + 1);
    expect(textbox.value).toBe("second message");
  });

  it("keeps Send disabled across a supersede - an aborted request's own cleanup must not re-enable it early", async () => {
    vi.spyOn(chatStream, "fetchChatHistory").mockResolvedValue([]);
    let rejectA!: (reason: unknown) => void;
    const pendingA = new Promise<AsyncGenerator<ChatEvent>>((_resolve, reject) => {
      rejectA = reject;
    });
    let resolveB!: (events: AsyncGenerator<ChatEvent>) => void;
    const pendingB = new Promise<AsyncGenerator<ChatEvent>>((resolve) => {
      resolveB = resolve;
    });
    const askChatSpy = vi
      .spyOn(chatStream, "askChat")
      .mockReturnValueOnce(pendingA)
      .mockReturnValueOnce(pendingB);
    // See the previous test's comment: the spy's call count accumulates across
    // the file, so track the delta from here rather than an absolute count.
    const callsBefore = askChatSpy.mock.calls.length;

    await renderReady();
    const textbox = screen.getByLabelText("question");
    const sendButton = screen.getByText("Send");
    fireEvent.change(textbox, { target: { value: "when can I visit?" } });

    // Two native clicks dispatched synchronously in a single act() batch, before
    // React commits the first click's setLoading(true) (React itself refuses to
    // dispatch onClick to an already-disabled button once that commit lands, and
    // the old bug's real-world trigger - an unguarded Enter keydown - is now
    // fixed too). This reproduces the same kind of race: a second send starts
    // while the first is still in flight. abortRef is a ref, not render state,
    // so it still tracks correctly across both handlers even though both belong
    // to the same pre-batch render.
    act(() => {
      sendButton.click();
      sendButton.click();
    });

    expect(askChatSpy.mock.calls.length).toBe(callsBefore + 2);
    expect(sendButton).toBeDisabled();

    // The first send's own fetch now rejects because the second send's
    // handleSend aborted its controller - its finally must not clear loading
    // while the second send is still in flight.
    rejectA(new DOMException("The operation was aborted.", "AbortError"));
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(sendButton).toBeDisabled();
    expect(screen.queryByTestId("error")).toBeNull();

    resolveB(fakeEvents([{ type: "done", grounded: true, citations: [] }]));
    await waitFor(() => expect(sendButton).not.toBeDisabled());
  });
});
