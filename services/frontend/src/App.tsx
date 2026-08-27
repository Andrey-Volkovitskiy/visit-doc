import { useCallback, useEffect, useRef, useState } from "react";
import { ChatList } from "./components/ChatList";
import { ChatWindow } from "./components/ChatWindow";
import {
  createChat,
  deleteChat,
  fetchChats,
  renameChatPatient,
  type ChatListing,
  type ChatSummary,
} from "./lib/chatStream";

function App() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The server returns the list already ordered - chats holding messages first,
  // newest message first - so "the chat I was last talking in" is simply the first
  // one, and the rule lives in one place rather than being re-derived per render.
  const load = useCallback(async (): Promise<ChatListing> => {
    const listing = await fetchChats();
    setChats(listing.chats);
    return listing;
  }, []);

  const create = useCallback(async (): Promise<void> => {
    setError(null);
    // The response already *is* the new row, so the list is extended rather than
    // refetched — the server's ordering puts a brand-new, message-less chat first
    // among its peers, which is exactly where prepending puts it.
    const created = await createChat();
    setChats((prev) => [created, ...prev]);
    setActiveChatId(created.id);
  }, []);

  // A first arrival must provision exactly one session, and this effect can run twice
  // before either POST resolves — StrictMode double-invokes it in development, and both
  // requests would go out cookie-less and mint a separate session, chat, patient and
  // practitioner. Only the last `Set-Cookie` survives, stranding the other set.
  const provisioning = useRef(false);

  useEffect(() => {
    void load()
      .then((listing) => {
        // A first arrival is given a session, a chat, a patient, and a practitioner. An
        // *emptied* session looks identical from the list alone but must be left alone,
        // so the server's own answer decides it rather than a guess from `chats.length`.
        if (!listing.session_exists) {
          if (provisioning.current) return;
          provisioning.current = true;
          void create().catch((err: unknown) => {
            provisioning.current = false;
            setError(
              err instanceof Error ? err.message : "Could not start a chat.",
            );
          });
          return;
        }
        setActiveChatId((current) => current ?? listing.chats[0]?.id ?? null);
      })
      .catch((err: unknown) => {
        // Without this the whole first paint fails silently: no chats, no active chat,
        // and ChatWindow's empty state showing indefinitely with nothing explaining it.
        setError(
          err instanceof Error ? err.message : "Could not load your chats.",
        );
      });
  }, [load, create]);

  async function handleDelete(chatId: string): Promise<void> {
    setError(null);
    try {
      await deleteChat(chatId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete this chat.");
      return;
    }
    try {
      const remaining = await load();
      if (activeChatId === chatId) {
        // A session with zero chats is a valid state, so this legitimately lands on
        // null rather than provisioning a replacement.
        setActiveChatId(remaining.chats[0]?.id ?? null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your chats.");
    }
  }

  // The response carries the name the server actually stored, so the row is patched
  // from it rather than from what was typed — and there is no refetch, which is what
  // makes the new name appear the moment the request returns. Errors are re-thrown for
  // ChatList to show beside the input it was typed in.
  async function handleRename(chatId: string, fullName: string): Promise<void> {
    setError(null);
    const renamed = await renameChatPatient(chatId, fullName);
    setChats((prev) =>
      prev.map((chat) =>
        chat.id === renamed.chat_id
          ? { ...chat, patient_name: renamed.patient_name }
          : chat,
      ),
    );
  }

  return (
    <div>
      <h1>VisitDoc</h1>
      <ChatList
        chats={chats}
        activeChatId={activeChatId}
        onSelect={setActiveChatId}
        onCreate={() =>
          void create().catch((err: unknown) => {
            setError(
              err instanceof Error ? err.message : "Could not start a chat.",
            );
          })
        }
        onDelete={(chatId) => void handleDelete(chatId)}
        onRename={handleRename}
      />
      {error && <p data-testid="chat-list-error">{error}</p>}
      <ChatWindow
        chatId={activeChatId}
        // Only the sidebar's ordering depends on a completed turn, and only when the
        // active chat was not already first — refetching the whole list after every
        // turn re-runs a join over every message in the session to learn that nothing
        // moved. A failed refresh leaves the order stale, which is not worth an error
        // banner over a reply the patient can already see.
        onTurnComplete={() => {
          if (activeChatId !== null && chats[0]?.id === activeChatId) return;
          void load().catch(() => undefined);
        }}
      />
    </div>
  );
}

export default App;
