import { useState } from "react";
import type { ChatSummary } from "../lib/chatStream";

/**
 * The label for a chat whose patient record does not exist yet.
 *
 * The server sends `patient_name: null` rather than inventing a placeholder, so the
 * label is built here from the chat's creation time — which is the only thing that
 * distinguishes one unnamed chat from another.
 */
export function chatLabel(chat: ChatSummary): string {
  if (chat.patient_name !== null) return chat.patient_name;
  const created = new Date(chat.created_at);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `Unnamed · ${pad(created.getHours())}:${pad(created.getMinutes())}`;
}

interface ChatListProps {
  chats: ChatSummary[];
  activeChatId: string | null;
  onSelect: (chatId: string) => void;
  onCreate: () => void;
  onDelete: (chatId: string) => void;
}

export function ChatList({
  chats,
  activeChatId,
  onSelect,
  onCreate,
  onDelete,
}: ChatListProps) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  return (
    <div data-testid="chat-list">
      <button onClick={onCreate}>New chat</button>
      <ul>
        {chats.map((chat) => (
          <li key={chat.id} data-testid="chat-list-item" data-chat-id={chat.id}>
            <button
              aria-current={chat.id === activeChatId ? "true" : undefined}
              onClick={() => onSelect(chat.id)}
            >
              {chatLabel(chat)}
            </button>
            <button
              aria-label={`Delete ${chatLabel(chat)}`}
              onClick={() => setConfirmingId(chat.id)}
            >
              Delete
            </button>
          </li>
        ))}
      </ul>
      {confirmingId !== null && (
        <div role="dialog">
          <p>
            This deletes the chat, its messages, its patient, and that patient&apos;s
            appointments. Do you agree?
          </p>
          <button
            onClick={() => {
              const target = confirmingId;
              setConfirmingId(null);
              onDelete(target);
            }}
          >
            Delete
          </button>
          <button onClick={() => setConfirmingId(null)}>Cancel</button>
        </div>
      )}
    </div>
  );
}

export default ChatList;
