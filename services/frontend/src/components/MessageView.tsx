import type { AttentionMark, Citation } from "../lib/chatStream";
import { ATTENTION_MARK_LABEL } from "../lib/consoleApi";

interface MessageViewProps {
  sender: "patient" | "assistant" | "staff";
  content: string;
  citations?: Citation[] | null;
  /**
   * Whether this reply was checked against retrieved clinic documents.
   *
   * Null means it never was — a booking reply is a real action's outcome, not a
   * claim about clinic policy, so it is neither grounded nor abstaining and shows
   * no citation block at all. A staff message was never retrieved against either.
   */
  grounded?: boolean | null;
  /**
   * Why this message needs a person, when something decided one is needed.
   *
   * Passed only by the staff side. The patient sees their own message plainly: a mark
   * is a note for whoever has to act on it, not a status the sender is owed.
   */
  mark?: AttentionMark | null;
}

/**
 * What each sender is called on screen.
 *
 * The patient's own messages are absent deliberately: they are the reader's own, and a
 * label would say nothing they do not already know. Neither label is a person's name —
 * there is no staff member to name, and a human-sounding one would invite the patient to
 * believe there is.
 */
const ROLE_LABEL: Record<string, string> = {
  assistant: "AI assistant",
  staff: "Staff",
};

/**
 * Renders one message by sender, reused for historical and in-progress messages.
 *
 * No derived "unanswered" treatment: a patient message with no reply yet is the
 * normal shape of a mid-burst message, not a failure signal.
 */
export function MessageView({
  sender,
  content,
  citations,
  grounded,
  mark,
}: MessageViewProps) {
  const showCitations = citations !== null && citations !== undefined && citations.length > 0;
  const label = ROLE_LABEL[sender];
  return (
    <div
      data-testid="message"
      data-sender={sender}
      data-grounded={grounded === null || grounded === undefined ? undefined : String(grounded)}
    >
      {label !== undefined && (
        <p data-testid="role-label" style={{ opacity: 0.7, fontSize: "0.85em" }}>
          {label}
        </p>
      )}
      <p style={{ whiteSpace: "pre-wrap" }}>{content}</p>
      {mark !== null && mark !== undefined && (
        <p data-testid="attention-mark" data-mark={mark} style={{ fontSize: "0.85em" }}>
          {ATTENTION_MARK_LABEL[mark]}
        </p>
      )}
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
