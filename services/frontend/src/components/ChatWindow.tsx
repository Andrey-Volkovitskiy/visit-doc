import { useRef, useState } from "react";
import { askChat, fetchChatHistory, type Message } from "../lib/chatStream";
import { useThreadReads, type Banner } from "../lib/useThreadReads";
import { MessageView } from "./MessageView";

let nextMessageId = 0;

// Marks a message this pane put on screen itself, which the server has not published
// back to it yet. Server ids are ULIDs, so the two can never collide.
const LOCAL_ID_PREFIX = "local-";

function localId(): string {
  nextMessageId += 1;
  return `${LOCAL_ID_PREFIX}${nextMessageId}`;
}

/**
 * Fold a freshly fetched history into what is on screen, keeping what it does not
 * carry yet.
 *
 * A turn's `done` event is streamed only once the server has committed the assistant
 * row, so a read *issued after* a turn ends always carries its reply. A read still in
 * flight *across* one does not: the mount/chat-switch fetch and the poll-driven
 * refetch are each answered from the thread as it stood when they were issued, and
 * anything this pane appended in the meantime is not in that answer - the patient's own
 * bubble, put up the moment they hit send and before its insert has landed, or a reply
 * that arrived while the fetch was outstanding. Replacing the thread with that answer
 * blanks them until the next poll tick notices the inserts: a couple of seconds in
 * which the patient's question looks unsent, or unanswered.
 *
 * So fetched history is the authority on everything it *does* carry, and locally
 * appended messages it does not account for are kept on the end rather than dropped.
 * Each one leaves as soon as a later read accounts for it, so a reply is never shown
 * twice once the server publishes its own row for it.
 *
 * Matched on sender and content, one server row consumed per local message, because a
 * local message has no server id to match on. Only the rows the server has grown since
 * this pane's last read are searched, so re-sending the same text is not mistaken for
 * the earlier identical send.
 */
function reconcile(shown: Message[], history: Message[]): Message[] {
  // Local messages are only ever appended, and a fetch only ever puts server rows
  // ahead of them, so they are always a suffix of what is on screen.
  const local = shown.filter((message) => message.id.startsWith(LOCAL_ID_PREFIX));
  if (local.length === 0) return history;

  const grown = history.slice(Math.max(shown.length - local.length, 0));
  const unpublished: Message[] = [];
  for (const message of local) {
    const index = grown.findIndex(
      (row) => row.sender === message.sender && row.content === message.content,
    );
    if (index === -1) {
      unpublished.push(message);
    } else {
      grown.splice(index, 1);
    }
  }
  return unpublished.length === 0 ? history : [...history, ...unpublished];
}

// Must match `ChatRequest.message`'s `max_length` in
// services/chat/src/chat/domain/schemas.py - checked client-side too so the
// patient gets immediate feedback instead of a round trip to hit the same 422.
const MAX_MESSAGE_LENGTH = 2000;

interface ChatWindowProps {
  /** The chat to show and send to. Null when the session holds no chats at all. */
  chatId: string | null;
  /** Called after a turn completes, so the chat list can refresh its ordering. */
  onTurnComplete?: () => void;
  /**
   * The newest message time the console poll reports for this chat.
   *
   * When it advances past the value this pane last read, something wrote into the
   * thread that this pane did not — a staff reply — and the history is refetched. That
   * is the whole mechanism by which a staff reply appears here without a reload, and it
   * rides the one poll that already runs rather than opening a channel of its own.
   */
  lastMessageAt?: string | null;
  /**
   * How many times that poll has answered, which changes on every tick.
   *
   * It is what makes a *retry* possible at all. `lastMessageAt` stops changing the
   * moment the newest message is the newest message, so an effect watching only it runs
   * once per new message and never again — a read that failed would have nothing left to
   * wake it. Ticking this instead lets the refetch below be attempted every couple of
   * seconds for as long as it is still owed, and cost nothing on the ticks where it is
   * not. Omitted by a caller not feeding this pane the poll, which then behaves as it
   * always did: a refetch per new value, and no retry.
   */
  pollTick?: number;
}

export function ChatWindow({
  chatId,
  onTurnComplete,
  lastMessageAt,
  pollTick,
}: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  // Keyed by turn rather than a single string: several turns can be in flight at once
  // (see below), and one shared slot means whichever finishes first clears the other's
  // in-progress bubble, while their tokens interleave in one bubble until it does.
  const [streaming, setStreaming] = useState<Record<string, string>>({});
  // Why the banner is up, not just its words. Two things raise one — a history that
  // would not load and a turn that would not send — and only the first is disproved by
  // a later read landing. See `Banner`.
  const [banner, setBanner] = useState<Banner | null>(null);
  // Every send's own controller lives here for the duration of its request, not
  // just the latest one - several turns can be genuinely in flight at once (a
  // burst of quick patient messages), and each must run to completion
  // independently. The server alone decides whether an earlier turn in the same
  // chat gets superseded (`cancelled` event) - a still-genuinely-completing
  // earlier request must never be aborted from here just because a newer send
  // started, or its final `done` event (and the reply the server already
  // persisted) would be thrown away client-side, only reappearing on reload.
  const activeControllersRef = useRef<Set<AbortController>>(new Set());
  const streamingCount = Object.keys(streaming).length;

  useThreadReads<Message[]>({
    chatId,
    lastMessageAt,
    pollTick,
    // Left unhandled while a reply is streaming, so the tick is retried once the turn
    // finishes: replacing the history mid-stream would race the reply about to be
    // appended to it.
    paused: streamingCount > 0,
    read: (id, signal) => fetchChatHistory(id, signal),
    onReset: () => {
      // Switching chats abandons whatever the previous one had in flight: its reply
      // belongs to a thread that is no longer on screen, and letting it land would
      // append it to the wrong history. The *reads* are the hook's to abort; these are
      // this pane's own streaming turns.
      for (const controller of activeControllersRef.current) {
        controller.abort();
      }
      activeControllersRef.current.clear();
      setMessages([]);
      setStreaming({});
      setBanner(null);
    },
    // Reconciled rather than assigned, on every read: a message sent before this answer
    // was composed is already on screen and is not in it — the patient's own bubble,
    // put up the moment they hit send, or a reply that arrived while the read was out.
    onLoaded: (history) => {
      setMessages((shown) => reconcile(shown, history));
      // This is the history the failed opening read could not load, so the banner it
      // raised goes with it — and only that one. A banner about a turn that would not
      // send is not disproved by a history that loaded, and clearing it would tell the
      // patient their question went in.
      setBanner((previous) => (previous?.kind === "read" ? null : previous));
    },
    onOpenFailed: (err) => {
      // A chat deleted in another tab 404s here. Reporting it beats leaving the pane
      // silently empty, and leaves `messages` a real array either way.
      setBanner({
        kind: "read",
        text: err instanceof Error ? err.message : "Could not load this chat's history.",
      });
    },
  });

  function clearStreaming(turnKey: string): void {
    setStreaming((prev) => {
      const { [turnKey]: _removed, ...rest } = prev;
      return rest;
    });
  }

  async function handleSend(): Promise<void> {
    const messageText = input;
    if (chatId === null) return;
    if (!messageText.trim() || messageText.length > MAX_MESSAGE_LENGTH) return;

    setInput("");
    setBanner(null);

    const turnKey = localId();
    setStreaming((prev) => ({ ...prev, [turnKey]: "" }));

    const controller = new AbortController();
    activeControllersRef.current.add(controller);

    setMessages((prev) => [
      ...prev,
      {
        id: localId(),
        sender: "patient",
        content: messageText,
        grounded: null,
        citations: null,
        attention_mark: null,
        created_at: new Date().toISOString(),
      },
    ]);

    let accumulated = "";
    try {
      const events = await askChat(chatId, messageText, controller.signal);
      for await (const event of events) {
        if (event.type === "token") {
          accumulated += event.text;
          setStreaming((prev) => ({ ...prev, [turnKey]: accumulated }));
        } else if (event.type === "silent") {
          // A person is handling this conversation, so nothing was generated and there
          // is nothing to render. The message stays in the thread exactly as sent.
          clearStreaming(turnKey);
          return;
        } else if (event.type === "cancelled") {
          // This turn produced no reply - superseded by a newer message, or a person
          // took the conversation over before it was written. Remove the in-progress
          // bubble and any partial tokens entirely; never shown as final, never as an
          // error. Only this turn's, so a sibling turn still streaming keeps its own.
          clearStreaming(turnKey);
          return;
        } else {
          clearStreaming(turnKey);
          setMessages((prev) => [
            ...prev,
            {
              id: localId(),
              sender: "assistant",
              // `message` is set only when there is no streamed text to show (the
              // FAQ abstention case); otherwise the accumulated tokens are the
              // reply, whether it came from the FAQ path, the booking path, or both.
              // Falsy rather than nullish, so an empty `message` falls back to the
              // tokens exactly as the server's own `done_event.message or answer`
              // does. Diverging here renders a bubble holding text the thread does
              // not hold, and `reconcile` matches on content - so that bubble would
              // never be accounted for by a history read, and would sit on screen
              // until the chat is switched away from.
              content: event.message || accumulated,
              grounded: event.grounded,
              citations: event.citations,
              attention_mark: null,
              created_at: new Date().toISOString(),
            },
          ]);
          onTurnComplete?.();
        }
      }
    } catch (err) {
      if (controller.signal.aborted) {
        // Aborted by a chat switch or a deletion, both of which already reset the
        // display state - there is nothing left for this stale request to do.
        return;
      }
      setBanner({
        kind: "send",
        text:
          err instanceof Error ? err.message : "Something went wrong. Please try again.",
      });
      clearStreaming(turnKey);
      setInput(messageText);
    } finally {
      activeControllersRef.current.delete(controller);
      // The client half of the server's guarantee that every turn ends in exactly one
      // terminal event: whatever ended this one - a terminal event, an abort, a broken
      // stream, or a stream that simply stopped - the in-progress bubble goes with it.
      // A no-op on every path that has already cleared its own, and the only thing
      // standing between a stream that ends without an ending and a bubble the patient
      // watches until they switch chats.
      clearStreaming(turnKey);
    }
  }

  if (chatId === null) {
    return (
      <div data-testid="no-chat" style={{ opacity: 0.5 }}>
        <p>No chat selected. Create one to start talking.</p>
      </div>
    );
  }

  return (
    <div>
      <div data-testid="messages">
        {messages.map((message) => (
          <MessageView
            key={message.id}
            sender={message.sender}
            content={message.content}
            citations={message.citations}
            grounded={message.grounded}
          />
        ))}
        {Object.entries(streaming).map(([turnKey, text]) => (
          <MessageView key={turnKey} sender="assistant" content={text} />
        ))}
      </div>
      <textarea
        aria-label="question"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void handleSend();
          }
        }}
        placeholder="Ask a question..."
      />
      <button onClick={() => void handleSend()}>Send</button>
      {input.length > MAX_MESSAGE_LENGTH && (
        <p data-testid="length-error" style={{ color: "red" }}>
          Message is too long ({input.length}/{MAX_MESSAGE_LENGTH} characters).
        </p>
      )}
      {banner && <p data-testid="error">{banner.text}</p>}
    </div>
  );
}

export default ChatWindow;
