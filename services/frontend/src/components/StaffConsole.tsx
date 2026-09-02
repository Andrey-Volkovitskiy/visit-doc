import type { ConsoleConversation } from "../lib/consoleApi";

interface StaffConsoleProps {
  /** Already in display order — emphasized first, longest wait first. */
  conversations: ConsoleConversation[];
  /** How many conversations need a person, counted once each by the server. */
  attentionTotal: number;
  activeChatId: string | null;
  onSelect: (chatId: string) => void;
}

function label(conversation: ConsoleConversation): string {
  // The server does not invent a name for a chat whose patient record does not exist
  // yet, so neither does this — it says what is actually true of the row.
  return conversation.patient_name ?? "Unnamed conversation";
}

/**
 * The session's conversations, with the ones needing a person marked and first.
 *
 * The order is rendered exactly as received. Emphasized first, longest wait first is a
 * rule the one query that can see every conversation applies; re-deriving it here would
 * be a second copy able to disagree with the total sitting beside it.
 *
 * Every conversation is listed, not only the emphasized ones: reading a conversation
 * nobody flagged is an ordinary thing for a staff member to do, and a queue that hides
 * the rest makes it impossible.
 */
export function StaffConsole({
  conversations,
  attentionTotal,
  activeChatId,
  onSelect,
}: StaffConsoleProps) {
  return (
    <div data-testid="staff-console">
      {/* Rendered even at zero: a missing badge and a badge reading zero say different
          things, and only one of them means "nothing needs you". */}
      <p>
        Needs a person: <strong data-testid="attention-total">{attentionTotal}</strong>
      </p>
      {conversations.length === 0 ? (
        <p data-testid="staff-no-conversations">No conversations yet.</p>
      ) : (
        <ul data-testid="staff-conversations">
          {conversations.map((conversation) => (
            <li key={conversation.chat_id}>
              <button
                data-testid="staff-conversation"
                data-emphasized={String(conversation.emphasized)}
                aria-current={conversation.chat_id === activeChatId}
                style={{
                  fontWeight: conversation.emphasized ? 700 : 400,
                  opacity: conversation.emphasized ? 1 : 0.7,
                }}
                onClick={() => onSelect(conversation.chat_id)}
              >
                {label(conversation)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default StaffConsole;
