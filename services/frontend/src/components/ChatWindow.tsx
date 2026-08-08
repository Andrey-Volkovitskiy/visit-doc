import { useEffect, useRef, useState } from "react";
import { askChat, fetchChatHistory, type Message } from "../lib/chatStream";
import { ClearChatButton } from "./ClearChatButton";
import { MessageView } from "./MessageView";

let nextMessageId = 0;

function localId(): string {
  nextMessageId += 1;
  return `local-${nextMessageId}`;
}

// Must match `ChatRequest.message`'s `max_length` in
// services/chat/src/chat/domain/schemas.py - checked client-side too so the
// patient gets immediate feedback instead of a round trip to hit the same 422.
const MAX_MESSAGE_LENGTH = 2000;

export function ChatWindow() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Every send's own controller lives here for the duration of its request, not
  // just the latest one - several turns can be genuinely in flight at once (a
  // burst of quick patient messages, FR-015), and each must run to completion
  // independently. The server alone decides whether an earlier turn in the same
  // chat gets superseded (register_and_cancel_previous, `cancelled` event,
  // FR-016) - a still-genuinely-completing earlier request must never be aborted
  // from here just because a newer send started, or its final `done` event (and
  // the reply the server already persisted) would be thrown away client-side,
  // only reappearing on the next reload.
  const activeControllersRef = useRef<Set<AbortController>>(new Set());

  useEffect(() => {
    void fetchChatHistory().then(setMessages);
  }, []);

  function handleCleared(): void {
    // The chat and its messages are gone server-side (FR-005); abort every
    // in-flight reply so a stale generation can't populate the now-empty chat
    // (FR-006) - there can be more than one in flight at once (see above).
    for (const controller of activeControllersRef.current) {
      controller.abort();
    }
    activeControllersRef.current.clear();
    setMessages([]);
    setStreaming(null);
    setError(null);
  }

  async function handleSend(): Promise<void> {
    const messageText = input;
    if (!messageText.trim() || messageText.length > MAX_MESSAGE_LENGTH) return;

    setInput("");
    setError(null);
    setStreaming("");

    const controller = new AbortController();
    activeControllersRef.current.add(controller);

    setMessages((prev) => [
      ...prev,
      {
        id: localId(),
        sender: "patient",
        content: messageText,
        grounded: null,
        citations: null,
        created_at: new Date().toISOString(),
      },
    ]);

    let accumulated = "";
    try {
      const events = await askChat(messageText, controller.signal);
      for await (const event of events) {
        if (event.type === "token") {
          accumulated += event.text;
          setStreaming(accumulated);
        } else if (event.type === "cancelled") {
          // Superseded by a newer message - remove the in-progress bubble and any
          // partial tokens entirely; never shown as final, never as an error
          // (FR-016, research.md #10).
          setStreaming(null);
          return;
        } else {
          setStreaming(null);
          setMessages((prev) => [
            ...prev,
            {
              id: localId(),
              sender: "assistant",
              content: event.grounded ? accumulated : (event.message ?? ""),
              grounded: event.grounded,
              citations: event.citations,
              created_at: new Date().toISOString(),
            },
          ]);
        }
      }
    } catch (err) {
      if (controller.signal.aborted) {
        // Only Clear Chat aborts a controller now (see `activeControllersRef`
        // above) - its own `handleCleared` already reset all display state, so
        // there's nothing left for this stale request to do.
        return;
      }
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      setStreaming(null);
      setInput(messageText);
    } finally {
      activeControllersRef.current.delete(controller);
    }
  }

  return (
    <div>
      <ClearChatButton onCleared={handleCleared} />
      <div data-testid="messages">
        {messages.map((message) => (
          <MessageView
            key={message.id}
            sender={message.sender}
            content={message.content}
            citations={message.citations}
          />
        ))}
        {streaming !== null && <MessageView sender="assistant" content={streaming} />}
      </div>
      <textarea
        aria-label="question"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void handleSend();
          }
        }}
        placeholder="Ask a question..."
      />
      <button onClick={() => void handleSend()}>Send</button>
      {input.length > MAX_MESSAGE_LENGTH && (
        <p data-testid="length-error" style={{ color: "red" }}>
          Message is too long ({input.length}/{MAX_MESSAGE_LENGTH} characters).
        </p>
      )}
      {error && <p data-testid="error">{error}</p>}
    </div>
  );
}

export default ChatWindow;
