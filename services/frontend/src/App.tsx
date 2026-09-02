import { useCallback, useEffect, useRef, useState } from "react";
import { ChatList } from "./components/ChatList";
import { ChatWindow } from "./components/ChatWindow";
import { FaqAdmin } from "./components/FaqAdmin";
import { PractitionerAdmin } from "./components/PractitionerAdmin";
import { StaffConsole } from "./components/StaffConsole";
import { StaffThread } from "./components/StaffThread";
import { setAssistant } from "./lib/consoleApi";
import { useConsolePoll } from "./lib/useConsolePoll";
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
  const [staffChatId, setStaffChatId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Whether this browser has a session yet, which is the precondition for every
  // session-scoped panel below. The session cookie is HttpOnly, so the SPA cannot look:
  // this is only ever the server's own answer, either `session_exists` on a listing or
  // the fact that a POST /chats came back.
  //
  // Monotonic on purpose. A session is minted once and never withdrawn while the page is
  // open, so this latches true and is never set back from a later listing — a panel that
  // unmounted on one transient answer would throw away whatever a staff member had typed
  // into it, and remounting is not a refresh of anything.
  const [sessionExists, setSessionExists] = useState(false);
  // One poll, every pane: the staff list renders it, and both open threads refetch when
  // their conversation's newest message advances past what they hold — which is what
  // makes a staff reply appear in the patient's thread, and a patient message appear in
  // the staff member's, with no channel of its own to keep in step.
  const poll = useConsolePoll();
  const activeLastMessageAt =
    poll.conversations.find((c) => c.chat_id === activeChatId)?.last_message_at ??
    null;
  const staffConversation = poll.conversations.find(
    (c) => c.chat_id === staffChatId,
  );

  // The poll re-reads the switch's position a moment later anyway, so nothing is
  // cached from the response here - which is what stops two tabs disagreeing about a
  // conversation one of them just took.
  function handleSetAssistant(enabled: boolean): void {
    if (staffChatId === null) return;
    setError(null);
    void setAssistant(staffChatId, enabled).catch((err: unknown) => {
      setError(
        err instanceof Error
          ? err.message
          : "Could not change the assistant for this conversation.",
      );
    });
  }

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
    // A returned chat proves a session: this call either minted one and set its cookie,
    // or was made under one that was already there. Either way the panels that need one
    // may now mount, and this is the only signal a first arrival ever gets — the
    // provisioning POST is the very thing that creates what they read.
    setSessionExists(true);
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
        setSessionExists(true);
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

  // Both sides at once, with no way in and out: this is a single-visitor
  // demonstration, so the staff member and the patient are the same person in two
  // panes. There is no authentication in this phase and no prompt for one.
  return (
    <div>
      <h1>VisitDoc</h1>
      <div style={{ display: "flex", gap: "2rem", alignItems: "flex-start" }}>
        <div data-testid="patient-pane" style={{ flex: 1, minWidth: 0 }}>
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
        lastMessageAt={activeLastMessageAt}
      />
        </div>
        <div data-testid="staff-pane" style={{ flex: 1, minWidth: 0 }}>
          <h2>Staff</h2>
          <StaffConsole
            conversations={poll.conversations}
            attentionTotal={poll.attentionTotal}
            activeChatId={staffChatId}
            onSelect={setStaffChatId}
          />
          <StaffThread
            chatId={staffChatId}
            assistantMayReply={staffConversation?.assistant_may_reply ?? true}
            pauseSecondsRemaining={
              staffConversation?.pause_seconds_remaining ?? null
            }
            // The same poll, serving the staff side's open thread as it already serves
            // the patient's: a patient message arriving into the conversation a staff
            // member is reading appears there without them clicking away and back.
            lastMessageAt={staffConversation?.last_message_at ?? null}
            onSetAssistant={handleSetAssistant}
          />
          {/*
            Not rendered until a session exists, which is what makes the wrong state
            unrepresentable rather than recovered from. Each of these panels reads
            something owned by the session and fetches it once, on mount: their effect
            has no reason to run a second time, so a fetch made before the session
            existed is the only answer they would ever have. On a first arrival that
            answer is a 401 from GET /console/practitioners, and the panel went on
            showing "no session" over an empty roster while the session it was minted
            beside sat there holding practitioners.

            Withholding them until `sessionExists` means the mount that performs the
            fetch cannot happen too early — there is no early state to repair, no second
            fetch to schedule, and nothing in the network layer that has to know about
            any of this. FaqAdmin joins them not because it misbehaves today but because
            it has the identical shape: GET /faq answering 200-with-an-empty-list for a
            session-less caller is the endpoint's choice, and a panel that is only
            correct while that choice holds is a panel resting on someone else's status
            code.
          */}
          {sessionExists && (
            <>
              <PractitionerAdmin />
              <FaqAdmin />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
