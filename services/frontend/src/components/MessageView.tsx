import type { Citation } from "../lib/chatStream";

interface MessageViewProps {
  sender: "patient" | "assistant";
  content: string;
  citations?: Citation[] | null;
}

/**
 * Renders one message by sender, reused for historical and in-progress messages.
 *
 * No derived "unanswered" treatment: a patient message with no reply yet is the
 * normal shape of a mid-burst message (FR-014), not a failure signal (research.md #8).
 */
export function MessageView({ sender, content, citations }: MessageViewProps) {
  return (
    <div data-testid="message" data-sender={sender}>
      <p style={{ whiteSpace: "pre-wrap" }}>{content}</p>
      {citations && citations.length > 0 && (
        <ul data-testid="citations">
          {citations.map((citation) => (
            <li key={`${citation.entry_id}-${citation.chunk_index}`}>{citation.chunk_text}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default MessageView;
