import type { AttentionMark, Citation, FaqVerdict } from "../lib/chatStream";
import { ATTENTION_MARK_LABEL } from "../lib/consoleApi";

interface MessageViewProps {
  sender: "patient" | "assistant" | "staff";
  content: string;
  citations?: Citation[] | null;
  /**
   * Whether to draw the citation list at all.
   *
   * The staff console draws it; the patient pane does not. A citation is evidence
   * about how an answer was produced — useful to a staff member auditing it, and the
   * clinic's working notes to a patient who asked a question and wants the answer.
   *
   * This is presentation, not access: the citations are in the payload either way,
   * and the session reading the patient pane owns the corpus and can read every entry
   * of it on the FAQ screen.
   */
  showCitations?: boolean;
  /**
   * What the FAQ half of this turn did.
   *
   * Null means no FAQ specialist ran — a booking reply is a real action's outcome,
   * not a claim about clinic policy, and a staff message was never retrieved against.
   */
  faqVerdict?: FaqVerdict | null;
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
  showCitations = false,
  faqVerdict,
  mark,
}: MessageViewProps) {
  const citationsVisible =
    showCitations && citations !== null && citations !== undefined && citations.length > 0;
  // Only one verdict is worth a marker. An answer with citations and an abstention
  // message already say what they are, and a marker on every message marks nothing.
  const degraded = faqVerdict === "answered_unreranked";
  const label = ROLE_LABEL[sender];
  return (
    <div
      data-testid="message"
      data-sender={sender}
      data-faq-verdict={faqVerdict ?? undefined}
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
      {degraded && (
        <p
          data-testid="verdict-mark"
          title={
            "Produced without the reranking stage, so this answer rests on more, " +
            "less precisely selected clinic documents than usual."
          }
          style={{ fontSize: "0.85em", opacity: 0.7 }}
        >
          Answered without reranking
        </p>
      )}
      {citationsVisible && (
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
