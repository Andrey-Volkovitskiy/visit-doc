import { AlertTriangle } from "lucide-react";
import type { ConsoleConversation } from "../lib/consoleApi";
import { ATTENTION_MARK_LABEL } from "../lib/consoleApi";

interface StaffConsoleProps {
  /** Already in display order — emphasized first, longest wait first. */
  conversations: ConsoleConversation[];
  activeChatId: string | null;
  onSelect: (chatId: string) => void;
  /**
   * Whether the poll has answered at all.
   *
   * Without it, a rail that has not heard back and a session that genuinely holds no
   * conversations render the same — and FR-010a wants those told apart, because they
   * ask different things of the reader.
   */
  loaded?: boolean;
}

/*
 * The attention total used to be rendered here and now lives in the console header
 * that `App` owns, outside the tabbed region (FR-021).
 *
 * It had to move: inside this component it sits inside the Conversations tab, so it
 * would disappear the moment a staff member opened Practitioners — and a count you have
 * to navigate back to in order to see is not a signal. The hook kept its name and its
 * meaning; only its owner changed, and the three tests that pinned it moved with it to
 * `App.test.tsx`.
 */

/**
 * What to call the reason a conversation needs a person.
 *
 * A guarded lookup rather than a direct index, because `escalation_reason` is typed
 * `string`, not the `AttentionMark` union. Every cause the server names today does have
 * a label — including the two no *message* ever carries, a corpus gap and a failed turn
 * — so this is not covering for a gap that exists now. It covers the ordinary way one
 * appears: a later phase naming a new cause. An unrecognised one falls back to the plain
 * statement rather than rendering `undefined` or inventing a wording for it.
 */
function reasonLabel(reason: string): string {
  return (
    (ATTENTION_MARK_LABEL as Record<string, string | undefined>)[reason] ??
    "Needs a person"
  );
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
  activeChatId,
  onSelect,
  loaded = true,
}: StaffConsoleProps) {
  return (
    <div
      data-testid="staff-console"
      className="border-rule bg-surface-sunken panes:border-r panes:border-b-0 min-h-0 overflow-y-auto border-b p-2"
    >
      {!loaded ? (
        <p
          data-testid="region-loading"
          data-region="conversations"
          className="text-ink-muted p-2 text-sm"
        >
          Loading conversations…
        </p>
      ) : conversations.length === 0 ? (
        <p data-testid="staff-no-conversations" className="text-ink-muted p-2 text-sm">
          No conversations yet.
        </p>
      ) : (
        <ul data-testid="staff-conversations">
          {conversations.map((conversation) => {
            const isActive = conversation.chat_id === activeChatId;
            return (
              <li key={conversation.chat_id}>
                <button
                  type="button"
                  data-testid="staff-conversation"
                  data-emphasized={String(conversation.emphasized)}
                  aria-current={isActive}
                  onClick={() => onSelect(conversation.chat_id)}
                  className={[
                    "mb-0.5 block w-full rounded-md border px-2.5 py-2 text-left text-sm",
                    isActive
                      ? "bg-accent-wash border-accent/30 font-medium"
                      : "hover:bg-page border-transparent",
                  ].join(" ")}
                >
                  <span className="flex items-center gap-2">
                    {/*
                      Never colour alone (FR-006). A conversation needing a person is
                      marked by a glyph, by weight, and by the words underneath — each of
                      which survives a greyscale screen and a reader who cannot
                      distinguish the hue.
                    */}
                    {conversation.emphasized && (
                      <AlertTriangle
                        className="text-attention size-3.5 flex-none"
                        aria-hidden="true"
                      />
                    )}
                    <span
                      className={`min-w-0 truncate ${
                        conversation.emphasized ? "font-semibold" : ""
                      }`}
                    >
                      {label(conversation)}
                    </span>
                  </span>
                  {conversation.escalation_reason !== null && (
                    <span className="text-attention mt-0.5 block text-xs">
                      {reasonLabel(conversation.escalation_reason)}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default StaffConsole;
