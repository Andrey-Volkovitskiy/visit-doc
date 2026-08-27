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
  onRename: (chatId: string, fullName: string) => Promise<void>;
}

export function ChatList({
  chats,
  activeChatId,
  onSelect,
  onCreate,
  onDelete,
  onRename,
}: ChatListProps) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  // Kept beside the input rather than raised to the app's banner: a rejected name is
  // answered where it was typed, and the field stays open to correct it.
  const [renameError, setRenameError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function startRenaming(chat: ChatSummary): void {
    setRenamingId(chat.id);
    // A chat with no patient yet has only a derived label, which is not a name anyone
    // typed - so the field starts empty rather than pre-filled with it.
    setDraftName(chat.patient_name ?? "");
    setRenameError(null);
  }

  function stopRenaming(): void {
    setRenamingId(null);
    setDraftName("");
    setRenameError(null);
  }

  async function submitRename(chatId: string): Promise<void> {
    const trimmed = draftName.trim();
    if (trimmed === "") {
      setRenameError("Enter a name.");
      return;
    }
    setSaving(true);
    setRenameError(null);
    try {
      await onRename(chatId, trimmed);
      stopRenaming();
    } catch (err) {
      // The field stays open on every failure: some of these mean the name may in
      // fact have been saved, and closing it would suggest the attempt is over.
      setRenameError(
        err instanceof Error ? err.message : "Could not rename this chat.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div data-testid="chat-list">
      <button onClick={onCreate}>New chat</button>
      <ul>
        {chats.map((chat) => (
          <li key={chat.id} data-testid="chat-list-item" data-chat-id={chat.id}>
            {renamingId === chat.id ? (
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void submitRename(chat.id);
                }}
              >
                <input
                  aria-label="Patient name"
                  autoFocus
                  maxLength={200}
                  value={draftName}
                  onChange={(event) => setDraftName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") stopRenaming();
                  }}
                />
                <button type="submit" disabled={saving}>
                  Save
                </button>
                <button type="button" onClick={stopRenaming} disabled={saving}>
                  Cancel
                </button>
                {renameError !== null && (
                  <p data-testid="rename-error" role="alert">
                    {renameError}
                  </p>
                )}
              </form>
            ) : (
              <>
                <button
                  aria-current={chat.id === activeChatId ? "true" : undefined}
                  onClick={() => onSelect(chat.id)}
                >
                  {chatLabel(chat)}
                </button>
                <button
                  aria-label={`Rename ${chatLabel(chat)}`}
                  onClick={() => startRenaming(chat)}
                >
                  Rename
                </button>
                <button
                  aria-label={`Delete ${chatLabel(chat)}`}
                  onClick={() => setConfirmingId(chat.id)}
                >
                  Delete
                </button>
              </>
            )}
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
