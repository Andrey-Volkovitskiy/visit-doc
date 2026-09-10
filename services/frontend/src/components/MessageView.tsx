import type { AttentionMark, RequestOutcome } from "../lib/chatStream";
import { ATTENTION_MARK_LABEL } from "../lib/consoleApi";

interface MessageViewProps {
  sender: "patient" | "assistant" | "staff";
  content: string;
  /**
   * What each request of this turn got — its question, its verdict, its citations.
   *
   * Null means no FAQ half ran: a booking reply is a real action's outcome, not a claim
   * about clinic policy, and a staff message was never retrieved against.
   */
  requestOutcomes?: RequestOutcome[] | null;
  /**
   * Whether to draw the outcome blocks at all.
   *
   * The staff console draws them; the patient pane does not. An outcome is evidence
   * about how an answer was produced — useful to a staff member auditing it, and the
   * clinic's working notes to a patient who asked a question and wants the answer.
   *
   * This is presentation, not access: the outcomes are in the payload either way, and
   * the session reading the patient pane owns the corpus and can read every entry of it
   * on the FAQ screen.
   */
  showOutcomes?: boolean;
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
 * One request's block: what was asked, and either what the answer stood on or that
 * nobody answered it and staff now have it.
 *
 * The degraded marker sits here rather than on the message, because the verdict does:
 * a marker on the message would be a claim about the requests it does not describe.
 */
function OutcomeView({ outcome }: { outcome: RequestOutcome }) {
  const degraded = outcome.verdict === "answered_unreranked";
  return (
    <div
      data-testid="request-outcome"
      data-position={outcome.position}
      data-verdict={outcome.verdict}
      style={{ borderLeft: "2px solid rgba(0,0,0,0.15)", paddingLeft: "0.5em" }}
    >
      <p data-testid="outcome-question" style={{ fontSize: "0.85em", opacity: 0.7 }}>
        {outcome.question}
      </p>
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
      {outcome.answer === null ? (
        <p data-testid="outcome-unanswered" style={{ fontSize: "0.85em" }}>
          Not answered from the knowledge base — forwarded to staff.
        </p>
      ) : (
        outcome.citations.length > 0 && (
          <ul data-testid="citations">
            {outcome.citations.map((citation) => (
              <li key={`${citation.entry_id}-${citation.chunk_index}`}>{citation.chunk_text}</li>
            ))}
          </ul>
        )
      )}
    </div>
  );
}

/**
 * Renders one message by sender, reused for historical and in-progress messages.
 *
 * No derived "unanswered" treatment: a patient message with no reply yet is the
 * normal shape of a mid-burst message, not a failure signal.
 */
export function MessageView({
  sender,
  content,
  requestOutcomes,
  showOutcomes = false,
  mark,
}: MessageViewProps) {
  const outcomes =
    showOutcomes && requestOutcomes !== null && requestOutcomes !== undefined
      ? requestOutcomes
      : [];
  const label = ROLE_LABEL[sender];
  return (
    <div data-testid="message" data-sender={sender}>
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
      {outcomes.map((outcome) => (
        <OutcomeView key={outcome.position} outcome={outcome} />
      ))}
    </div>
  );
}

export default MessageView;
