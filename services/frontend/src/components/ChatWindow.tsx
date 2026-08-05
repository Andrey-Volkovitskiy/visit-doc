import { useState } from "react";
import { askChat, type Citation } from "../lib/chatStream";

export function ChatWindow() {
  const [message, setMessage] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSend(): Promise<void> {
    setAnswer("");
    setCitations([]);
    setError(null);
    setLoading(true);

    try {
      const events = await askChat(message);
      for await (const event of events) {
        if (event.type === "token") {
          setAnswer((prev) => prev + event.text);
        } else {
          setCitations(event.citations);
          if (!event.grounded && event.message) {
            setAnswer(event.message);
          }
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <input
        aria-label="question"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder="Ask a question..."
      />
      <button onClick={() => void handleSend()} disabled={loading}>
        Send
      </button>
      <p data-testid="answer">{answer}</p>
      {error && <p data-testid="error">{error}</p>}
      {citations.length > 0 && (
        <ul data-testid="citations">
          {citations.map((citation) => (
            <li key={`${citation.entry_id}-${citation.chunk_index}`}>{citation.chunk_text}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default ChatWindow;
