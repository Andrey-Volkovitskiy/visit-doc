import type { Citation } from "../lib/chatStream";

interface MessageViewProps {
  sender: "patient" | "assistant";
  content: string;
  citations?: Citation[] | null;
  /**
   * Whether this reply was checked against retrieved clinic documents.
   *
   * Null means it never was — a booking reply is a real action's outcome, not a
   * claim about clinic policy, so it is neither grounded nor abstaining and shows
   * no citation block at all.
   */
  grounded?: boolean | null;
}

/**
 * Renders one message by sender, reused for historical and in-progress messages.
 *
 * No derived "unanswered" treatment: a patient message with no reply yet is the
 * normal shape of a mid-burst message, not a failure signal.
 */
export function MessageView({ sender, content, citations, grounded }: MessageViewProps) {
  const showCitations = citations !== null && citations !== undefined && citations.length > 0;
  return (
    <div
      data-testid="message"
      data-sender={sender}
      data-grounded={grounded === null || grounded === undefined ? undefined : String(grounded)}
    >
      <p style={{ whiteSpace: "pre-wrap" }}>{content}</p>
      {showCitations && (
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
