import { useEffect, useRef, useState } from "react";
import { askChat, fetchChatHistory, type Message } from "../lib/chatStream";
import { ClearChatButton } from "./ClearChatButton";
import { MessageView } from "./MessageView";

let nextMessageId = 0;

function localId(): string {
  nextMessageId += 1;
  return `local-${nextMessageId}`;
}

export function ChatWindow() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    void fetchChatHistory().then(setMessages);
  }, []);

  function handleCleared(): void {
    // The chat and its messages are gone server-side (FR-005); abort any in-flight
    // reply so a stale generation can't populate the now-empty chat (FR-006).
    abortRef.current?.abort();
    setMessages([]);
    setStreaming(null);
    setError(null);
  }

  async function handleSend(): Promise<void> {
    const messageText = input;
    if (!messageText.trim()) return;

    setInput("");
    setError(null);
    setLoading(true);
    setStreaming("");

    // Aborts this window's own prior in-flight fetch, for immediate UI
    // responsiveness - the server-side generation registry is the authoritative
    // cancel-and-restart mechanism regardless (FR-015, research.md #9/#10).
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

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
        // This window aborted its own fetch because the patient sent a newer
        // message - that newer send's own flow handles display, not an error.
        return;
      }
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      setStreaming(null);
      setInput(messageText);
    } finally {
      // Only this send's own controller is still current if no newer handleSend
      // call has superseded it - otherwise clearing loading here would re-enable
      // Send while that newer call is still actively streaming.
      if (abortRef.current === controller) {
        setLoading(false);
      }
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
          if (e.key === "Enter" && !e.shiftKey && !loading) {
            e.preventDefault();
            void handleSend();
          }
        }}
        placeholder="Ask a question..."
      />
      <button onClick={() => void handleSend()} disabled={loading}>
        Send
      </button>
      {error && <p data-testid="error">{error}</p>}
    </div>
  );
}

export default ChatWindow;
